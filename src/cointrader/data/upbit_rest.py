"""Upbit public REST candles (no API key needed).

Used for history and for filling gaps the live stream missed. Every
candle is stamped `source="upbit_rest"`.

Upbit only emits a candle for an interval in which at least one trade
happened, so an illiquid market can have legitimate gaps. KRW-BTC and
other major KRW markets trade every minute in practice; for them a gap
still means missing data. `data.quality` reports gaps either way.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

from cointrader._time import require_aware
from cointrader.data.models import Candle, Timeframe
from cointrader.data.rate_limit import RequestBudget, parse_upbit_remaining_req

BASE_URL = "https://api.upbit.com/v1"
SOURCE = "upbit_rest"
MAX_COUNT = 200  # Upbit's per-request candle limit

_PATHS = {
    Timeframe.MINUTE_1: "candles/minutes/1",
    Timeframe.MINUTE_3: "candles/minutes/3",
    Timeframe.MINUTE_5: "candles/minutes/5",
    Timeframe.MINUTE_15: "candles/minutes/15",
    Timeframe.HOUR_1: "candles/minutes/60",
    Timeframe.HOUR_4: "candles/minutes/240",
    Timeframe.DAY_1: "candles/days",
}

# Upbit documents 10 requests/second for the quotation (public) API.
DEFAULT_BUDGET_PER_SECOND = 8

Transport = Callable[[str], tuple[bytes, dict]]


def _urllib_transport(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read(), dict(response.headers)


def parse_candles(payload: list[dict], market: str, timeframe: Timeframe, received_at: datetime) -> list[Candle]:
    """Upbit returns newest first; this returns oldest first."""
    candles = []
    for row in payload:
        open_time = datetime.fromisoformat(row["candle_date_time_utc"]).replace(tzinfo=timezone.utc)
        candles.append(Candle(
            market=row.get("market", market),
            timeframe=timeframe,
            open_time=open_time,
            open=float(row["opening_price"]),
            high=float(row["high_price"]),
            low=float(row["low_price"]),
            close=float(row["trade_price"]),
            volume=float(row["candle_acc_trade_volume"]),
            source=SOURCE,
            received_at=received_at,
        ))
    candles.sort(key=lambda c: c.open_time)
    return candles


class UpbitRestCandles:
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

    def _get_page(self, market: str, timeframe: Timeframe, to: datetime, count: int) -> list[Candle]:
        query = urllib.parse.urlencode({
            "market": market,
            "to": to.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "count": count,
        })
        self._budget.acquire()
        body, headers = self._transport(f"{BASE_URL}/{_PATHS[timeframe]}?{query}")
        self._budget.observe_remaining(parse_upbit_remaining_req(headers.get("Remaining-Req")))
        return parse_candles(json.loads(body), market, timeframe, self._now())

    def fetch(self, market: str, timeframe: Timeframe, start: datetime, end: datetime) -> list[Candle]:
        """Closed candles with `start <= open_time < end`, oldest first.
        Pages backwards from `end` until `start` is covered or Upbit
        returns nothing more."""
        require_aware("start", start)
        require_aware("end", end)
        if end <= start:
            return []
        by_time: dict[datetime, Candle] = {}
        cursor = end
        while cursor > start:
            page = self._get_page(market, timeframe, cursor, MAX_COUNT)
            if not page:
                break
            for c in page:
                if start <= c.open_time < end:
                    by_time[c.open_time] = c
            earliest = page[0].open_time
            if earliest >= cursor:
                break  # no progress; never loop forever on a misbehaving response
            cursor = earliest
        now = self._now()
        return [c for t, c in sorted(by_time.items()) if c.close_time <= now]
