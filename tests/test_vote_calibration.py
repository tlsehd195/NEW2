"""Plumbing tests on generated candles (not a validation result)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from cointrader.strategies.indicator_vote import IndicatorVote
from cointrader.validation.vote_calibration import collect_forecasts, summarize
from tests.helpers import make_candles

_spec = importlib.util.spec_from_file_location("calib", Path(__file__).resolve().parents[1] / "scripts" / "calibrate_vote.py")
calib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(calib)


def test_outcome_is_direction_horizon_bars_later_and_sees_no_future():
    candles = make_candles(60, seed=1)
    seen = []

    def p(view):
        seen.append(len(view))
        return 0.7

    f = collect_forecasts(candles, p, horizon=5, first_index=10)
    assert len(f.probs) == len(f.outcomes) == len(range(10, 55, 5))
    for i, y in zip(range(10, 55, 5), f.outcomes):
        assert y == (1 if candles[i + 5].close > candles[i].close else 0)
    assert seen == [i + 1 for i in range(10, 55, 5)]  # decision i only ever sees candles[:i+1]


def test_gap_and_missing_verdict_are_dropped_and_counted():
    candles = make_candles(40, seed=2)
    del candles[20]  # a hole
    f = collect_forecasts(candles, lambda v: None if len(v) == 11 else 0.6, horizon=4, first_index=10, stride=1)
    assert f.dropped["no_verdict"] == 1 and f.dropped["candle_gap"] >= 4


def test_summarize_fail_closed_when_few_samples():
    candles = make_candles(60, seed=3)
    f = collect_forecasts(candles, lambda v: 0.6, horizon=5, first_index=10)
    s = summarize(f, horizon=5, stride=5, enter_confidence=0.6)
    assert s["measurable"] is False and "need 100" in s["reason"] and s["brier"] is None


def test_bad_arguments():
    with pytest.raises(ValueError):
        collect_forecasts(make_candles(10), lambda v: 0.5, horizon=0, first_index=1)


def test_script_calibrate_runs_end_to_end():
    candles = make_candles(420, seed=5)
    strat = IndicatorVote()
    out = calib.calibrate(candles, strat, strat.warmup)
    assert out["strategy_id"] == strat.strategy_id and out["samples"] + sum(out["dropped"].values()) > 0
