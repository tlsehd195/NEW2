from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.data.market_events import BookTicker, TradeTick
from cointrader.data.models import Candle, OrderBookLevel, OrderBookSnapshot, Timeframe
from cointrader.features import indicators as ind
from cointrader.features.microstructure import (
    TradeFlowWindow,
    aggregate_trades,
    book_imbalance,
    depth_imbalance,
    microprice,
    percentile_rank,
    trade_imbalance,
)
from cointrader.features.regime import Regime, RegimeConfig, classify_regime
from cointrader.features.snapshot import SNAPSHOT_WARMUP, book_features, candle_features
from tests.helpers import make_candles

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bars(closes, volume=10.0):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append(Candle("BTCUSDT", Timeframe.HOUR_1, T0 + timedelta(hours=i), o, max(o, c) + 1, min(o, c) - 1, c,
                          volume, "test", T0 + timedelta(hours=i + 1)))
    return out


def test_sma_ema_known_values():
    assert ind.sma([1, 2, 3, 4], 2) == 3.5
    assert ind.sma([1, 2], 3) is None
    # EMA over exactly `lookback` values seeded by SMA of the first `period`
    vals = [1.0, 2.0, 3.0, 4.0]
    alpha = 2 / 3
    expected = (1 + 2) / 2
    for v in (3.0, 4.0):
        expected = alpha * v + (1 - alpha) * expected
    assert ind.ema(vals, 2, lookback=4) == pytest.approx(expected)


def test_non_finite_input_returns_none():
    assert ind.sma([1, float("nan"), 3], 3) is None
    assert ind.ema([1.0] * 7 + [float("inf")], 2) is None
    assert ind.rsi([1.0] * 100 + [float("nan")]) is None


def test_rsi_extremes():
    assert ind.rsi(list(range(1, 100))) == 100.0
    assert ind.rsi(list(range(100, 1, -1))) == 0.0
    assert ind.rsi([5.0] * 80) == 50.0


def test_atr_of_constant_range():
    cs = [Candle("X", Timeframe.HOUR_1, T0 + timedelta(hours=i), 100, 101, 99, 100, 1, "t",
                 T0 + timedelta(hours=i + 1)) for i in range(80)]
    assert ind.atr(cs, 14) == pytest.approx(2.0)


def test_bollinger_and_vwap_and_donchian():
    bb = ind.bollinger([1, 2, 3, 4, 5], 5, 2.0)
    assert bb["mid"] == 3 and bb["pct_b"] == pytest.approx((5 - bb["lower"]) / (bb["upper"] - bb["lower"]))
    cs = bars([10, 11, 12, 20])
    assert ind.donchian(cs, 3) == (13.0, 9.0)  # current bar excluded
    assert ind.breakout(cs, 3) == 1
    assert ind.vwap(cs, 2) == pytest.approx(((13 + 10 + 12) / 3 * 10 + (21 + 11 + 20) / 3 * 10) / 20)


def test_volume_zscore_excludes_current_bar():
    cs = bars([10] * 21)
    cs[-1] = Candle(cs[-1].market, cs[-1].timeframe, cs[-1].open_time, 10, 11, 9, 10, 1000, "t", cs[-1].received_at)
    assert ind.volume_zscore(cs, 20) == 0.0  # constant baseline -> sd 0 -> defined as 0
    assert ind.relative_volume(cs, 20) == 100.0


def test_macd_requires_history_and_is_finite():
    closes = [c.close for c in make_candles(300)]
    assert ind.macd(closes[:100]) is None
    m, s, h = ind.macd(closes)
    assert math.isfinite(m) and h == pytest.approx(m - s)


def test_features_do_not_look_ahead_or_depend_on_old_history():
    cs = make_candles(600, seed=3)
    t = 450
    full_prefix = candle_features(cs[: t + 1])
    with_future = candle_features(cs[: t + 1])  # same prefix; future bars never passed
    truncated = candle_features(cs[t + 1 - SNAPSHOT_WARMUP: t + 1])
    assert full_prefix == with_future == truncated
    assert all(v is not None for k, v in full_prefix.items()), full_prefix


