"""Leak-free (features, forward-return) samples from a candle series."""

from __future__ import annotations

import math
from typing import Optional, Sequence

from cointrader.data.models import Candle
from cointrader.ml.features import FEATURE_WINDOW, compute_feature_vector
from cointrader.ml.samples import MLSample


def forward_log_return(candles: Sequence[Candle], index: int, horizon: int) -> Optional[float]:
    """Close-to-close log return from bar `index` to bar `index + horizon`.
    None when the target bar does not exist yet or prices are unusable."""
    j = index + horizon
    if horizon < 1 or index < 0 or j >= len(candles):
        return None
    a, b = candles[index].close, candles[j].close
    if a <= 0 or b <= 0:
        return None
    r = math.log(b / a)
    return r if math.isfinite(r) else None


def sample_at(candles: Sequence[Candle], index: int, horizon: int,
              features: Optional[dict] = None) -> Optional[MLSample]:
    """Sample for bar `index`, or None if its features/target are unavailable.
    Pass `features` to reuse an already computed vector for that bar."""
    if features is None:
        if index + 1 < FEATURE_WINDOW:
            return None
        features = compute_feature_vector(candles[index + 1 - FEATURE_WINDOW: index + 1])
    target = forward_log_return(candles, index, horizon)
    if features is None or target is None:
        return None
    return MLSample(candles[index].close_time, candles[index + horizon].close_time, features, target)


def build_samples(candles: Sequence[Candle], horizon: int, *, step: int = 1) -> list[MLSample]:
    """All usable samples, oldest first. A bar whose features or target are
    missing is skipped (and not imputed)."""
    out = []
    for i in range(FEATURE_WINDOW - 1, len(candles) - horizon, max(1, step)):
        s = sample_at(candles, i, horizon)
        if s is not None:
            out.append(s)
    return out
