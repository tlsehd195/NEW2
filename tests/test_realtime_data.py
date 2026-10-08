from __future__ import annotations

import json
import struct
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.data import websocket as ws
from cointrader.data.binance_ws import (
    combined_stream_url,
    kline_stream,
    parse_message,
    routed_stream_urls,
    standard_streams,
    stream_path,
)
from cointrader.data.feed import FeedEvent, FeedUnavailable
from cointrader.data.market_events import BookTicker, DataQualityEvent, DepthDelta, MarkPriceUpdate, TradeTick
from cointrader.data.models import Candle, Timeframe
from cointrader.data.orderbook import DepthSnapshot, LocalOrderBook
from cointrader.data.quality_gate import QualityLimits, compare_sources, evaluate_data_quality
from cointrader.data.realtime import FeedHealthMonitor, FeedLimits, MultiMessageSource, ResilientEventFeed
from cointrader.live.config import HealthStatus
from tests.helpers import make_candles

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
SLOW = FeedLimits(stale_after=timedelta(hours=1))


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------- websocket --
class FakeSocket:
    def __init__(self, incoming: bytes) -> None:
        self.incoming = incoming
        self.sent = b""
        self.closed = False

    def recv(self, n: int) -> bytes:
        out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def close(self) -> None:
        self.closed = True


