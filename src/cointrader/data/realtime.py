"""Resilient real-time market event feed.

Extends the principle of `data.feed.ResilientCandleFeed` (reconnect with
exponential backoff, REST backfill of any missed candle interval,
provenance kept, every disconnect reported) from a single candle stream
to a multiplexed exchange stream carrying trades, book tickers, depth
deltas, mark prices and klines.

What this layer guarantees to everything downstream:

- **Reconnect**: a `ConnectionError`/`TimeoutError` from the source
  reconnects with exponential backoff; `max_consecutive_failures` in a
  row raises `FeedUnavailable` (fail-closed signal, same as `data.feed`).
- **Heartbeat / stale detection**: the source answers pings; if no
  message at all arrives within `stale_after`, the connection is treated
  as dead and re-established (and a `DataQualityEvent("stale_feed")`
  is emitted).
- **Duplicates and ordering**: trades are de-duplicated by trade id,
  book tickers by update id, klines by open time. An exchange timestamp
  that goes backwards, or that is implausibly far from local receipt
  time, is reported as a `DataQualityEvent` -- the item is dropped but
  the problem never is.
- **Gaps**: a kline gap is backfilled from REST (`CandleHistory`) before
  the newer bar is emitted; a trade-id gap is reported. Depth sequence
  gaps are the order book's job (`data.orderbook`).
- **Health**: `FeedHealthMonitor` answers "is this feed usable right
  now?" as a `live.config.HealthStatus`, for the quality gate, the kill
  switch trigger context and the safety gate.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator, Optional, Protocol, Union

from cointrader.data.binance_ws import parse_message
from cointrader.data.feed import CandleHistory, FeedEvent, FeedUnavailable
from cointrader.data.market_events import BookTicker, DataQualityEvent, DepthDelta, MarkPriceUpdate, TradeTick
from cointrader.data.models import Candle, Timeframe
from cointrader.live.config import HealthStatus

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MessageSource(Protocol):
    def connect(self) -> None: ...

    def messages(self) -> Iterator[str]:
        """Yields raw text messages. Raises ConnectionError on a drop and
        TimeoutError when nothing arrived within its read timeout."""
        ...

    def close(self) -> None: ...


class WebSocketMessageSource:
    """`MessageSource` over `data.websocket`. Pings are answered here, so
    the exchange's heartbeat keeps the connection alive."""

    def __init__(self, url: str, *, read_timeout: float = 30.0, connect_fn=None) -> None:
        from cointrader.data import websocket as ws

        self._url = url
        self._timeout = read_timeout
        self._connect_fn = connect_fn or (lambda u: ws.connect(u, timeout=read_timeout))
        self._ws_module = ws
        self._conn = None

    def connect(self) -> None:
        self._conn = self._connect_fn(self._url)

    def messages(self) -> Iterator[str]:
        ws = self._ws_module
        conn = self._conn  # bound once: a late reader of a closed connection must never read its replacement
        if conn is None:
            raise ConnectionError("not connected")
        while True:
            msg = conn.recv()
            if msg.opcode == ws.OP_PING:
                conn.send_pong(msg.payload)
                continue
            if msg.opcode == ws.OP_PONG:
                continue
            yield msg.text

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class MultiMessageSource:
    """Merges several `MessageSource`s (one per Binance route, ADR-0045) into one. Every connection must keep
    delivering: an error or `read_timeout` seconds without any message from the merged stream is raised to the
    feed, which reconnects all of them."""

    def __init__(self, sources: list, *, read_timeout: float = 30.0) -> None:
        if not sources:
            raise ValueError("at least one source is required")
        self._sources = sources
        self._timeout = read_timeout
        self._queue: queue.Queue = queue.Queue()

    def connect(self) -> None:
        self._queue = queue.Queue()
        try:
            for s in self._sources:
                s.connect()
        except Exception:
            self.close()
            raise
        for s in self._sources:
            threading.Thread(target=self._pump, args=(s, self._queue), name="ws-pump", daemon=True).start()

    def _pump(self, source, q: queue.Queue) -> None:
        try:
            for text in source.messages():
                q.put(("msg", text))
        except Exception as exc:  # noqa: BLE001 - handed to the consumer, which re-raises it
            q.put(("err", exc))
        else:
            q.put(("err", ConnectionError("websocket source ended")))

    def messages(self) -> Iterator[str]:
        q = self._queue
        while True:
            try:
                kind, value = q.get(timeout=self._timeout)
            except queue.Empty:
                raise TimeoutError(f"no message for {self._timeout:.0f}s on any connection") from None
            if kind == "err":
                if isinstance(value, (ConnectionError, TimeoutError)):
                    raise value
                raise ConnectionError(f"{type(value).__name__}: {value}") from value
            yield value

    def close(self) -> None:
        for s in self._sources:
            try:
                s.close()
            except OSError:
                pass


