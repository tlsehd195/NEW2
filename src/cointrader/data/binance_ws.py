"""Binance USDⓈ-M futures WebSocket market streams: URLs and parsers.

Sources checked at implementation time (2026-09-29). This sandbox cannot
reach developers.binance.com or fapi.binance.com (egress policy), and
GitHub Actions gets HTTP 451 from fapi (ADR-0012). What was checked
directly is Binance's own official connector
(github.com/binance/binance-futures-connector-python,
`binance/websocket/um_futures/websocket_client.py`):

- base URL `wss://fstream.binance.com`, raw streams under `/ws/<name>`,
  combined streams under `/stream?streams=<a>/<b>` (payload wrapped as
  `{"stream": ..., "data": ...}`),
- stream names `<symbol>@aggTrade`, `<symbol>@bookTicker`,
  `<symbol>@depth@<speed>ms`, `<symbol>@kline_<interval>`,
  `<symbol>@markPrice@<speed>s` (symbol lower-cased).

The payload FIELD names parsed below (`E`, `T`, `p`, `q`, `m`, `U`, `u`,
`pu`, `b`, `a`, `k.x` ...) are from Binance's published stream docs as
known to this session, NOT re-verified against a live message here.
Every parser therefore fails closed: a missing or malformed field raises
`ValueError`, which `parse_message` turns into a `DataQualityEvent`
instead of guessing. The first real run must be checked against a live
message before any paper result is trusted (ADR-0015, "unverified").
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterable, Optional, Union

from cointrader.data.market_events import (
    BookTicker,
    DataQualityEvent,
    DepthDelta,
    MarkPriceUpdate,
    TradeTick,
)
from cointrader.data.models import Candle, Timeframe

BASE_URL = "wss://fstream.binance.com"
SOURCE = "binance_futures_ws"

_INTERVALS = {
    Timeframe.MINUTE_1: "1m", Timeframe.MINUTE_3: "3m", Timeframe.MINUTE_5: "5m",
    Timeframe.MINUTE_15: "15m", Timeframe.HOUR_1: "1h", Timeframe.HOUR_4: "4h", Timeframe.DAY_1: "1d",
}
_BY_INTERVAL = {v: k for k, v in _INTERVALS.items()}


def agg_trade_stream(symbol: str) -> str:
    return f"{symbol.lower()}@aggTrade"


def book_ticker_stream(symbol: str) -> str:
    return f"{symbol.lower()}@bookTicker"


def depth_stream(symbol: str, speed_ms: int = 100) -> str:
    if speed_ms not in (100, 250, 500):
        raise ValueError("depth speed must be 100, 250 or 500 ms")
    return f"{symbol.lower()}@depth@{speed_ms}ms"


def kline_stream(symbol: str, timeframe: Timeframe) -> str:
    return f"{symbol.lower()}@kline_{_INTERVALS[timeframe]}"


def mark_price_stream(symbol: str, speed_s: int = 1) -> str:
    if speed_s not in (1, 3):
        raise ValueError("mark price speed must be 1 or 3 s")
    return f"{symbol.lower()}@markPrice@{speed_s}s"


def combined_stream_url(streams: Iterable[str], base_url: str = BASE_URL) -> str:
    names = list(streams)
    if not names:
        raise ValueError("at least one stream is required")
    if len(set(names)) != len(names):
        raise ValueError("duplicate stream names")
    return f"{base_url}/stream?streams={'/'.join(names)}"


def standard_streams(symbol: str, timeframes: Iterable[Timeframe]) -> list[str]:
    """Everything the paper trader needs for one symbol."""
    return [agg_trade_stream(symbol), book_ticker_stream(symbol), depth_stream(symbol),
            mark_price_stream(symbol)] + [kline_stream(symbol, tf) for tf in timeframes]


def _ms(value) -> datetime:
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"invalid epoch-ms timestamp {value!r}")
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _f(value) -> float:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError(f"invalid numeric field {value!r}")
    return float(value)


def _levels(rows) -> tuple[tuple[float, float], ...]:
    if not isinstance(rows, list):
        raise ValueError("depth levels must be a list")
    return tuple((_f(p), _f(q)) for p, q in rows)


def parse_agg_trade(d: dict, received_at: datetime) -> TradeTick:
    # `m` true = the buyer was the maker, so the aggressor (taker) sold.
    if not isinstance(d.get("m"), bool):
        raise ValueError("aggTrade.m missing")
    return TradeTick(
        symbol=d["s"], price=_f(d["p"]), quantity=_f(d["q"]), aggressor_side="sell" if d["m"] else "buy",
        trade_id=int(d["a"]), exchange_time=_ms(d["T"]), received_at=received_at, source=SOURCE,
    )


def parse_book_ticker(d: dict, received_at: datetime) -> BookTicker:
    return BookTicker(
        symbol=d["s"], bid_price=_f(d["b"]), bid_quantity=_f(d["B"]), ask_price=_f(d["a"]),
        ask_quantity=_f(d["A"]), update_id=int(d["u"]), exchange_time=_ms(d.get("T") or d["E"]),
        received_at=received_at, source=SOURCE,
    )


def parse_depth_update(d: dict, received_at: datetime) -> DepthDelta:
    return DepthDelta(
        symbol=d["s"], first_update_id=int(d["U"]), final_update_id=int(d["u"]),
        previous_final_update_id=int(d["pu"]) if "pu" in d else None,
        bids=_levels(d["b"]), asks=_levels(d["a"]), exchange_time=_ms(d.get("T") or d["E"]),
        received_at=received_at, source=SOURCE,
    )


def parse_mark_price(d: dict, received_at: datetime) -> MarkPriceUpdate:
    rate = d.get("r")
    nft = d.get("T")
    return MarkPriceUpdate(
        symbol=d["s"], mark_price=_f(d["p"]), index_price=_f(d["i"]) if d.get("i") not in (None, "") else None,
        funding_rate=_f(rate) if rate not in (None, "") else None,
        next_funding_time=_ms(nft) if isinstance(nft, int) and nft > 0 else None,
        exchange_time=_ms(d["E"]), received_at=received_at, source=SOURCE,
    )


def parse_kline(d: dict, received_at: datetime) -> Optional[Candle]:
    """A closed kline (`k.x` true) becomes a `Candle`; a still-forming one
    returns None -- it must never reach a strategy (no look-ahead)."""
    k = d["k"]
    if not isinstance(k.get("x"), bool):
        raise ValueError("kline.x missing")
    if not k["x"]:
        return None
    timeframe = _BY_INTERVAL.get(k["i"])
    if timeframe is None:
        raise ValueError(f"unsupported kline interval {k['i']!r}")
    return Candle(
        market=k["s"], timeframe=timeframe, open_time=_ms(k["t"]), open=_f(k["o"]), high=_f(k["h"]),
        low=_f(k["l"]), close=_f(k["c"]), volume=_f(k["v"]), source=SOURCE, received_at=received_at,
    )


_PARSERS = {
    "aggTrade": parse_agg_trade,
    "bookTicker": parse_book_ticker,
    "depthUpdate": parse_depth_update,
    "markPriceUpdate": parse_mark_price,
    "kline": parse_kline,
}

ParsedItem = Union[TradeTick, BookTicker, DepthDelta, MarkPriceUpdate, Candle, DataQualityEvent, None]


def parse_message(text: str, received_at: datetime) -> ParsedItem:
    """One raw frame -> one normalized item. None = a message that is
    valid but carries nothing to act on (an open kline, a subscription
    ack). Anything malformed becomes a DataQualityEvent, never dropped."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return DataQualityEvent("malformed_message", "?", received_at, f"invalid JSON: {exc}", SOURCE)
    data = obj.get("data", obj) if isinstance(obj, dict) else None
    if not isinstance(data, dict):
        return DataQualityEvent("malformed_message", "?", received_at, "payload is not an object", SOURCE)
    if "result" in data and "id" in data:
        return None  # subscribe/unsubscribe acknowledgement
    event_type = data.get("e")
    # bookTicker payloads carry e="bookTicker" on futures; tolerate its absence.
    if event_type is None and {"b", "B", "a", "A", "u"} <= data.keys():
        event_type = "bookTicker"
    parser = _PARSERS.get(event_type)
    symbol = str(data.get("s", "?"))
    if parser is None:
        return DataQualityEvent("unknown_event", symbol, received_at, f"unhandled event type {event_type!r}",
                                SOURCE, blocks_trading=False)
    try:
        return parser(data, received_at)
    except (KeyError, TypeError, ValueError) as exc:
        return DataQualityEvent("malformed_message", symbol, received_at,
                                f"{event_type}: {type(exc).__name__}: {exc}", SOURCE)
