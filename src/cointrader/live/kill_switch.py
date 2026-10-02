"""Kill switch.

Principle adopted as-is from tlsehd195/NEW- (`broker/live/kill_switch.py`):
the automated system may ENGAGE the kill switch on its own; RELEASING it
always requires a human's explicit action. `release_kill_switch` takes a
`LiveActivationApproval`, and no code path in `src/` calls it -- enforced
by an AST scan in `tests/test_safety_boundaries.py`. This file is
protected by `.claude/hooks/protect-safety-files.sh`.

State is an append-only event log; the current state is the latest event.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from cointrader._time import require_aware
from cointrader.live.approval import LiveActivationApproval
from cointrader.live.config import HealthStatus, LiveTradingConfig


@dataclass(frozen=True)
class KillSwitchEvent:
    engaged: bool
    reason: str
    triggered_by: str  # "SYSTEM" for an automatic engage; the operator for a release
    occurred_at: datetime
    configuration_version: str

    def __post_init__(self) -> None:
        if not self.reason:
            raise ValueError("KillSwitchEvent.reason must not be empty")
        if not self.triggered_by:
            raise ValueError("KillSwitchEvent.triggered_by must not be empty")
        require_aware("KillSwitchEvent.occurred_at", self.occurred_at)


@dataclass(frozen=True)
class KillSwitchTriggerContext:
    """All inputs already computed by the caller; this module does no I/O."""

    exchange_health: Optional[HealthStatus]
    data_feed_health: Optional[HealthStatus]
    account_state_known: bool
    position_state_known: bool
    daily_loss: Optional[float]  # KRW, positive = loss; None if not computable
    orders_in_last_hour: Optional[int]
    config: LiveTradingConfig


_CRITICAL = frozenset({HealthStatus.UNAVAILABLE, HealthStatus.UNKNOWN})


def evaluate_kill_switch_triggers(ctx: KillSwitchTriggerContext) -> Optional[str]:
    """First triggered reason, or None. A health value of None is treated
    as UNKNOWN, and a configured limit that cannot be measured triggers
    its own `*_unmeasurable` reason: not knowing is a reason to stop."""
    for name, status in (("exchange", ctx.exchange_health), ("data_feed", ctx.data_feed_health)):
        if status is None or status in _CRITICAL:
            return f"{name}_health_{(status or HealthStatus.UNKNOWN).value.lower()}"
    if not ctx.account_state_known:
        return "account_state_unknown"
    if not ctx.position_state_known:
        return "position_state_unknown"
    if ctx.config.max_daily_loss is not None:
        if ctx.daily_loss is None:
            return "daily_loss_unmeasurable"
        if ctx.daily_loss >= ctx.config.max_daily_loss:
            return "daily_loss_limit_breached"
    if ctx.config.max_orders_per_hour is not None:
        if ctx.orders_in_last_hour is None:
            return "order_frequency_unmeasurable"
        if ctx.orders_in_last_hour > ctx.config.max_orders_per_hour:
            return "abnormal_order_frequency"
    return None


def engage_kill_switch(*, reason: str, occurred_at: datetime, configuration_version: str) -> KillSwitchEvent:
    return KillSwitchEvent(True, reason, "SYSTEM", occurred_at, configuration_version)


def release_kill_switch(
    *, approval: LiveActivationApproval, occurred_at: datetime, configuration_version: str,
) -> KillSwitchEvent:
    """Human-only. There is no parameter-free way to produce engaged=False."""
    if not isinstance(approval, LiveActivationApproval) or not approval.is_valid():
        raise ValueError("release_kill_switch requires a valid LiveActivationApproval")
    return KillSwitchEvent(False, "released_by_operator", approval.approved_by, occurred_at, configuration_version)


class KillSwitchLog:
    """Append-only JSONL log. Missing or unreadable log => engaged
    (fail-closed): if the system cannot tell whether trading was halted,
    it must assume it was."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def record(self, event: KillSwitchEvent) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "engaged": event.engaged, "reason": event.reason, "triggered_by": event.triggered_by,
                "occurred_at": event.occurred_at.isoformat(), "configuration_version": event.configuration_version,
            }) + "\n")

    def latest(self) -> Optional[KillSwitchEvent]:
        lines = [l for l in self._path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            return None
        d = json.loads(lines[-1])
        return KillSwitchEvent(d["engaged"], d["reason"], d["triggered_by"],
                               datetime.fromisoformat(d["occurred_at"]), d["configuration_version"])

    def is_engaged(self) -> bool:
        if not self._path.exists():
            return True
        try:
            latest = self.latest()
        except (OSError, ValueError, KeyError):
            return True
        return latest is None or latest.engaged