def server_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    head = bytes([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    else:
        head += bytes([126]) + struct.pack("!H", n)
    return head + payload


def test_handshake_verifies_accept_key_and_reads_frames():
    key_bytes = b"\x01" * 16
    import base64
    key = base64.b64encode(key_bytes).decode()
    response = (f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {ws.expected_accept(key)}\r\n\r\n").encode()
    payload = b'{"hello": 1}'
    sock = FakeSocket(response + server_frame(ws.OP_TEXT, payload[:5], fin=False)
                      + server_frame(ws.OP_CONTINUATION, payload[5:]) + server_frame(ws.OP_PING, b"hb"))
    conn = ws.handshake(sock, host="h", port=443, path="/ws", random_bytes=lambda n: b"\x01" * n)
    assert b"Sec-WebSocket-Key: " + key.encode() in sock.sent
    assert conn.recv().text == '{"hello": 1}'
    ping = conn.recv()
    assert ping.opcode == ws.OP_PING and ping.payload == b"hb"


def test_handshake_rejects_bad_accept_key():
    sock = FakeSocket(b"HTTP/1.1 101 OK\r\nSec-WebSocket-Accept: nope\r\n\r\n")
    with pytest.raises(ws.WebSocketProtocolError):
        ws.handshake(sock, host="h", port=443, path="/", random_bytes=lambda n: b"\x00" * n)


def test_client_frames_are_masked_and_close_frame_raises():
    sock = FakeSocket(server_frame(ws.OP_CLOSE, struct.pack("!H", 1001) + b"bye"))
    conn = ws.WebSocketConnection(sock, random_bytes=lambda n: b"\x0f" * n)
    conn.send_text("hi")
    assert sock.sent[1] & 0x80  # mask bit
    with pytest.raises(ws.WebSocketClosed) as exc:
        conn.recv()
    assert exc.value.code == 1001


def test_masked_server_frame_is_a_protocol_error():
    sock = FakeSocket(bytes([0x81, 0x80 | 1]) + b"\x00\x00\x00\x00a")
    with pytest.raises(ws.WebSocketProtocolError):
        ws.WebSocketConnection(sock).recv()


# ----------------------------------------------------------------- parsers --
def agg(tid: int, at: datetime, *, price="100.5", qty="2", maker=True) -> str:
    return json.dumps({"stream": "btcusdt@aggTrade", "data": {
        "e": "aggTrade", "E": ms(at), "s": "BTCUSDT", "a": tid, "p": price, "q": qty, "f": 1, "l": 2,
        "T": ms(at), "m": maker}})


def book(uid: int, at: datetime, bid="100", ask="100.1") -> str:
    return json.dumps({"stream": "btcusdt@bookTicker", "data": {
        "e": "bookTicker", "u": uid, "E": ms(at), "T": ms(at), "s": "BTCUSDT", "b": bid, "B": "3", "a": ask, "A": "4"}})


def kline(open_time: datetime, closed: bool, close="101") -> str:
    return json.dumps({"stream": "btcusdt@kline_1m", "data": {"e": "kline", "E": ms(open_time), "s": "BTCUSDT", "k": {
        "t": ms(open_time), "T": ms(open_time + timedelta(minutes=1)) - 1, "s": "BTCUSDT", "i": "1m",
        "o": "100", "h": "102", "l": "99", "c": close, "v": "10", "x": closed}}})


def test_stream_names_and_url():
    assert kline_stream("BTCUSDT", Timeframe.MINUTE_5) == "btcusdt@kline_5m"
    url = combined_stream_url(standard_streams("BTCUSDT", [Timeframe.MINUTE_1]))
    assert url.startswith("wss://fstream.binance.com/stream?streams=btcusdt@aggTrade/")
    with pytest.raises(ValueError):
        combined_stream_url(["a", "a"])


def test_streams_are_split_by_binance_route():
    """Binance routes USDⓈ-M streams by tier (ADR-0045): an unrouted URL only gets the public tier, which
    left a paper run with order-book data but no klines and no decisions."""
    names = standard_streams("BTCUSDT", [Timeframe.MINUTE_15]) + standard_streams("ETHUSDT", [Timeframe.MINUTE_15])
    public, market = routed_stream_urls(names)
    assert public.startswith("wss://fstream.binance.com/public/stream?streams=")
    assert market.startswith("wss://fstream.binance.com/market/stream?streams=")
    assert "btcusdt@bookTicker" in public and "ethusdt@depth@100ms" in public
    assert all(k not in public for k in ("aggTrade", "markPrice", "kline"))
    for k in ("btcusdt@aggTrade", "btcusdt@markPrice@1s", "btcusdt@kline_15m", "ethusdt@kline_15m"):
        assert k in market
    assert "bookTicker" not in market and "depth" not in market
    assert [stream_path(n) for n in names if "kline" in n] == ["market", "market"]
    assert len(routed_stream_urls(["btcusdt@aggTrade"])) == 1  # a tier without streams gets no connection
    with pytest.raises(ValueError):
        stream_path("btcusdt@somethingNew")


class ListSource:
    def __init__(self, items=(), error=None, block=False) -> None:
        self.items, self.error, self.block = list(items), error, block
        self.connected = self.closed = False

    def connect(self) -> None:
        self.connected = True

    def messages(self):
        yield from self.items
        if self.error is not None:
            raise self.error
        if self.block:
            import time
            while not self.closed:
                time.sleep(0.01)
            raise ConnectionError("closed")

    def close(self) -> None:
        self.closed = True


def test_multi_source_merges_connections_and_reports_a_drop():
    a = ListSource(["a1", "a2"], block=True)
    b = ListSource(["b1"], error=ConnectionError("dropped"))
    m = MultiMessageSource([a, b], read_timeout=2.0)
    m.connect()
    got = []
    with pytest.raises(ConnectionError, match="dropped"):
        for text in m.messages():
            got.append(text)
    m.close()
    assert "b1" in got and set(got) <= {"a1", "a2", "b1"}
    assert a.closed and b.closed


def test_multi_source_times_out_when_every_connection_is_silent():
    a, b = ListSource(block=True), ListSource(block=True)
    m = MultiMessageSource([a, b], read_timeout=0.1)
    m.connect()
    with pytest.raises(TimeoutError):
        next(m.messages())
    m.close()


def test_multi_source_connect_failure_closes_the_ones_already_open():
    class Refuses(ListSource):
        def connect(self) -> None:
            raise ConnectionError("refused")

    a = ListSource()
    m = MultiMessageSource([a, Refuses()])
    with pytest.raises(ConnectionError):
        m.connect()
    assert a.connected and a.closed


def test_parse_agg_trade_maps_aggressor_and_keeps_both_clocks():
    rx = T0 + timedelta(milliseconds=50)
    t = parse_message(agg(7, T0, maker=True), rx)
    assert isinstance(t, TradeTick) and t.aggressor_side == "sell" and t.trade_id == 7
    assert t.exchange_time == T0 and t.received_at == rx and t.source == "binance_futures_ws"
    assert parse_message(agg(8, T0, maker=False), rx).aggressor_side == "buy"


def test_open_kline_is_never_emitted_closed_one_is():
    rx = T0 + timedelta(minutes=1, seconds=1)
    assert parse_message(kline(T0, closed=False), rx) is None
    c = parse_message(kline(T0, closed=True), rx)
    assert isinstance(c, Candle) and c.timeframe is Timeframe.MINUTE_1 and c.close == 101


def test_malformed_messages_become_quality_events():
    rx = T0
    assert parse_message("not json", rx).kind == "malformed_message"
    bad_price = agg(1, T0, price="nan")
    assert isinstance(parse_message(bad_price, rx), DataQualityEvent)
    neg = agg(1, T0, qty="-1")
    assert parse_message(neg, rx).kind == "malformed_message"
    crossed = book(1, T0, bid="101", ask="100")
    assert parse_message(crossed, rx).kind == "malformed_message"
    missing = json.dumps({"data": {"e": "aggTrade", "s": "BTCUSDT"}})
    assert parse_message(missing, rx).kind == "malformed_message"
    assert parse_message(json.dumps({"result": None, "id": 1}), rx) is None


def test_depth_and_mark_price_parse():
    d = parse_message(json.dumps({"data": {"e": "depthUpdate", "E": ms(T0), "T": ms(T0), "s": "BTCUSDT",
                                           "U": 10, "u": 12, "pu": 9, "b": [["100", "1"]], "a": [["101", "0"]]}}), T0)
    assert isinstance(d, DepthDelta) and d.previous_final_update_id == 9 and d.asks == ((101.0, 0.0),)
    m = parse_message(json.dumps({"data": {"e": "markPriceUpdate", "E": ms(T0), "s": "BTCUSDT", "p": "100",
                                           "i": "99.9", "P": "100", "r": "0.0001", "T": ms(T0 + timedelta(hours=8))}}), T0)
    assert isinstance(m, MarkPriceUpdate) and m.funding_rate == 0.0001


# -------------------------------------------------------------------- feed --
class ScriptedSource:
    """Each connection plays one script: a list of (clock_time, message)
    or an exception to raise."""

    def __init__(self, scripts, clock) -> None:
        self.scripts = list(scripts)
        self.clock = clock
        self.connects = 0

    def connect(self) -> None:
        self.connects += 1
        if not self.scripts:
            raise ConnectionError("no more scripts")
        self.current = self.scripts.pop(0)
        if isinstance(self.current, Exception):
            raise self.current

    def messages(self):
        for step in self.current:
            if isinstance(step, Exception):
                raise step
            at, text = step
            self.clock.now = at
            yield text

    def close(self) -> None:
        pass


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class FakeHistory:
    def __init__(self, candles):
        self.candles = candles
        self.calls = []

    def fetch(self, market, timeframe, start, end):
        self.calls.append((start, end))
        return [c for c in self.candles if start <= c.open_time < end]


def rest_candle(open_time):
    return Candle("BTCUSDT", Timeframe.MINUTE_1, open_time, 100, 102, 99, 101, 10, "binance_futures_rest",
                  open_time + timedelta(minutes=2))


def test_feed_dedupes_detects_gaps_and_backfills_klines():
    clock = Clock(T0)
    t1 = T0 + timedelta(minutes=1, seconds=1)
    t4 = T0 + timedelta(minutes=4, seconds=1)
    source = ScriptedSource([[
        (T0, agg(1, T0)), (T0, agg(1, T0)), (T0, agg(4, T0)),  # duplicate, then a gap of 2 ids
        (t1, kline(T0, closed=True)),
        (t4, kline(T0 + timedelta(minutes=3), closed=True)),  # minutes 1 and 2 missing
    ]], clock)
    history = FakeHistory([rest_candle(T0 + timedelta(minutes=m)) for m in (1, 2)])
    items = list(ResilientEventFeed(source, history=history, now=clock, sleep=lambda s: None, limits=SLOW).run())
    trades = [i for i in items if isinstance(i, TradeTick)]
    assert [t.trade_id for t in trades] == [1, 4]
    assert any(isinstance(i, DataQualityEvent) and i.kind == "trade_sequence_gap" for i in items)
    candles = [i for i in items if isinstance(i, Candle)]
    assert [c.open_time.minute for c in candles] == [0, 1, 2, 3]
    assert [c.source for c in candles] == ["binance_futures_ws", "binance_futures_rest", "binance_futures_rest",
                                          "binance_futures_ws"]
    assert any(isinstance(i, FeedEvent) and i.kind == "backfilled" for i in items)


def test_feed_accepts_a_closed_kline_seen_slightly_early_by_a_slow_local_clock():
    """A local clock 1s behind the exchange saw every closed bar as "received before it closed"."""
    close = T0 + timedelta(minutes=1)
    clock = Clock(T0)
    source = ScriptedSource([[
        (close - timedelta(seconds=1), kline(T0, closed=True)),  # within the 5s skew allowance
        (close + timedelta(minutes=1) - timedelta(seconds=30), kline(T0 + timedelta(minutes=1), closed=True)),  # not
    ]], clock)
    items = list(ResilientEventFeed(source, history=FakeHistory([]), now=clock, sleep=lambda s: None, limits=SLOW).run())
    assert [c.open_time for c in items if isinstance(c, Candle)] == [T0]
    assert [i.kind for i in items if isinstance(i, DataQualityEvent)] == ["unclosed_candle"]


def test_feed_reports_incomplete_backfill_as_quality_event():
    clock = Clock(T0)
    source = ScriptedSource([[
        (T0 + timedelta(minutes=1, seconds=1), kline(T0, closed=True)),
        (T0 + timedelta(minutes=4, seconds=1), kline(T0 + timedelta(minutes=3), closed=True)),
    ]], clock)
    items = list(ResilientEventFeed(source, history=FakeHistory([]), now=clock, sleep=lambda s: None, limits=SLOW).run())
    assert any(isinstance(i, DataQualityEvent) and i.kind == "missing_candle" for i in items)
    assert any(isinstance(i, FeedEvent) and i.kind == "backfill_incomplete" for i in items)


def test_feed_rejects_backwards_and_skewed_timestamps():
    clock = Clock(T0)
    source = ScriptedSource([[
        (T0, book(1, T0)),
        (T0, book(2, T0 - timedelta(seconds=1))),  # exchange time went back
        (T0, book(3, T0 + timedelta(seconds=30))),  # from the future
        (T0 + timedelta(seconds=60), book(4, T0 + timedelta(seconds=1))),  # 59s late
    ]], clock)
    items = list(ResilientEventFeed(source, now=clock, sleep=lambda s: None, limits=SLOW).run())
    kinds = [i.kind for i in items if isinstance(i, DataQualityEvent)]
    assert kinds == ["timestamp_out_of_order", "clock_skew", "latency"]
    assert [i.update_id for i in items if isinstance(i, BookTicker)] == [1]


def test_feed_reconnects_with_backoff_then_gives_up():
    clock = Clock(T0)
    sleeps = []
    source = ScriptedSource([[(T0, agg(1, T0)), ConnectionError("reset")], ConnectionError("refused"),
                             ConnectionError("refused")], clock)
    feed = ResilientEventFeed(source, now=clock, sleep=sleeps.append, max_consecutive_failures=3)
    items = []
    with pytest.raises(FeedUnavailable):
        for item in feed.run():
            items.append(item)
    assert sleeps == [1.0, 2.0]
    assert sum(1 for i in items if isinstance(i, FeedEvent) and i.kind == "disconnected") == 3


def test_feed_treats_silence_as_stale_and_reconnects():
    clock = Clock(T0)
    source = ScriptedSource([[(T0, agg(1, T0)), (T0 + timedelta(seconds=45), agg(2, T0 + timedelta(seconds=45)))],
                             [(T0 + timedelta(seconds=46), agg(3, T0 + timedelta(seconds=46)))]], clock)
    feed = ResilientEventFeed(source, now=clock, sleep=lambda s: None, limits=FeedLimits(stale_after=timedelta(seconds=30)))
    items = list(feed.run())
    assert any(isinstance(i, DataQualityEvent) and i.kind == "stale_feed" for i in items)
    assert [i.trade_id for i in items if isinstance(i, TradeTick)] == [1, 3]
    assert source.connects == 2


def test_dedupe_memory_is_bounded():
    clock = Clock(T0)
    msgs = [(T0, agg(i, T0)) for i in range(1, 2001)]
    feed = ResilientEventFeed(ScriptedSource([msgs], clock), now=clock, sleep=lambda s: None,
                              limits=FeedLimits(dedupe_window=100))
    list(feed.run())
    assert len(feed._trade_ids["BTCUSDT"]) == 100


def test_health_monitor_states():
    mon = FeedHealthMonitor(stale_after=timedelta(seconds=10))
    assert mon.status("BTCUSDT", T0)[0] is HealthStatus.UNKNOWN
    mon.observe(FeedEvent("connected", T0, ""))
    assert mon.status("BTCUSDT", T0)[0] is HealthStatus.UNKNOWN
    bt = BookTicker("BTCUSDT", 100, 1, 100.1, 1, 1, T0, T0, "x")
    mon.observe(bt)
    assert mon.status("BTCUSDT", T0 + timedelta(seconds=5))[0] is HealthStatus.HEALTHY
    assert mon.status("BTCUSDT", T0 + timedelta(seconds=11))[0] is HealthStatus.DEGRADED
    mon.observe(DataQualityEvent("malformed_message", "BTCUSDT", T0, "", "x"))
    assert mon.status("BTCUSDT", T0 + timedelta(seconds=5))[1] == "quality_malformed_message"
    mon.observe(FeedEvent("disconnected", T0, ""))
    assert mon.status("BTCUSDT", T0)[0] is HealthStatus.UNAVAILABLE


# --------------------------------------------------------------- orderbook --
def delta(U, u, pu, bids=(), asks=()):
    return DepthDelta("BTCUSDT", U, u, pu, tuple(bids), tuple(asks), T0, T0, "x")


def test_orderbook_syncs_from_snapshot_and_buffered_deltas():
    ob = LocalOrderBook("BTCUSDT")
    ob.apply(delta(95, 99, 94, bids=[(99.0, 1.0)]))  # older than snapshot, dropped
    ob.apply(delta(100, 105, 99, bids=[(100.0, 2.0)]))
    events = ob.load_snapshot(DepthSnapshot("BTCUSDT", 102, ((100.0, 1.0), (99.5, 1.0)), ((101.0, 1.0),), T0))
    assert events == [] and ob.synced
    assert ob.apply(delta(106, 110, 105, asks=[(101.0, 0.0), (101.5, 3.0)])) is None
    snap = ob.snapshot(T0)
    assert snap.bids[0].price == 100.0 and snap.bids[0].size == 2.0 and snap.asks[0].price == 101.5


def test_orderbook_sequence_gap_fails_closed():
    ob = LocalOrderBook("BTCUSDT")
    ob.load_snapshot(DepthSnapshot("BTCUSDT", 100, ((100.0, 1.0),), ((101.0, 1.0),), T0))
    assert ob.apply(delta(100, 101, 99)) is None
    ev = ob.apply(delta(103, 104, 102))  # pu 102 != previous u 101
    assert ev.kind == "orderbook_sequence_gap" and not ob.synced
    with pytest.raises(RuntimeError):
        ob.snapshot(T0)


def test_orderbook_first_delta_must_bracket_snapshot():
    ob = LocalOrderBook("BTCUSDT")
    ob.load_snapshot(DepthSnapshot("BTCUSDT", 100, ((100.0, 1.0),), ((101.0, 1.0),), T0))
    assert ob.apply(delta(105, 110, 104)).kind == "orderbook_sequence_gap"


def test_orderbook_memory_bounded():
    ob = LocalOrderBook("BTCUSDT", max_levels=5)
    ob.load_snapshot(DepthSnapshot("BTCUSDT", 1, tuple((100.0 - i, 1.0) for i in range(20)),
                                   tuple((101.0 + i, 1.0) for i in range(20)), T0))
    ob.apply(delta(1, 2, 0))
    assert len(ob.snapshot(T0, depth=50).bids) == 5


# ------------------------------------------------------------ quality gate --
def good_candles(n=30):
    return make_candles(n, start=T0, timeframe=Timeframe.MINUTE_1, market="BTCUSDT")


def test_quality_gate_allows_clean_data():
    cs = good_candles()
    now = cs[-1].close_time + timedelta(seconds=5)
    bt = BookTicker("BTCUSDT", 100, 1, 100.05, 1, 1, now, now, "x")
    d = evaluate_data_quality(now=now, candles=cs, feed_health=HealthStatus.HEALTHY, book=bt)
    assert d.allowed, d.reasons


@pytest.mark.parametrize("mutate,reason", [
    (lambda cs: cs[:10] + cs[11:], "candle_gap"),
    (lambda cs: cs + [cs[-1]], "candle_duplicate"),
    (lambda cs: cs[:-2] + [cs[-1], cs[-2]], "candle_out_of_order"),
])
def test_quality_gate_blocks_bad_series(mutate, reason):
    cs = good_candles()
    now = cs[-1].close_time + timedelta(seconds=5)
    bt = BookTicker("BTCUSDT", 100, 1, 100.05, 1, 1, now, now, "x")
    d = evaluate_data_quality(now=now, candles=mutate(cs), feed_health=HealthStatus.HEALTHY, book=bt)
    assert not d.allowed and any(r.startswith(reason) for r in d.reasons)


def test_quality_gate_blocks_stale_unhealthy_wide_spread_and_events():
    cs = good_candles()
    now = cs[-1].close_time + timedelta(minutes=5)
    wide = BookTicker("BTCUSDT", 100, 1, 101, 1, 1, now, now, "x")
    ev = DataQualityEvent("orderbook_sequence_gap", "BTCUSDT", now, "", "x")
    d = evaluate_data_quality(now=now, candles=cs, feed_health=HealthStatus.DEGRADED, book=wide, recent_events=[ev])
    joined = " ".join(d.reasons)
    for part in ("candles_stale", "feed_degraded", "spread_too_wide", "quality_event_orderbook_sequence_gap"):
        assert part in joined
    assert "book_missing" in evaluate_data_quality(now=now, candles=cs, feed_health=None).reasons


def test_quality_gate_flags_price_spike():
    cs = good_candles()
    c = cs[15]
    cs[15] = Candle(c.market, c.timeframe, c.open_time, c.open, c.close * 2, c.low, c.close * 2, c.volume, c.source,
                    c.received_at)
    now = cs[-1].close_time
    d = evaluate_data_quality(now=now, candles=cs, feed_health=HealthStatus.HEALTHY,
                              limits=QualityLimits(require_book=False))
    assert any(r.startswith("price_spike") for r in d.reasons)


def test_rest_ws_mismatch_detected():
    a = rest_candle(T0)
    b = Candle(a.market, a.timeframe, a.open_time, 100, 102, 99, 103, 10, "binance_futures_ws", a.received_at)
    assert compare_sources(b, a, tolerance=0.001).kind == "rest_ws_mismatch"
    assert compare_sources(a, a, tolerance=0.001) is None
