"""Stream-first candle feed with reconnect and REST gap backfill.

The provenance principle is carried over from tlsehd195/NEW-'s REST
fallback chain, but the shape is different: live data comes from an
exchange WebSocket stream; when the stream drops, the feed reconnects
with backoff, and any interval the stream missed is filled from REST
before newer bars are emitted. Every emitted candle keeps the `source`
of whoever actually produced it, and every disconnect/reconnect/backfill
is reported as a `FeedEvent` -- never hidden.

If the stream cannot be re-established after `max_consecutive_failures`
attempts the feed raises `FeedUnavailable`. That is a fail-closed signal
for the kill switch (`live.kill_switch`), not something to retry forever.

For swing timeframes (1h+) a REST-only stream (`RestPollingStream`) is
adequate and is the default until a WebSocket adapter is added.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterator, Optional, Protocol, Union

from cointrader.data.models import Candle, Timeframe


class CandleStream(Protocol):
    def connect(self) -> None: ...

    def closed_candles(self) -> Iterator[Candle]:
        """Yields closed candles in time order; raises `ConnectionError`
        when the connection drops."""
        ...


class CandleHistory(Protocol):
    def fetch(self, market: str, timeframe: Timeframe, start: datetime, end: datetime) -> list[Candle]: ...


class FeedUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class FeedEvent:
    kind: str  # "connected" | "disconnected" | "backfilled" | "backfill_incomplete"
    at: datetime
    detail: str


FeedItem = Union[Candle, FeedEvent]


class ResilientCandleFeed:
    def __init__(
        self,
        market: str,
        timeframe: Timeframe,
        stream: CandleStream,
        history: CandleHistory,
        *,
        last_open_time: Optional[datetime] = None,
        max_consecutive_failures: int = 5,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._market = market
        self._timeframe = timeframe
        self._stream = stream
        self._history = history
        self._last = last_open_time
        self._max_failures = max_consecutive_failures
        self._base_backoff = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._sleep = sleep
        self._now = now

    def _backfill(self, start: datetime, end: datetime) -> Iterator[FeedItem]:
        step = self._timeframe.delta
        filled = [c for c in self._history.fetch(self._market, self._timeframe, start, end)
                  if self._last is None or c.open_time > self._last]
        for c in filled:
            self._last = c.open_time
            yield c
        expected = int((end - start) / step)
        kind = "backfilled" if len(filled) == expected else "backfill_incomplete"
        yield FeedEvent(kind, self._now(), f"{len(filled)}/{expected} bars from REST for [{start.isoformat()}, {end.isoformat()})")

    def run(self) -> Iterator[FeedItem]:
        failures = 0
        while True:
            try:
                self._stream.connect()
                yield FeedEvent("connected", self._now(), f"attempt after {failures} failure(s)")
                for candle in self._stream.closed_candles():
                    failures = 0
                    if self._last is not None and candle.open_time <= self._last:
                        continue  # already emitted (e.g. via backfill)
                    if self._last is not None and candle.open_time - self._last > self._timeframe.delta:
                        yield from self._backfill(self._last + self._timeframe.delta, candle.open_time)
                    self._last = candle.open_time
                    yield candle
                return  # stream ended cleanly
            except ConnectionError as exc:
                failures += 1
                yield FeedEvent("disconnected", self._now(), f"{type(exc).__name__}: {exc}")
                if failures >= self._max_failures:
                    raise FeedUnavailable(
                        f"{self._market} {self._timeframe.value} stream failed {failures} times in a row"
                    ) from exc
                self._sleep(min(self._max_backoff, self._base_backoff * 2 ** (failures - 1)))


class RestPollingStream:
    """A `CandleStream` that polls REST once per closed bar. Enough for
    swing timeframes; scalping needs a real WebSocket stream."""

    def __init__(
        self,
        market: str,
        timeframe: Timeframe,
        history: CandleHistory,
        *,
        start_after: datetime,
        poll_delay_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._market = market
        self._timeframe = timeframe
        self._history = history
        self._cursor = start_after
        self._delay = poll_delay_seconds
        self._sleep = sleep
        self._now = now

    def connect(self) -> None:
        pass

    def closed_candles(self) -> Iterator[Candle]:
        step = self._timeframe.delta
        while True:
            next_close = self._cursor + 2 * step
            wait = (next_close - self._now()).total_seconds() + self._delay
            if wait > 0:
                self._sleep(wait)
            try:
                candles = self._history.fetch(self._market, self._timeframe, self._cursor + step, self._now())
            except OSError as exc:
                raise ConnectionError(str(exc)) from exc
            if not candles:
                self._sleep(self._delay)  # bar not published yet; never hammer the API
                continue
            for c in candles:
                self._cursor = c.open_time
                yield c
