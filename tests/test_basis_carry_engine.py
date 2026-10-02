from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.backtest.basis_carry_engine import run_basis_carry_backtest
from cointrader.backtest.costs import CandleCostModel
from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle, Timeframe

T0 = datetime(2021, 1, 1, tzinfo=timezone.utc)


def _record(i: int, rate: float) -> FundingRateRecord:
    return FundingRateRecord(
        symbol="BTCUSDT", funding_time=T0 + timedelta(hours=8 * i), funding_rate=rate, mark_price=float("nan"),
    )


def _candle(i: int, price: float, volume: float = 1000.0, *, market: str = "BTCUSDT") -> Candle:
    # Closes exactly at settlement i's funding_time, so it is fully
    # closed by the time that settlement is decided.
    t = T0 + timedelta(hours=8 * i) - timedelta(hours=1)
    return Candle(
        market=market, timeframe=Timeframe.HOUR_1, open_time=t, open=price, high=price, low=price,
        close=price, volume=volume, source="test", received_at=t + timedelta(hours=1),
    )


class TestRunBasisCarryBacktest:
    def test_too_few_records_returns_empty(self):
        result = run_basis_carry_backtest([_record(0, 0.0001)], lambda s, f, fu: 0.0, warmup=0)
        assert result == run_basis_carry_backtest([], lambda s, f, fu: 0.0, warmup=0)

    def test_always_flat_strategy_has_no_trades_and_zero_return(self):
        records = [_record(i, 0.0001) for i in range(5)]
        spot = [_candle(i, 100.0) for i in range(5)]
        futures = [_candle(i, 100.0) for i in range(5)]
        result = run_basis_carry_backtest(
            records, lambda s, f, fu: 0.0, spot_history=spot, futures_history=futures, warmup=0,
        )
        assert result.trades == 0
        assert result.total_cost_fraction == 0.0
        assert result.gaps == 0
        assert all(r == 0.0 for r in result.price_returns)
        assert all(r == 0.0 for r in result.funding_returns)
        assert result.total_return == 0.0

    def test_positive_target_collects_positive_funding(self):
        records = [_record(i, 0.001) for i in range(4)]
        spot = [_candle(i, 100.0) for i in range(4)]
        futures = [_candle(i, 100.0) for i in range(4)]
        result = run_basis_carry_backtest(
            records, lambda s, f, fu: 1.0, spot_history=spot, futures_history=futures, warmup=0, equity=0.0,
        )
        # target=1.0 means long spot / short futures -- collects funding
        # when the rate is positive (same Binance convention as the
        # single-leg engine).
        assert all(r > 0 for r in result.funding_returns)
        assert result.funding_returns[0] == pytest.approx(0.001)

    def test_negative_target_pays_positive_funding(self):
        records = [_record(i, 0.001) for i in range(4)]
        spot = [_candle(i, 100.0) for i in range(4)]
        futures = [_candle(i, 100.0) for i in range(4)]
        result = run_basis_carry_backtest(
            records, lambda s, f, fu: -1.0, spot_history=spot, futures_history=futures, warmup=0, equity=0.0,
        )
        assert all(r < 0 for r in result.funding_returns)

    def test_price_return_is_the_real_measured_basis_not_assumed_zero(self):
        # Spot and futures move by DIFFERENT amounts each settlement --
        # the engine must report the real difference, not assume a
        # perfect hedge cancels to exactly zero.
        records = [_record(i, 0.0) for i in range(4)]
        spot = [_candle(i, 100.0 * (1.05**i)) for i in range(4)]      # spot up 5%/settlement
        futures = [_candle(i, 100.0 * (1.03**i)) for i in range(4)]   # futures up 3%/settlement
        result = run_basis_carry_backtest(
            records, lambda s, f, fu: 1.0, spot_history=spot, futures_history=futures, warmup=0, equity=0.0,
        )
        assert result.price_returns[0] == 0.0  # no previous mark yet
        # basis return ~= spot_ret - futures_ret = 1.05 - 1.03 = 0.02 (approx, compounding)
        assert result.price_returns[1] == pytest.approx(1.05 - 1.03, rel=1e-6)

    def test_perfect_hedge_cancels_identical_moves(self):
        # When spot and futures move identically, the basis return is
        # genuinely zero -- not because it's assumed, but because the
        # measured difference is zero.
        records = [_record(i, 0.0) for i in range(4)]
        spot = [_candle(i, 100.0 * (1.1**i)) for i in range(4)]
        futures = [_candle(i, 100.0 * (1.1**i)) for i in range(4)]
        result = run_basis_carry_backtest(
            records, lambda s, f, fu: 1.0, spot_history=spot, futures_history=futures, warmup=0, equity=0.0,
        )
        assert result.price_returns[1] == pytest.approx(0.0, abs=1e-9)

    def test_gap_when_futures_leg_has_no_closed_candle_yet_forces_both_flat(self):
        # No futures candle has closed by settlement 0's decision time at
        # all (the only futures candle is for settlement 2) -- the
        # earliest a "closest prior close" join can ever report a
        # genuine gap, since once any candle exists every later
        # settlement finds a prior one.
        records = [_record(i, 0.001) for i in range(3)]
        spot = [_candle(i, 100.0) for i in range(3)]
        futures = [_candle(2, 100.0)]
        result = run_basis_carry_backtest(
            records, lambda s, f, fu: 1.0, spot_history=spot, futures_history=futures, warmup=0, equity=0.0,
        )
        # Both settlement 0 and 1 decide before the only futures candle
        # (for settlement 2) has closed, so both are gaps.
        assert result.gaps == 2
        assert result.exposures == (0.0, 0.0)
        assert result.price_returns == (0.0, 0.0)
        assert result.funding_returns == (0.0, 0.0)

    def test_out_of_range_exposure_rejected(self):
        records = [_record(i, 0.0001) for i in range(3)]
        spot = [_candle(i, 100.0) for i in range(3)]
        futures = [_candle(i, 100.0) for i in range(3)]
        with pytest.raises(ValueError):
            run_basis_carry_backtest(
                records, lambda s, f, fu: 1.5, spot_history=spot, futures_history=futures, warmup=0,
            )

    def test_strategy_sees_only_strictly_prior_funding_history(self):
        seen_lengths = []

        def probe(spot_history, futures_history, funding_history):
            seen_lengths.append(len(funding_history))
            return 0.0

        records = [_record(i, 0.0001) for i in range(4)]
        spot = [_candle(i, 100.0) for i in range(4)]
        futures = [_candle(i, 100.0) for i in range(4)]
        run_basis_carry_backtest(records, probe, spot_history=spot, futures_history=futures, warmup=1)
        assert seen_lengths == [1, 2]

    def test_oversized_order_capped_by_the_thinner_leg_keeps_both_legs_equal(self):
        # Regression for the exact H-0011 crash mode, now with two legs:
        # a thin bar on EITHER leg must cap both legs together (never
        # leave one leg filled and the other not, which would break the
        # hedge), not raise.
        records = [_record(0, 0.0), _record(1, 0.0), _record(2, 0.0)]
        spot = [_candle(1, 100.0, volume=1_000_000.0)]   # deep
        thin_futures = [_candle(1, 100.0, volume=1.0)]   # thin -- bar_value tiny
        result = run_basis_carry_backtest(
            records, lambda s, f, fu: 1.0, spot_history=spot, futures_history=thin_futures, warmup=0,
            equity=1_000_000.0, cost_model=CandleCostModel(max_participation=0.05),
        )
        assert result.liquidity_capped_settlements >= 1
        assert all(-1.0 <= e <= 1.0 for e in result.exposures)

    def test_total_return_combines_price_and_funding(self):
        records = [_record(i, 0.01) for i in range(3)]
        spot = [_candle(i, 100.0 * (1.02**i)) for i in range(3)]
        futures = [_candle(i, 100.0) for i in range(3)]
        result = run_basis_carry_backtest(
            records, lambda s, f, fu: 1.0, spot_history=spot, futures_history=futures, warmup=0, equity=0.0,
        )
        expected = 1.0
        for pr, fr in zip(result.price_returns, result.funding_returns):
            expected *= 1 + pr + fr
        assert result.total_return == pytest.approx(expected - 1)
