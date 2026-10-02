"""Funding-rate carry backtest (ADR-0009): a second engine, independent
of `backtest/engine.py`'s candle-only `Strategy` path used by every
momentum hypothesis so far. Momentum's `run_backtest`/`Strategy` are
untouched.

Position and PnL are evaluated at each funding settlement (Binance's
default is every 8h -- see `data/binance_funding.py`). Price PnL and
funding PnL are tracked separately (`FundingCarryResult.price_returns`
vs. `funding_returns`) so a report can never blur which part of a
result came from carry.

Every settlement is fail-closed (ADR-0009 decision 3, CLAUDE.md rule 4):
a non-finite `mark_price` forces the position flat for that step rather
than guessing at a price, and is counted in `FundingCarryResult.gaps`
so it is never silently absorbed into the return series.

A desired position change larger than `cost_model.max_participation` of
the settlement's bar traded value is capped, not aborted -- same
guarantee as `backtest.engine.run_backtest` (see its docstring): a thin
bar fills only partially and is counted in
`FundingCarryResult.liquidity_capped_settlements`, never thrown away the
whole study. (H-0011's first real-data run crashed on exactly this before
the cap was added here to match `engine.py`.)

**Scope gap, stated rather than hidden** (`backtest-integrity-review`
skill, futures section): exposure is a fraction of `equity` directly (no
leverage multiplier), so this engine does not model liquidation. A
result from it says nothing about what happens under leverage high
enough to be liquidated intrabar -- combine with
`risk/leverage.py`/`risk/futures_sizing.py` before trusting any
leveraged variant of a candidate that passes here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

from cointrader.backtest.costs import CandleCostModel
from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle

# Same margin as `backtest.engine.run_backtest`, for the same reason: keep
# a capped order fractionally below the exact participation cap so a
# subsequent float-rounding comparison inside `cost_model.cost_fraction`
# never sees it as still over the limit.
_CAP_SAFETY_MARGIN = 1 - 1e-9

# `price_history`/`funding_history` are each the prefix strictly before
# the settlement being decided (no look-ahead). Returns target exposure
# in [-1, 1]: negative = short (collects funding when the rate is
# positive), positive = long, 0 = flat.
FundingAwareStrategy = Callable[[Sequence[Candle], Sequence[FundingRateRecord]], float]


@dataclass(frozen=True)
class FundingCarryResult:
    price_returns: tuple[float, ...]  # net of cost; mark-to-market between settlements
    funding_returns: tuple[float, ...]  # funding settlement PnL only
    exposures: tuple[float, ...]
    trades: int
    total_cost_fraction: float
    gaps: int = 0  # settlements skipped for a missing/invalid mark_price
    liquidity_capped_settlements: int = 0  # settlements where the desired size exceeded max_participation

    @property
    def total_return(self) -> float:
        growth = 1.0
        for price_ret, funding_ret in zip(self.price_returns, self.funding_returns):
            growth *= 1 + price_ret + funding_ret
        return growth - 1


def _closed_candles_before(sorted_candles: Sequence[Candle], at) -> list[Candle]:
    """Only bars whose `close_time` is at or before `at` -- a bar whose
    `open_time <= at < close_time` has not finished yet and must not be
    visible (lookahead)."""
    return [c for c in sorted_candles if c.close_time <= at]


def _traded_value_at_or_before(sorted_candles: Sequence[Candle], at) -> float | None:
    closed = _closed_candles_before(sorted_candles, at)
    if not closed:
        return None
    last = closed[-1]
    return last.volume * last.open


def run_funding_carry_backtest(
    funding_history: Sequence[FundingRateRecord],
    strategy: FundingAwareStrategy,
    *,
    price_history: Sequence[Candle] = (),
    cost_model: CandleCostModel = CandleCostModel(),
    equity: float = 10_000_000.0,
    warmup: int = 0,
) -> FundingCarryResult:
    """`equity` only matters for the size-dependent cost term (same role
    as in `backtest.engine.run_backtest`). A settlement's mark price and
    funding rate come only from that settlement's own
    `FundingRateRecord` -- there is no candle-based mark-to-market
    between settlements in this first version (ADR-0009 decision 2).

    `price_history` (real futures candles, for their traded volume) is
    what prices every position change's cost; with no `price_history`
    (the default), no bar-traded-value is ever found, so every trade is
    zero-cost -- fine for unit tests of the strategy/engine logic, but a
    caller validating a real candidate must pass real futures candles or
    the result understates cost."""
    records = sorted(funding_history, key=lambda r: r.funding_time)
    if len(records) < warmup + 2:
        return FundingCarryResult((), (), (), 0, 0.0, 0, 0)
    price_sorted = sorted(price_history, key=lambda c: c.open_time)

    exposure = 0.0
    price_returns: list[float] = []
    funding_returns: list[float] = []
    exposures: list[float] = []
    trades = 0
    total_cost = 0.0
    gaps = 0
    liquidity_capped_settlements = 0
    prev_mark: float | None = None

    for i in range(warmup, len(records) - 1):
        record = records[i]
        mark_price = record.mark_price
        if mark_price is None or not math.isfinite(mark_price) or mark_price <= 0:
            gaps += 1
            if exposure != 0.0:
                trades += 1
            exposure = 0.0
            price_returns.append(0.0)
            funding_returns.append(0.0)
            exposures.append(0.0)
            prev_mark = None
            continue

        funding_prefix = records[:i]
        price_prefix = _closed_candles_before(price_sorted, record.funding_time)
        target = strategy(price_prefix, funding_prefix)
        if not (-1.0 <= target <= 1.0) or math.isnan(target):
            raise ValueError(f"strategy returned exposure {target!r}; must be within [-1, 1]")

        cost = 0.0
        if target != exposure:
            desired_delta = target - exposure
            desired_value = abs(desired_delta) * equity
            bar_value = _traded_value_at_or_before(price_sorted, record.funding_time)
            if desired_value > 0 and bar_value and bar_value > 0:
                max_value = cost_model.max_participation * bar_value
                filled_value = min(desired_value, max_value * _CAP_SAFETY_MARGIN)
                if filled_value < desired_value:
                    liquidity_capped_settlements += 1
                if filled_value > 0:
                    filled_delta = filled_value / equity * (1.0 if desired_delta > 0 else -1.0)
                    cost = cost_model.cost_fraction(filled_value, bar_value) * abs(filled_delta)
                    exposure += filled_delta
                    trades += 1
            else:
                # no priceable bar to size the order against: execute in
                # full at zero modeled cost, same fallback as no
                # `price_history` at all (documented above) -- never
                # silently refuse the position change instead.
                exposure = target
                trades += 1

        price_ret = 0.0
        if prev_mark is not None and exposure != 0.0:
            price_ret = exposure * (mark_price / prev_mark - 1)

        # Longs pay when funding_rate > 0 (Binance convention, ADR-0004);
        # shorts receive. Sign flips with exposure's sign, so this holds
        # for both directions without a branch on side.
        funding_ret = 0.0
        if exposure != 0.0 and math.isfinite(record.funding_rate):
            funding_ret = -exposure * record.funding_rate

        price_returns.append(price_ret - cost)
        funding_returns.append(funding_ret)
        exposures.append(exposure)
        total_cost += cost
        prev_mark = mark_price

    return FundingCarryResult(
        tuple(price_returns), tuple(funding_returns), tuple(exposures), trades, total_cost,
        gaps, liquidity_capped_settlements,
    )
