"""Crowd-positioning scores (ADR-0080). DIAGNOSTIC ONLY: nothing here is wired into a strategy or `DEFAULT_PANEL`."""

from __future__ import annotations

import math
from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Optional, Sequence

SAFETY_LAG = timedelta(minutes=5)  # a reading stamped T is used only from T + 5 min (one whole 5-min row of margin)
MAX_STALE = timedelta(minutes=30)  # an older reading counts as missing, not as "still valid"


def sample_at_decisions(times: Sequence[datetime], values: Sequence[float], decisions: Sequence[datetime]) -> list[Optional[float]]:
    """Latest reading stamped at or before `decision - SAFETY_LAG`, or None if it is older than MAX_STALE."""
    out: list[Optional[float]] = []
    for d in decisions:
        k = bisect_right(times, d - SAFETY_LAG) - 1
        out.append(values[k] if k >= 0 and d - times[k] <= MAX_STALE + SAFETY_LAG else None)
    return out


def causal_zscore(x: Sequence[Optional[float]], lookback: int, min_share: float = 0.8) -> list[Optional[float]]:
    """z of x[i] against the `lookback` values BEFORE i (x[i] itself excluded). None while fewer than
    `min_share * lookback` of them are present, or when they are constant."""
    out: list[Optional[float]] = []
    s = s2 = 0.0
    n = 0
    for i, v in enumerate(x):
        if i >= 1:  # bring x[i-1] into the window, drop x[i-1-lookback]
            a = x[i - 1]
            if a is not None:
                s, s2, n = s + a, s2 + a * a, n + 1
            j = i - 1 - lookback
            if j >= 0 and x[j] is not None:
                s, s2, n = s - x[j], s2 - x[j] * x[j], n - 1
        if v is None or n < min_share * lookback:
            out.append(None)
            continue
        m = s / n
        var = s2 / n - m * m
        out.append((v - m) / math.sqrt(var) if var > 1e-12 else None)
    return out
