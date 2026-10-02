from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.data.market_events import BookTicker, MarkPriceUpdate, TradeTick
from cointrader.data.models import OrderBookLevel, OrderBookSnapshot
from cointrader.execution.binance_client import (
    BinanceApiError,
    BinanceFuturesClient,
    Credentials,
    MissingCredentials,
    order_params,
    parse_exchange_info,
    parse_leverage_brackets,
    parse_order,
    sign,
)
from cointrader.execution.engine import ExecutionEngine, OrderStore
from cointrader.execution.live_broker import LiveBroker, LiveOrderRefused
from cointrader.execution.models import OrderIntent, OrderState, OrderStatus, PositionSnapshot, client_order_id
from cointrader.execution.paper_broker import PaperBroker
from cointrader.execution.reconciliation import apply_to_engine, reconcile
from cointrader.live.config import HealthStatus, LiveTradingConfig
from cointrader.live.safety_gate import SafetyGateContext

T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)


def intent(purpose="entry", side="BUY", qty=1.0, order_type="MARKET", at=T0, **kw):
    base = dict(strategy_id="s1", candidate_id="s1", symbol="BTCUSDT", side=side, position_side="BOTH",
                order_type=order_type, quantity=qty, price=kw.pop("price", None), reduce_only=purpose != "entry",
                stop_price=kw.pop("stop_price", None), take_profit=None, reason="test", signal_timestamp=at,
                data_timestamp=at, config_version="v", risk_decision_id="rd_1" if purpose == "entry" else "",
                purpose=purpose)
    base.update(kw)
    return OrderIntent(**base)


def book(at, bid=100.0, ask=100.1, bq=5.0, aq=5.0, uid=1):
    return BookTicker("BTCUSDT", bid, bq, ask, aq, uid, at, at, "test")


# ------------------------------------------------------------- intents --
def test_client_order_id_is_deterministic_and_exchange_valid():
    a, b = intent(), intent()
    assert a.client_order_id == b.client_order_id and len(a.client_order_id) <= 36
    assert intent(purpose="stop", side="SELL", order_type="STOP_MARKET", stop_price=95).client_order_id != a.client_order_id
    with pytest.raises(ValueError):
        intent(client_order_id="ct_forged")
    with pytest.raises(ValueError, match="reduce_only"):
        intent(purpose="exit", side="SELL", reduce_only=False)
    with pytest.raises(ValueError, match="risk decision"):
        intent(risk_decision_id="")
    with pytest.raises(ValueError, match="newer"):
        intent(data_timestamp=T0 + timedelta(seconds=1))


# -------------------------------------------------------- paper broker --
def test_paper_market_order_fills_after_latency_at_book_with_taker_fee():
    pb = PaperBroker(initial_balance=10_000, latency=timedelta(milliseconds=200))
    pb.on_book(book(T0))
    st = pb.submit(intent())
    assert st.state is OrderState.NEW
    assert pb.on_book(book(T0 + timedelta(milliseconds=100), uid=2)) == []  # latency not elapsed
    fills = pb.on_book(book(T0 + timedelta(milliseconds=300), ask=100.2, uid=3))
    assert len(fills) == 1 and fills[0].price == 100.2 and fills[0].liquidity == "taker"
    assert fills[0].fee == pytest.approx(100.2 * 0.0005)
    assert fills[0].slippage > 0
    assert pb.positions()[0].quantity == 1.0


def test_paper_market_order_partial_fill_on_thin_top_of_book():
    pb = PaperBroker(initial_balance=10_000, latency=timedelta(0))
    pb.on_book(book(T0, aq=0.4))
    pb.submit(intent())
    pb.on_book(book(T0 + timedelta(seconds=1), aq=0.4, uid=2))
    st = pb.query("BTCUSDT", intent().client_order_id)
    assert st.state is OrderState.CANCELED and st.filled_quantity == pytest.approx(0.4)


def test_paper_uses_depth_when_available():
    pb = PaperBroker(initial_balance=10_000, latency=timedelta(0))
    pb.on_book(book(T0))
    pb.on_depth(OrderBookSnapshot("BTCUSDT", T0 + timedelta(seconds=1), (OrderBookLevel(100.0, 5),),
                                  (OrderBookLevel(100.1, 0.5), OrderBookLevel(100.3, 5))))
    pb.submit(intent())
    fills = pb.on_book(book(T0 + timedelta(seconds=1), uid=2))
    assert fills[0].price == pytest.approx((0.5 * 100.1 + 0.5 * 100.3) / 1.0)


