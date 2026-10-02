from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Timeframe
from cointrader.strategies.funding_carry import funding_carry_candidate_grid
from cointrader.validation.locked_windows import LockedWindow, LockedWindowViolation
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog
from cointrader.validation.funding_study import run_funding_study
from tests.helpers import make_candles

T0 = datetime(2021, 1, 1, tzinfo=timezone.utc)
STEP = timedelta(hours=8)


def make_funding_records(n: int, *, start: datetime = T0, seed: int = 0, symbol: str = "BTCUSDT") -> list[FundingRateRecord]:
    import random

    rng = random.Random(seed)
    price = 30000.0
    out = []
    for i in range(n):
        price *= math.exp(rng.gauss(0, 0.01))
        out.append(FundingRateRecord(
            symbol=symbol, funding_time=start + i * STEP, funding_rate=rng.gauss(0, 0.0003), mark_price=price,
        ))
    return out


def _hypothesis(**overrides) -> Hypothesis:
    grid = funding_carry_candidate_grid()
    base = dict(
        hypothesis_id="H-F1", statement="s", market="BTCUSDT", timeframe="8h",
        data_start=T0, data_end=T0 + 3000 * STEP, candidates=tuple(c.name for c in grid),
        success_criteria={"max_pbo": 0.2}, registered_by="tester", registered_at=T0,
    )
    base.update(overrides)
    return Hypothesis(**base)


class TestFundingStudy:
    def test_end_to_end_on_synthetic_data(self, tmp_path):
        funding = make_funding_records(3000, seed=3)
        candles = make_candles(3000 * 8, timeframe=Timeframe.HOUR_1, market="BTCUSDT", seed=3)
        candidates = funding_carry_candidate_grid()
        log = PreregistrationLog(tmp_path / "p.jsonl")
        log.register(_hypothesis(hypothesis_id="H-F0", candidates=("x", "y")))  # earlier trials
        h = _hypothesis()
        log.register(h)
        report = run_funding_study(h, log, funding, candles, candidates, (), fold_train=100 * STEP, fold_test=50 * STEP)
        assert report.fold_count >= 8
        assert 0.0 <= report.pbo <= 1.0
        assert report.trials_deflated_against == 5  # 2 from H-F0 + 3 from H-F1
        assert report.must_lock_test_window
        for c in report.candidates:
            assert len(c.fold_returns) == report.fold_count
            assert math.isfinite(c.test_return)
            assert math.isfinite(c.test_funding_only_return)

    def test_refuses_unregistered_or_locked(self, tmp_path):
        funding = make_funding_records(3000)
        candles = []
        candidates = funding_carry_candidate_grid()
        log = PreregistrationLog(tmp_path / "p.jsonl")
        h = _hypothesis()
        with pytest.raises(ValueError):
            run_funding_study(h, log, funding, candles, candidates, (), fold_train=100 * STEP, fold_test=50 * STEP)
        log.register(h)
        locked = (LockedWindow("TEST-1", "BTCUSDT", T0 + 2900 * STEP, T0 + 3000 * STEP, (), "n"),)
        with pytest.raises(LockedWindowViolation):
            run_funding_study(h, log, funding, candles, candidates, locked, fold_train=100 * STEP, fold_test=50 * STEP)
