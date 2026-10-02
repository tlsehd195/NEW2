"""Bar-by-bar backtest for a single-market, long-only spot strategy.

No look-ahead by construction: the strategy sees only candles up to and
including bar t (all closed), and its target position is executed at bar
t+1's open. Costs come from `CandleCostModel` on every change of
position, charged on the traded notional.

A desired position change larger than `cost_model.max_participation` of
the entry bar's traded value is never executed in full and never aborts
the run: it is filled only up to that cap, same spirit as
`costs.simulate_market_fill`'s partial fill on a thin order book. A real
strategy trading real size will hit real thin bars; silently pretending
every bar has unlimited liquidity would overstate every result, and
raising mid-backtest would make one illiquid hour in a multi-year run
throw away the whole study. `BacktestResult.liquidity_capped_bars` counts
how often this happened, so it is reported, never hidden
(`backtest-integrity-review` skill, section 4).

This is intentionally small. It exists to produce per-period net
returns that `validation.walk_forward` and `validation.pbo_dsr` consume;
it is not a portfolio engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence, overload

from cointrader.backtest.costs import CandleCostModel
from cointrader.data.models import Candle

# A strategy maps the history so far (oldest..current, all closed) to a
# target exposure in [0, 1]. 0 = flat, 1 = fully invested.
Strategy = Callable[[Sequence[Candle]], float]


class PrefixView(Sequence[Candle]):
    """Read-only view of `candles[:end]` without copying, so a strategy
    physically cannot index a bar that has not closed yet."""

    def __init__(self, candles: Sequence[Candle], end: int) -> None:
        self._candles = candles
        self._end = end

    def __len__(self) -> int:
        return self._end

    @overload
    def __getitem__(self, index: int) -> Candle: ...
    @overload
    def __getitem__(self, index: slice) -> Sequence[Candle]: ...

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self._candles[i] for i in range(*index.indices(self._end))]
        if index < 0:
            index += self._end
        if not 0 <= index < self._end:
            raise IndexError("index outside the visible (closed) history")
        return self._candles[index]


@dataclass(frozen=True)
class BacktestResult:
    period_returns: tuple[float, ...]  # net return of each bar from t+1 open to t+2 open, per executed bar
    exposures: tuple[float, ...]
    trades: int
    total_cost_fraction: float  # sum of cost fractions paid, as a fraction of equity
    liquidity_capped_bars: int = 0  # bars where the desired size exceeded max_participation

    @property
    def total_return(self) -> float:
        growth = 1.0
        for r in self.period_returns:
            growth *= 1 + r
        return growth - 1


# Shrink a capped order fractionally below the exact participation cap so
# a subsequent float-rounding comparison inside cost_model.cost_fraction
# never sees it as still over the limit.
_CAP_SAFETY_MARGIN = 1 - 1e-9


def run_backtest(
    candles: Sequence[Candle],
    strategy: Strategy,
    *,
    cost_model: CandleCostModel = CandleCostModel(),
    equity: float = 10_000_000.0,
    warmup: int = 0,
) -> BacktestResult:
    """`equity` (KRW) only matters for the size-dependent impact term.
    Returns are open-to-open: the position decided at bar t is held from
    bar t+1's open to bar t+2's open."""
    if len(candles) < warmup + 3:
        return BacktestResult((), (), 0, 0.0)
    exposure = 0.0
    returns: list[float] = []
    exposures: list[float] = []
    trades = 0
    total_cost = 0.0
    liquidity_capped_bars = 0
    for t in range(warmup, len(candles) - 2):
        target = strategy(PrefixView(candles, t + 1))
        if not (0.0 <= target <= 1.0) or math.isnan(target):
            raise ValueError(f"strategy returned exposure {target!r}; must be within [0, 1]")
        entry_bar, exit_bar = candles[t + 1], candles[t + 2]
        cost = 0.0
        if target != exposure:
            desired_delta = target - exposure
            desired_value = abs(desired_delta) * equity
            bar_value = entry_bar.volume * entry_bar.open
            max_value = cost_model.max_participation * bar_value if bar_value > 0 else 0.0
            filled_value = min(desired_value, max_value * _CAP_SAFETY_MARGIN)
            if filled_value < desired_value:
                liquidity_capped_bars += 1
            if filled_value > 0:
                filled_delta = filled_value / equity * (1.0 if desired_delta > 0 else -1.0)
                cost = cost_model.cost_fraction(filled_value, bar_value) * abs(filled_delta)
                exposure += filled_delta
                trades += 1
        gross = exposure * (exit_bar.open / entry_bar.open - 1)
        returns.append(gross - cost)
        exposures.append(exposure)
        total_cost += cost
    return BacktestResult(tuple(returns), tuple(exposures), trades, total_cost, liquidity_capped_bars)