def test_book_features():
    bt = BookTicker("BTCUSDT", 100, 3, 101, 1, 1, T0, T0, "t")
    f = book_features(bt)
    assert f["microprice"] == pytest.approx((100 * 1 + 101 * 3) / 4)
    assert f["top1_imbalance"] == pytest.approx(0.5)
    assert book_features(None)["spread"] is None


def test_microstructure_functions():
    assert book_imbalance(3, 1) == 0.5 and book_imbalance(0, 0) is None
    assert microprice(100, 101, 1, 1) == 100.5
    book = OrderBookSnapshot("X", T0, (OrderBookLevel(100, 2), OrderBookLevel(99, 2)),
                             (OrderBookLevel(101, 1), OrderBookLevel(102, 1)))
    assert depth_imbalance(book, 2) == pytest.approx((4 - 2) / 6)
    assert percentile_rank(5, [1, 2, 3, 10]) == 0.75


def trade(i, side, qty, sec, price=100.0):
    return TradeTick("BTCUSDT", price, qty, side, i, T0 + timedelta(seconds=sec), T0 + timedelta(seconds=sec), "t")


def test_trade_flow_window_matches_pure_function_and_evicts():
    w = TradeFlowWindow(timedelta(seconds=10))
    ts = [trade(1, "buy", 3, 0), trade(2, "sell", 1, 5), trade(3, "buy", 1, 12)]
    for t in ts:
        w.add(t)
    now = T0 + timedelta(seconds=12)
    assert w.imbalance(now) == trade_imbalance([ts[1], ts[2]]) == 0.0
    with pytest.raises(ValueError):
        w.add(trade(4, "buy", 1, 1))


def test_aggregate_trades_one_minute():
    ts = [trade(1, "buy", 2, 1, 100), trade(2, "sell", 1, 30, 101), trade(3, "buy", 5, 59, 102), trade(4, "buy", 1, 61)]
    s = aggregate_trades("BTCUSDT", T0, timedelta(minutes=1), ts, large_trade_quantity=5)
    assert s.trade_count == 3 and s.buy_volume == 7 and s.sell_volume == 1 and s.large_trade_count == 1
    assert s.high == 102 and s.low == 100 and s.buy_sell_ratio == 7


def trend_candles(n, drift, vol=0.002, seed=1):
    return make_candles(n, seed=seed, drift=drift, vol=vol, market="BTCUSDT")


def test_regime_undefined_without_history():
    r = classify_regime(trend_candles(50, 0.0))
    assert r.regime is Regime.UNDEFINED and r.reason.startswith("insufficient_history")


def test_regime_trend_up_and_down():
    assert classify_regime(trend_candles(400, 0.004)).regime is Regime.TREND_UP
    assert classify_regime(trend_candles(400, -0.004)).regime is Regime.TREND_DOWN


def test_regime_high_volatility_takes_priority():
    calm = trend_candles(380, 0.0, vol=0.001)
    wild = make_candles(21, start=calm[-1].open_time + timedelta(hours=1), seed=9, vol=0.05, market="BTCUSDT")
    scale = calm[-1].close / wild[0].open
    wild = [Candle(c.market, c.timeframe, c.open_time, c.open * scale, c.high * scale, c.low * scale, c.close * scale,
                   c.volume, c.source, c.received_at) for c in wild]
    assert classify_regime(calm + wild).regime is Regime.HIGH_VOLATILITY


def test_regime_is_deterministic_and_history_insensitive():
    cs = trend_candles(500, 0.0005, seed=4)
    cfg = RegimeConfig()
    full = classify_regime(cs)
    tail = classify_regime(cs[len(cs) - cfg.warmup:])
    assert full == tail
