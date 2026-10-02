from __future__ import annotations

import dataclasses
import math

import pytest

from cointrader.backtest.costs import CandleCostModel, Side, simulate_market_fill
from cointrader.backtest.engine import PrefixView, run_backtest
from cointrader.data.models import OrderBookLevel, OrderBookSnapshot
from cointrader.strategies.baselines import (
    DonchianBreakout,
    MovingAverageCross,
    TimeSeriesMomentum,
    default_candidate_grid,
    momentum_candidate_grid,
    momentum_candidate_grid_daily,
    momentum_candidate_grid_daily_decorrelated,
)
from tests.helpers import T0, make_candles

BOOK = OrderBookSnapshot(
    "KRW-BTC", T0,
    bids=(OrderBookLevel(99.0, 1.0), OrderBookLevel(98.0, 2.0)),
    asks=(OrderBookLevel(101.0, 1.0), OrderBookLevel(102.0, 2.0)),
)


class TestMarketFill:
    def test_walks_the_book(self):
        fill = simulate_market_fill(BOOK, Side.BUY, 2.0, fee_rate=0.001)
        assert fill.average_price == pytest.approx(101.5)
        assert fill.slippage_vs_mid == pytest.approx(0.015)
        assert fill.fee == pytest.approx(0.203)
        assert fill.levels_consumed == 2 and fill.fully_filled

    def test_partial_fill_is_reported_not_extrapolated(self):
        fill = simulate_market_fill(BOOK, Side.SELL, 10.0, fee_rate=0.0)
        assert fill.filled_quantity == 3.0 and not fill.fully_filled


class TestCandleCostModel:
    def test_cost_grows_with_size(self):
        m = CandleCostModel()
        assert m.cost_fraction(1e5, 1e10) < m.cost_fraction(1e8, 1e10)
        assert m.cost_fraction(0, 1e10) == 0.0

    def test_refuses_oversized_order(self):
        with pytest.raises(ValueError):
            CandleCostModel(max_participation=0.01).cost_fraction(2e8, 1e10)


class TestEngine:
    def test_prefix_view_hides_future_bars(self):
        cs = make_candles(10)
        view = PrefixView(cs, 4)
        assert len(view) == 4 and view[-1] is cs[3]
        with pytest.raises(IndexError):
            view[4]
        assert view[2:10] == cs[2:4]

    def test_strategy_never_sees_the_bar_it_trades(self):
        cs = make_candles(30)
        seen = []

        def spy(history):
            seen.append(history[-1].open_time)
            return 1.0

        run_backtest(cs, spy)
        # decision on bar t executes at bar t+1 open: last decision uses bar n-3
        assert seen[-1] == cs[-3].open_time

    def test_always_flat_costs_nothing(self):
        r = run_backtest(make_candles(30), lambda h: 0.0)
        assert r.total_return == 0.0 and r.trades == 0

    def test_buy_and_hold_matches_price_path_minus_one_entry_cost(self):
        cs = make_candles(30)
        model = CandleCostModel()
        r = run_backtest(cs, lambda h: 1.0, cost_model=model)
        gross = cs[-1].open / cs[1].open - 1
        assert r.trades == 1
        assert r.total_return < gross
        assert r.total_return == pytest.approx(gross, abs=0.01)

    def test_thin_bar_caps_the_fill_instead_of_crashing(self):
        # Real bug found running against live Upbit data in CI: a desired
        # order far larger than what a thin bar actually traded used to
        # raise ValueError and abort the whole multi-year backtest.
        cs = make_candles(10)
        thin = dataclasses.replace(cs[3], volume=cs[3].volume * 1e-6)
        cs = cs[:3] + [thin] + cs[4:]
        r = run_backtest(cs, lambda h: 1.0 if len(h) >= 3 else 0.0)
        assert r.liquidity_capped_bars == 1
        assert 0.0 < r.exposures[2] < 1.0  # capped on the thin bar (t=2)
        assert r.exposures[3] == pytest.approx(1.0)  # catches up next bar

    def test_zero_volume_bar_blocks_the_trade_entirely(self):
        cs = make_candles(10)
        cs = cs[:3] + [dataclasses.replace(cs[3], volume=0.0)] + cs[4:]
        r = run_backtest(cs, lambda h: 1.0 if len(h) >= 3 else 0.0)
        assert r.exposures[2] == 0.0
        assert r.liquidity_capped_bars == 1

    def test_rejects_out_of_range_exposure(self):
        with pytest.raises(ValueError):
            run_backtest(make_candles(10), lambda h: 2.0)


class TestBaselines:
    def test_grid_names_unique(self):
        names = [c.name for c in default_candidate_grid()]
        assert len(names) == len(set(names))

    def test_ma_cross_long_in_uptrend(self):
        cs = make_candles(100, drift=0.01, vol=0.001)
        assert MovingAverageCross(5, 20)(cs) == 1.0

    def test_donchian_enters_on_breakout(self):
        cs = make_candles(100, drift=0.01, vol=0.001)
        assert DonchianBreakout(10, 5)(cs) == 1.0
        down = make_candles(100, drift=-0.01, vol=0.001)
        assert DonchianBreakout(10, 5)(down) == 0.0

    def test_ts_momentum_long_after_positive_lookback_return(self):
        cs = make_candles(100, drift=0.01, vol=0.001)
        assert TimeSeriesMomentum(24)(cs) == 1.0
        down = make_candles(100, drift=-0.01, vol=0.001)
        assert TimeSeriesMomentum(24)(down) == 0.0

    def test_ts_momentum_flat_before_lookback_is_available(self):
        assert TimeSeriesMomentum(24)(make_candles(10)) == 0.0

    def test_ts_momentum_rejects_short_lookback(self):
        with pytest.raises(ValueError):
            TimeSeriesMomentum(1)

    def test_momentum_grid_names_unique_and_disjoint_from_default(self):
        momentum_names = {c.name for c in momentum_candidate_grid()}
        default_names = {c.name for c in default_candidate_grid()}
        assert len(momentum_names) == len(momentum_candidate_grid())
        assert momentum_names.isdisjoint(default_names)

    def test_momentum_daily_grid_names_unique_and_disjoint_from_hourly(self):
        daily_names = {c.name for c in momentum_candidate_grid_daily()}
        hourly_names = {c.name for c in momentum_candidate_grid()}
        assert len(daily_names) == len(momentum_candidate_grid_daily())
        assert daily_names.isdisjoint(hourly_names)

    def test_momentum_daily_decorrelated_grid_is_a_subset_of_the_full_daily_grid(self):
        decorrelated = momentum_candidate_grid_daily_decorrelated()
        full = {c.name for c in momentum_candidate_grid_daily()}
        assert len(decorrelated) == 3
        assert {c.name for c in decorrelated} <= full
        assert len({c.name for c in decorrelated}) == 3