@dataclass(frozen=True)
class FeedLimits:
    stale_after: timedelta = timedelta(seconds=30)
    max_clock_skew: timedelta = timedelta(seconds=5)  # exchange time ahead of local receipt
    max_latency: timedelta = timedelta(seconds=10)  # local receipt behind exchange time
    dedupe_window: int = 50_000  # bounded memory for seen trade ids per symbol


FeedItem = Union[TradeTick, BookTicker, DepthDelta, MarkPriceUpdate, Candle, DataQualityEvent, FeedEvent]


class _RecentIds:
    """Bounded set of recently seen ids (constant memory)."""

    def __init__(self, maxlen: int) -> None:
        self._order: deque = deque()
        self._set: set = set()
        self._max = maxlen

    def seen(self, key) -> bool:
        if key in self._set:
            return True
        self._order.append(key)
        self._set.add(key)
        if len(self._order) > self._max:
            self._set.discard(self._order.popleft())
        return False

    def __len__(self) -> int:
        return len(self._order)


class ResilientEventFeed:
    def __init__(
        self,
        source: MessageSource,
        *,
        history: Optional[CandleHistory] = None,
        limits: FeedLimits = FeedLimits(),
        max_consecutive_failures: int = 5,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Clock = _utcnow,
    ) -> None:
        self._source = source
        self._history = history
        self._limits = limits
        self._max_failures = max_consecutive_failures
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._sleep = sleep
        self._now = now
        self._trade_ids: dict[str, _RecentIds] = {}
        self._last_trade_id: dict[str, int] = {}
        self._last_book_update: dict[str, int] = {}
        self._last_exchange_time: dict[tuple[str, str], datetime] = {}
        self._last_kline: dict[tuple[str, Timeframe], datetime] = {}
        self.stats = {"messages": 0, "duplicates": 0, "quality_events": 0, "reconnects": 0}

    # -- validation helpers -------------------------------------------------
    def _timing_problem(self, kind: str, symbol: str, exchange_time: datetime, received_at: datetime) -> Optional[DataQualityEvent]:
        if exchange_time - received_at > self._limits.max_clock_skew:
            return DataQualityEvent("clock_skew", symbol, received_at,
                                    f"{kind} exchange_time {exchange_time.isoformat()} is ahead of local receipt", "feed")
        if received_at - exchange_time > self._limits.max_latency:
            return DataQualityEvent("latency", symbol, received_at,
                                    f"{kind} arrived {(received_at - exchange_time).total_seconds():.1f}s late", "feed")
        key = (kind, symbol)
        last = self._last_exchange_time.get(key)
        if last is not None and exchange_time < last:
            return DataQualityEvent("timestamp_out_of_order", symbol, received_at,
                                    f"{kind} exchange_time went back from {last.isoformat()} to {exchange_time.isoformat()}",
                                    "feed")
        self._last_exchange_time[key] = exchange_time
        return None

    def _accept(self, item) -> Iterator[FeedItem]:
        if isinstance(item, TradeTick):
            ids = self._trade_ids.setdefault(item.symbol, _RecentIds(self._limits.dedupe_window))
            if ids.seen(item.trade_id):
                self.stats["duplicates"] += 1
                return
            problem = self._timing_problem("trade", item.symbol, item.exchange_time, item.received_at)
            if problem:
                yield problem
                return
            last = self._last_trade_id.get(item.symbol)
            if last is not None and item.trade_id > last + 1:
                yield DataQualityEvent("trade_sequence_gap", item.symbol, item.received_at,
                                       f"{item.trade_id - last - 1} aggregate trade id(s) missing after {last}", "feed",
                                       blocks_trading=False)
            self._last_trade_id[item.symbol] = max(item.trade_id, last or item.trade_id)
            yield item
        elif isinstance(item, BookTicker):
            last = self._last_book_update.get(item.symbol)
            if last is not None and item.update_id <= last:
                self.stats["duplicates"] += 1
                return
            problem = self._timing_problem("book_ticker", item.symbol, item.exchange_time, item.received_at)
            if problem:
                yield problem
                return
            self._last_book_update[item.symbol] = item.update_id
            yield item
        elif isinstance(item, (DepthDelta, MarkPriceUpdate)):
            kind = "depth" if isinstance(item, DepthDelta) else "mark_price"
            problem = self._timing_problem(kind, item.symbol, item.exchange_time, item.received_at)
            if problem:
                yield problem
                return
            yield item
        elif isinstance(item, Candle):
            key = (item.market, item.timeframe)
            last = self._last_kline.get(key)
            if last is not None and item.open_time <= last:
                self.stats["duplicates"] += 1
                return
            if item.received_at < item.close_time:
                yield DataQualityEvent("unclosed_candle", item.market, item.received_at,
                                       f"kline {item.open_time.isoformat()} received before it closed", "feed")
                return
            if last is not None and item.open_time - last > item.timeframe.delta:
                yield from self._backfill(key, last + item.timeframe.delta, item.open_time)
            self._last_kline[key] = item.open_time
            yield item
        elif isinstance(item, DataQualityEvent):
            yield item

    def _backfill(self, key: tuple[str, Timeframe], start: datetime, end: datetime) -> Iterator[FeedItem]:
        market, timeframe = key
        expected = int((end - start) / timeframe.delta)
        if self._history is None:
            yield DataQualityEvent("missing_candle", market, self._now(),
                                   f"{expected} {timeframe.value} bar(s) missing in [{start.isoformat()}, {end.isoformat()}) "
                                   "and no REST history configured", "feed")
            return
        try:
            fetched = self._history.fetch(market, timeframe, start, end)
        except OSError as exc:
            yield DataQualityEvent("backfill_failed", market, self._now(), f"{type(exc).__name__}: {exc}", "feed")
            return
        filled = 0
        for c in fetched:
            if start <= c.open_time < end and c.open_time > self._last_kline.get(key, start - timeframe.delta):
                self._last_kline[key] = c.open_time
                filled += 1
                yield c
        kind = "backfilled" if filled == expected else "backfill_incomplete"
        yield FeedEvent(kind, self._now(), f"{market} {timeframe.value}: {filled}/{expected} bars from REST")
        if filled != expected:
            yield DataQualityEvent("missing_candle", market, self._now(),
                                   f"REST backfill returned {filled}/{expected} {timeframe.value} bars", "feed")

    # -- main loop ----------------------------------------------------------
    def run(self) -> Iterator[FeedItem]:
        failures = 0
        while True:
            try:
                self._source.connect()
                yield FeedEvent("connected", self._now(), f"attempt after {failures} failure(s)")
                last_message_at = self._now()
                for text in self._source.messages():
                    now = self._now()
                    if now - last_message_at > self._limits.stale_after:
                        raise TimeoutError(f"no message for {(now - last_message_at).total_seconds():.0f}s")
                    last_message_at = now
                    failures = 0
                    self.stats["messages"] += 1
                    parsed = parse_message(text, now)
                    if parsed is None:
                        continue
                    for out in self._accept(parsed):
                        if isinstance(out, DataQualityEvent):
                            self.stats["quality_events"] += 1
                        yield out
                self._source.close()
                return  # source ended cleanly (replay / test)
            except (ConnectionError, TimeoutError) as exc:
                self._source.close()
                failures += 1
                self.stats["reconnects"] += 1
                if isinstance(exc, TimeoutError):
                    yield DataQualityEvent("stale_feed", "*", self._now(), str(exc), "feed")
                yield FeedEvent("disconnected", self._now(), f"{type(exc).__name__}: {exc}")
                if failures >= self._max_failures:
                    raise FeedUnavailable(f"event feed failed {failures} times in a row") from exc
                self._sleep(min(self._max_backoff, self._base_backoff * 2 ** (failures - 1)))


