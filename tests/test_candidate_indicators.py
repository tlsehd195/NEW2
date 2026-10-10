"""Candidate indicators (ADR-0070): match a naive computation, never read the future."""
import math
import random
from datetime import datetime, timedelta, timezone

from cointrader.data.models import Candle, Timeframe
from cointrader.features.candidate_indicators import ALL_FEATURES, MIN_INDEX, CandleSeries

T0 = datetime(2023, 1, 1, tzinfo=timezone.utc)


def make(n, seed=1):
    rnd, px, out = random.Random(seed), 100.0, []
    for i in range(n):
        o = px
        px *= math.exp(rnd.gauss(0, 0.003))
        hi, lo = max(o, px) * (1 + abs(rnd.gauss(0, 0.001))), min(o, px) * (1 - abs(rnd.gauss(0, 0.001)))
        out.append(Candle("X", Timeframe.MINUTE_15, T0 + timedelta(minutes=15 * i), o, hi, lo, px,
                          1000 * (1 + rnd.random()), "test", T0))
    return out


def test_matches_naive_formulas():
    c = make(700)
    s = CandleSeries()
    s.extend(c)
    t = 650
    f = s.features(t)
    x = [math.log(b.close) for b in c]
    lr = [0.0] + [x[i] - x[i - 1] for i in range(1, len(x))]
    sd96 = math.sqrt(sum(v * v for v in lr[t - 95:t + 1]) / 96)
    sd480 = math.sqrt(sum(v * v for v in lr[t - 479:t + 1]) / 480)
    assert math.isclose(f["rv_ratio"], math.log(sd96 / sd480), rel_tol=1e-9)
    v4, vb = sum(b.volume for b in c[t - 3:t + 1]) / 4, sum(b.volume for b in c[t - 479:t + 1]) / 480
    assert math.isclose(f["volspike"], math.log(v4 / vb), rel_tol=1e-9)
    ac = sum(lr[i] * lr[i - 1] for i in range(t - 479, t + 1)) / sum(v * v for v in lr[t - 479:t + 1])
    assert math.isclose(f["ac1"], ac, rel_tol=1e-9, abs_tol=1e-12)
    d8 = sum((x[i] - x[i - 8]) ** 2 for i in range(t - 479, t + 1)) / 480
    assert math.isclose(f["vr"], d8 / (8 * sum(v * v for v in lr[t - 479:t + 1]) / 480) - 1, rel_tol=1e-9)
    clv = sum((2 * b.close - b.high - b.low) / (b.high - b.low) for b in c[t - 15:t + 1]) / 16
    assert math.isclose(f["clv16"], clv, rel_tol=1e-9)
    m2 = sum(v * v for v in lr[t - 95:t + 1]) / 96
    assert math.isclose(f["kurt96"], sum(v ** 4 for v in lr[t - 95:t + 1]) / 96 / m2 ** 2, rel_tol=1e-9)
    assert set(f) == set(ALL_FEATURES)


def test_no_lookahead():
    c = make(900)
    a, b = CandleSeries(), CandleSeries()
    a.extend(c[:700])
    b.extend(c)
    for t in (MIN_INDEX, 600, 699):
        assert a.features(t) == b.features(t)
    assert a.features(700) is None  # not yet seen
    assert a.features(MIN_INDEX - 1) is None


def test_vote_scores_bounded():
    c = make(700)
    s = CandleSeries()
    s.extend(c)
    sc = s.vote_scores(650, ALL_FEATURES)
    assert sc is not None and all(-1.0 <= v <= 1.0 for v in sc.values())
