from __future__ import annotations

import dataclasses
import math

import pytest

from cointrader.backtest.costs import CandleCostModel
from cointrader.backtest.funding import apply_funding_payment
from cointrader.backtest.futures_engine import run_futures_backtest
from cointrader.risk.futures_sizing import FuturesSizingConfig, size_futures_position
from cointrader.risk.leverage import (
    MarginTier,
    MarginTierNotFound,
    PositionSide,
    estimate_liquidation_price,
    margin_ratio,
)
from tests.helpers import make_candles

TIERS = [
    MarginTier(0, 50_000, 0.004, 0.0),
    MarginTier(50_000, None, 0.005, 50.0),
]


class TestMarginTier:
    def test_rejects_empty_tiers(self):
        with pytest.raises(MarginTierNotFound):
            estimate_liquidation_price(
                side=PositionSide.LONG, entry_price=100, position_size=10, wallet_balance=500, tiers=[]
            )

    def test_rejects_notional_no_tier_covers(self):
        tiers = [MarginTier(0, 1000, 0.004, 0.0)]
        with pytest.raises(MarginTierNotFound):
            estimate_liquidation_price(
                side=PositionSide.LONG, entry_price=100, position_size=100, wallet_balance=500, tiers=tiers
            )

    def test_invalid_tier_bounds_rejected(self):
        with pytest.raises(ValueError):
            MarginTier(100, 50, 0.004, 0.0)
        with pytest.raises(ValueError):
            MarginTier(0, 100, 1.5, 0.0)


class TestLiquidationPrice:
    def test_long_liquidates_below_entry(self):
        liq = estimate_liquidation_price(
            side=PositionSide.LONG, entry_price=100.0, position_size=100.0, wallet_balance=3400.0, tiers=TIERS
        )
        assert liq < 100.0

    def test_short_liquidates_above_entry(self):
        liq = estimate_liquidation_price(
            side=PositionSide.SHORT, entry_price=100.0, position_size=100.0, wallet_balance=3400.0, tiers=TIERS
        )
        assert liq > 100.0

    def test_more_leverage_moves_liquidation_closer_to_entry(self):
        far = estimate_liquidation_price(
            side=PositionSide.LONG, entry_price=100.0, position_size=100.0, wallet_balance=5000.0, tiers=TIERS
        )
        near = estimate_liquidation_price(
            side=PositionSide.LONG, entry_price=100.0, position_size=100.0, wallet_balance=1500.0, tiers=TIERS
        )
        assert near > far  # less margin -> liquidation price closer to (below) entry

    def test_rejects_non_finite_inputs(self):
        for bad in (math.nan, math.inf, 0.0, -1.0):
            with pytest.raises(ValueError):
                estimate_liquidation_price(
                    side=PositionSide.LONG, entry_price=bad, position_size=100.0, wallet_balance=3400.0, tiers=TIERS
                )

    def test_fully_collateralized_position_refuses_rather_than_returning_zero(self):
        # wallet_balance == full notional (no real leverage): the isolated
        # margin formula solves to a liquidation price of 0, which is not
        # a meaningful "can't be liquidated" answer -- fail-closed instead.
        with pytest.raises(ValueError):
            estimate_liquidation_price(
                side=PositionSide.LONG, entry_price=100.0, position_size=100.0, wallet_balance=10_000.0, tiers=TIERS
            )


class TestMarginRatio:
    def test_rises_as_mark_price_falls_for_a_long(self):
        r1 = margin_ratio(
            side=PositionSide.LONG, entry_price=100, mark_price=100, position_size=100, wallet_balance=3400, tiers=TIERS
        )
        r2 = margin_ratio(
            side=PositionSide.LONG, entry_price=100, mark_price=70, position_size=100, wallet_balance=3400, tiers=TIERS
        )
        assert r2 > r1