def test_paper_rejects_without_fresh_market_data():
    pb = PaperBroker(initial_balance=10_000)
    assert pb.submit(intent()).detail == "no_fresh_market_data"
    pb.on_book(book(T0))
    pb.now = T0 + timedelta(seconds=30)
    assert pb.submit(intent(at=T0 + timedelta(seconds=1))).state is OrderState.REJECTED


def test_paper_limit_needs_trade_through_and_can_partially_fill():
    pb = PaperBroker(initial_balance=10_000, latency=timedelta(0))
    pb.on_book(book(T0))
    lim = intent(order_type="LIMIT", price=99.0, qty=2.0)
    pb.submit(lim)
    touch = TradeTick("BTCUSDT", 99.0, 5, "sell", 1, T0 + timedelta(seconds=1), T0 + timedelta(seconds=1), "t")
    assert pb.on_trade(touch) == []
    through = TradeTick("BTCUSDT", 98.9, 0.5, "sell", 2, T0 + timedelta(seconds=2), T0 + timedelta(seconds=2), "t")
    fills = pb.on_trade(through)
    assert fills[0].liquidity == "maker" and fills[0].quantity == 0.5 and fills[0].price == 99.0
    assert pb.query("BTCUSDT", lim.client_order_id).state is OrderState.PARTIALLY_FILLED


def test_paper_stop_triggers_on_trade_then_fills_as_market_and_realizes_pnl():
    pb = PaperBroker(initial_balance=10_000, latency=timedelta(0), taker_fee=0.0)
    pb.on_book(book(T0))
    pb.submit(intent())
    pb.on_book(book(T0 + timedelta(seconds=1), uid=2))  # long 1 @ 100.1
    stop = intent(purpose="stop", side="SELL", order_type="STOP_MARKET", stop_price=95.0, at=T0 + timedelta(seconds=2))
    assert pb.submit(stop).state is OrderState.NEW
    pb.on_trade(TradeTick("BTCUSDT", 94.9, 1, "sell", 3, T0 + timedelta(seconds=3), T0 + timedelta(seconds=3), "t"))
    pb.on_book(book(T0 + timedelta(seconds=4), bid=94.5, ask=94.6, uid=3))
    assert pb.positions()[0].quantity == 0
    assert pb.balance == pytest.approx(10_000 + (94.5 - 100.1))


def test_paper_reduce_only_cannot_open_or_increase():
    pb = PaperBroker(initial_balance=10_000)
    pb.on_book(book(T0))
    st = pb.submit(intent(purpose="exit", side="SELL"))
    assert st.state is OrderState.REJECTED and "reduce_only" in st.detail


def test_paper_funding_and_state_roundtrip():
    pb = PaperBroker(initial_balance=10_000, latency=timedelta(0), taker_fee=0.0)
    pb.on_book(book(T0))
    pb.submit(intent())
    pb.on_book(book(T0 + timedelta(seconds=1), uid=2))
    nft = T0 + timedelta(hours=8)
    pb.on_mark_price(MarkPriceUpdate("BTCUSDT", 100.0, 100.0, 0.001, nft, T0, T0, "t"))
    paid = pb.on_mark_price(MarkPriceUpdate("BTCUSDT", 100.0, 100.0, 0.001, nft + timedelta(hours=8), nft, nft, "t"))
    assert paid == pytest.approx(0.1)
    restored = PaperBroker.from_dict(json.loads(json.dumps(pb.to_dict())))
    assert restored.balance == pb.balance and restored.positions() == pb.positions()
    assert restored.query("BTCUSDT", intent().client_order_id).state is OrderState.FILLED


# ------------------------------------------------------ execution engine --
class FlakyBroker:
    mode = "paper"

    def __init__(self, submit_error=None, query_error=None, query_state=OrderState.NEW):
        self.submits = 0
        self.submit_error, self.query_error, self.query_state = submit_error, query_error, query_state

    def submit(self, i):
        self.submits += 1
        if self.submit_error:
            raise self.submit_error
        return OrderStatus(i.client_order_id, OrderState.NEW)

    def query(self, symbol, cid):
        if self.query_error:
            raise self.query_error
        return OrderStatus(cid, self.query_state)


