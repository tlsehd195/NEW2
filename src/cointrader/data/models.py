"""Market data models.

Every candle carries its provenance: which source actually produced it
(`upbit_ws`, `upbit_rest`, ...) and when this process received it. The
same principle as tlsehd195/NEW-'s `FallbackDataProvider`: a caller can
always tell which source answered, and a REST-backfilled bar is never
presented as if it had arrived live.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from cointrader._time import require_aware


class Timeframe(Enum):
    MINUTE_1 = "1m"
    MINUTE_3 = "3m"
    MINUTE_5 = "5m"
    MINUTE_15 = "15m"
    HOUR_1 = "1h"
    HOUR_4 = "4h"
    DAY_1 = "1d"

    @property
    def delta(self) -> timedelta:
        return _DELTAS[self]


_DELTAS = {
    Timeframe.MINUTE_1: timedelta(minutes=1),
    Timeframe.MINUTE_3: timedelta(minutes=3),
    Timeframe.MINUTE_5: timedelta(minutes=5),
    Timeframe.MINUTE_15: timedelta(minutes=15),
    Timeframe.HOUR_1: timedelta(hours=1),
    Timeframe.HOUR_4: timedelta(hours=4),
    Timeframe.DAY_1: timedelta(days=1),
}


@dataclass(frozen=True)
class Candle:
    """One closed OHLCV bar. `open_time` is the bar's start; the bar
    covers `[open_time, open_time + timeframe.delta)`. `volume` is in the
    base asset (e.g. BTC for KRW-BTC)."""

    market: str
    timeframe: Timeframe
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str
    received_at: datetime

    def __post_init__(self) -> None:
        require_aware("Candle.open_time", self.open_time)
        require_aware("Candle.received_at", self.received_at)
        if not self.market:
            raise ValueError("Candle.market must not be empty")
        if not self.source:
            raise ValueError("Candle.source must not be empty")
        for name in ("open", "high", "low", "close", "volume"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"Candle.{name} must be finite")

    @property
    def close_time(self) -> datetime:
        return self.open_time + self.timeframe.delta


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Best-first levels on each side: `bids` descending by price,
    `asks` ascending."""

    market: str
    as_of: datetime
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]

    def __post_init__(self) -> None:
        require_aware("OrderBookSnapshot.as_of", self.as_of)
        if any(a.price <= b.price for a, b in zip(self.bids, self.bids[1:])):
            raise ValueError("bids must be strictly descending by price")
        if any(b.price <= a.price for a, b in zip(self.asks, self.asks[1:])):
            raise ValueError("asks must be strictly ascending by price")
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise ValueError("crossed book: best bid >= best ask")

    @property
    def mid(self) -> float:
        if not self.bids or not self.asks:
            raise ValueError("mid is undefined for a one-sided book")
        return (self.bids[0].price + self.asks[0].price) / 2
