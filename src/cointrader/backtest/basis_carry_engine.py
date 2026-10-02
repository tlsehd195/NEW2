"""Basis-neutral funding carry backtest (ADR-0013): a hedged variant of
`funding_carry_engine.py`'s single-direction carry (ADR-0009).

H-0011 (`funding_carry_engine`, TEST-11) failed all pre-registered
criteria, but its own `test_funding_only_return` was slightly positive --
the loss came almost entirely from unhedged directional price risk, not
the funding-collection mechanism. This engine hedges that risk out by
holding **both legs at once**: a spot position and an opposite-sign
futures position of equal notional (`abs(spot_delta) == abs(futures_delta)`,
opposite signs). Whatever direction a strategy picks, the two legs are
sized dollar-neutral so a pure price move cancels between them --only the
*basis* (the spot-vs-futures price difference) and the funding payment
are exposed.

**The basis return is measured from two real price series, never assumed
to be zero** (CLAUDE.md rule 4/5): `price_returns` here is the actual
`spot_return - futures_return` each settlement, using real spot candles
(`data.binance_vision.BinanceVisionSpotCandles`, ADR-0013) and real
futures candles/funding (as `funding_carry_engine` already uses). If the
basis widens or narrows, that shows up as real P&L, not as an assumed
zero.

Fail-closed data gaps (CLAUDE.md rule 4): a settlement where either leg's
mark price cannot be found (`_closest_prior_close` returns `None`) forces
**both** legs flat for that step and is counted in
`BasisCarryResult.gaps` -- never left half-hedged (one leg exposed, the
other flat) just because only one side's price was missing.

Liquidity capping (mirrors `funding_carry_engine`'s fix for H-0011's
crash): a desired position change is capped independently against each
leg's own bar-traded-value, and the **smaller** of the two allowed fills
is applied to both legs together, so the hedge never drifts out of
dollar-neutral just because one market was thinner than the other that
settlement. Counted in `BasisCarryResult.liquidity_capped_settlements`.

**Scope gap, stated rather than hidden** (`backtest-integrity-review`
skill): this treats "short spot" as symmetric with "short futures" for a
negative target -- real spot venues generally require a margin/borrow
facility to actually go short, which this backtest does not model. A
candidate that only ever needs `target >= 0` (short futures / long spot,
the direction the funding-carry literature actually describes) sidesteps
this; a candidate that goes negative should be treated as unvalidated for
short-spot mechanics specifically. Leverage/liquidation are still out of
scope, same as `funding_carry_engine`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from cointrader.backtest.costs import CandleCostModel
from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle

_CAP_SAFETY_MARGIN = 1 - 1e-9

# `spot_history`/`futures_history`/`funding_history` are each the prefix
# strictly before the settlement being decided (no look-ahead). Returns a
# target in [-1, 1]: positive = the "standard" carry direction (long spot
# / short futures, collects funding when the rate is positive), negative
# = the reverse, 0 = flat both legs.
BasisCarryStrategy = Callable[
    [Sequence[Candle], Sequence[Candle], Sequence[FundingRateRecord]], float
]


@dataclass(frozen=True)
class BasisCarryResult:
    price_returns: tuple[float, ...]  # net of cost; the REAL measured basis return each settlement
    funding_returns: tuple[float, ...]
    exposures: tuple[float, ...]  # the scalar target actually filled (post-cap) each settlement
    trades: int
    total_cost_fraction: float
    gaps: int = 0  # settlements skipped for a missing spot or futures mark price
    liquidity_capped_settlements: int = 0

    @property
    def total_return(self) -> float:
        growth = 1.0
        for price_ret, funding_ret in zip(self.price_returns, self.funding_returns):
            growth *= 1 + price_ret + funding_ret
        return growth - 1


def _closed_candles_before(sorted_candles: Sequence[Candle], at) -> list[Candle]:
    return [c for c in sorted_candles if c.close_time <= at]


def _closest_prior_close(sorted_candles: Sequence[Candle], at) -> Optional[float]:
    closed = _closed_candles_before(sorted_candles, at)
    if not closed:
        return None
    return closed[-1].close


def _traded_value_at_or_before(sorted_candles: Sequence[Candle], at) -> Optional[float]:
    closed = _closed_candles_before(sorted_candles, at)
    if not closed:
        return None
    last = closed[-1]
    return last.volume * last.open


def run_basis_carry_backtest(
    funding_history: Sequence[FundingRateRecord],
    strategy: BasisCarryStrategy,
    *,
    spot_history: Sequence[Candle] = (),
    futures_history: Sequence[Candle] = (),
    cost_model: CandleCostModel = CandleCostModel(),
    equity: float = 10_000_000.0,
    warmup: int = 0,
) -> BasisCarryResult:
    """Settlement cadence and `equity`'s role match
    `funding_carry_engine.run_funding_carry_backtest`. Unlike that engine,
    both `spot_history` and `futures_history` price the position (and its
    cost): with either left empty, that leg is never priceable, so every
    settlement reports a gap and stays flat -- this engine never silently
    assumes a hedge it cannot actually measure."""
    records = sorted(funding_history, key=lambda r: r.funding_time)
    if len(records) < warmup + 2:
        return BasisCarryResult((), (), (), 0, 0.0, 0, 0)
    spot_sorted = sorted(spot_history, key=lambda c: c.open_time)
    futures_sorted = sorted(futures_history, key=lambda c: c.open_time)

    exposure = 0.0
    price_returns: list[float] = []
    funding_returns: list[float] = []
    exposures: list[float] = []
    trades = 0
    total_cost = 0.0
    gaps = 0
    liquidity_capped_settlements = 0
    prev_spot_mark: Optional[float] = None
    prev_futures_mark: Optional[float] = None

    for i in range(warmup, len(records) - 1):
        record = records[i]
        spot_mark = _closest_prior_close(spot_sorted, record.funding_time)
        futures_mark = _closest_prior_close(futures_sorted, record.funding_time)
        marks_ok = (
            spot_mark is not None and math.isfinite(spot_mark) and spot_mark > 0
            and futures_mark is not None and math.isfinite(futures_mark) and futures_mark > 0
        )
        if not marks_ok:
            gaps += 1
            if exposure != 0.0:
                trades += 1
            exposure = 0.0
            price_returns.append(0.0)
            funding_returns.append(0.0)
            exposures.append(0.0)
            prev_spot_mark = None
            prev_futures_mark = None
            continue

        spot_prefix = _closed_candles_before(spot_sorted, record.funding_time)
        futures_prefix = _closed_candles_before(futures_sorted, record.funding_time)
        funding_prefix = records[:i]
        target = strategy(spot_prefix, futures_prefix, funding_prefix)
        if not (-1.0 <= target <= 1.0) or math.isnan(target):
            raise ValueError(f"strategy returned exposure {target!r}; must be within [-1, 1]")

        cost = 0.0
        if target != exposure:
            desired_delta = target - exposure
            desired_value = abs(desired_delta) * equity
            spot_bar_value = _traded_value_at_or_before(spot_sorted, record.funding_time)
            futures_bar_value = _traded_value_at_or_before(futures_sorted, record.funding_time)
            if desired_value > 0 and spot_bar_value and futures_bar_value and spot_bar_value > 0 and futures_bar_value > 0:
                spot_max = cost_model.max_participation * spot_bar_value
                futures_max = cost_model.max_participation * futures_bar_value
                filled_value = min(desired_value, spot_max, futures_max) * _CAP_SAFETY_MARGIN
                if filled_value < desired_value:
                    liquidity_capped_settlements += 1
                if filled_value > 0:
                    filled_delta = filled_value / equity * (1.0 if desired_delta > 0 else -1.0)
                    spot_cost = cost_model.cost_fraction(filled_value, spot_bar_value)
                    futures_cost = cost_model.cost_fraction(filled_value, futures_bar_value)
                    cost = (spot_cost + futures_cost) * abs(filled_delta)
                    exposure += filled_delta
                    trades += 1
            else:
                # no priceable bar on one or both legs to size the order
                # against: execute in full at zero modeled cost, same
                # documented fallback as `funding_carry_engine` -- never
                # silently refuse the position change instead.
                exposure = target
                trades += 1

        price_ret = 0.0
        if prev_spot_mark is not None and prev_futures_mark is not None and exposure != 0.0:
            spot_ret = spot_mark / prev_spot_mark - 1
            futures_ret = futures_mark / prev_futures_mark - 1
            price_ret = exposure * (spot_ret - futures_ret)

        # exposure > 0 means long spot / short futures; the futures leg
        # (short when exposure>0) collects funding when funding_rate>0,
        # same Binance convention as funding_carry_engine.
        funding_ret = 0.0
        if exposure != 0.0 and math.isfinite(record.funding_rate):
            funding_ret = exposure * record.funding_rate

        price_returns.append(price_ret - cost)
        funding_returns.append(funding_ret)
        exposures.append(exposure)
        total_cost += cost
        prev_spot_mark = spot_mark
        prev_futures_mark = futures_mark

    return BasisCarryResult(
        tuple(price_returns), tuple(funding_returns), tuple(exposures), trades, total_cost,
        gaps, liquidity_capped_settlements,
    )
