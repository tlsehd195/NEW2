"""Trading protections: temporary pauses after a streak of losses or a
drawdown.

Borrowed idea: Freqtrade's `StoplossGuard`, `MaxDrawdown` and
`CooldownPeriod` protections (ADR-0014). They sit *below* the kill
switch: a protection only blocks NEW entries for a fixed time and then
lapses on its own, it never closes positions and never touches
`live.kill_switch`. Anything that needs a human (a real incident, a
broken feed) is the kill switch's job, and its release stays human-only.

The module is pure: the caller passes the closed trades and equity
points it already has, plus `now`. Fail-closed: a NaN/missing value in
the inputs blocks entries with a stated reason instead of being skipped.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from cointrader._time import require_aware


@dataclass(frozen=True)
class ClosedTrade:
    closed_at: datetime
    net_return: float  # fraction, after costs; < 0 is a loss

    def __post_init__(self) -> None:
        require_aware("ClosedTrade.closed_at", self.closed_at)


@dataclass(frozen=True)
class EquityPoint:
    at: datetime
    equity: float

    def __post_init__(self) -> None:
        require_aware("EquityPoint.at", self.at)


@dataclass(frozen=True)
class ProtectionDecision:
    entries_allowed: bool
    reason: str  # empty when allowed
    locked_until: Optional[datetime] = None


@dataclass(frozen=True)
class StoplossGuard:
    """Pause entries for `pause` after `max_losses` losing trades closed
    within the last `lookback`."""

    lookback: timedelta
    max_losses: int
    pause: timedelta

    def __post_init__(self) -> None:
        if self.max_losses < 1 or self.lookback <= timedelta(0) or self.pause <= timedelta(0):
            raise ValueError("StoplossGuard needs max_losses >= 1 and positive lookback/pause")

    def lock_until(self, trades: Sequence[ClosedTrade], now: datetime) -> Optional[datetime]:
        losses = sorted(
            t.closed_at for t in trades
            if now - self.lookback <= t.closed_at <= now and t.net_return < 0
        )
        if len(losses) < self.max_losses:
            return None
        return losses[-1] + self.pause


@dataclass(frozen=True)
class MaxDrawdownGuard:
    """Pause entries for `pause` when the peak-to-trough drawdown of the
    equity points within the last `lookback` exceeds `max_drawdown`
    (fraction, e.g. 0.1 = 10%)."""

    lookback: timedelta
    max_drawdown: float
    pause: timedelta

    def __post_init__(self) -> None:
        if not (0.0 < self.max_drawdown < 1.0) or self.lookback <= timedelta(0) or self.pause <= timedelta(0):
            raise ValueError("MaxDrawdownGuard needs 0 < max_drawdown < 1 and positive lookback/pause")

    def lock_until(self, equity: Sequence[EquityPoint], now: datetime) -> Optional[datetime]:
        window = sorted((p for p in equity if now - self.lookback <= p.at <= now), key=lambda p: p.at)
        peak = -math.inf
        breached_at: Optional[datetime] = None
        for p in window:
            peak = max(peak, p.equity)
            if peak > 0 and 1 - p.equity / peak > self.max_drawdown:
                breached_at = p.at
        return None if breached_at is None else breached_at + self.pause


@dataclass(frozen=True)
class CooldownPeriod:
    """No new entry within `pause` of the last closed trade, win or lose."""

    pause: timedelta

    def __post_init__(self) -> None:
        if self.pause <= timedelta(0):
            raise ValueError("CooldownPeriod.pause must be positive")

    def lock_until(self, trades: Sequence[ClosedTrade], now: datetime) -> Optional[datetime]:
        closed = [t.closed_at for t in trades if t.closed_at <= now]
        return max(closed) + self.pause if closed else None


def evaluate_protections(
    *,
    now: datetime,
    trades: Sequence[ClosedTrade],
    equity: Sequence[EquityPoint],
    stoploss_guards: Sequence[StoplossGuard] = (),
    drawdown_guards: Sequence[MaxDrawdownGuard] = (),
    cooldowns: Sequence[CooldownPeriod] = (),
) -> ProtectionDecision:
    """Entries are allowed only if no protection is locked at `now`.
    When several are locked, the one that lasts longest is reported."""
    require_aware("now", now)
    bad = [t for t in trades if not math.isfinite(t.net_return)]
    bad_eq = [p for p in equity if not math.isfinite(p.equity)]
    if bad or bad_eq:
        return ProtectionDecision(False, "protection inputs contain non-finite values; cannot evaluate")

    locks: list[tuple[datetime, str]] = []
    for g in stoploss_guards:
        until = g.lock_until(trades, now)
        if until is not None and until > now:
            locks.append((until, f"{g.max_losses} losing trades within {g.lookback}"))
    for g in drawdown_guards:
        until = g.lock_until(equity, now)
        if until is not None and until > now:
            locks.append((until, f"drawdown above {g.max_drawdown:.1%} within {g.lookback}"))
    for g in cooldowns:
        until = g.lock_until(trades, now)
        if until is not None and until > now:
            locks.append((until, f"cooldown {g.pause} after last closed trade"))
    if not locks:
        return ProtectionDecision(True, "")
    until, reason = max(locks)
    return ProtectionDecision(False, reason, until)
