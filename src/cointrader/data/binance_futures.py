"""Binance USDT-M futures historical klines (public, no API key).

Pairs with `binance_funding.py`: a funding-rate carry backtest needs
both the funding rate series AND the perpetual's own price history
(for mark-to-market and for the strategy's entry/exit decisions). Same
trust level as Upbit's public REST candles -- no key, no signature.

Every candle is stamped `source="binance_futures_rest"` for the same
provenance reason every `Candle` does (CLAUDE.md rule 5). `market` is
the Binance symbol (e.g. "BTCUSDT"), distinct from Upbit's "KRW-BTC"
market strings, so this never collides with spot data.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from cointrader._time import require_aware
from cointrader.data.models import Candle, Timeframe
from cointrader.data.rate_limit import RequestBudget

BASE_URL = "https://fapi.binance.com/fapi/v1"
SOURCE = "binance_futures_rest"
MAX_LIMIT = 1500  # Binance's per-request cap for this endpoint

# Same conservative fixed client-side ceiling as binance_funding.py (see
# its comment); this endpoint costs more weight per request than
# fundingRate, but 8 req/s stays well inside the shared 2400/min budget
# for the request volumes a backtest needs.
DEFAULT_BUDGET_PER_SECOND = 8

_INTERVALS = {
    Timeframe.MINUTE_1: "1m",
    Timeframe.MINUTE_3: "3m",
    Timeframe.MINUTE_5: "5m",
    Timeframe.MINUTE_15: "15m",
    Timeframe.HOUR_1: "1h",
    Timeframe.HOUR_4: "4h",
    Timeframe.DAY_1: "1d",
}

Transport = Callable[[str], bytes]


def _urllib_transport(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read()


def parse_klines(payload: list[list], symbol: str, timeframe: Timeframe, received_at: datetime) -> list[Candle]:
    """Binance returns klines oldest first already; this keeps that order
    and only includes bars whose close time already elapsed (a kline row
    for the still-open current bar is otherwise indistinguishable from a
    closed one in this payload shape)."""
    candles = []
    for row in payload:
        open_time = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc)
        close_time = datetime.fromtimestamp(row[6] / 1000, tz=timezone.utc)
        if close_time > received_at:
            continue
        candles.append(Candle(
            market=symbol,
            timeframe=timeframe,
            open_time=open_time,
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            source=SOURCE,
            received_at=received_at,
            taker_buy_volume=float(row[9]) if len(row) > 9 else None,
        ))
    candles.sort(key=lambda c: c.open_time)
    return candles


class BinanceFuturesCandles:
    def __init__(
        self,
        *,
        transport: Transport = _urllib_transport,
        budget: Optional[RequestBudget] = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._transport = transport
        self._budget = budget or RequestBudget(DEFAULT_BUDGET_PER_SECOND)
        self._now = now

    def _get_page(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime, limit: int) -> list[Candle]:
        if timeframe not in _INTERVALS:
            raise ValueError(f"unsupported timeframe for Binance klines: {timeframe!r}")
        query = urllib.parse.urlencode({
            "symbol": symbol,
            "interval": _INTERVALS[timeframe],
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": limit,
        })
        self._budget.acquire()
        body = self._transport(f"{BASE_URL}/klines?{query}")
        return parse_klines(json.loads(body), symbol, timeframe, self._now())

    def fetch(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> list[Candle]:
        """Closed candles with `start <= open_time < end`, oldest first,
        paging forward from `start` (klines are naturally ascending)."""
        require_aware("start", start)
        require_aware("end", end)
        if end <= start:
            return []
        by_time: dict[datetime, Candle] = {}
        cursor = start
        while cursor < end:
            page = self._get_page(symbol, timeframe, cursor, end, MAX_LIMIT)
            if not page:
                break
            for c in page:
                if start <= c.open_time < end:
                    by_time[c.open_time] = c
            latest = page[-1].open_time
            if latest < cursor:
                break  # no progress; never loop forever on a misbehaving response
            cursor = latest + timeframe.delta
            if len(page) < MAX_LIMIT:
                break  # short page: this was the last one
        return [c for _, c in sorted(by_time.items())]
