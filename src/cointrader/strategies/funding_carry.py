"""Funding-rate carry candidates (ADR-0009).

These read `FundingAwareStrategy`'s two histories
(`backtest.funding_carry_engine`), not the price-only `Strategy` history
that every momentum candidate in `baselines.py` reads -- a different
research family, not a variant of momentum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle


@dataclass(frozen=True)
class FundingCarry:
    """Single-asset time-series carry (Management Science "Crypto Carry"
    family, single-leg simplification -- see ADR-0009 decision 5; the
    papers' cross-sectional, multi-asset version is out of scope here).

    If the trailing mean of the last `lookback` settlement funding rates
    is above `threshold`, go short to collect it (longs are paying); if
    it is below `-threshold`, go long. Otherwise flat. A dead zone
    around zero, same purpose as `AdaptiveIndicatorEnsemble`'s
    hysteresis band: it stops flipping position on noise around a
    near-zero average rate.
    """

    lookback: int
    threshold: float

    def __post_init__(self) -> None:
        if self.lookback < 1:
            raise ValueError("lookback must be >= 1")
        if self.threshold < 0:
            raise ValueError("threshold must be >= 0")

    @property
    def name(self) -> str:
        return f"funding_carry_{self.lookback}_{self.threshold}"

    @property
    def warmup(self) -> int:
        return self.lookback

    def __call__(self, price_history: Sequence[Candle], funding_history: Sequence[FundingRateRecord]) -> float:
        n = len(funding_history)
        if n < self.lookback:
            return 0.0
        recent = funding_history[n - self.lookback : n]
        mean_rate = sum(r.funding_rate for r in recent) / self.lookback
        if mean_rate > self.threshold:
            return -1.0
        if mean_rate < -self.threshold:
            return 1.0
        return 0.0


def funding_carry_candidate_grid() -> list[FundingCarry]:
    """Three lookback/threshold pairs, spread out the same way
    `momentum_candidate_grid_daily_decorrelated()` is (ADR-0007): a
    short, a medium and a long trailing window, not several near-duplicate
    lookbacks, so a future PBO/DSR study on this grid does not repeat the
    correlated-candidate problem H-0006 hit."""
    return [
        FundingCarry(lookback=3, threshold=0.0001),
        FundingCarry(lookback=9, threshold=0.0001),
        FundingCarry(lookback=21, threshold=0.0001),
    ]
