#!/usr/bin/env python3
"""Hands out ADR numbers so parallel sessions stop colliding.

Why: several Claude sessions add ADRs at the same time, each picking
"highest number on *its* copy of main + 1". Two sessions branched from
the same main pick the same number, and git never notices because the
file names differ (`ADR-0220-a.md` vs `ADR-0220-b.md`) -- the clash only
surfaced by hand at merge time and forced a renumber (ADR-0164, 0203,
0214, 0218, 0219, 0220 all hit this). See ADR-0221.

The fix is to treat every pushed branch as a claim, not just main: a
number is "taken" once it exists on origin/main OR on any unmerged
remote branch. A session that pushes its ADR stub right after `new`
makes its claim visible to every other session within seconds, instead
of only when it merges hours later. `check` is the merge gate that
catches the remaining race, and `renumber` makes losing that race cheap.

Subcommands (all fetch every remote branch first unless --no-fetch):
    next                 print the next free number
    new SLUG --title T   create docs/decisions/ADR-NNNN-SLUG.md (stub);
                         commit and push it immediately to publish the claim
    check                exit 1 if one of this branch's ADR numbers is also
                         used, under a different file name, on origin/main or
                         on another branch that claimed it first
    renumber NNNN        move this branch's ADR-NNNN to the next free number
                         and rewrite `ADR-NNNN` citations this branch added

Usage:
    python3 scripts/adr_number.py new my-decision --title "My decision"
    git add docs/decisions && git commit -m "Claim ADR-NNNN" && git push
    ...
    python3 scripts/adr_number.py check   # right before merging
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ADR_DIR = "docs/decisions"
MAIN_REF = "origin/main"
_NAME_RE = re.compile(r"^ADR-(\d{4})-.+\.md$")
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    ).stdout


def fetch(repo: Path) -> None:
    _git(repo, "fetch", "--no-tags", "--prune", "--quiet", "origin",
         "+refs/heads/*:refs/remotes/origin/*")


def _number(name: str) -> int | None:
    match = _NAME_RE.match(name)
    return int(match.group(1)) if match else None


def _files_on_ref(repo: Path, ref: str) -> set[str]:
    out = _git(repo, "ls-tree", "--name-only", ref, f"{ADR_DIR}/")
    return {Path(line).name for line in out.splitlines() if _number(Path(line).name) is not None}


def _local_files(repo: Path) -> set[str]:
    return {p.name for p in (repo / ADR_DIR).glob("ADR-*.md") if _number(p.name) is not None}


def _current_branch(repo: Path) -> str:
    return _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def _other_branch_refs(repo: Path) -> list[str]:
    """Unmerged remote branches other than main and this branch's own
    upstream. Merged branches are skipped: everything they hold is
    already on main (or was deliberately renamed there since)."""
    own = f"origin/{_current_branch(repo)}"
    refs = _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").split()
    result = []
    for ref in refs:
        if ref in (MAIN_REF, own, "origin/HEAD", "origin"):
            continue
        merged = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ref, MAIN_REF], cwd=repo,
        ).returncode == 0
        if not merged:
            result.append(ref)
    return result


def claims(repo: Path) -> dict[int, set[tuple[str, str]]]:
    """number -> {(ref, file name)} across origin/main and other branches."""
    taken: dict[int, set[tuple[str, str]]] = {}
    for ref in [MAIN_REF, *_other_branch_refs(repo)]:
        for name in _files_on_ref(repo, ref):
            taken.setdefault(_number(name), set()).add((ref, name))
    return taken


def next_number(repo: Path) -> int:
    used = set(claims(repo)) | {_number(n) for n in _local_files(repo)}
    # Our own pushed branch may still hold a number we since moved away
    # from; count it too so we never hand it back out.
    own = f"origin/{_current_branch(repo)}"
    if _git(repo, "for-each-ref", f"refs/remotes/{own}").strip():
        used |= {_number(n) for n in _files_on_ref(repo, own)}
    return max(used, default=0) + 1


def _claim_time(repo: Path, ref: str, name: str) -> float:
    """When `ref` first added `name` (a rename counts as adding the new
    name). Uncommitted files claim "now"."""
    rev_range = ref if ref == "HEAD" else f"{MAIN_REF}..{ref}"
    out = _git(repo, "log", "--no-renames", "--diff-filter=A", "--format=%ct",
               rev_range, "--", f"{ADR_DIR}/{name}").split()
    return float(out[-1]) if out else time.time()


def find_collisions(repo: Path) -> list[str]:
    main_names = _files_on_ref(repo, MAIN_REF)
    ours = sorted(_local_files(repo) - main_names)
    problems = []
    by_number: dict[int, list[str]] = {}
    for name in _local_files(repo):
        by_number.setdefault(_number(name), []).append(name)
    for number, names in sorted(by_number.items()):
        if len(names) > 1:
            problems.append(f"ADR-{number:04d} used twice in this checkout: {sorted(names)}")
    taken = claims(repo)
    for name in ours:
        number = _number(name)
        for ref, other in sorted(taken.get(number, ())):
            if other == name:
                continue
            if ref == MAIN_REF:
                problems.append(f"{name}: ADR-{number:04d} is already on main as {other}")
            elif _claim_time(repo, ref, other) <= _claim_time(repo, "HEAD", name):
                problems.append(f"{name}: ADR-{number:04d} was claimed first by {ref} ({other})")
    return problems


def _lines_added_by_branch(repo: Path, base: str) -> dict[str, set[int]]:
    """Modified tracked files -> line numbers (in the working tree) this
    branch added or changed since `base`."""
    diff = _git(repo, "diff", "--no-renames", "-U0", "--diff-filter=M", base)
    added: dict[str, set[int]] = {}
    path = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif (match := _HUNK_RE.match(line)) and path:
            start, count = int(match.group(1)), int(match.group(2) or 1)
            added.setdefault(path, set()).update(range(start, start + count))
    return added


def renumber(repo: Path, old: int) -> int:
    old_tag = f"ADR-{old:04d}"
    main_names = _files_on_ref(repo, MAIN_REF)
    matches = [n for n in _local_files(repo) if _number(n) == old and n not in main_names]
    if len(matches) != 1:
        raise SystemExit(f"expected exactly one {old_tag} file added by this branch, found {matches}")
    new = next_number(repo)
    new_tag = f"ADR-{new:04d}"
    src = repo / ADR_DIR / matches[0]
    src.rename(src.with_name(matches[0].replace(old_tag, new_tag, 1)))

    pattern = re.compile(re.escape(old_tag) + r"(?!\d)")
    base = _git(repo, "merge-base", MAIN_REF, "HEAD").strip()
    whole_files = set(_git(repo, "diff", "--no-renames", "--name-only", "--diff-filter=A", base).split())
    whole_files |= set(_git(repo, "ls-files", "--others", "--exclude-standard").split())
    for rel in sorted(whole_files):
        path = repo / rel
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="surrogateescape")
            if pattern.search(text):
                path.write_text(pattern.sub(new_tag, text), encoding="utf-8", errors="surrogateescape")
    # In files main also has, only touch lines this branch wrote: a
    # pre-existing `ADR-NNNN` there cites main's ADR, not ours.
    for rel, line_numbers in _lines_added_by_branch(repo, base).items():
        path = repo / rel
        lines = path.read_text(encoding="utf-8", errors="surrogateescape").splitlines(keepends=True)
        changed = False
        for i in line_numbers:
            if i - 1 < len(lines) and pattern.search(lines[i - 1]):
                lines[i - 1] = pattern.sub(new_tag, lines[i - 1])
                changed = True
        if changed:
            path.write_text("".join(lines), encoding="utf-8", errors="surrogateescape")
    return new


def create(repo: Path, slug: str, title: str) -> Path:
    number = next_number(repo)
    path = repo / ADR_DIR / f"ADR-{number:04d}-{slug}.md"
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    path.write_text(
        f"# ADR-{number:04d}: {title}\n\n"
        f"**Status:** Proposed\n**Date:** {today}\n"
        f"**Deciders:** account owner, Claude Code session\n\n"
        f"## Context\n\n## Decision\n\n## Consequences\n",
        encoding="utf-8",
    )
    return path


def _repo_root() -> Path:
    return Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-fetch", action="store_true", help="use remote refs as already fetched")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("next")
    new_parser = sub.add_parser("new")
    new_parser.add_argument("slug")
    new_parser.add_argument("--title", required=True)
    sub.add_parser("check")
    renumber_parser = sub.add_parser("renumber")
    renumber_parser.add_argument("number", type=int)
    args = parser.parse_args(argv)

    repo = _repo_root()
    if not args.no_fetch:
        fetch(repo)
    if args.command == "next":
        print(f"{next_number(repo):04d}")
    elif args.command == "new":
        path = create(repo, args.slug, args.title)
        print(path.relative_to(repo))
        print("Commit and push this file now so other sessions see the claim.", file=sys.stderr)
    elif args.command == "check":
        problems = find_collisions(repo)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print("Run `python3 scripts/adr_number.py renumber NNNN` for each, then re-check.",
                  file=sys.stderr)
            return 1
        print("ADR numbers OK")
    elif args.command == "renumber":
        print(f"ADR-{args.number:04d} -> ADR-{renumber(repo, args.number):04d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
