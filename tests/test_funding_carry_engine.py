from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.backtest.costs import CandleCostModel
from cointrader.backtest.funding_carry_engine import run_funding_carry_backtest
from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle, Timeframe

T0 = datetime(2021, 1, 1, tzinfo=timezone.utc)


def _record(i: int, rate: float, mark_price) -> FundingRateRecord:
    return FundingRateRecord(
        symbol="BTCUSDT", funding_time=T0 + timedelta(hours=8 * i), funding_rate=rate, mark_price=mark_price
    )


def _candle(i: int, price: float, volume: float = 1000.0) -> Candle:
    # Closes exactly at settlement i's funding_time, so it is a fully
    # closed bar by the time that settlement is decided.
    t = T0 + timedelta(hours=8 * i) - timedelta(hours=1)
    return Candle(
        market="BTCUSDT", timeframe=Timeframe.HOUR_1, open_time=t, open=price, high=price, low=price,
        close=price, volume=volume, source="test", received_at=t + timedelta(hours=1),
    )


class TestRunFundingCarryBacktest:
    def test_too_few_records_returns_empty(self):
        result = run_funding_carry_backtest([_record(0, 0.0001, 30000.0)], lambda p, f: 0.0, warmup=0)
        assert result == run_funding_carry_backtest([], lambda p, f: 0.0, warmup=0)

    def test_always_flat_strategy_has_no_trades_and_zero_return(self):
        records = [_record(i, 0.0001, 30000.0) for i in range(5)]
        result = run_funding_carry_backtest(records, lambda p, f: 0.0, warmup=0)
        assert result.trades == 0
        assert result.total_cost_fraction == 0.0
        assert result.gaps == 0
        assert all(r == 0.0 for r in result.price_returns)
        assert all(r == 0.0 for r in result.funding_returns)
        assert result.total_return == 0.0

    def test_short_collects_positive_funding(self):
        records = [_record(i, 0.001, 30000.0) for i in range(4)]
        result = run_funding_carry_backtest(records, lambda p, f: -1.0, warmup=0, equity=0.0)
        # Every scored settlement is short while the rate is positive -> receives funding.
        assert all(r > 0 for r in result.funding_returns)
        assert result.funding_returns[0] == pytest.approx(0.001)

    def test_long_pays_positive_funding(self):
        records = [_record(i, 0.001, 30000.0) for i in range(4)]
        result = run_funding_carry_backtest(records, lambda p, f: 1.0, warmup=0, equity=0.0)
        assert all(r < 0 for r in result.funding_returns)
        assert result.funding_returns[0] == pytest.approx(-0.001)

    def test_price_mark_to_market_between_settlements(self):
        records = [_record(i, 0.0, 100.0 * (1.1**i)) for i in range(4)]
        result = run_funding_carry_backtest(records, lambda p, f: 1.0, warmup=0, equity=0.0)
        # First scored settlement has no previous mark yet -> zero price return.
        assert result.price_returns[0] == 0.0
        assert result.price_returns[1] == pytest.approx(0.1, rel=1e-6)

    def test_gap_on_invalid_mark_price_forces_flat_and_is_counted(self):
        records = [_record(0, 0.001, 30000.0), _record(1, 0.001, math.nan), _record(2, 0.001, 30000.0)]
        result = run_funding_carry_backtest(records, lambda p, f: -1.0, warmup=0, equity=0.0)
        assert result.gaps == 1
        assert result.exposures[1] == 0.0
        assert result.price_returns[1] == 0.0
        assert result.funding_returns[1] == 0.0

    def test_out_of_range_exposure_rejected(self):
        records = [_record(i, 0.0001, 30000.0) for i in range(3)]
        with pytest.raises(ValueError):
            run_funding_carry_backtest(records, lambda p, f: 1.5, warmup=0)

    def test_cost_charged_on_position_change_using_price_history_volume(self):
        records = [_record(i, 0.0, 30000.0) for i in range(3)]
        candles = [_candle(i, 30000.0) for i in range(3)]
        result = run_funding_carry_backtest(
            records, lambda p, f: -1.0, price_history=candles, warmup=0, equity=1_000_000.0,
            cost_model=CandleCostModel(max_participation=1.0),
        )
        assert result.trades == 1  # only enters once; stays short after that
        assert result.total_cost_fraction > 0

    def test_strategy_sees_only_strictly_prior_funding_history(self):
        seen_lengths = []

        def probe(price_history, funding_history):
            seen_lengths.append(len(funding_history))
            return 0.0

        records = [_record(i, 0.0001, 30000.0) for i in range(4)]
        run_funding_carry_backtest(records, probe, warmup=1)
        assert seen_lengths == [1, 2]

    def test_oversized_order_is_capped_not_raised(self):
        # A flip from full short to full long against a thin bar would ask
        # for far more than max_participation of that bar's traded value.
        # Regression test for the H-0011 crash: this used to raise
        # ValueError out of cost_model.cost_fraction and kill the whole
        # study instead of capping the fill, unlike backtest.engine's
        # run_backtest, which never lets one thin bar abort a multi-year
        # backtest.
        records = [_record(0, 0.0, 100.0), _record(1, 0.0, 100.0), _record(2, 0.0, 100.0)]
        thin_candle = _candle(1, 100.0, volume=1.0)  # bar_value = 100 -- tiny
        strategies = iter([-1.0, 1.0])  # flip short -> long straight into the thin bar
        result = run_funding_carry_backtest(
            records, lambda p, f: next(strategies), price_history=[thin_candle], warmup=0,
            equity=1_000_000.0, cost_model=CandleCostModel(max_participation=0.05),
        )
        assert result.liquidity_capped_settlements >= 1
        assert all(-1.0 <= e <= 1.0 for e in result.exposures)

    def test_total_return_combines_price_and_funding(self):
        records = [_record(i, 0.01, 100.0) for i in range(3)]
        result = run_funding_carry_backtest(records, lambda p, f: -1.0, warmup=0, equity=0.0)
        expected = 1.0
        for pr, fr in zip(result.price_returns, result.funding_returns):
            expected *= 1 + pr + fr
        assert result.total_return == pytest.approx(expected - 1)
