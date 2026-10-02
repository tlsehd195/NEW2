"""Basis-neutral funding carry candidates (ADR-0013).

These read `BasisCarryStrategy`'s three histories
(`backtest.basis_carry_engine`) -- spot, futures, and funding -- not
`FundingCarry`'s two-history signature (`strategies/funding_carry.py`,
ADR-0009). Sign convention differs from `FundingCarry` too: here
`target > 0` means long spot / short futures (the hedged direction that
collects funding when the rate is positive), matching
`basis_carry_engine`'s docstring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle


@dataclass(frozen=True)
class BasisCarry:
    """Same trailing-mean-funding-rate signal as `FundingCarry`
    (ADR-0009), but the position it returns is always hedged
    (`basis_carry_engine` holds spot and futures at equal, opposite
    notional) instead of a single directional futures exposure. This is
    the actual cash-and-carry construction the Management Science
    "Crypto Carry" literature validates (ADR-0013 context), which
    H-0011's single-leg grid was not.

    `allow_reverse=False` (the default) restricts `target` to `[0, 1]`
    only -- the reverse direction needs shorting spot, which real spot
    venues generally cannot do without a separate margin/borrow facility
    that this backtest does not model (`basis_carry_engine`'s scope-gap
    note). Set it `True` only for a candidate explicitly meant to explore
    that unvalidated mechanic.
    """

    lookback: int
    threshold: float
    allow_reverse: bool = False

    def __post_init__(self) -> None:
        if self.lookback < 1:
            raise ValueError("lookback must be >= 1")
        if self.threshold < 0:
            raise ValueError("threshold must be >= 0")

    @property
    def name(self) -> str:
        suffix = "_rev" if self.allow_reverse else ""
        return f"basis_carry_{self.lookback}_{self.threshold}{suffix}"

    @property
    def warmup(self) -> int:
        return self.lookback

    def __call__(
        self,
        spot_history: Sequence[Candle],
        futures_history: Sequence[Candle],
        funding_history: Sequence[FundingRateRecord],
    ) -> float:
        n = len(funding_history)
        if n < self.lookback:
            return 0.0
        recent = funding_history[n - self.lookback : n]
        mean_rate = sum(r.funding_rate for r in recent) / self.lookback
        if mean_rate > self.threshold:
            return 1.0
        if mean_rate < -self.threshold and self.allow_reverse:
            return -1.0
        return 0.0


def basis_carry_candidate_grid() -> list[BasisCarry]:
    """Same lookback grid as `funding_carry_candidate_grid()` (ADR-0007's
    decorrelation rationale: short/medium/long, not near-duplicates), so
    H-0012's result is directly comparable to H-0011's on the signal
    itself, isolating the hedge as the one thing that changed."""
    return [
        BasisCarry(lookback=3, threshold=0.0001),
        BasisCarry(lookback=9, threshold=0.0001),
        BasisCarry(lookback=21, threshold=0.0001),
    ]
