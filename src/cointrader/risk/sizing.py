"""Position sizing for spot (no leverage).

Principles carried over from tlsehd195/NEW-'s `risk/sizing.py`:

- inverse-volatility sizing toward a target volatility,
- a hard cap on the fraction of equity in any one market,
- fail-closed: any missing, non-finite or out-of-range input yields a
  zero-size result with the reason named, never a guess.

Spot only. Futures (leverage, margin, liquidation price) are out of
scope until designed separately; `size_spot_position` has no leverage
parameter on purpose.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class SizingConfig:
    target_annual_volatility: float = 0.20
    max_weight: float = 0.25
    min_order_value: float = 5_000.0  # Upbit KRW market minimum order value
    periods_per_year: float = 24 * 365  # hourly bars; crypto trades 24/7


@dataclass(frozen=True)
class SizingResult:
    target_weight: float
    target_value: float
    reason: str  # "sized" or the first failed condition

    @property
    def trade_allowed(self) -> bool:
        return self.reason == "sized"


def _ok(x: Optional[float]) -> bool:
    return x is not None and math.isfinite(x)


def realized_volatility(returns: Sequence[float], periods_per_year: float) -> Optional[float]:
    """Annualized sample stdev of per-period returns; None with < 2 points."""
    if len(returns) < 2 or not all(math.isfinite(r) for r in returns):
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year)


def size_spot_position(
    *,
    signal_exposure: Optional[float],
    equity: Optional[float],
    annual_volatility: Optional[float],
    config: SizingConfig = SizingConfig(),
) -> SizingResult:
    """`signal_exposure` in [0, 1] is the strategy's desired exposure;
    the result scales it by target/realized volatility and caps it."""

    def refuse(reason: str) -> SizingResult:
        return SizingResult(0.0, 0.0, reason)

    if not _ok(signal_exposure):
        return refuse("signal_missing")
    if not 0.0 <= signal_exposure <= 1.0:
        return refuse("signal_out_of_range")
    if not _ok(equity) or equity <= 0:
        return refuse("equity_unknown")
    if not _ok(annual_volatility) or annual_volatility <= 0:
        return refuse("volatility_unknown")
    if signal_exposure == 0.0:
        return SizingResult(0.0, 0.0, "sized")
    weight = min(config.max_weight, signal_exposure * config.target_annual_volatility / annual_volatility)
    value = weight * equity
    if value < config.min_order_value:
        return refuse("below_min_order_value")
    return SizingResult(weight, value, "sized")
