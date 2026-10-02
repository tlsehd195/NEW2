"""Baseline swing strategies.

These are research *candidates*, not claims of an edge. They exist so
the validation pipeline (walk-forward -> PBO/DSR -> locked TEST) has
something real to run end to end. Every parameter set is a separate
trial and must be counted as one in `pbo_dsr` (see
`validation.preregistration`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from cointrader.data.models import Candle


@dataclass(frozen=True)
class MovingAverageCross:
    """Long when the fast SMA of closes is above the slow SMA, else flat."""

    fast: int
    slow: int

    def __post_init__(self) -> None:
        if not (1 <= self.fast < self.slow):
            raise ValueError("need 1 <= fast < slow")

    @property
    def name(self) -> str:
        return f"ma_cross_{self.fast}_{self.slow}"

    @property
    def warmup(self) -> int:
        return self.slow

    def __call__(self, history: Sequence[Candle]) -> float:
        n = len(history)
        if n < self.slow:
            return 0.0
        fast = sum(history[i].close for i in range(n - self.fast, n)) / self.fast
        slow = sum(history[i].close for i in range(n - self.slow, n)) / self.slow
        return 1.0 if fast > slow else 0.0


@dataclass(frozen=True)
class DonchianBreakout:
    """Enter long when close breaks the prior `entry`-bar high; exit when
    close falls below the prior `exit`-bar low. Stateless: the position
    is re-derived from the last `exit + 1` bars each call, assuming flat
    before that window. That bounded memory is a deliberate
    simplification (the result is identical for any run whose exit
    condition fires within `exit` bars of the true one), stated here so
    it is not mistaken for a full stateful breakout system."""

    entry: int
    exit: int

    def __post_init__(self) -> None:
        if self.entry < 2 or self.exit < 2:
            raise ValueError("entry and exit lookbacks must be >= 2")

    @property
    def name(self) -> str:
        return f"donchian_{self.entry}_{self.exit}"

    @property
    def warmup(self) -> int:
        return self.entry + self.exit

    def __call__(self, history: Sequence[Candle]) -> float:
        n = len(history)
        if n <= self.warmup:
            return 0.0
        # Walk forward over the recent window to rebuild the position state.
        position = 0.0
        for t in range(n - self.exit - 1, n):
            close = history[t].close
            prior_high = max(history[i].high for i in range(t - self.entry, t))
            prior_low = min(history[i].low for i in range(t - self.exit, t))
            if position == 0.0 and close > prior_high:
                position = 1.0
            elif position == 1.0 and close < prior_low:
                position = 0.0
        return position


@dataclass(frozen=True)
class TimeSeriesMomentum:
    """Long when the trailing `lookback`-bar close-to-close return is
    positive, else flat. Absolute (time-series) momentum -- the sign of
    the recent past return, not a moving-average crossover.

    Literature grounding (S-grade, see ADR-0005): Liu & Tsyvinski (2021,
    Review of Financial Studies, "Risks and Returns of Cryptocurrency")
    document significant 1-4 week-ahead return continuation for BTC/ETH/
    XRP from 1-week and daily past returns; Liu, Tsyvinski & Wu (2022,
    Journal of Finance, "Common Risk Factors in Cryptocurrency") build a
    weekly-rebalanced momentum factor from 1-4 week formation windows and
    find it earns significant excess returns (t-stats 2.0-2.7) that a
    market+size two-factor model cannot explain. Both are single-asset
    (BTC) or cross-sectional results on WEEKLY bars; `lookback` here is in
    HOURLY bars, so a literal 1-week/3-week test uses 168/504."""

    lookback: int

    def __post_init__(self) -> None:
        if self.lookback < 2:
            raise ValueError("lookback must be >= 2")

    @property
    def name(self) -> str:
        return f"ts_momentum_{self.lookback}"

    @property
    def warmup(self) -> int:
        return self.lookback

    def __call__(self, history: Sequence[Candle]) -> float:
        n = len(history)
        if n <= self.lookback:
            return 0.0
        past_close = history[n - 1 - self.lookback].close
        now_close = history[n - 1].close
        return 1.0 if now_close > past_close else 0.0


def default_candidate_grid() -> list:
    """The fixed grid H-0001 already compares. Fixed here, before any
    result is seen, so the trial count fed to DSR is honest. Never add
    new candidates to this function -- H-0001 is already pre-registered
    and locked against exactly this list; a new candidate set gets its
    own grid function and its own hypothesis id."""
    grid: list = [MovingAverageCross(f, s) for f, s in ((12, 48), (24, 96), (24, 168), (48, 240))]
    grid += [DonchianBreakout(e, x) for e, x in ((24, 12), (48, 24), (96, 48), (168, 72))]
    return grid


def momentum_candidate_grid() -> list:
    """The literature-grounded grid for H-0002 (ADR-0005): the exact
    formation windows Liu & Tsyvinski (2021) and Liu, Tsyvinski & Wu
    (2022) test on weekly bars -- 1, 2, 3 and 4 weeks, expressed in
    hourly bars (168/336/504/672) -- plus the daily horizon the RFS
    paper also finds significant for BTC (24 bars)."""
    return [TimeSeriesMomentum(n) for n in (24, 168, 336, 504, 672)]


def momentum_candidate_grid_daily_decorrelated() -> list:
    """The shortest, middle and longest horizons from
    `momentum_candidate_grid_daily()` (2/14/28 days) instead of all 5
    adjacent lookbacks (ADR-0007). H-0006 found DSR up to 0.84 (real
    per-candidate edge) but PBO rose to 0.857 with all 5 candidates --
    likely because 5 adjacent lookbacks of the same rule are too
    correlated for CSCV's in-sample/out-of-sample comparison to be
    informative, not because the signal itself is overfit. Same
    TimeSeriesMomentum instances/names as the 5-candidate grid; only
    which ones are compared changes."""
    return [TimeSeriesMomentum(n) for n in (2, 14, 28)]


def momentum_candidate_grid_daily() -> list:
    """Same literature horizons as `momentum_candidate_grid`, expressed in
    DAILY bars instead of hourly (ADR-0006): 1/2/3/4 weeks -> 7/14/21/28,
    plus the shortest lookback `TimeSeriesMomentum` allows (2 days) in
    place of the RFS paper's literal 1-day horizon. H-0002 tested these
    horizons but decided and could trade every HOUR, far more often than
    the papers' weekly rebalance; this grid tests the same signal closer
    to that native cadence."""
    return [TimeSeriesMomentum(n) for n in (2, 7, 14, 21, 28)]
