"""LiveActivationApproval: the human-sourced token needed to (a) pass
the live safety gate and (b) release an engaged kill switch.

Pattern adopted as-is from tlsehd195/NEW- (`broker/live/approval.py`).
Nothing in `src/` constructs one; only a human running
`scripts/grant_live_approval.py` does, and
`tests/test_safety_boundaries.py` scans the source tree to keep it that
way. This file is protected by `.claude/hooks/protect-safety-files.sh`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cointrader._time import require_aware

# A fixed, non-secret phrase the operator types verbatim -- not a
# credential, a deliberate extra step against an accidental/scripted True.
REQUIRED_CONFIRMATION_TOKEN = "I CONFIRM LIVE TRADING ACTIVATION"

_AUTOMATED_ACTORS = frozenset({"AI", "SYSTEM", "CLAUDE", "BOT", "SCHEDULER"})


@dataclass(frozen=True)
class LiveActivationApproval:
    approved_by: str  # a real operator identity, never an automated actor
    approved_at: datetime
    confirmation_token: str
    checklist_completed: bool
    # The strategy's walk-forward + PBO/DSR + held-out TEST evidence was
    # reviewed by a human, not only its status label.
    strategy_evidence_reviewed: bool

    def __post_init__(self) -> None:
        if not self.approved_by or self.approved_by.strip().upper() in _AUTOMATED_ACTORS:
            raise ValueError("approved_by must name a real human operator, never an automated actor")
        require_aware("LiveActivationApproval.approved_at", self.approved_at)
        if self.confirmation_token != REQUIRED_CONFIRMATION_TOKEN:
            raise ValueError("confirmation_token does not match the required phrase")
        if not self.checklist_completed:
            raise ValueError("checklist_completed must be True")
        if not self.strategy_evidence_reviewed:
            raise ValueError("strategy_evidence_reviewed must be True")

    def is_valid(self) -> bool:
        """Always True once constructed; __post_init__ rejected anything invalid."""
        return True


def approval_to_payload(approval: LiveActivationApproval) -> dict:
    return {
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at.isoformat(),
        "confirmation_token": approval.confirmation_token,
        "checklist_completed": approval.checklist_completed,
        "strategy_evidence_reviewed": approval.strategy_evidence_reviewed,
    }


def payload_to_approval(data: dict) -> LiveActivationApproval:
    """Re-runs full validation, so a hand-edited file is rejected exactly
    as if it had never been granted."""
    return LiveActivationApproval(
        approved_by=data["approved_by"],
        approved_at=datetime.fromisoformat(data["approved_at"]),
        confirmation_token=data["confirmation_token"],
        checklist_completed=data["checklist_completed"],
        strategy_evidence_reviewed=data["strategy_evidence_reviewed"],
    )
