"""Candidate lifecycle and the automatic-promotion boundary.

Structure adopted from tlsehd195/NEW- (`evolution/criteria.py`):

    CANDIDATE -> BACKTESTED -> VALIDATED -> OOS_TESTED   (automatic)
    OOS_TESTED -> APPROVED -> DEPLOYED                    (human only)

`next_automatic_status` is the only function the unattended pipeline
uses to promote a candidate, and it can never return APPROVED or
DEPLOYED. `approve_for_live` is the human path and requires a
`LiveActivationApproval`; no module in `src/` calls it
(`tests/test_safety_boundaries.py` checks both facts by AST scan).
Transitions are append-only records; a candidate's status is its latest
transition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from cointrader._time import require_aware
from cointrader.live.approval import LiveActivationApproval


class CandidateStatus(Enum):
    CANDIDATE = "CANDIDATE"
    BACKTESTED = "BACKTESTED"
    VALIDATED = "VALIDATED"
    OOS_TESTED = "OOS_TESTED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"
    DEPLOYED = "DEPLOYED"


HUMAN_ONLY_STATUSES = frozenset({CandidateStatus.APPROVED, CandidateStatus.DEPLOYED})


@dataclass(frozen=True)
class PromotionCriteria:
    """Fixed before any candidate is evaluated. Defaults are starting
    points to be set in a pre-registered hypothesis, not tuned values."""

    min_folds: int = 16
    max_pbo: float = 0.2
    min_deflated_sharpe: float = 0.95
    min_test_excess_return: float = 0.0  # vs buy-and-hold over the same TEST window


@dataclass(frozen=True)
class Evidence:
    """What is known about a candidate so far. None = not measured yet."""

    backtest_completed: bool = False
    fold_count: Optional[int] = None
    pbo: Optional[float] = None
    deflated_sharpe: Optional[float] = None
    test_excess_return: Optional[float] = None


@dataclass(frozen=True)
class StatusTransition:
    candidate_id: str
    from_status: CandidateStatus
    to_status: CandidateStatus
    reason: str
    decided_by: str  # "SYSTEM" for automatic transitions
    at: datetime
    criteria: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_aware("StatusTransition.at", self.at)


def next_automatic_status(
    current: CandidateStatus, evidence: Evidence, criteria: PromotionCriteria,
) -> tuple[CandidateStatus, str]:
    """One step forward, stay, or REJECTED. Never APPROVED/DEPLOYED."""
    if current is CandidateStatus.CANDIDATE:
        if evidence.backtest_completed:
            return CandidateStatus.BACKTESTED, "backtest_completed"
        return current, "backtest_not_completed"
    if current is CandidateStatus.BACKTESTED:
        if evidence.fold_count is None or evidence.pbo is None or evidence.deflated_sharpe is None:
            return current, "walk_forward_evidence_missing"
        if evidence.fold_count < criteria.min_folds:
            return CandidateStatus.REJECTED, "too_few_folds"
        if evidence.pbo > criteria.max_pbo:
            return CandidateStatus.REJECTED, "pbo_above_max"
        if evidence.deflated_sharpe < criteria.min_deflated_sharpe:
            return CandidateStatus.REJECTED, "deflated_sharpe_below_min"
        return CandidateStatus.VALIDATED, "walk_forward_criteria_met"
    if current is CandidateStatus.VALIDATED:
        if evidence.test_excess_return is None:
            return current, "held_out_test_missing"
        if evidence.test_excess_return < criteria.min_test_excess_return:
            return CandidateStatus.REJECTED, "held_out_test_below_benchmark"
        return CandidateStatus.OOS_TESTED, "held_out_test_passed"
    # OOS_TESTED, REJECTED, APPROVED, DEPLOYED: nothing automatic beyond here.
    return current, "no_automatic_transition"


def approve_for_live(
    candidate_id: str, current: CandidateStatus, approval: LiveActivationApproval, at: datetime,
) -> StatusTransition:
    """Human-only promotion OOS_TESTED -> APPROVED."""
    if not isinstance(approval, LiveActivationApproval) or not approval.is_valid():
        raise ValueError("approve_for_live requires a valid LiveActivationApproval")
    if current is not CandidateStatus.OOS_TESTED:
        raise ValueError(f"only an OOS_TESTED candidate can be approved, not {current.value}")
    return StatusTransition(candidate_id, current, CandidateStatus.APPROVED, "human_approval",
                            approval.approved_by, at)
