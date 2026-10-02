"""Votes that need data outside the candle: funding rate and open interest,
plus the candle-only volatility gate (ADR-0025).

Every function takes only records already known at `now` (an
as-of-time-filtered view) and returns None when the input is missing or
too short -- the caller treats None as "no vote / no trade" (fail-closed).
Neither vote is taken from a paper: they are design choices, graded B and
tested on their own by pre-registration like everything else.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional, Sequence

from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.binance_vision import OpenInterestPoint
from cointrader.data.models import Candle

FUNDING_SCALE = 0.0005  # 0.05% per 8h is a crowded market; score saturates around there
OI_SCALE = 0.05  # a 5% day-over-lookback OI change is large
PRICE_SCALE = 0.05


def known_funding(records: Sequence[FundingRateRecord], now: datetime) -> list[FundingRateRecord]:
    return [r for r in records if r.funding_time <= now]


def known_open_interest(points: Sequence[OpenInterestPoint], now: datetime) -> list[OpenInterestPoint]:
    return [p for p in points if p.as_of <= now]


def funding_crowding_score(known: Sequence[FundingRateRecord], *, lookback: int = 9) -> Optional[float]:
    """Contrarian crowding: persistently high funding = crowded longs =
    bearish (score < 0); persistently negative = crowded shorts = bullish.
    Mean of the last `lookback` settlements (9 = three days)."""
    if len(known) < lookback:
        return None
    mean = math.fsum(r.funding_rate for r in known[-lookback:]) / lookback
    return -math.tanh(mean / FUNDING_SCALE)


def oi_confirmation_score(
    known: Sequence[OpenInterestPoint], closes: Sequence[float], *, lookback: int = 5,
) -> Optional[float]:
    """Trend confirmation: price move over `lookback` days counts only when
    open interest ROSE over the same days (new positions behind the move).
    Falling or flat OI abstains (score 0.0), it never votes the other way."""
    if len(known) <= lookback or len(closes) <= lookback:
        return None
    oi_now, oi_then = known[-1].open_interest, known[-1 - lookback].open_interest
    if oi_then <= 0 or closes[-1 - lookback] <= 0:
        return None
    oi_change = oi_now / oi_then - 1.0
    price_change = closes[-1] / closes[-1 - lookback] - 1.0
    return math.tanh(price_change / PRICE_SCALE) * max(0.0, math.tanh(oi_change / OI_SCALE))


def volatility_ratio(history: Sequence[Candle], *, short: int = 10, long: int = 60) -> Optional[float]:
    """Short-window / long-window realized volatility of close-to-close
    returns. ~1 = normal, >> 1 = turbulence, << 1 = compression."""
    if len(history) < long + 1:
        return None
    closes = [c.close for c in history[-(long + 1):]]
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < long:
        return None

    def sd(xs: Sequence[float]) -> float:
        m = math.fsum(xs) / len(xs)
        return math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1))

    long_sd = sd(rets)
    return None if long_sd == 0 else sd(rets[-short:]) / long_sd
