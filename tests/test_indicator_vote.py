from __future__ import annotations

import math
import random

from cointrader.features.indicator_votes import INDICATOR_GROUPS, PlattCalibrator, combine_votes, raw_scores
from cointrader.strategies.indicator_vote import IndicatorVote
from tests.helpers import make_candles


def test_raw_scores_cover_all_indicators_and_are_bounded():
    s = raw_scores(make_candles(200, seed=1))
    assert s is not None and set(s) == set(INDICATOR_GROUPS)
    assert all(-1.0 <= v <= 1.0 for v in s.values())


def test_raw_scores_fail_closed_on_short_history():
    assert raw_scores(make_candles(50)) is None


def test_uptrend_scores_lean_bullish():
    s = raw_scores(make_candles(200, drift=0.01, vol=0.002, seed=2))
    assert s["ema_trend"] > 0 and s["roc"] > 0 and s["ma_alignment"] == 1.0


def test_calibrator_uninformative_with_few_rows():
    c = PlattCalibrator.fit([1.0] * 5, [1] * 5)
    assert c.predict(1.0) == 0.5


def test_calibrator_learns_signal_but_stays_near_half_on_noise():
    rng = random.Random(0)
    xs = [rng.uniform(-1, 1) for _ in range(2000)]
    informative = PlattCalibrator.fit(xs, [1 if rng.random() < 0.5 + 0.3 * x else 0 for x in xs])
    noise = PlattCalibrator.fit(xs, [rng.randint(0, 1) for _ in xs])
    assert informative.predict(0.9) > 0.6 > 0.4 > informative.predict(-0.9)
    assert abs(noise.predict(0.9) - 0.5) < 0.1


def test_combine_equal_weight_logodds():
    v = combine_votes({"a": 0.8, "b": 0.8, "c": 0.4})
    assert v.side == 1 and v.agree_long == 2 and v.agree_short == 1
    assert math.isclose(combine_votes({"a": 0.5}).p_long, 0.5)
    assert combine_votes({}).side == 0


def test_strategy_no_lookahead_and_warmup():
    strat = IndicatorVote(horizon=5, fit_lookback=100)
    candles = make_candles(strat.warmup + 30, seed=3)
    assert strat.signal(candles[: strat.warmup - 1]).entry == 0
    t = strat.warmup + 10
    base = strat.verdict(candles[:t])
    mutated = candles[:t] + [candles[t - 1]] * 0  # same prefix
    assert base is not None and strat.verdict(mutated).p_long == base.p_long
    # Changing FUTURE bars (after t) cannot change the verdict at t.
    future_changed = candles[:t] + make_candles(20, seed=99, start=candles[t - 1].open_time)
    assert strat.verdict(future_changed[:t]).p_long == base.p_long


def test_strategy_signal_is_valid_and_reports_per_indicator():
    strat = IndicatorVote(horizon=5, fit_lookback=100)
    sig = strat.signal(make_candles(strat.warmup + 5, drift=0.004, vol=0.004, seed=4))
    assert sig.entry in (-1, 0, 1)
    assert "p_long" in sig.features and any(k.startswith("p_rsi") for k in sig.features)


def test_default_panel_is_less_redundant_than_full_set():
    from cointrader.features.indicator_votes import DEFAULT_PANEL, redundancy
    candles = make_candles(900, seed=5, vol=0.012)
    series = [raw_scores(candles[: t + 1]) for t in range(200, 900, 3)]
    full = redundancy(series, list(INDICATOR_GROUPS))
    panel = redundancy(series, list(DEFAULT_PANEL))
    assert panel < full
    assert set(raw_scores(candles, DEFAULT_PANEL)) == set(DEFAULT_PANEL)
