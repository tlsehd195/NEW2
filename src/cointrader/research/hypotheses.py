"""Guardrails on NEW hypotheses, so the automated research loop cannot
turn into hidden optimisation or repeat known dead ends
(`docs/PROJECT_STATUS.md`, "죽은 방향").

A new hypothesis is refused when:

- any candidate belongs to a dead family: the daily/hourly time-series
  momentum grid (`ts_momentum_*`, H-0002/H-0005..H-0010) or the funding /
  basis carry 3/9/21 grids (`funding_carry_*`, `basis_carry_*`,
  H-0011/H-0012);
- the exact same candidate set was already registered under another id
  (the "same grid, different asset" pattern that PROJECT_STATUS calls a
  multiple-testing trap) -- unless a human registers it as an explicit
  replication;
- a candidate id was already seen on a locked TEST window for the same
  market (re-testing a known answer);
- it has no written rationale saying what is new;
- the rolling registration budget is spent (default: 3 hypotheses per
  30 days per family, and 3 per 30 days over all families combined, ADR-0031), so the loop cannot
  brute-force its way to a pass.
"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence

from cointrader.validation.locked_windows import LockedWindow
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog

DEAD_CANDIDATE_PATTERNS = ("ts_momentum_*", "funding_carry_*", "basis_carry_*")
AUTOMATED_ACTORS = frozenset({"AI", "SYSTEM", "CLAUDE", "BOT", "SCHEDULER", "RESEARCH_LOOP"})


class HypothesisRefused(ValueError):
    pass


@dataclass(frozen=True)
class Budget:
    max_per_window: int = 3
    window: timedelta = timedelta(days=30)
    # Cap over every kind combined (ADR-0031): a new kind's own per-kind bucket cannot add trial capacity.
    max_all_kinds: int = 3


def _family(candidates: Sequence[str]) -> str:
    return candidates[0].split("_")[0] if candidates else "?"


def check_new_hypothesis(
    hypothesis: Hypothesis,
    log_rows: Sequence[dict],
    locked: Sequence[LockedWindow],
    *,
    rationale: str,
    replication: bool = False,
    budget: Budget = Budget(),
) -> None:
    if any(r["hypothesis_id"] == hypothesis.hypothesis_id for r in log_rows):
        return  # already registered: `PreregistrationLog.verify` decides if it matches
    problems = []
    for c in hypothesis.candidates:
        for pat in DEAD_CANDIDATE_PATTERNS:
            if fnmatch.fnmatch(c, pat):
                problems.append(f"{c} belongs to a dead family ({pat}); see docs/PROJECT_STATUS.md")
    same = [r["hypothesis_id"] for r in log_rows if sorted(r["candidates"]) == sorted(hypothesis.candidates)]
    if same:
        automated = hypothesis.registered_by.strip().upper() in AUTOMATED_ACTORS
        if not replication or automated:
            problems.append(f"identical candidate set already registered as {same}; "
                            "only a human may register an explicit replication")
    for w in locked:
        if w.market in (hypothesis.market, "*"):
            seen = set(w.observed_by) & set(hypothesis.candidates)
            if seen:
                problems.append(f"{sorted(seen)} already observed on locked {w.name} for {w.market}")
    if len(rationale.strip()) < 40:
        problems.append("rationale must say (>= 40 chars) what evidence motivates it and how it differs from prior ones")
    fam = _family(hypothesis.candidates)
    recent = [r for r in log_rows if _family(r["candidates"]) == fam
              and datetime.fromisoformat(r["registered_at"]) >= hypothesis.registered_at - budget.window]
    if len(recent) >= budget.max_per_window:
        problems.append(f"registration budget spent: {len(recent)} {fam} hypotheses within {budget.window}")
    recent_all = [r for r in log_rows
                  if datetime.fromisoformat(r["registered_at"]) >= hypothesis.registered_at - budget.window]
    if len(recent_all) >= budget.max_all_kinds:
        problems.append(f"combined registration cap spent: {len(recent_all)} hypotheses of all kinds within "
                        f"{budget.window} (max {budget.max_all_kinds})")
    if problems:
        raise HypothesisRefused("; ".join(problems))


def record_rationale(path: Path, hypothesis_id: str, rationale: str, at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"hypothesis_id": hypothesis_id, "rationale": rationale, "at": at.isoformat()},
                           ensure_ascii=False) + "\n")


def register_checked(log: PreregistrationLog, log_path: Path, hypothesis: Hypothesis, locked: Sequence[LockedWindow],
                     *, rationale: str, replication: bool = False, budget: Budget = Budget()) -> str:
    rows = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()] \
        if log_path.exists() else []
    check_new_hypothesis(hypothesis, rows, locked, rationale=rationale, replication=replication, budget=budget)
    already = any(r["hypothesis_id"] == hypothesis.hypothesis_id for r in rows)
    digest = log.register(hypothesis)
    if not already:  # a re-run of the same registration keeps its original rationale
        record_rationale(log_path.with_name("hypothesis_rationale.jsonl"), hypothesis.hypothesis_id, rationale,
                         hypothesis.registered_at)
    return digest
