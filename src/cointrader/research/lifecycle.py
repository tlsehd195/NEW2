"""Append-only candidate status ledger (`research/candidate_status.jsonl`).

The lifecycle is `evolution.status`:

    CANDIDATE -> BACKTESTED -> VALIDATED -> OOS_TESTED   (automatic, here)
    OOS_TESTED -> APPROVED -> DEPLOYED                    (human only)

This module only ever writes AUTOMATIC transitions, each one computed by
`next_automatic_status`, which cannot return a human-only status. It
refuses to write any transition into a human-only status at all: how a
human's APPROVED/DEPLOYED decision is recorded (and how a running
process would load the `LiveActivationApproval` that backs it) is a
safety-boundary decision left to the account owner (ADR-0015, "held for
human decision"). Reading such a row, if a human ever writes one, is
supported so the live path can see it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from cointrader._time import require_aware
from cointrader.evolution.status import (
    HUMAN_ONLY_STATUSES,
    CandidateStatus,
    Evidence,
    PromotionCriteria,
    StatusTransition,
    next_automatic_status,
)

DEFAULT_LEDGER = Path(__file__).resolve().parents[3] / "research" / "candidate_status.jsonl"


class CandidateLedger:
    def __init__(self, path: Path = DEFAULT_LEDGER) -> None:
        self._path = path

    def rows(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(l) for l in self._path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def current(self, candidate_id: str) -> CandidateStatus:
        status = CandidateStatus.CANDIDATE
        for r in self.rows():
            if r["candidate_id"] == candidate_id:
                status = CandidateStatus(r["to_status"])
        return status

    def history(self, candidate_id: str) -> list[dict]:
        return [r for r in self.rows() if r["candidate_id"] == candidate_id]

    def _append(self, t: StatusTransition, hypothesis_id: Optional[str]) -> None:
        if t.to_status in HUMAN_ONLY_STATUSES:
            raise PermissionError("automated code never writes a human-only status transition")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "candidate_id": t.candidate_id, "from_status": t.from_status.value, "to_status": t.to_status.value,
                "reason": t.reason, "decided_by": t.decided_by, "at": t.at.isoformat(), "criteria": t.criteria,
                "hypothesis_id": hypothesis_id,
            }, ensure_ascii=False) + "\n")

    def advance(self, candidate_id: str, evidence: Evidence, criteria: PromotionCriteria, at: datetime,
                *, hypothesis_id: Optional[str] = None) -> list[StatusTransition]:
        """Apply automatic steps until nothing changes. Returns what was
        written (possibly nothing)."""
        require_aware("at", at)
        written = []
        current = self.current(candidate_id)
        while True:
            nxt, reason = next_automatic_status(current, evidence, criteria)
            if nxt is current:
                return written
            t = StatusTransition(candidate_id, current, nxt, reason, "SYSTEM", at, {
                "max_pbo": criteria.max_pbo, "min_dsr": criteria.min_deflated_sharpe,
                "min_folds": criteria.min_folds, "min_test_excess_return": criteria.min_test_excess_return,
                "evidence": {"fold_count": evidence.fold_count, "pbo": evidence.pbo,
                             "deflated_sharpe": evidence.deflated_sharpe,
                             "test_excess_return": evidence.test_excess_return},
            })
            self._append(t, hypothesis_id)
            written.append(t)
            current = nxt
