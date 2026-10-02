from __future__ import annotations

import random

from cointrader.monitoring.drift import (
    DriftStatus, any_drift, distribution_shift, feature_drift, mean_shift, variance_shift,
)


def _g(n, mu=0.0, sd=1.0, seed=0):
    rng = random.Random(seed)
    return [rng.gauss(mu, sd) for _ in range(n)]


def test_no_drift_on_same_distribution():
    b, c = _g(300, seed=1), _g(300, seed=2)
    assert mean_shift("x", b, c).status is DriftStatus.NO_DRIFT
    assert variance_shift("x", b, c).status is DriftStatus.NO_DRIFT
    assert distribution_shift("x", b, c).status is DriftStatus.NO_DRIFT


def test_detects_mean_variance_and_distribution_shift():
    b = _g(300, seed=1)
    assert mean_shift("x", b, _g(300, mu=5, seed=2)).status is DriftStatus.DRIFT_DETECTED
    assert variance_shift("x", b, _g(300, sd=4, seed=2)).status is DriftStatus.DRIFT_DETECTED
    assert distribution_shift("x", b, _g(300, mu=3, seed=2)).status is DriftStatus.DRIFT_DETECTED


def test_unknown_is_fail_closed_with_reason():
    r = mean_shift("x", [1.0] * 5, [1.0] * 5)
    assert r.status is DriftStatus.UNKNOWN and r.reason == "insufficient_sample"
    assert mean_shift("x", [1.0] * 40, [2.0] * 40).reason == "zero_baseline_stdev"
    nan = float("nan")
    assert mean_shift("x", [nan] * 100, _g(100)).status is DriftStatus.UNKNOWN


def test_feature_drift_flags_shifted_feature_only():
    base = [{"a": v, "b": w} for v, w in zip(_g(200, seed=1), _g(200, seed=2))]
    cur = [{"a": v + 6, "b": w} for v, w in zip(_g(200, seed=3), _g(200, seed=4))]
    res = feature_drift(base, cur, ["a", "b"])
    assert any_drift([r for r in res if r.metric == "a"])
    assert not any_drift([r for r in res if r.metric == "b"])