class TestFuturesSizing:
    def test_sizes_within_leverage_and_buffer(self):
        r = size_futures_position(
            side=PositionSide.LONG,
            signal_exposure=1.0,
            equity=10_000_000,
            annual_volatility=0.3,
            entry_price=100.0,
            tiers=TIERS,
            config=FuturesSizingConfig(max_leverage=3.0, min_liquidation_buffer=0.15),
        )
        assert r.trade_allowed
        assert r.notional / r.wallet_balance <= 3.0 + 1e-9
        assert abs(r.liquidation_price - 100.0) / 100.0 >= 0.15 - 1e-9

    def test_refuses_when_liquidation_too_close(self):
        r = size_futures_position(
            side=PositionSide.LONG,
            signal_exposure=1.0,
            equity=10_000_000,
            annual_volatility=0.3,
            entry_price=100.0,
            tiers=TIERS,
            config=FuturesSizingConfig(max_leverage=3.0, min_liquidation_buffer=0.99),
        )
        assert r.reason == "liquidation_too_close"

    def test_refuses_without_entry_price(self):
        r = size_futures_position(
            side=PositionSide.LONG, signal_exposure=1.0, equity=1.0, annual_volatility=0.3, entry_price=None, tiers=TIERS
        )
        assert r.reason == "entry_price_unknown"


class TestFundingPayment:
    def test_positive_rate_charges_a_long(self):
        p = apply_funding_payment(side=PositionSide.LONG, position_size=10, mark_price=100, funding_rate=0.0001)
        assert p.amount == pytest.approx(0.1)

    def test_positive_rate_pays_a_short(self):
        p = apply_funding_payment(side=PositionSide.SHORT, position_size=10, mark_price=100, funding_rate=0.0001)
        assert p.amount == pytest.approx(-0.1)

    def test_rejects_missing_rate(self):
        with pytest.raises(ValueError):
            apply_funding_payment(side=PositionSide.LONG, position_size=10, mark_price=100, funding_rate=math.nan)


class TestFuturesEngine:
    def test_funding_rate_length_mismatch_rejected(self):
        cs = make_candles(10)
        with pytest.raises(ValueError):
            run_futures_backtest(cs, lambda h: 1.0, [0.0001] * 3, tiers=TIERS)

    def test_missing_funding_rate_while_position_open_fails_closed(self):
        cs = make_candles(10)
        rates = [None] * len(cs)
        with pytest.raises(ValueError):
            run_futures_backtest(cs, lambda h: 2.0, rates, max_leverage=3.0, tiers=TIERS)

    def test_out_of_range_exposure_rejected(self):
        cs = make_candles(10)
        rates = [0.0] * len(cs)
        with pytest.raises(ValueError):
            run_futures_backtest(cs, lambda h: 5.0, rates, max_leverage=3.0, tiers=TIERS)

    def test_flat_strategy_costs_nothing(self):
        cs = make_candles(10)
        rates = [0.0] * len(cs)
        r = run_futures_backtest(cs, lambda h: 0.0, rates, max_leverage=3.0, tiers=TIERS)
        assert r.trades == 0 and r.total_return == 0.0 and r.liquidation_events == 0

    def test_leveraged_long_pays_funding_and_costs(self):
        cs = make_candles(20)
        rates = [0.0001] * len(cs)
        r = run_futures_backtest(cs, lambda h: 2.0, rates, max_leverage=3.0, tiers=TIERS)
        assert r.trades == 1
        assert r.total_funding_fraction > 0  # long paid positive funding
        assert r.total_cost_fraction > 0

    def test_crash_through_liquidation_price_force_closes_the_position(self):
        cs = make_candles(10, drift=0.0, vol=0.0)
        # Force a deep low on the entry bar (index 2 -> decision at t=1,
        # entry bar index t+1=2) that must cross any plausible long
        # liquidation price at 2x leverage.
        crashed = dataclasses.replace(cs[2], low=cs[2].open * 0.01)
        cs = cs[:2] + [crashed] + cs[3:]
        rates = [0.0] * len(cs)
        r = run_futures_backtest(cs, lambda h: 2.0 if len(h) >= 2 else 0.0, rates, max_leverage=3.0, tiers=TIERS)
        assert r.liquidation_events >= 1
        assert any(pr <= -1.9 for pr in r.period_returns)  # full margin loss on the 2x position
