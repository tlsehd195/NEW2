"""Consistency check between the candidate ledgers and the window registries (T9).

Read-only. Looks at `configs/locked_windows.json`, `configs/reserved_windows.json`,
`research/screening.jsonl`, `research/preregistration.jsonl` and
`research/candidate_status.jsonl` and reports contradictions that would let a
held-out TEST range be reused. An `ERROR` fails CI; a `WARN` is something a
person should look at (for example a registration whose TEST was never run).
It never edits a ledger: those files are append-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Problem:
    level: str  # "ERROR" | "WARN"
    code: str
    message: str

    def __str__(self) -> str:
        return f"{self.level} {self.code}: {self.message}"


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _t(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _overlap(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> bool:
    return a0 < b1 and a1 > b0


def check_ledgers(configs: Path = _REPO / "configs", research: Path = _REPO / "research") -> list[Problem]:
    out: list[Problem] = []
    err = lambda code, msg: out.append(Problem("ERROR", code, msg))  # noqa: E731
    warn = lambda code, msg: out.append(Problem("WARN", code, msg))  # noqa: E731

    locked = _json(configs / "locked_windows.json")
    reserved = _json(configs / "reserved_windows.json")
    screening = _rows(research / "screening.jsonl")
    prereg = _rows(research / "preregistration.jsonl")
    status = _rows(research / "candidate_status.jsonl")

    # 1. locked windows: unique names, sane ranges, cross-sectional children match their parent.
    by_name: dict[str, dict] = {}
    for w in locked:
        if w["name"] in by_name:
            err("locked-duplicate-name", f"{w['name']} appears twice in locked_windows.json")
        by_name[w["name"]] = w
        if _t(w["end"]) <= _t(w["start"]):
            err("locked-bad-range", f"{w['name']} ends before it starts")
    for name, w in by_name.items():
        if ":" in name:
            parent = by_name.get(name.split(":", 1)[0])
            if parent is None:
                err("locked-orphan-child", f"{name} has no parent window {name.split(':', 1)[0]}")
            elif (parent["start"], parent["end"]) != (w["start"], w["end"]):
                err("locked-child-range", f"{name} range differs from its parent {parent['name']}")

    # 2. reserved windows: one per market, never inside a locked window.
    seen_markets: set[str] = set()
    for r in reserved:
        if r["market"] in seen_markets:
            err("reserved-duplicate", f"{r['market']} has two reserved windows")
        seen_markets.add(r["market"])
        r0, r1 = _t(r["start"]), _t(r["end"])
        if r1 <= r0:
            err("reserved-bad-range", f"{r['market']} reserved window ends before it starts")
        for w in locked:
            if w["market"] in ("*", r["market"]) and _overlap(r0, r1, _t(w["start"]), _t(w["end"])):
                err("reserved-in-locked", f"{r['market']} reserved window overlaps locked {w['name']}")

    # 3. screening ledger vs reservations and locked windows.
    res_by_market = {r["market"]: r for r in reserved}
    for i, row in enumerate(screening, 1):
        tag = f"screening row {i} ({row.get('market')} {row.get('timeframe')})"
        start, val_end, end = _t(row["start"]), _t(row["validation_end"]), _t(row["end"])
        if not start < val_end < end:
            err("screening-bad-split", f"{tag}: start < validation_end < end does not hold")
        for w in locked:
            if w["market"] in ("*", row["market"]) and _overlap(start, val_end, _t(w["start"]), _t(w["end"])):
                err("screening-reads-locked", f"{tag}: train/validation range overlaps locked {w['name']}")
        held: Optional[dict] = res_by_market.get(row["market"])
        if held is None:
            if not any(w["market"] == row["market"] and _t(w["start"]) == val_end for w in locked):
                warn("screening-unreserved", f"{tag}: no reservation and no locked window starting at its validation_end")
        elif _t(held["start"]) < val_end:
            err("screening-reads-reserved", f"{tag}: validation_end {val_end:%Y-%m-%d} is after the reserved TEST start")

    # 4. registrations: unique ids; a TEST window that ends where the registration ends should be locked.
    ids: set[str] = set()
    for h in prereg:
        if h["hypothesis_id"] in ids:
            err("prereg-duplicate", f"{h['hypothesis_id']} registered twice")
        ids.add(h["hypothesis_id"])
        end = _t(h["data_end"])
        if not any(w["market"] in ("*", h["market"]) and _t(w["end"]) == end for w in locked):
            warn("prereg-not-locked", f"{h['hypothesis_id']} ({h['market']}, data_end {end:%Y-%m-%d}) has no locked window "
                                       "ending there: TEST still pending, or the lock was never recorded")

    # 5. lifecycle rows must point at a registered hypothesis.
    for s in status:
        hid = s.get("hypothesis_id")
        if hid and hid not in ids:
            err("status-unknown-hypothesis", f"{s.get('candidate_id')} refers to unregistered {hid}")
    return out