class FeedHealthMonitor:
    """Tracks the last good item per (kind, symbol) and whether the
    connection is up. `status` is UNKNOWN before anything was seen,
    UNAVAILABLE while disconnected, DEGRADED when stale or when a
    blocking quality event is still recent, else HEALTHY."""

    def __init__(self, *, stale_after: timedelta = timedelta(seconds=30),
                 quality_hold: timedelta = timedelta(seconds=60)) -> None:
        self._stale_after = stale_after
        self._quality_hold = quality_hold
        self._last_seen: dict[tuple[str, str], datetime] = {}
        self._connected = False
        self._last_blocking_quality: dict[str, tuple[datetime, str]] = {}

    def observe(self, item: FeedItem) -> None:
        if isinstance(item, FeedEvent):
            if item.kind == "connected":
                self._connected = True
            elif item.kind == "disconnected":
                self._connected = False
            return
        if isinstance(item, DataQualityEvent):
            if item.blocks_trading:
                self._last_blocking_quality[item.symbol] = (item.at, item.kind)
            return
        if isinstance(item, Candle):
            self._last_seen[("candle", item.market)] = item.received_at
        else:
            kind = type(item).__name__
            self._last_seen[(kind, item.symbol)] = item.received_at

    def status(self, symbol: str, now: datetime, *, required: tuple[str, ...] = ("BookTicker",)) -> tuple[HealthStatus, str]:
        if not self._connected:
            return (HealthStatus.UNAVAILABLE, "disconnected") if self._last_seen else (HealthStatus.UNKNOWN, "never_connected")
        for kind in required:
            seen = self._last_seen.get((kind, symbol))
            if seen is None:
                return HealthStatus.UNKNOWN, f"no_{kind}_yet"
            if now - seen > self._stale_after:
                return HealthStatus.DEGRADED, f"{kind}_stale_{(now - seen).total_seconds():.0f}s"
        for key in (symbol, "*"):
            q = self._last_blocking_quality.get(key)
            if q is not None and now - q[0] < self._quality_hold:
                return HealthStatus.DEGRADED, f"quality_{q[1]}"
        return HealthStatus.HEALTHY, "ok"
