"""Binance USDⓈ-M futures REST client (signed + public), stdlib only.

Endpoint paths were checked against Binance's official connector
(github.com/binance/binance-futures-connector-python, `um_futures/
account.py` and `market.py`, fetched 2026-09-29): `/fapi/v1/order`
(POST new / GET query / DELETE cancel), `/fapi/v1/openOrders`,
`/fapi/v3/account`, `/fapi/v3/balance`, `/fapi/v3/positionRisk`,
`/fapi/v1/userTrades`, `/fapi/v1/leverageBracket`,
`/fapi/v1/commissionRate`, `/fapi/v1/exchangeInfo`, `/fapi/v1/depth`;
signing = HMAC-SHA256 of the query string (with `timestamp`) sent as
`signature`, API key in the `X-MBX-APIKEY` header.

Response FIELD names in the parsers below are from Binance's API docs as
known to this session and could not be re-verified here (sandbox egress
blocks binance.com; GitHub Actions gets HTTP 451, ADR-0012). Every
parser is strict: a missing field raises instead of defaulting.

Credentials come only from the environment (`BINANCE_API_KEY`,
`BINANCE_API_SECRET`); they are never logged, never put in an exception
message, and never written to any file by this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from cointrader.data.orderbook import DepthSnapshot
from cointrader.execution.models import AccountSnapshot, OrderIntent, OrderState, OrderStatus, PositionSnapshot
from cointrader.risk.engine import SymbolFilters
from cointrader.risk.leverage import MarginTier

BASE_URL = "https://fapi.binance.com"
SOURCE = "binance_futures_rest"

# (method, url, headers, body) -> (http_status, body_bytes)
Transport = Callable[[str, str, dict, Optional[bytes]], tuple[int, bytes]]


def _urllib_transport(method: str, url: str, headers: dict, body: Optional[bytes]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConnectionError(f"{method} {urllib.parse.urlsplit(url).path}: {type(exc).__name__}") from exc


class BinanceApiError(RuntimeError):
    def __init__(self, http_status: int, code: Optional[int], message: str) -> None:
        super().__init__(f"HTTP {http_status} code={code}: {message}")
        self.http_status = http_status
        self.code = code


class MissingCredentials(RuntimeError):
    pass


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str

    @classmethod
    def from_env(cls) -> "Credentials":
        key, secret = os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET")
        if not key or not secret:
            raise MissingCredentials("BINANCE_API_KEY / BINANCE_API_SECRET are not set")
        return cls(key, secret)

    def __repr__(self) -> str:  # never print secrets
        return "Credentials(<redacted>)"


def sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


_STATES = {"NEW": OrderState.NEW, "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED, "FILLED": OrderState.FILLED,
           "CANCELED": OrderState.CANCELED, "REJECTED": OrderState.REJECTED, "EXPIRED": OrderState.EXPIRED,
           "EXPIRED_IN_MATCH": OrderState.EXPIRED}
ORDER_NOT_FOUND = -2013  # "Order does not exist."


def parse_order(d: dict) -> OrderStatus:
    state = _STATES.get(d["status"])
    if state is None:
        return OrderStatus(d["clientOrderId"], OrderState.UNKNOWN, detail=f"unmapped status {d['status']!r}")
    filled = float(d["executedQty"])
    avg = float(d["avgPrice"]) if filled > 0 else None
    return OrderStatus(d["clientOrderId"], state, filled, avg, str(d["orderId"]), d["status"])


def parse_exchange_info(d: dict) -> dict[str, SymbolFilters]:
    out = {}
    for s in d["symbols"]:
        if s.get("status") != "TRADING":
            continue
        f = {x["filterType"]: x for x in s["filters"]}
        out[s["symbol"]] = SymbolFilters(
            s["symbol"], float(f["PRICE_FILTER"]["tickSize"]), float(f["LOT_SIZE"]["stepSize"]),
            float(f["LOT_SIZE"]["minQty"]), float(f["MIN_NOTIONAL"]["notional"]),
        )
    return out


def parse_leverage_brackets(rows: list, symbol: str) -> list[MarginTier]:
    for r in rows:
        if r["symbol"] == symbol:
            tiers = []
            brackets = sorted(r["brackets"], key=lambda b: b["notionalFloor"])
            for i, b in enumerate(brackets):
                cap = None if i == len(brackets) - 1 else float(b["notionalCap"])
                tiers.append(MarginTier(float(b["notionalFloor"]), cap, float(b["maintMarginRatio"]), float(b["cum"])))
            return tiers
    raise KeyError(f"no leverage brackets for {symbol}")


def parse_positions(rows: list) -> list[PositionSnapshot]:
    out = []
    for r in rows:
        qty = float(r["positionAmt"])
        out.append(PositionSnapshot(r["symbol"], qty, float(r["entryPrice"]) if qty else None))
    return out


def parse_account(d: dict, at: datetime) -> AccountSnapshot:
    return AccountSnapshot(float(d["totalWalletBalance"]), float(d["totalMarginBalance"]),
                           float(d["availableBalance"]), at)


def parse_depth(d: dict, symbol: str, at: datetime) -> DepthSnapshot:
    return DepthSnapshot(symbol, int(d["lastUpdateId"]), tuple((float(p), float(q)) for p, q in d["bids"]),
                         tuple((float(p), float(q)) for p, q in d["asks"]), at)


def _fmt(x: float) -> str:
    return format(x, ".12f").rstrip("0").rstrip(".")


def order_params(intent: OrderIntent) -> dict:
    p = {"symbol": intent.symbol, "side": intent.side, "type": intent.order_type, "quantity": _fmt(intent.quantity),
         "newClientOrderId": intent.client_order_id, "newOrderRespType": "RESULT"}
    if intent.position_side != "BOTH":
        p["positionSide"] = intent.position_side
    elif intent.reduce_only:
        p["reduceOnly"] = "true"
    if intent.order_type == "LIMIT":
        p["price"] = _fmt(intent.price)
        p["timeInForce"] = "GTC"
    if intent.order_type in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
        p["stopPrice"] = _fmt(intent.stop_price)
        p["workingType"] = "MARK_PRICE"
    return p


class BinanceFuturesClient:
    def __init__(self, credentials: Optional[Credentials] = None, *, transport: Transport = _urllib_transport,
                 base_url: str = BASE_URL, recv_window_ms: int = 5000,
                 clock_ms: Callable[[], int] = lambda: int(time.time() * 1000)) -> None:
        self._cred = credentials
        self._transport = transport
        self._base = base_url
        self._recv = recv_window_ms
        self._clock = clock_ms

    def _request(self, method: str, path: str, params: Optional[dict] = None, *, signed: bool = False):
        params = dict(params or {})
        headers = {"Accept": "application/json"}
        if signed:
            if self._cred is None:
                raise MissingCredentials("signed endpoint needs credentials")
            params["timestamp"] = self._clock()
            params["recvWindow"] = self._recv
            query = urllib.parse.urlencode(params)
            query += "&signature=" + sign(query, self._cred.api_secret)
            headers["X-MBX-APIKEY"] = self._cred.api_key
        else:
            query = urllib.parse.urlencode(params)
        url = f"{self._base}{path}" + (f"?{query}" if query else "")
        status, body = self._transport(method, url, headers, None)
        try:
            data = json.loads(body) if body else None
        except json.JSONDecodeError:
            data = None
        if status >= 400 or data is None:
            code = data.get("code") if isinstance(data, dict) else None
            msg = data.get("msg", "") if isinstance(data, dict) else "non-JSON response"
            raise BinanceApiError(status, code, msg)
        return data

    # public
    def exchange_filters(self) -> dict[str, SymbolFilters]:
        return parse_exchange_info(self._request("GET", "/fapi/v1/exchangeInfo"))

    def depth_snapshot(self, symbol: str, limit: int = 1000) -> DepthSnapshot:
        return parse_depth(self._request("GET", "/fapi/v1/depth", {"symbol": symbol, "limit": limit}), symbol,
                           datetime.now(timezone.utc))

    def ping(self) -> bool:
        self._request("GET", "/fapi/v1/ping")
        return True

    # signed, read-only
    def account(self) -> AccountSnapshot:
        return parse_account(self._request("GET", "/fapi/v3/account", signed=True), datetime.now(timezone.utc))

    def positions(self) -> list[PositionSnapshot]:
        return parse_positions(self._request("GET", "/fapi/v3/positionRisk", signed=True))

    def open_orders(self, symbol: Optional[str] = None) -> list[OrderStatus]:
        rows = self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol} if symbol else {}, signed=True)
        return [parse_order(r) for r in rows]

    def query_order(self, symbol: str, client_order_id: str) -> OrderStatus:
        try:
            return parse_order(self._request("GET", "/fapi/v1/order",
                                             {"symbol": symbol, "origClientOrderId": client_order_id}, signed=True))
        except BinanceApiError as exc:
            if exc.code == ORDER_NOT_FOUND:
                return OrderStatus(client_order_id, OrderState.REJECTED, detail="order does not exist (never placed)")
            raise

    def leverage_brackets(self, symbol: str) -> list[MarginTier]:
        return parse_leverage_brackets(self._request("GET", "/fapi/v1/leverageBracket", {"symbol": symbol}, signed=True),
                                       symbol)

    def symbol_config(self, symbol: Optional[str] = None) -> list[dict]:
        """Per-symbol margin type and leverage (read-only; ADR-0037)."""
        return self._request("GET", "/fapi/v1/symbolConfig", {"symbol": symbol} if symbol else {}, signed=True)

    def commission_rate(self, symbol: str) -> tuple[float, float]:
        d = self._request("GET", "/fapi/v1/commissionRate", {"symbol": symbol}, signed=True)
        return float(d["makerCommissionRate"]), float(d["takerCommissionRate"])

    def user_trades(self, symbol: str, limit: int = 100) -> list[dict]:
        return self._request("GET", "/fapi/v1/userTrades", {"symbol": symbol, "limit": limit}, signed=True)

    # signed, order-changing: reachable ONLY through LiveBroker, which checks the safety gate first
    def _new_order(self, intent: OrderIntent) -> OrderStatus:
        return parse_order(self._request("POST", "/fapi/v1/order", order_params(intent), signed=True))

    def _cancel_order(self, symbol: str, client_order_id: str) -> OrderStatus:
        return parse_order(self._request("DELETE", "/fapi/v1/order",
                                         {"symbol": symbol, "origClientOrderId": client_order_id}, signed=True))
