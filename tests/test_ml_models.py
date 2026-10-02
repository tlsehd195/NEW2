from __future__ import annotations

import math
import random
from datetime import timedelta

import pytest

from cointrader.backtest.engine import PrefixView
from cointrader.ml.dataset import build_samples, forward_log_return, sample_at
from cointrader.ml.features import FEATURE_IDS, FEATURE_WINDOW, compute_feature_vector
from cointrader.ml.linear_model import RidgeModel, select_ridge_via_expanding_window_cv
from cointrader.ml.ml_strategy import MLStrategy
from cointrader.ml.samples import MLSample
from cointrader.ml.tree_model import BaggedTreeModel
from tests.helpers import T0, make_candles


def _synthetic(n=300, seed=1):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        x = {"a": rng.gauss(0, 1), "b": rng.gauss(0, 1), "c": rng.gauss(0, 1)}
        y = 0.5 * x["a"] - 0.2 * x["b"] + rng.gauss(0, 0.05)
        t = T0 + timedelta(hours=i)
        out.append(MLSample(t, t + timedelta(hours=1), x, y))
    return out


class TestSamples:
    def test_target_must_be_after_features(self):
        with pytest.raises(ValueError):
            MLSample(T0, T0, {"a": 1.0}, 0.0)

    def test_non_finite_rejected_not_imputed(self):
        with pytest.raises(ValueError):
            MLSample(T0, T0 + timedelta(hours=1), {"a": float("nan")}, 0.0)


class TestRidge:
    def test_recovers_signal_and_confidence_bounded(self):
        s = _synthetic()
        m = RidgeModel(["a", "b", "c"], ridge=0.1)
        m.fit(s)
        assert m.coefficients["a"] > 0 > m.coefficients["b"]
        assert abs(m.coefficients["c"]) < 0.1
        pred, conf = m.predict_with_confidence({"a": 2.0, "b": 0.0, "c": 0.0})
        assert pred > 0.5 and 0.0 <= conf <= 1.0

    def test_predict_before_fit_and_missing_feature(self):
        m = RidgeModel(["a"])
        with pytest.raises(RuntimeError):
            m.predict({"a": 1.0})
        m.fit(_synthetic())
        with pytest.raises(ValueError):
            m.predict({})

    def test_empty_fit_raises(self):
        with pytest.raises(ValueError):
            RidgeModel(["a"]).fit([])

    def test_cv_never_raises_on_tiny_data_and_picks_from_grid(self):
        assert select_ridge_via_expanding_window_cv(_synthetic(5), ["a", "b", "c"]) == 0.01
        assert select_ridge_via_expanding_window_cv(_synthetic(), ["a", "b", "c"]) in (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)


class TestForest:
    def test_deterministic_and_directional(self):
        s = _synthetic()
        m1, m2 = BaggedTreeModel(["a", "b", "c"]), BaggedTreeModel(["a", "b", "c"])
        m1.fit(s)
        m2.fit(s)
        hi = {"a": 2.0, "b": 0.0, "c": 0.0}
        assert m1.predict(hi) == m2.predict(hi)
        p_hi, conf = m1.predict_with_confidence(hi)
        p_lo, _ = m1.predict_with_confidence({"a": -2.0, "b": 0.0, "c": 0.0})
        assert p_hi > p_lo and 0.5 <= conf <= 1.0

    def test_non_finite_feature_rejected(self):
        bad = _synthetic(20)
        object.__setattr__(bad[0], "features", {"a": float("inf"), "b": 0.0, "c": 0.0})
        with pytest.raises(ValueError):
            BaggedTreeModel(["a", "b", "c"]).fit(bad)


