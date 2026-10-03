from __future__ import annotations

import random

import pytest

from cointrader.validation.calibration import (
    beats_base_rate, brier_score, calibration_report, expected_calibration_error, log_loss, reliability_bins,
)


def _draw(n, truth, seed=0):
    rng = random.Random(seed)
    probs = [rng.random() for _ in range(n)]
    return probs, [1 if rng.random() < truth(p) else 0 for p in probs]


def test_perfect_and_worst_forecasts():
    assert brier_score([1.0, 0.0], [1, 0]) == 0.0
    assert brier_score([1.0, 0.0], [0, 1]) == 1.0
    assert log_loss([0.5, 0.5], [1, 0]) == pytest.approx(0.693147, abs=1e-5)


def test_log_loss_is_finite_at_certainty():
    assert log_loss([1.0], [0]) < 40


def test_calibrated_forecast_has_small_ece_and_beats_base_rate():
    p, y = _draw(5000, lambda q: q, seed=1)
    r = calibration_report(p, y)
    assert r.reason == "" and r.ece < 0.05
    assert beats_base_rate(r) is True


def test_overconfident_forecast_is_flagged():
    # says 90% / 10% but the truth is only 55% / 45%
    p, y = _draw(5000, lambda q: 0.55 if q > 0.5 else 0.45, seed=2)
    p = [0.9 if q > 0.5 else 0.1 for q in p]
    assert expected_calibration_error(p, y) > 0.3


def test_useless_forecast_does_not_beat_base_rate():
    p, y = _draw(5000, lambda q: 0.5, seed=3)
    assert beats_base_rate(calibration_report(p, y)) is False


def test_fail_closed_paths():
    assert "need 100" in calibration_report([0.5] * 10, [1, 0] * 5).reason
    assert calibration_report([0.5] * 200, [1] * 200).reason == "outcomes are all one class"
    assert calibration_report([float("nan")] * 200, [1, 0] * 100).reason
    assert calibration_report([1.5] * 200, [1, 0] * 100).brier is None
    assert beats_base_rate(calibration_report([], [])) is None
    with pytest.raises(ValueError):
        brier_score([0.5], [1, 0])


def test_bins_cover_all_samples_and_include_one():
    bins = reliability_bins([0.0, 0.05, 0.95, 1.0], [0, 0, 1, 1], n_bins=10)
    assert sum(b.count for b in bins) == 4 and bins[-1].count == 2
