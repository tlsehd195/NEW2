"""Feature vector for the ML models, computed from candles only.

Six indicators, one per distinct kind of information, chosen so no two
restate the same thing (RSI and a ROC/MACD pair would):

    rsi_scaled   short-term momentum         (RSI14 - 50) / 50
    bb_z         mean-reversion distance     Bollinger(20) z-score
    trend        trend position              close / EMA50 - 1
    vol_z        participation               volume z-score vs prior 20 bars
    vwap_dev     price vs volume-weighted    close / VWAP20 - 1
    atr_pct      volatility regime           ATR14 / close

Point-in-time safe by construction: the vector for the bar at index `i`
uses only `candles[:i + 1]`. **No imputation**: if any feature is
unavailable or non-finite the function returns None and the caller must
drop that sample -- never fill with 0 or a mean.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from cointrader.data.models import Candle
from cointrader.features import indicators as ind

FEATURE_IDS: tuple[str, ...] = ("rsi_scaled", "bb_z", "trend", "vol_z", "vwap_dev", "atr_pct")
FEATURE_WINDOW = 100  # bars of history the vector needs (EMA50 over 100 bars)


def compute_feature_vector(history: Sequence[Candle]) -> Optional[dict]:
    if len(history) < FEATURE_WINDOW:
        return None
    h = list(history[len(history) - FEATURE_WINDOW:])
    closes = [c.close for c in h]
    rsi = ind.rsi(closes, 14)
    bb = ind.bollinger(closes, 20)
    atr = ind.atr(h, 14)
    close = closes[-1]
    ema50 = ind.ema(closes, 50, lookback=FEATURE_WINDOW)
    raw = {
        "rsi_scaled": None if rsi is None else (rsi - 50.0) / 50.0,
        "bb_z": None if bb is None else bb["zscore"],
        "trend": None if not ema50 else close / ema50 - 1.0,
        "vol_z": ind.volume_zscore(h, 20),
        "vwap_dev": ind.vwap_deviation(h, 20),
        "atr_pct": None if atr is None or close <= 0 else atr / close,
    }
    if any(v is None or not math.isfinite(v) for v in raw.values()):
        return None
    return raw
