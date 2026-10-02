from __future__ import annotations

import math

import pytest

from cointrader.backtest.engine import PrefixView
from cointrader.strategies.adaptive_ensemble import (
    AdaptiveIndicatorEnsemble,
    _features,
    adaptive_candidate_grid,
    adaptive_hysteresis_candidate_grid,
)
from tests.helpers import make_candles


def _brute_force(history, fit_lookback, w=20):
    """Reference implementation: full O(fit_lookback) rescan every call,
    no caching. Used only to cross-check the cached/incremental version."""
    n = len(history)
    warmup = fit_lookback + w + 1
    if n <= warmup:
        return 0.0
    train_start = n - 1 - fit_lookback
    rows, targets = [], []
    for i in range(train_start, n - 1):
        if i - w + 1 < 0:
            continue
        rows.append(_features(history[i - w + 1 : i + 1]))
        targets.append(history[i + 1].close / history[i].close - 1.0)
    if len(rows) < 5 * w:
        return 0.0
    weights = []
    for k in range(4):
        xs = [r[k] for r in rows]
        ys = targets
        count = len(xs)
        sx, sx2 = sum(xs), sum(x * x for x in xs)
        sy, sy2 = sum(ys), sum(y * y for y in ys)
        sxy = sum(x * y for x, y in zip(xs, ys))
        denom = (count * sx2 - sx * sx) * (count * sy2 - sy * sy)
        weights.append(0.0 if denom <= 0 else (count * sxy - sx * sy) / math.sqrt(denom))
    today = _features(history[n - w : n])
    score = sum(a * b for a, b in zip(weights, today))
    return 1.0 if score > 0 else 0.0


class TestAdaptiveIndicatorEnsemble:
    def test_rejects_too_small_indicator_window(self):
        with pytest.raises(ValueError):
            AdaptiveIndicatorEnsemble(fit_lookback=200, indicator_window=2)

    def test_rejects_fit_lookback_too_small_relative_to_window(self):
        with pytest.raises(ValueError):
            AdaptiveIndicatorEnsemble(fit_lookback=10, indicator_window=20)

    def test_flat_before_warmup(self):
        strat = AdaptiveIndicatorEnsemble(fit_lookback=100)
        assert strat(make_candles(50)) == 0.0

    def test_matches_brute_force_reference_over_many_bars(self):
        cs = make_candles(350, seed=7, vol=0.02)
        strat = AdaptiveIndicatorEnsemble(fit_lookback=100)
        for end in range(strat.warmup + 1, len(cs)):
            hist = cs[:end]
            assert strat(hist) == _brute_force(hist, 100)

    def test_incremental_cache_matches_fresh_instance_after_switching_series(self):
        cs1 = make_candles(300, seed=1, vol=0.02)
        cs2 = make_candles(300, seed=2, vol=0.02, drift=0.004)
        strat = AdaptiveIndicatorEnsemble(fit_lookback=100)
        # "warm up" the cache on cs1, then switch to an unrelated series
        for end in range(strat.warmup + 1, len(cs1)):
            strat(PrefixView(cs1, end))
        reused = [strat(PrefixView(cs2, end)) for end in range(strat.warmup + 1, len(cs2))]

        fresh = AdaptiveIndicatorEnsemble(fit_lookback=100)
        expected = [fresh(PrefixView(cs2, end)) for end in range(fresh.warmup + 1, len(cs2))]
        assert reused == expected

    def test_output_is_always_zero_or_one(self):
        cs = make_candles(300, seed=9, vol=0.03)
        strat = AdaptiveIndicatorEnsemble(fit_lookback=100)
        for end in range(strat.warmup + 1, len(cs)):
            assert strat(PrefixView(cs, end)) in (0.0, 1.0)

    def test_grid_names_unique(self):
        names = [c.name for c in adaptive_candidate_grid()]
        assert len(names) == len(set(names))

    def test_rejects_negative_hysteresis(self):
        with pytest.raises(ValueError):
            AdaptiveIndicatorEnsemble(fit_lookback=100, hysteresis=-0.1)

    def test_hysteresis_band_reduces_position_flips(self):
        cs = make_candles(1500, seed=11, vol=0.02)
        no_band = AdaptiveIndicatorEnsemble(fit_lookback=240)
        banded = AdaptiveIndicatorEnsemble(fit_lookback=240, hysteresis=0.3)
        outs_no_band = [no_band(PrefixView(cs, end)) for end in range(no_band.warmup + 1, len(cs))]
        outs_banded = [banded(PrefixView(cs, end)) for end in range(banded.warmup + 1, len(cs))]
        flips = lambda xs: sum(1 for a, b in zip(xs, xs[1:]) if a != b)  # noqa: E731
        assert flips(outs_banded) < flips(outs_no_band)

    def test_hysteresis_holds_previous_exposure_inside_the_band(self):
        # A band that swallows any score keeps the position wherever it
        # started (flat), never trading.
        cs = make_candles(400, seed=3, vol=0.02)
        strat = AdaptiveIndicatorEnsemble(fit_lookback=100, hysteresis=1e6)
        outs = [strat(PrefixView(cs, end)) for end in range(strat.warmup + 1, len(cs))]
        assert all(x == 0.0 for x in outs)

    def test_hysteresis_grid_names_disjoint_from_plain_grid(self):
        plain = {c.name for c in adaptive_candidate_grid()}
        banded = {c.name for c in adaptive_hysteresis_candidate_grid()}
        assert plain.isdisjoint(banded)
        assert len(banded) == len(adaptive_hysteresis_candidate_grid())
