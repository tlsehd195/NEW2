from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.data.feed import FeedEvent, FeedUnavailable, ResilientCandleFeed
from cointrader.data.models import Candle, OrderBookLevel, OrderBookSnapshot, Timeframe
from cointrader.data.quality import check_candles, find_gaps
from cointrader.data.rate_limit import RequestBudget, parse_upbit_remaining_req
from cointrader.data.upbit_rest import UpbitRestCandles, parse_candles
from tests.helpers import T0, make_candles


class TestModels:
    def test_naive_datetime_rejected(self):
        c = make_candles(1)[0]
        with pytest.raises(ValueError):
            dataclasses.replace(c, open_time=datetime(2024, 1, 1))

    def test_non_finite_price_rejected(self):
        with pytest.raises(ValueError):
            dataclasses.replace(make_candles(1)[0], close=float("nan"))

    def test_crossed_book_rejected(self):
        with pytest.raises(ValueError):
            OrderBookSnapshot("KRW-BTC", T0, (OrderBookLevel(101, 1),), (OrderBookLevel(100, 1),))


class TestQuality:
    def test_clean_series_has_no_issues(self):
        assert check_candles(make_candles(50)) == []

    def test_gap_duplicate_and_invariant_are_reported(self):
        cs = make_candles(10)
        broken = cs[:3] + cs[5:] + [cs[-1]]
        broken[0] = dataclasses.replace(broken[0], high=broken[0].low * 0.5)
        kinds = [i.kind for i in check_candles(broken)]
        assert "gap" in kinds and "duplicate" in kinds and "ohlc_invariant" in kinds

    def test_find_gaps_returns_missing_range(self):
        cs = make_candles(10)
        assert find_gaps(cs[:3] + cs[6:]) == [(cs[3].open_time, cs[6].open_time)]


class TestUpbitRest:
    def _row(self, t: datetime, price: float) -> dict:
        return {"market": "KRW-BTC", "candle_date_time_utc": t.strftime("%Y-%m-%dT%H:%M:%S"),
                "opening_price": price, "high_price": price + 1, "low_price": price - 1,
                "trade_price": price, "candle_acc_trade_volume": 3.0}

    def test_parse_orders_oldest_first_and_stamps_source(self):
        rows = [self._row(T0 + timedelta(hours=1), 2), self._row(T0, 1)]
        cs = parse_candles(rows, "KRW-BTC", Timeframe.HOUR_1, T0 + timedelta(hours=3))
        assert [c.close for c in cs] == [1, 2]
        assert all(c.source == "upbit_rest" and c.open_time.tzinfo for c in cs)

    def test_fetch_pages_backwards_and_excludes_open_bar(self):
        bars = {T0 + timedelta(hours=i): i for i in range(450)}
        urls = []

        def transport(url):
            urls.append(url)
            to = datetime.strptime(url.split("to=")[1].split("&")[0].replace("%3A", ":"),
                                   "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            page = sorted((t for t in bars if t < to), reverse=True)[:200]
            return json.dumps([self._row(t, bars[t]) for t in page]).encode(), {
                "Remaining-Req": "group=candles; min=1000; sec=9"}

        now = T0 + timedelta(hours=449, minutes=30)  # bar 449 still open
        client = UpbitRestCandles(transport=transport, budget=RequestBudget(100), now=lambda: now)
        cs = client.fetch("KRW-BTC", Timeframe.HOUR_1, T0 + timedelta(hours=10), T0 + timedelta(hours=500))
        assert [c.close for c in cs] == list(range(10, 449))
        assert len(urls) == 3


class TestRateLimit:
    def test_parse_header(self):
        r = parse_upbit_remaining_req("group=candles; min=1800; sec=29")
        assert (r.group, r.per_minute, r.per_second) == ("candles", 1800, 29)
        assert parse_upbit_remaining_req(None) is None
        assert parse_upbit_remaining_req("garbage") is None

    def test_budget_waits_when_window_full(self):
        clock = [0.0]
        slept = []

        def sleep(s):
            slept.append(s)
            clock[0] += s

        budget = RequestBudget(2, clock=lambda: clock[0], sleep=sleep)
        for _ in range(3):
            budget.acquire()
        assert slept == [pytest.approx(1.0)]

    def test_budget_respects_exchange_reported_exhaustion(self):
        clock = [0.0]
        slept = []
        budget = RequestBudget(10, clock=lambda: clock[0], sleep=lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s)))
        budget.observe_remaining(parse_upbit_remaining_req("group=candles; min=500; sec=0"))
        budget.acquire()
        assert slept and slept[0] == pytest.approx(1.0)


class _FlakyStream:
    def __init__(self, batches):
        self._batches = list(batches)  # each: list of candles, or an Exception to raise on connect

    def connect(self):
        item = self._batches[0]
        if isinstance(item, Exception):
            self._batches.pop(0)
            raise item

    def closed_candles(self):
        batch = self._batches.pop(0)
        yield from batch
        if self._batches:
            raise ConnectionError("dropped")


class _History:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def fetch(self, market, timeframe, start, end):
        self.calls.append((start, end))
        return [dataclasses.replace(c, source="rest") for c in self.candles if start <= c.open_time < end]


class TestResilientFeed:
    def test_reconnects_and_backfills_gap_with_provenance(self):
        cs = make_candles(10, source="ws")
        stream = _FlakyStream([cs[:3], cs[6:]])
        history = _History(cs)
        feed = ResilientCandleFeed("KRW-BTC", Timeframe.HOUR_1, stream, history, sleep=lambda s: None, now=lambda: T0)
        items = list(feed.run())
        candles = [i for i in items if isinstance(i, Candle)]
        assert [c.open_time for c in candles] == [c.open_time for c in cs]
        assert [c.source for c in candles] == ["ws"] * 3 + ["rest"] * 3 + ["ws"] * 4
        kinds = [i.kind for i in items if isinstance(i, FeedEvent)]
        assert kinds == ["connected", "disconnected", "connected", "backfilled"]

    def test_gives_up_after_consecutive_failures(self):
        stream = _FlakyStream([ConnectionError("x")] * 3 + [[]])
        feed = ResilientCandleFeed("KRW-BTC", Timeframe.HOUR_1, stream, _History([]),
                                   max_consecutive_failures=3, sleep=lambda s: None, now=lambda: T0)
        with pytest.raises(FeedUnavailable):
            list(feed.run())
