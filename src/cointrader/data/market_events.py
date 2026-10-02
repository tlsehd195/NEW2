"""Normalized real-time market events (NORMALIZED layer, ADR-0015).

Every event keeps BOTH clocks: `exchange_time` (when the exchange says
it happened) and `received_at` (when this process received it), plus the
`source` that actually produced it (CLAUDE.md rule 5). A normalized
event is exchange-agnostic: `aggressor_side`, not Binance's `m` flag;
floats, not the exchange's decimal strings.

Construction validates and raises on anything malformed (non-finite or
non-positive prices, negative sizes, naive datetimes). Parsers turn a
`ValueError` into a DATA_QUALITY_EVENT instead of silently dropping the
message.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from cointrader._time import require_aware


def _finite_positive(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")


def _finite_non_negative(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")


def _clocks(kind: str, exchange_time: datetime, received_at: datetime, source: str, symbol: str) -> None:
    require_aware(f"{kind}.exchange_time", exchange_time)
    require_aware(f"{kind}.received_at", received_at)
    if not source:
        raise ValueError(f"{kind}.source must not be empty")
    if not symbol:
        raise ValueError(f"{kind}.symbol must not be empty")


@dataclass(frozen=True)
class TradeTick:
    symbol: str
    price: float
    quantity: float
    aggressor_side: str  # "buy" (taker bought) | "sell" (taker sold)
    trade_id: int
    exchange_time: datetime
    received_at: datetime
    source: str

    def __post_init__(self) -> None:
        _clocks("TradeTick", self.exchange_time, self.received_at, self.source, self.symbol)
        _finite_positive("TradeTick.price", self.price)
        _finite_positive("TradeTick.quantity", self.quantity)
        if self.aggressor_side not in ("buy", "sell"):
            raise ValueError(f"TradeTick.aggressor_side must be buy|sell, got {self.aggressor_side!r}")


@dataclass(frozen=True)
class BookTicker:
    symbol: str
    bid_price: float
    bid_quantity: float
    ask_price: float
    ask_quantity: float
    update_id: int
    exchange_time: datetime
    received_at: datetime
    source: str

    def __post_init__(self) -> None:
        _clocks("BookTicker", self.exchange_time, self.received_at, self.source, self.symbol)
        _finite_positive("BookTicker.bid_price", self.bid_price)
        _finite_positive("BookTicker.ask_price", self.ask_price)
        _finite_non_negative("BookTicker.bid_quantity", self.bid_quantity)
        _finite_non_negative("BookTicker.ask_quantity", self.ask_quantity)
        if self.bid_price >= self.ask_price:
            raise ValueError(f"crossed book ticker: bid {self.bid_price} >= ask {self.ask_price}")

    @property
    def mid(self) -> float:
        return (self.bid_price + self.ask_price) / 2

    @property
    def spread_fraction(self) -> float:
        return (self.ask_price - self.bid_price) / self.mid


@dataclass(frozen=True)
class DepthDelta:
    """One diff-depth update. `bids`/`asks` are absolute quantities at a
    price level (0 = remove the level), not increments."""

    symbol: str
    first_update_id: int  # Binance `U`
    final_update_id: int  # Binance `u`
    previous_final_update_id: Optional[int]  # Binance futures `pu`
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    exchange_time: datetime
    received_at: datetime
    source: str

    def __post_init__(self) -> None:
        _clocks("DepthDelta", self.exchange_time, self.received_at, self.source, self.symbol)
        if self.final_update_id < self.first_update_id:
            raise ValueError("DepthDelta.final_update_id < first_update_id")
        for side in (self.bids, self.asks):
            for price, qty in side:
                _finite_positive("DepthDelta price", price)
                _finite_non_negative("DepthDelta quantity", qty)


@dataclass(frozen=True)
class MarkPriceUpdate:
    symbol: str
    mark_price: float
    index_price: Optional[float]
    funding_rate: Optional[float]  # None when the exchange did not send one
    next_funding_time: Optional[datetime]
    exchange_time: datetime
    received_at: datetime
    source: str

    def __post_init__(self) -> None:
        _clocks("MarkPriceUpdate", self.exchange_time, self.received_at, self.source, self.symbol)
        _finite_positive("MarkPriceUpdate.mark_price", self.mark_price)
        if self.index_price is not None:
            _finite_positive("MarkPriceUpdate.index_price", self.index_price)
        if self.funding_rate is not None and not math.isfinite(self.funding_rate):
            raise ValueError("MarkPriceUpdate.funding_rate must be finite")
        if self.next_funding_time is not None:
            require_aware("MarkPriceUpdate.next_funding_time", self.next_funding_time)


@dataclass(frozen=True)
class DataQualityEvent:
    """DATA_QUALITY_EVENT: a problem that was detected and recorded, never
    silently dropped. `blocks_trading` says whether this event must stop
    new signals until the condition clears."""

    kind: str
    symbol: str
    at: datetime
    detail: str
    source: str
    blocks_trading: bool = True

    def __post_init__(self) -> None:
        require_aware("DataQualityEvent.at", self.at)