def test_duplicate_submission_is_never_sent_twice(tmp_path):
    broker = FlakyBroker()
    eng = ExecutionEngine(broker, OrderStore(tmp_path / "orders.jsonl"), now=lambda: T0)
    assert eng.execute(intent()).submitted
    r = eng.execute(intent())
    assert not r.submitted and "duplicate" in r.detail and broker.submits == 1
    # survives a restart: the store is reloaded from disk
    eng2 = ExecutionEngine(broker, OrderStore(tmp_path / "orders.jsonl"), now=lambda: T0)
    assert not eng2.execute(intent()).submitted and broker.submits == 1


def test_unknown_state_blocks_all_new_orders_until_resolved(tmp_path):
    broker = FlakyBroker(submit_error=TimeoutError("read timeout"), query_error=ConnectionError("down"))
    eng = ExecutionEngine(broker, OrderStore(tmp_path / "o.jsonl"), now=lambda: T0)
    r = eng.execute(intent())
    assert not r.submitted and "UNKNOWN" in r.detail
    other = intent(at=T0 + timedelta(minutes=5))
    assert "blocked" in eng.execute(other).detail and broker.submits == 1
    broker.query_error = None
    broker.query_state = OrderState.FILLED
    eng.resolve_unknown()
    assert eng.blocked_reason is None
    broker.submit_error = None
    assert eng.execute(other).submitted


def test_submit_error_resolved_by_query(tmp_path):
    broker = FlakyBroker(submit_error=ConnectionError("reset"), query_state=OrderState.FILLED)
    eng = ExecutionEngine(broker, OrderStore(tmp_path / "o.jsonl"), now=lambda: T0)
    r = eng.execute(intent())
    assert r.status.state is OrderState.FILLED and eng.blocked_reason is None


def test_mode_mismatch_refused(tmp_path):
    eng = ExecutionEngine(FlakyBroker(), OrderStore(tmp_path / "o.jsonl"), now=lambda: T0)
    with pytest.raises(Exception, match="mode"):
        eng.execute(intent(mode="live"))


# ------------------------------------------------------- reconciliation --
def test_reconciliation_blocks_on_mismatch_and_never_trades(tmp_path):
    broker = FlakyBroker()
    eng = ExecutionEngine(broker, OrderStore(tmp_path / "o.jsonl"), now=lambda: T0)
    rep = reconcile(at=T0, local_positions={"BTCUSDT": 1.0}, exchange_positions=[PositionSnapshot("BTCUSDT", 0.5, 100)],
                    local_open_order_ids=["ct_a"], exchange_open_orders=[OrderStatus("manual1", OrderState.NEW)],
                    step_sizes={"BTCUSDT": 0.001})
    assert not rep.ok and len(rep.mismatches) == 3
    assert any("NOT placed by this system" in m for m in rep.mismatches)
    apply_to_engine(rep, eng)
    assert "blocked" in eng.execute(intent()).detail and broker.submits == 0
    ok = reconcile(at=T0, local_positions={"BTCUSDT": 0.5}, exchange_positions=[PositionSnapshot("BTCUSDT", 0.5, 100)],
                   local_open_order_ids=[], exchange_open_orders=[], step_sizes={"BTCUSDT": 0.001})
    apply_to_engine(ok, eng)
    assert eng.execute(intent()).submitted


# ---------------------------------------------------------- live broker --
def gate_ctx(**kw):
    base = dict(as_of=T0, config=LiveTradingConfig(), approval=None, strategy_status="OOS_TESTED",
                kill_switch_engaged=True, exchange_health=HealthStatus.HEALTHY, data_feed_health=HealthStatus.HEALTHY,
                account_state_known=True, position_state_known=True)
    base.update(kw)
    return SafetyGateContext(**base)


class RecordingTransport:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers))
        return self.responses.pop(0) if self.responses else (200, b"{}")


def test_live_broker_refuses_without_safety_gate_and_sends_nothing(tmp_path):
    transport = RecordingTransport()
    client = BinanceFuturesClient(Credentials("k", "s"), transport=transport)
    broker = LiveBroker(client, lambda: gate_ctx())
    li = intent(mode="live")
    with pytest.raises(LiveOrderRefused) as exc:
        broker.submit(li)
    msg = str(exc.value)
    for cond in ("environment_not_live", "live_trading_not_enabled", "activation_approval_missing_or_invalid",
                 "strategy_not_human_approved", "kill_switch_engaged", "risk_limit_not_configured_max_daily_loss"):
        assert cond in msg
    eng = ExecutionEngine(broker, OrderStore(tmp_path / "o.jsonl"), now=lambda: T0)
    r = eng.execute(li)
    assert not r.submitted and "safety gate failed" in r.detail
    assert transport.calls == [] and OrderStore(tmp_path / "o.jsonl").get(li.client_order_id) is None


