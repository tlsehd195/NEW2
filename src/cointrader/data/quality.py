"""Candle-series quality checks.

Same principle as tlsehd195/NEW-'s `data_infra/quality.py`: a problem is
recorded explicitly as a `QualityIssue`, never skipped or silently
repaired. Callers decide what to do (backfill a gap, drop a run, halt);
this module only reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from cointrader.data.models import Candle


@dataclass(frozen=True)
class QualityIssue:
    kind: str  # "gap" | "duplicate" | "out_of_order" | "ohlc_invariant" | "negative_volume" | "misaligned"
    market: str
    at: datetime
    detail: str


def check_candles(candles: Sequence[Candle]) -> list[QualityIssue]:
    """Checks one market/timeframe series, in the order given."""
    issues: list[QualityIssue] = []
    if not candles:
        return issues
    market, timeframe = candles[0].market, candles[0].timeframe
    step = timeframe.delta
    seen: set[datetime] = set()
    previous: Candle | None = None
    for c in candles:
        if c.market != market or c.timeframe != timeframe:
            raise ValueError("check_candles expects a single market/timeframe series")
        if c.low > min(c.open, c.close) or c.high < max(c.open, c.close) or c.low > c.high:
            issues.append(QualityIssue("ohlc_invariant", market, c.open_time,
                                       f"o={c.open} h={c.high} l={c.low} c={c.close}"))
        if c.volume < 0:
            issues.append(QualityIssue("negative_volume", market, c.open_time, f"volume={c.volume}"))
        if c.open_time.timestamp() % step.total_seconds() != 0 and step.days == 0:
            issues.append(QualityIssue("misaligned", market, c.open_time,
                                       f"open_time not aligned to {timeframe.value}"))
        if c.open_time in seen:
            issues.append(QualityIssue("duplicate", market, c.open_time, "repeated open_time"))
        seen.add(c.open_time)
        if previous is not None:
            if c.open_time < previous.open_time:
                issues.append(QualityIssue("out_of_order", market, c.open_time,
                                           f"after {previous.open_time.isoformat()}"))
            elif c.open_time - previous.open_time > step:
                missing = int((c.open_time - previous.open_time) / step) - 1
                issues.append(QualityIssue("gap", market, previous.open_time + step,
                                           f"{missing} missing bar(s) before {c.open_time.isoformat()}"))
        previous = c
    return issues


def find_gaps(candles: Sequence[Candle]) -> list[tuple[datetime, datetime]]:
    """Missing `[start, end)` open-time ranges between consecutive bars
    of an ordered series -- exactly what the REST backfill needs to
    request."""
    gaps = []
    for prev, cur in zip(candles, candles[1:]):
        step = prev.timeframe.delta
        if cur.open_time - prev.open_time > step:
            gaps.append((prev.open_time + step, cur.open_time))
    return gaps
