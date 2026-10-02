"""Order-book and trade-flow features for scalping research.

Pure functions over a book snapshot / ticker / a window of trades that
already happened. `TradeFlowWindow` is the incremental form: it keeps a
bounded deque of trades inside a time window and recomputes its sums
with `math.fsum` on demand, so its answer is exactly what the pure
function gives on the same trades (no drifting running sums).
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence

from cointrader.data.market_events import BookTicker, TradeTick
from cointrader.data.models import OrderBookSnapshot


def book_imbalance(bid_quantity: float, ask_quantity: float) -> Optional[float]:
    """(bid - ask) / (bid + ask) in [-1, 1]; None for an empty book."""
    total = bid_quantity + ask_quantity
    if not math.isfinite(total) or total <= 0:
        return None
    return (bid_quantity - ask_quantity) / total


def ticker_imbalance(t: BookTicker) -> Optional[float]:
    return book_imbalance(t.bid_quantity, t.ask_quantity)


def depth_imbalance(book: OrderBookSnapshot, levels: int) -> Optional[float]:
    if levels < 1 or not book.bids or not book.asks:
        return None
    b = math.fsum(l.size for l in book.bids[:levels])
    a = math.fsum(l.size for l in book.asks[:levels])
    return book_imbalance(b, a)


def depth_notional(book: OrderBookSnapshot, levels: int) -> tuple[float, float]:
    return (math.fsum(l.price * l.size for l in book.bids[:levels]),
            math.fsum(l.price * l.size for l in book.asks[:levels]))


def microprice(bid: float, ask: float, bid_quantity: float, ask_quantity: float) -> Optional[float]:
    """Size-weighted mid: leans toward the side with LESS resting size
    (the side more likely to be taken out next)."""
    total = bid_quantity + ask_quantity
    if total <= 0 or bid <= 0 or ask <= bid:
        return None
    return (bid * ask_quantity + ask * bid_quantity) / total


def spread_fraction(bid: float, ask: float) -> Optional[float]:
    if bid <= 0 or ask <= bid:
        return None
    return (ask - bid) / ((ask + bid) / 2)


def percentile_rank(value: float, history: Sequence[float]) -> Optional[float]:
    """Fraction of `history` strictly below `value` (history excludes the
    current observation)."""
    if not history or not math.isfinite(value):
        return None
    return sum(1 for h in history if h < value) / len(history)


def trade_imbalance(trades: Iterable[TradeTick]) -> Optional[float]:
    """Signed aggressor volume / total volume in [-1, 1]."""
    buy = sell = 0.0
    parts_b, parts_s = [], []
    for t in trades:
        (parts_b if t.aggressor_side == "buy" else parts_s).append(t.quantity)
    buy, sell = math.fsum(parts_b), math.fsum(parts_s)
    return book_imbalance(buy, sell)


def aggressor_count_imbalance(trades: Iterable[TradeTick]) -> Optional[float]:
    b = s = 0
    for t in trades:
        if t.aggressor_side == "buy":
            b += 1
        else:
            s += 1
    return (b - s) / (b + s) if b + s else None


@dataclass(frozen=True)
class TradeStats:
    """Research aggregate of one interval of trades (the long-term form
    of raw ticks, ADR-0015 retention: raw ticks -> 1m statistics)."""

    symbol: str
    interval_start: datetime
    interval_seconds: int
    trade_count: int
    buy_volume: float
    sell_volume: float
    buy_sell_ratio: Optional[float]
    average_trade_size: Optional[float]
    large_trade_count: int
    realized_volatility: Optional[float]
    high: Optional[float]
    low: Optional[float]
    vwap: Optional[float]


def aggregate_trades(symbol: str, interval_start: datetime, interval: timedelta, trades: Sequence[TradeTick],
                     *, large_trade_quantity: float) -> TradeStats:
    inside = [t for t in trades if interval_start <= t.exchange_time < interval_start + interval]
    buy = math.fsum(t.quantity for t in inside if t.aggressor_side == "buy")
    sell = math.fsum(t.quantity for t in inside if t.aggressor_side == "sell")
    prices = [t.price for t in inside]
    rets = [math.log(b / a) for a, b in zip(prices, prices[1:])]
    rv = None
    if len(rets) >= 2:
        m = math.fsum(rets) / len(rets)
        rv = math.sqrt(math.fsum((r - m) ** 2 for r in rets) / (len(rets) - 1))
    vol = buy + sell
    return TradeStats(
        symbol=symbol, interval_start=interval_start, interval_seconds=int(interval.total_seconds()),
        trade_count=len(inside), buy_volume=buy, sell_volume=sell,
        buy_sell_ratio=buy / sell if sell > 0 else None,
        average_trade_size=vol / len(inside) if inside else None,
        large_trade_count=sum(1 for t in inside if t.quantity >= large_trade_quantity),
        realized_volatility=rv, high=max(prices) if prices else None, low=min(prices) if prices else None,
        vwap=math.fsum(t.price * t.quantity for t in inside) / vol if vol > 0 else None,
    )


class TradeFlowWindow:
    """Trades within the trailing `window` (by exchange time). Bounded by
    `max_trades` as well, so a burst can never grow memory unboundedly."""

    def __init__(self, window: timedelta, *, max_trades: int = 100_000) -> None:
        self._window = window
        self._trades: deque = deque(maxlen=max_trades)

    def add(self, trade: TradeTick) -> None:
        if self._trades and trade.exchange_time < self._trades[-1].exchange_time:
            raise ValueError("trades must be added in exchange-time order")
        self._trades.append(trade)
        self._evict(trade.exchange_time)

    def _evict(self, now: datetime) -> None:
        while self._trades and self._trades[0].exchange_time <= now - self._window:
            self._trades.popleft()

    def trades(self, now: datetime) -> list[TradeTick]:
        self._evict(now)
        return list(self._trades)

    def imbalance(self, now: datetime) -> Optional[float]:
        return trade_imbalance(self.trades(now))

    def __len__(self) -> int:
        return len(self._trades)