@pytest.mark.parametrize("change,cond", [
    (dict(exchange_health=HealthStatus.DEGRADED), "exchange_not_healthy"),
    (dict(data_feed_health=None), "data_feed_not_healthy"),
    (dict(account_state_known=False), "account_state_unknown"),
    (dict(position_state_known=False), "position_state_unknown"),
])
def test_live_gate_conditions_each_block(change, cond):
    broker = LiveBroker(BinanceFuturesClient(Credentials("k", "s"), transport=RecordingTransport()),
                        lambda: gate_ctx(**change))
    assert cond in broker.preflight(intent(mode="live"))


# ------------------------------------------------------- binance client --
def test_signed_request_has_signature_and_key_header_but_no_secret():
    transport = RecordingTransport([(200, json.dumps([]).encode())])
    client = BinanceFuturesClient(Credentials("KEY", "SECRET"), transport=transport, clock_ms=lambda: 1700000000000)
    client.open_orders("BTCUSDT")
    method, url, headers = transport.calls[0]
    assert url.startswith("https://fapi.binance.com/fapi/v1/openOrders?symbol=BTCUSDT&timestamp=1700000000000")
    query = url.split("?", 1)[1].rsplit("&signature=", 1)
    assert query[1] == sign(query[0], "SECRET")
    assert headers["X-MBX-APIKEY"] == "KEY" and "SECRET" not in url
    assert "SECRET" not in repr(Credentials("KEY", "SECRET"))


def test_credentials_only_from_env(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    with pytest.raises(MissingCredentials):
        Credentials.from_env()
    with pytest.raises(MissingCredentials):
        BinanceFuturesClient(None, transport=RecordingTransport()).positions()


def test_order_not_found_maps_to_never_placed_and_errors_raise():
    t = RecordingTransport([(400, b'{"code": -2013, "msg": "Order does not exist."}'),
                            (429, b'{"code": -1003, "msg": "Too many requests"}')])
    client = BinanceFuturesClient(Credentials("k", "s"), transport=t)
    assert client.query_order("BTCUSDT", "ct_x").state is OrderState.REJECTED
    with pytest.raises(BinanceApiError):
        client.query_order("BTCUSDT", "ct_y")


def test_parsers_and_order_params():
    info = {"symbols": [{"symbol": "BTCUSDT", "status": "TRADING", "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.10"}, {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
        {"filterType": "MIN_NOTIONAL", "notional": "100"}]}, {"symbol": "OLD", "status": "SETTLING", "filters": []}]}
    f = parse_exchange_info(info)
    assert list(f) == ["BTCUSDT"] and f["BTCUSDT"].min_notional == 100
    tiers = parse_leverage_brackets([{"symbol": "BTCUSDT", "brackets": [
        {"notionalFloor": 0, "notionalCap": 50000, "maintMarginRatio": 0.004, "cum": 0.0},
        {"notionalFloor": 50000, "notionalCap": 500000, "maintMarginRatio": 0.005, "cum": 50.0}]}], "BTCUSDT")
    assert tiers[0].notional_cap == 50000 and tiers[-1].notional_cap is None
    st = parse_order({"clientOrderId": "ct_1", "orderId": 9, "status": "PARTIALLY_FILLED", "executedQty": "0.5",
                      "avgPrice": "100.5"})
    assert st.state is OrderState.PARTIALLY_FILLED and st.average_price == 100.5
    assert parse_order({"clientOrderId": "c", "orderId": 1, "status": "WEIRD", "executedQty": "0", "avgPrice": "0"}).state \
        is OrderState.UNKNOWN
    p = order_params(intent(purpose="stop", side="SELL", order_type="STOP_MARKET", stop_price=95.5, qty=0.001))
    assert p["reduceOnly"] == "true" and p["stopPrice"] == "95.5" and p["quantity"] == "0.001"
    assert p["newClientOrderId"].startswith("ct_")
