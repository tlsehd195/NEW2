"""Streaming reduction of high-volume raw data into long-term research rows.

Raw trades and order-book updates are too large to keep forever
(`configs/retention.json` gives them a short retention). Before they
age out, each closed minute is reduced to one row that IS kept:

* `MinuteTradeAggregator` -> `TradeStats` per symbol-minute (count,
  buy/sell volume and ratio, average size, large trades, realized vol,
  range, VWAP).
* `MinuteBookAggregator` -> per symbol-minute spread / mid / microprice /
  top-1/5/10 imbalance / depth notional and the change in near-touch
  liquidity versus the previous minute.

Both hold at most one open minute per symbol, so memory is bounded no
matter how long the process runs. A minute is emitted only once a later
event proves it closed; out-of-order trades for an already-emitted
minute are counted in `late_trades` (and reported as a quality issue by
the caller) rather than silently merged.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Optional

from cointrader.data.market_events import BookTicker, TradeTick
from cointrader.data.models import OrderBookSnapshot
from cointrader.features.microstructure import (
    TradeStats,
    aggregate_trades,
    depth_imbalance,
    depth_notional,
    microprice,
    spread_fraction,
    ticker_imbalance,
)

MINUTE = timedelta(minutes=1)


def minute_floor(at: datetime) -> datetime:
    return at.replace(second=0, microsecond=0)


class MinuteTradeAggregator:
    def __init__(self, *, large_trade_quantity: dict[str, float], default_large_trade_quantity: float = float("inf")):
        self._large = large_trade_quantity
        self._default_large = default_large_trade_quantity
        self._open: dict[str, tuple[datetime, list[TradeTick]]] = {}
        self._last_emitted: dict[str, datetime] = {}
        self.late_trades = 0

    def add(self, trade: TradeTick) -> list[TradeStats]:
        m = minute_floor(trade.exchange_time)
        last = self._last_emitted.get(trade.symbol)
        if last is not None and m <= last:
            self.late_trades += 1
            return []
        out = []
        cur = self._open.get(trade.symbol)
        if cur is not None and m > cur[0]:
            out.append(self._emit(trade.symbol))
            cur = None
        if cur is None:
            self._open[trade.symbol] = (m, [trade])
        elif m < cur[0]:
            self.late_trades += 1
        else:
            cur[1].append(trade)
        return out

    def _emit(self, symbol: str) -> TradeStats:
        start, trades = self._open.pop(symbol)
        self._last_emitted[symbol] = start
        return aggregate_trades(symbol, start, MINUTE, trades,
                                large_trade_quantity=self._large.get(symbol, self._default_large))

    def flush_before(self, now: datetime) -> list[TradeStats]:
        """Emit every open minute that ended at or before `now` (call on a
        timer so a quiet market still closes its minute)."""
        return [self._emit(s) for s, (start, _) in list(self._open.items()) if start + MINUTE <= now]

    def open_minutes(self) -> int:
        return len(self._open)


def trade_stats_record(s: TradeStats, source: str) -> dict:
    d = asdict(s)
    d["interval_start"] = s.interval_start.isoformat()
    return {"kind": "trade_stats_1m", "source": source, **d}


def book_row(book: OrderBookSnapshot) -> dict:
    best_bid, best_ask = book.bids[0], book.asks[0]
    bid5, ask5 = depth_notional(book, 5)
    return {
        "spread": spread_fraction(best_bid.price, best_ask.price),
        "mid_price": book.mid,
        "microprice": microprice(best_bid.price, best_ask.price, best_bid.size, best_ask.size),
        "top1_imbalance": depth_imbalance(book, 1),
        "top5_imbalance": depth_imbalance(book, 5),
        "top10_imbalance": depth_imbalance(book, 10),
        "bid_depth_5": bid5,
        "ask_depth_5": ask5,
    }


def ticker_row(t: BookTicker) -> dict:
    return {
        "spread": spread_fraction(t.bid_price, t.ask_price),
        "mid_price": t.mid,
        "microprice": microprice(t.bid_price, t.ask_price, t.bid_quantity, t.ask_quantity),
        "top1_imbalance": ticker_imbalance(t),
    }


class MinuteBookAggregator:
    """Keeps the LAST book observation of each minute (a point-in-time
    snapshot, not an average, so it is exactly what a strategy could have
    seen) plus the minute's worst spread and update count."""

    def __init__(self) -> None:
        self._open: dict[str, dict] = {}
        self._prev_depth: dict[str, float] = {}

    def add(self, symbol: str, at: datetime, row: dict) -> Optional[dict]:
        m = minute_floor(at)
        cur = self._open.get(symbol)
        emitted = None
        if cur is not None and m > cur["minute"]:
            emitted = self._emit(symbol)
            cur = None
        if cur is None:
            cur = self._open[symbol] = {"minute": m, "updates": 0, "max_spread": None, "last": None}
        if m < cur["minute"]:
            return emitted
        cur["updates"] += 1
        sp = row.get("spread")
        if sp is not None:
            cur["max_spread"] = sp if cur["max_spread"] is None else max(cur["max_spread"], sp)
        cur["last"] = row
        return emitted

    def _emit(self, symbol: str) -> dict:
        cur = self._open.pop(symbol)
        last = dict(cur["last"] or {})
        depth = (last.get("bid_depth_5") or 0.0) + (last.get("ask_depth_5") or 0.0)
        prev = self._prev_depth.get(symbol)
        change = (depth - prev) / prev if prev and depth else None
        if depth:
            self._prev_depth[symbol] = depth
        return {"kind": "book_stats_1m", "symbol": symbol, "interval_start": cur["minute"].isoformat(),
                "updates": cur["updates"], "max_spread": cur["max_spread"], "liquidity_change_5": change, **last}

    def flush_before(self, now: datetime) -> list[dict]:
        return [self._emit(s) for s, cur in list(self._open.items()) if cur["minute"] + MINUTE <= now]
