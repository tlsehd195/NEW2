"""Binance USDT-M futures historical funding rate (public, no API key).

ADR-0004/ADR-0005 deferred the "Crypto Carry" paper (Management Science)
strategy because this project had no historical funding-rate data
source -- `backtest/funding.py` refuses to estimate a rate rather than
guess one. That blocker was specifically that *this sandbox* cannot
reach exchange APIs; GitHub Actions runners can (as already proven by
`UpbitRestCandles` running real studies there), and Binance's funding
rate history endpoint (`GET /fapi/v1/fundingRate`) is public -- no key,
no signature, same trust level as Upbit's public REST candles.

Every record carries `source="binance_rest"` for the same provenance
reason every `Candle` does (CLAUDE.md rule 5).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from cointrader._time import require_aware
from cointrader.data.rate_limit import RequestBudget

BASE_URL = "https://fapi.binance.com/fapi/v1"
SOURCE = "binance_rest"
MAX_LIMIT = 1000  # Binance's per-request cap for this endpoint

# Binance documents a 2400-weight-per-minute budget shared across all
# endpoints; this endpoint costs 1 weight/request. 8 req/s is a
# deliberately conservative fixed client-side ceiling (no
# X-MBX-USED-WEIGHT-1M parsing yet -- a future improvement, not a
# silent assumption: this budget alone is what keeps requests spaced,
# same fail-closed posture as Upbit's adapter before its header parser
# was added).
DEFAULT_BUDGET_PER_SECOND = 8

Transport = Callable[[str], bytes]


def _urllib_transport(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.read()


@dataclass(frozen=True)
class FundingRateRecord:
    symbol: str
    funding_time: datetime  # settlement instant, tz-aware UTC
    funding_rate: float  # positive = longs pay shorts
    mark_price: float  # NaN when Binance's response omits it (never estimated)
    source: str = SOURCE

    def __post_init__(self) -> None:
        require_aware("FundingRateRecord.funding_time", self.funding_time)


def _parse_records(payload: list[dict]) -> list[FundingRateRecord]:
    records = []
    for row in payload:
        mark_price_raw = row.get("markPrice", "")
        mark_price = float(mark_price_raw) if mark_price_raw not in ("", None) else float("nan")
        records.append(FundingRateRecord(
            symbol=row["symbol"],
            funding_time=datetime.fromtimestamp(row["fundingTime"] / 1000, tz=timezone.utc),
            funding_rate=float(row["fundingRate"]),
            mark_price=mark_price,
        ))
    records.sort(key=lambda r: r.funding_time)
    return records


class BinanceFundingRateHistory:
    """`fetch` returns closed settlements with `start <= funding_time <
    end`, oldest first, paging forward from `start` (unlike Upbit's
    candle adapter, which pages backward from `end`) since this endpoint
    is naturally ordered ascending by `startTime`."""

    def __init__(
        self,
        *,
        transport: Transport = _urllib_transport,
        budget: Optional[RequestBudget] = None,
    ) -> None:
        self._transport = transport
        self._budget = budget or RequestBudget(DEFAULT_BUDGET_PER_SECOND)

    def _get_page(self, symbol: str, start: datetime, end: datetime, limit: int) -> list[FundingRateRecord]:
        query = urllib.parse.urlencode({
            "symbol": symbol,
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
            "limit": limit,
        })
        self._budget.acquire()
        body = self._transport(f"{BASE_URL}/fundingRate?{query}")
        return _parse_records(json.loads(body))

    def fetch(self, symbol: str, start: datetime, end: datetime) -> list[FundingRateRecord]:
        require_aware("start", start)
        require_aware("end", end)
        if end <= start:
            return []
        by_time: dict[datetime, FundingRateRecord] = {}
        cursor = start
        while cursor < end:
            page = self._get_page(symbol, cursor, end, MAX_LIMIT)
            if not page:
                break
            for r in page:
                if start <= r.funding_time < end:
                    by_time[r.funding_time] = r
            latest = page[-1].funding_time
            if latest < cursor:
                break  # no progress; never loop forever on a misbehaving response
            cursor = latest + timedelta(milliseconds=1)
            if len(page) < MAX_LIMIT:
                break  # short page: this was the last one
        return [r for _, r in sorted(by_time.items())]
