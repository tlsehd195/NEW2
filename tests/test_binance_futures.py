from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from cointrader.data.binance_futures import BinanceFuturesCandles, parse_klines
from cointrader.data.models import Timeframe
from cointrader.data.rate_limit import RequestBudget

T0 = datetime(2021, 1, 1, tzinfo=timezone.utc)


def _row(open_time: datetime, close: float, timeframe: Timeframe) -> list:
    close_time = open_time + timeframe.delta - timedelta(milliseconds=1)
    return [
        int(open_time.timestamp() * 1000), str(close - 1), str(close + 1), str(close - 2), str(close),
        "10.0", int(close_time.timestamp() * 1000), "0", 0, "0", "0", "0",
    ]


class TestParseKlines:
    def test_parses_oldest_first_and_stamps_source(self):
        rows = [_row(T0, 100.0, Timeframe.HOUR_1), _row(T0 + timedelta(hours=1), 101.0, Timeframe.HOUR_1)]
        cs = parse_klines(rows, "BTCUSDT", Timeframe.HOUR_1, T0 + timedelta(hours=3))
        assert [c.close for c in cs] == [100.0, 101.0]
        assert all(c.source == "binance_futures_rest" and c.market == "BTCUSDT" and c.open_time.tzinfo for c in cs)

    def test_excludes_bar_not_yet_closed(self):
        rows = [_row(T0, 100.0, Timeframe.HOUR_1)]
        cs = parse_klines(rows, "BTCUSDT", Timeframe.HOUR_1, T0 + timedelta(minutes=30))
        assert cs == []


class TestBinanceFuturesCandles:
    def test_fetch_pages_forward_and_excludes_out_of_range(self):
        bars = {T0 + timedelta(hours=i): 100.0 + i for i in range(2000)}
        urls = []
        now = T0 + timedelta(hours=1999, minutes=30)

        def transport(url):
            urls.append(url)
            start_ms = int(url.split("startTime=")[1].split("&")[0])
            end_ms = int(url.split("endTime=")[1].split("&")[0])
            start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
            end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
            page = sorted(t for t in bars if start <= t < end)[:1500]
            return json.dumps([_row(t, bars[t], Timeframe.HOUR_1) for t in page]).encode()

        client = BinanceFuturesCandles(transport=transport, budget=RequestBudget(100), now=lambda: now)
        cs = client.fetch("BTCUSDT", Timeframe.HOUR_1, T0 + timedelta(hours=10), T0 + timedelta(hours=1990))
        assert [c.open_time for c in cs] == [T0 + timedelta(hours=i) for i in range(10, 1990)]
        assert len(urls) >= 2

    def test_fetch_empty_range_returns_nothing(self):
        client = BinanceFuturesCandles(transport=lambda url: b"[]", budget=RequestBudget(100))
        assert client.fetch("BTCUSDT", Timeframe.HOUR_1, T0, T0) == []

    def test_rejects_unsupported_timeframe(self):
        import pytest
        client = BinanceFuturesCandles(transport=lambda url: b"[]", budget=RequestBudget(100))
        with pytest.raises(ValueError):
            client.fetch("BTCUSDT", "not-a-timeframe", T0, T0 + timedelta(hours=1))
