"""The single pure function that decides whether a live order may be
sent. Pattern adopted as-is from tlsehd195/NEW- (`broker/live/
safety_gate.py`): every condition is independently required, and an
unconfigured limit is itself a failure. This file is protected by
`.claude/hooks/protect-safety-files.sh`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from cointrader._time import require_aware
from cointrader.live.approval import LiveActivationApproval
from cointrader.live.config import HealthStatus, LiveTradingConfig


@dataclass(frozen=True)
class SafetyGateContext:
    as_of: datetime
    config: LiveTradingConfig
    approval: Optional[LiveActivationApproval]
    strategy_status: Optional[str]  # evolution.status.CandidateStatus value
    kill_switch_engaged: bool
    exchange_health: Optional[HealthStatus]
    data_feed_health: Optional[HealthStatus]
    account_state_known: bool
    position_state_known: bool

    def __post_init__(self) -> None:
        require_aware("SafetyGateContext.as_of", self.as_of)


@dataclass(frozen=True)
class SafetyGateResult:
    passed: bool
    failed_conditions: tuple[str, ...]
    evaluated_at: datetime
    configuration_version: str

    def __post_init__(self) -> None:
        if self.passed == bool(self.failed_conditions):
            raise ValueError("passed must be True exactly when there are no failed conditions")


# Only a human-approved strategy may trade real money (evolution.status).
_LIVE_ELIGIBLE_STATUSES = frozenset({"APPROVED", "DEPLOYED"})


def evaluate_safety_gate(ctx: SafetyGateContext) -> SafetyGateResult:
    failed: list[str] = []
    if ctx.config.environment != "live":
        failed.append("environment_not_live")
    if not ctx.config.live_trading_enabled:
        failed.append("live_trading_not_enabled")
    if ctx.approval is None or not ctx.approval.is_valid():
        failed.append("activation_approval_missing_or_invalid")
    if ctx.strategy_status not in _LIVE_ELIGIBLE_STATUSES:
        failed.append("strategy_not_human_approved")
    if ctx.config.max_daily_loss is None:
        failed.append("risk_limit_not_configured_max_daily_loss")
    if ctx.config.max_orders_per_hour is None:
        failed.append("risk_limit_not_configured_max_orders_per_hour")
    if ctx.config.max_position_weight is None:
        failed.append("risk_limit_not_configured_max_position_weight")
    if ctx.kill_switch_engaged:
        failed.append("kill_switch_engaged")
    if ctx.exchange_health != HealthStatus.HEALTHY:
        failed.append("exchange_not_healthy")
    if ctx.data_feed_health != HealthStatus.HEALTHY:
        failed.append("data_feed_not_healthy")
    if not ctx.account_state_known:
        failed.append("account_state_unknown")
    if not ctx.position_state_known:
        failed.append("position_state_unknown")
    return SafetyGateResult(not failed, tuple(failed), ctx.as_of, ctx.config.configuration_version())
