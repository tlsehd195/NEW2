from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

from cointrader.data.binance_funding import BinanceFundingRateHistory, FundingRateRecord, _parse_records
from cointrader.data.rate_limit import RequestBudget

T0 = datetime(2021, 1, 1, tzinfo=timezone.utc)


def _row(t: datetime, rate: float, mark_price: float | None = 30000.0) -> dict:
    row = {"symbol": "BTCUSDT", "fundingTime": int(t.timestamp() * 1000), "fundingRate": f"{rate:.8f}"}
    row["markPrice"] = "" if mark_price is None else str(mark_price)
    return row


class TestParseRecords:
    def test_parses_and_sorts_oldest_first(self):
        rows = [_row(T0 + timedelta(hours=8), 0.0002), _row(T0, 0.0001)]
        records = _parse_records(rows)
        assert [r.funding_rate for r in records] == [0.0001, 0.0002]
        assert all(r.source == "binance_rest" and r.funding_time.tzinfo for r in records)

    def test_missing_mark_price_becomes_nan_not_estimated(self):
        records = _parse_records([_row(T0, 0.0001, mark_price=None)])
        assert math.isnan(records[0].mark_price)

    def test_naive_funding_time_rejected(self):
        import dataclasses
        import pytest

        rec = _parse_records([_row(T0, 0.0001)])[0]
        with pytest.raises(ValueError):
            dataclasses.replace(rec, funding_time=datetime(2021, 1, 1))


class TestBinanceFundingRateHistory:
    def test_fetch_pages_forward_and_excludes_out_of_range(self):
        settlements = {T0 + timedelta(hours=8 * i): 0.0001 * i for i in range(3000)}
        urls = []

        def transport(url):
            urls.append(url)
            start_ms = int(url.split("startTime=")[1].split("&")[0])
            end_ms = int(url.split("endTime=")[1].split("&")[0])
            start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
            end = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)
            page = sorted(t for t in settlements if start <= t < end)[:1000]
            return json.dumps([_row(t, settlements[t]) for t in page]).encode()

        client = BinanceFundingRateHistory(transport=transport, budget=RequestBudget(100))
        records = client.fetch("BTCUSDT", T0 + timedelta(hours=80), T0 + timedelta(hours=8 * 2500))
        expected_times = sorted(t for t in settlements if T0 + timedelta(hours=80) <= t < T0 + timedelta(hours=8 * 2500))
        assert [r.funding_time for r in records] == expected_times
        assert len(urls) >= 3  # >1000 records needs multiple pages

    def test_fetch_empty_range_returns_nothing(self):
        client = BinanceFundingRateHistory(transport=lambda url: b"[]", budget=RequestBudget(100))
        assert client.fetch("BTCUSDT", T0, T0) == []
