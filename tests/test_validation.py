from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.strategies.baselines import MovingAverageCross
from cointrader.validation.locked_windows import (
    LockedWindow, LockedWindowViolation, append_locked_window, assert_not_locked, load_locked_windows,
)
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog
from cointrader.validation.study import run_study
from cointrader.validation.walk_forward import build_chronological_split, generate_walk_forward_windows
from tests.helpers import T0, make_candles

H = timedelta(hours=1)


class TestWalkForward:
    def test_windows_are_ordered_and_non_overlapping_tests(self):
        ws = generate_walk_forward_windows(T0, T0 + 100 * H, train=20 * H, test=10 * H, step=10 * H, embargo=H)
        assert len(ws) == 7
        for w in ws:
            assert w.train_end + H == w.test_start and w.test_end - w.test_start == 10 * H
        for a, b in zip(ws, ws[1:]):
            assert a.test_end <= b.test_start + H

    def test_insufficient_history_is_empty_not_error(self):
        assert generate_walk_forward_windows(T0, T0 + 5 * H, train=10 * H, test=H, step=H) == []

    def test_split_is_chronological_and_bar_aligned(self):
        s = build_chronological_split(T0, T0 + 101 * H)
        assert s.train_start < s.train_end == s.validation_start < s.validation_end == s.test_start < s.test_end
        assert (s.train_end - T0) % H == timedelta(0)

    def test_naive_datetimes_rejected(self):
        with pytest.raises(ValueError):
            build_chronological_split(datetime(2024, 1, 1), datetime(2024, 2, 1))


class TestLockedWindows:
    def test_overlap_is_a_hard_stop(self, tmp_path):
        path = tmp_path / "locked.json"
        append_locked_window(LockedWindow("TEST-1", "KRW-BTC", T0, T0 + 10 * H, ("a",), "n"), path)
        windows = load_locked_windows(path)
        with pytest.raises(LockedWindowViolation):
            assert_not_locked(windows, "KRW-BTC", T0 + 5 * H, T0 + 20 * H)
        assert_not_locked(windows, "KRW-BTC", T0 + 10 * H, T0 + 20 * H)  # adjacent is fine
        assert_not_locked(windows, "KRW-ETH", T0, T0 + 20 * H)  # other market

    def test_wildcard_market_and_append_only_names(self, tmp_path):
        path = tmp_path / "locked.json"
        w = LockedWindow("TEST-1", "*", T0, T0 + H, (), "n")
        append_locked_window(w, path)
        with pytest.raises(ValueError):
            append_locked_window(w, path)
        with pytest.raises(LockedWindowViolation):
            assert_not_locked(load_locked_windows(path), "KRW-ETH", T0, T0 + H)

    def test_repo_registry_loads(self):
        load_locked_windows()


def _hypothesis(**overrides) -> Hypothesis:
    base = dict(
        hypothesis_id="H-1", statement="s", market="KRW-BTC", timeframe="1h",
        data_start=T0, data_end=T0 + 3000 * H, candidates=("ma_cross_5_20", "ma_cross_10_50"),
        success_criteria={"max_pbo": 0.2}, registered_by="tester", registered_at=T0,
    )
    base.update(overrides)
    return Hypothesis(**base)


class TestPreregistration:
    def test_changed_hypothesis_cannot_reuse_id(self, tmp_path):
        log = PreregistrationLog(tmp_path / "p.jsonl")
        log.register(_hypothesis())
        log.register(_hypothesis(registered_at=T0 + H))  # same content, re-register is a no-op
        with pytest.raises(ValueError):
            log.register(_hypothesis(candidates=("ma_cross_5_20",)))
        with pytest.raises(ValueError):
            log.verify(_hypothesis(data_end=T0 + 2000 * H))
        log.verify(_hypothesis())

    def test_trial_count_accumulates_across_hypotheses(self, tmp_path):
        log = PreregistrationLog(tmp_path / "p.jsonl")
        log.register(_hypothesis())
        log.register(_hypothesis(hypothesis_id="H-2", candidates=("a", "b", "c")))
        assert log.total_registered_candidates() == 5


class TestStudy:
    def test_end_to_end_on_synthetic_data(self, tmp_path):
        candles = make_candles(3000, seed=3)
        candidates = [MovingAverageCross(5, 20), MovingAverageCross(10, 50)]
        log = PreregistrationLog(tmp_path / "p.jsonl")
        log.register(_hypothesis(hypothesis_id="H-0", candidates=("x", "y", "z")))  # earlier trials
        h = _hypothesis()
        log.register(h)
        report = run_study(h, log, candles, candidates, (), fold_train=100 * H, fold_test=50 * H)
        assert report.fold_count >= 8
        assert 0.0 <= report.pbo <= 1.0
        assert report.trials_deflated_against == 5
        assert report.must_lock_test_window
        for c in report.candidates:
            assert len(c.fold_returns) == report.fold_count
            assert math.isfinite(c.test_return)

    def test_refuses_unregistered_or_locked(self, tmp_path):
        candles = make_candles(3000)
        candidates = [MovingAverageCross(5, 20), MovingAverageCross(10, 50)]
        log = PreregistrationLog(tmp_path / "p.jsonl")
        h = _hypothesis()
        with pytest.raises(ValueError):
            run_study(h, log, candles, candidates, (), fold_train=100 * H, fold_test=50 * H)
        log.register(h)
        locked = (LockedWindow("TEST-1", "KRW-BTC", T0 + 2900 * H, T0 + 3000 * H, (), "n"),)
        with pytest.raises(LockedWindowViolation):
            run_study(h, log, candles, candidates, locked, fold_train=100 * H, fold_test=50 * H)
