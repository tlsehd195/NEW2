"""The standard candle feature snapshot a strategy decision is logged with.

One function, one versioned set of names (`FEATURE_VERSION`). Strategies
may compute more, but every decision records at least this snapshot so
the FEATURE layer of the research store has a stable schema
(ADR-0015). A value that could not be computed is stored as `None`,
never as 0.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from cointrader.data.market_events import BookTicker
from cointrader.data.models import Candle
from cointrader.features import indicators as ind
from cointrader.features.indicators import FEATURE_VERSION
from cointrader.features.microstructure import microprice, spread_fraction, ticker_imbalance

# Bars of history the standard snapshot needs for every field to exist.
SNAPSHOT_WARMUP = 4 * 50 + 1


def _clean(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


def candle_features(candles: Sequence[Candle]) -> dict:
    if not candles:
        return {"feature_version": FEATURE_VERSION}
    if len(candles) > SNAPSHOT_WARMUP:
        candles = candles[len(candles) - SNAPSHOT_WARMUP:]  # every field below needs only this tail
    closes = [c.close for c in candles]
    bb = ind.bollinger(closes, 20, 2.0) or {}
    macd = ind.macd(closes) if len(closes) >= 4 * 26 + 4 * 9 else None
    dc = ind.donchian(candles, 20)
    atr14 = ind.atr(candles, 14)
    out = {
        "feature_version": FEATURE_VERSION,
        "close": closes[-1],
        "ema_20": ind.ema(closes, 20),
        "ema_50": ind.ema(closes, 50),
        "price_vs_ema_50": ind.price_vs_ema(closes, 50),
        "ema_20_slope_5": ind.ema_slope(closes, 20, 5),
        "ma_alignment_10_20_50": ind.ma_alignment(closes, (10, 20, 50)),
        "rsi_14": ind.rsi(closes, 14),
        "roc_10": ind.roc(closes, 10),
        "macd": macd[0] if macd else None,
        "macd_signal": macd[1] if macd else None,
        "macd_hist": macd[2] if macd else None,
        "atr_14": atr14,
        "atr_14_fraction": atr14 / closes[-1] if atr14 is not None and closes[-1] else None,
        "realized_vol_20": ind.realized_volatility(closes, 20),
        "bb_pct_b": bb.get("pct_b"),
        "bb_bandwidth": bb.get("bandwidth"),
        "bb_zscore": bb.get("zscore"),
        "volume_z_20": ind.volume_zscore(candles, 20),
        "relative_volume_20": ind.relative_volume(candles, 20),
        "vwap_20": ind.vwap(candles, 20),
        "vwap_deviation_20": ind.vwap_deviation(candles, 20),
        "donchian_high_20": dc[0] if dc else None,
        "donchian_low_20": dc[1] if dc else None,
        "breakout_20": ind.breakout(candles, 20),
        **ind.candle_shape(candles[-1]),
    }
    return {k: (v if k == "feature_version" else _clean(v)) for k, v in out.items()}


def book_features(book: Optional[BookTicker]) -> dict:
    if book is None:
        return {"spread": None, "mid_price": None, "microprice": None, "top1_imbalance": None}
    return {
        "spread": _clean(spread_fraction(book.bid_price, book.ask_price)),
        "mid_price": book.mid,
        "microprice": _clean(microprice(book.bid_price, book.ask_price, book.bid_quantity, book.ask_quantity)),
        "top1_imbalance": _clean(ticker_imbalance(book)),
    }
