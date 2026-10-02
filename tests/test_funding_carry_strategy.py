from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cointrader.data.binance_funding import FundingRateRecord
from cointrader.strategies.funding_carry import FundingCarry, funding_carry_candidate_grid

T0 = datetime(2021, 1, 1, tzinfo=timezone.utc)


def _records(rates: list[float]) -> list[FundingRateRecord]:
    return [
        FundingRateRecord(symbol="BTCUSDT", funding_time=T0 + timedelta(hours=8 * i), funding_rate=r, mark_price=30000.0)
        for i, r in enumerate(rates)
    ]


class TestFundingCarry:
    def test_rejects_invalid_params(self):
        import pytest

        with pytest.raises(ValueError):
            FundingCarry(lookback=0, threshold=0.0001)
        with pytest.raises(ValueError):
            FundingCarry(lookback=3, threshold=-0.0001)

    def test_flat_before_warmup(self):
        strategy = FundingCarry(lookback=3, threshold=0.0001)
        assert strategy([], _records([0.001, 0.001])) == 0.0

    def test_goes_short_when_funding_persistently_positive(self):
        strategy = FundingCarry(lookback=3, threshold=0.0001)
        assert strategy([], _records([0.001, 0.001, 0.001])) == -1.0

    def test_goes_long_when_funding_persistently_negative(self):
        strategy = FundingCarry(lookback=3, threshold=0.0001)
        assert strategy([], _records([-0.001, -0.001, -0.001])) == 1.0

    def test_flat_inside_dead_zone(self):
        strategy = FundingCarry(lookback=3, threshold=0.001)
        assert strategy([], _records([0.0001, -0.0001, 0.0002])) == 0.0

    def test_only_uses_the_trailing_lookback_window(self):
        strategy = FundingCarry(lookback=2, threshold=0.0001)
        # Oldest record is strongly negative but outside the lookback window.
        assert strategy([], _records([-0.01, 0.001, 0.001])) == -1.0

    def test_name_and_warmup(self):
        strategy = FundingCarry(lookback=9, threshold=0.0002)
        assert strategy.name == "funding_carry_9_0.0002"
        assert strategy.warmup == 9


class TestFundingCarryCandidateGrid:
    def test_three_distinct_lookbacks(self):
        grid = funding_carry_candidate_grid()
        assert len(grid) == 3
        assert len({c.lookback for c in grid}) == 3
        assert len({c.name for c in grid}) == 3