class TestFeaturesAndDataset:
    def test_vector_uses_only_past_bars(self):
        candles = make_candles(250, seed=3)
        v = compute_feature_vector(candles[:200])
        assert set(v) == set(FEATURE_IDS) and all(math.isfinite(x) for x in v.values())
        changed = candles[:200] + make_candles(50, seed=99, start=candles[200].open_time)
        assert compute_feature_vector(changed[:200]) == v

    def test_short_history_returns_none(self):
        assert compute_feature_vector(make_candles(FEATURE_WINDOW - 1)) is None

    def test_target_time_after_feature_time_and_no_tail_samples(self):
        candles = make_candles(220, seed=4)
        samples = build_samples(candles, horizon=5)
        assert samples and all(s.target_time > s.as_of_time for s in samples)
        assert samples[-1].target_time <= candles[-1].close_time
        assert forward_log_return(candles, len(candles) - 3, 5) is None
        assert sample_at(candles, 10, 5) is None


class TestIntegrity:
    @pytest.mark.parametrize("family", ["ridge", "forest"])
    def test_passes_lookahead_warmup_and_determinism_checks(self, family):
        from cointrader.validation.integrity import check_signal_strategy

        s = MLStrategy(model_family=family, train_bars=150, min_train=80, refit_every=24, horizon=6,
                       confidence_min=0.0, edge_min=0.0)
        candles = make_candles(s.warmup + 120, seed=21, drift=0.0003)
        report = check_signal_strategy(candles, s, samples=12)
        assert report.passed, report.findings


class TestMLStrategy:
    @pytest.mark.parametrize("family", ["ridge", "forest"])
    def test_warmup_then_valid_signals_with_confidence(self, family):
        s = MLStrategy(model_family=family, train_bars=200, min_train=100, refit_every=20, horizon=6,
                       confidence_min=0.0, edge_min=0.0)
        candles = make_candles(s.warmup + 80, seed=7, drift=0.0005)
        assert s.signal(candles[: s.warmup - 1]).reason == "warmup"
        sigs = [s.signal(PrefixView(candles, n)) for n in range(s.warmup, len(candles) + 1)]
        assert all(0.0 <= g.strength <= 1.0 for g in sigs)
        entered = [g for g in sigs if g.entry]
        assert entered and all(g.stop_distance > 0 and "confidence_pct" in g.features for g in entered)
        assert all(g.entry >= 0 for g in sigs)  # long_only default

    def test_signal_independent_of_future_and_call_order(self):
        s1 = MLStrategy(train_bars=200, min_train=100, refit_every=20, horizon=6, confidence_min=0.0, edge_min=0.0)
        s2 = MLStrategy(train_bars=200, min_train=100, refit_every=20, horizon=6, confidence_min=0.0, edge_min=0.0)
        candles = make_candles(s1.warmup + 60, seed=11)
        n = s1.warmup + 45
        a = s1.signal(PrefixView(candles, n))
        for k in range(s2.warmup, n):  # sequential run, then same bar
            s2.signal(PrefixView(candles, k))
        b = s2.signal(PrefixView(candles, n))
        assert (a.entry, a.strength, a.exit_long) == (b.entry, b.strength, b.exit_long)
        # future bars changed -> decision at n unchanged
        other = candles[:n] + make_candles(40, seed=5, start=candles[n].open_time)
        c = MLStrategy(train_bars=200, min_train=100, refit_every=20, horizon=6, confidence_min=0.0, edge_min=0.0).signal(PrefixView(other, n))
        assert (a.entry, a.strength) == (c.entry, c.strength)

    def test_high_confidence_threshold_blocks_entries(self):
        s = MLStrategy(train_bars=200, min_train=100, refit_every=20, horizon=6, confidence_min=1.0,
                       edge_min=1.0)
        candles = make_candles(s.warmup + 30, seed=2)
        assert all(s.signal(PrefixView(candles, n)).entry == 0 for n in range(s.warmup, len(candles) + 1))

    def test_rejects_bad_parameters(self):
        with pytest.raises(ValueError):
            MLStrategy(model_family="svm")
        with pytest.raises(ValueError):
            MLStrategy(train_bars=10, min_train=100)
