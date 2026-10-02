"""Grid trading candidates (ADR-0021).

A grid is not a signal. It is a ladder of resting limit orders around a
centre price: buys below, sells above, each fill replaced by the opposite
order one step away. It earns the step on every round trip while the
price oscillates inside the ladder, and it accumulates the losing side of
the inventory when the price trends out of it. That is the claim the
validation measures: range-bound profit versus trend loss, after costs.

Source: retail grid bots (frequant.kr, Binance/Pionex grid bots), not an
academic paper. The candidates below are fixed, pre-registered rules;
nothing here is fitted.

Every candidate re-centres on a fixed calendar schedule (`reset_days`,
aligned to the Unix epoch so every fold resets on the same dates). At a
reset it uses only bars that closed before the reset bar:

- centre = previous close;
- half-width = `width_sigmas` * sigma_day * sqrt(reset_days) * centre,
  where sigma_day is the realised daily volatility of the previous
  `vol_lookback_days` of bars;
- step = half-width / `levels_per_side`.

`mode`:

- "neutral" (futures grid): flat at the centre, long one lot per level
  below it, short one lot per level above it. One side fully filled =
  `exposure` * equity of notional.
- "spot" (the classic spot grid bot): buys `levels_per_side` lots at
  market when it starts, sells one lot per level above the centre, buys
  one per level below. Long-only; fully filled at the bottom =
  `exposure` * equity.

A stop closes everything at market when the price trades one step beyond
the filled edge; the grid then stays flat until the next reset.

`max_efficiency_ratio` (optional): at a reset, deploy only if the
Kaufman efficiency ratio of the previous `er_lookback_days` daily closes
is below it (a recent range, not a recent trend); otherwise stay flat
until the next reset.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GridCandidate:
    strategy_id: str
    mode: str  # "neutral" | "spot"
    levels_per_side: int = 8
    width_sigmas: float = 1.5
    reset_days: int = 7
    vol_lookback_days: int = 14
    exposure: float = 1.0
    max_efficiency_ratio: Optional[float] = None
    er_lookback_days: int = 14
    family: str = "grid"

    def __post_init__(self) -> None:
        if self.mode not in ("neutral", "spot"):
            raise ValueError("mode must be neutral or spot")
        if self.levels_per_side < 1 or self.reset_days < 1 or self.vol_lookback_days < 2:
            raise ValueError("levels_per_side, reset_days >= 1 and vol_lookback_days >= 2")
        if not 0 < self.exposure <= 3 or self.width_sigmas <= 0:
            raise ValueError("exposure must be in (0, 3] and width_sigmas > 0")
        if self.max_efficiency_ratio is not None and not 0 < self.max_efficiency_ratio < 1:
            raise ValueError("max_efficiency_ratio must be in (0, 1)")

    @property
    def lookback_days(self) -> int:
        er = self.er_lookback_days if self.max_efficiency_ratio is not None else 0
        return max(self.vol_lookback_days, er)


GRID_CANDIDATES = (
    GridCandidate("grid_neutral_1p5sigma_v1", mode="neutral"),
    GridCandidate("grid_spot_1p5sigma_v1", mode="spot"),
    GridCandidate("grid_neutral_range_filter_v1", mode="neutral", max_efficiency_ratio=0.3),
)
GRID_FACTORIES = {c.strategy_id: c for c in GRID_CANDIDATES}
