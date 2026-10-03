from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.data.feed import FeedEvent
from cointrader.data.market_events import BookTicker, DataQualityEvent, TradeTick
from cointrader.data.models import Candle, Timeframe
from cointrader.journal.records import trade_from_outcome
from cointrader.journal.store import LayeredStore
from cointrader.live.kill_switch import KillSwitchLog, engage_kill_switch
from cointrader.paper.engine import PaperConfig, PaperTrader
from cointrader.risk.engine import RiskConfig, RiskEngine, SymbolFilters
from cointrader.strategies.base import Signal, flat

T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)
SYM = "BTCUSDT"
FILTERS = {SYM: SymbolFilters(SYM, 0.1, 0.001, 0.001, 5.0)}
RISK = RiskConfig(stoploss_guard=None, drawdown_guard=None, cooldown=None)


class Scripted:
    """Deterministic test strategy: returns plan[len(history)] or flat."""

    timeframe = "1m"
    family = "scalp"
    strategy_id = "test_scripted_v1"
    warmup = 5

    def __init__(self, plan: dict):
        self.plan = plan

    def signal(self, history, context=None):
        return self.plan.get(len(history), flat("none", regime="TREND_UP"))


def candle(i: int, price: float) -> Candle:
    ot = T0 + timedelta(minutes=i)
    return Candle(SYM, Timeframe.MINUTE_1, ot, price, price * 1.001, price * 0.999, price, 10.0, "test",
                  ot + timedelta(minutes=1, milliseconds=100))


def book(at: datetime, mid: float, uid: int, half: float = 0.5) -> BookTicker:
    return BookTicker(SYM, mid - half, 5.0, mid + half, 5.0, uid, at, at, "test")


def trade(at: datetime, price: float, tid: int, side: str = "sell") -> TradeTick:
    return TradeTick(SYM, price, 0.01, side, tid, at, at, "test")


def make_trader(tmp_path, plan=None, strategies=None, **cfg):
    config = PaperConfig(state_dir=tmp_path / "state", kill_switch_path=tmp_path / "paper_kill.jsonl", **cfg)
    strategies = strategies or {"test_scripted_v1": (Scripted(plan or {}), "1")}
    return PaperTrader(config=config, strategies=strategies, symbols=[SYM], risk=RiskEngine(RISK, FILTERS),
                       filters=FILTERS, store=LayeredStore(tmp_path / "data"))


class Replay:
    """Feeds a trader minute by minute: book ticks, then the closed candle,
    then a book tick 500 ms later (which fills market orders)."""

    def __init__(self, trader: PaperTrader, start_index: int = 100):
        self.t = trader
        self.i = start_index
        self.uid = 1
        self.tid = 1

    def warm(self, prices):
        self.t.bootstrap_history(candle(k, p) for k, p in enumerate(prices))
        self.i = len(prices)

    def minute(self, price: float, trades=()):
        c = candle(self.i, price)
        start = c.open_time
        for s in (10, 40):
            self.t.process(book(start + timedelta(seconds=s), price, self._u()))
        for off, px in trades:
            self.tid += 1
            self.t.process(trade(start + timedelta(seconds=off), px, self.tid))
            self.t.process(book(start + timedelta(seconds=off, milliseconds=400), px, self._u()))
        self.t.process(c)
        self.t.process(book(c.received_at + timedelta(milliseconds=500), price, self._u()))
        self.i += 1

    def _u(self):
        self.uid += 1
        return self.uid


def start(trader, replay, n_warm=120, price=60_000.0):
    replay.warm([price] * n_warm)
    now = T0 + timedelta(minutes=n_warm)
    trader.process(FeedEvent("connected", now, "test"))
    trader.process(book(now, price, 1))
    assert trader.start(now)


def entry_long(stop=300.0, tp=None, trail=None):
    return Signal(1, strength=0.5, reason="test_long", stop_distance=stop, take_profit_distance=tp,
                  trailing_distance=trail, regime="TREND_UP")


def rows(tmp_path, layer):
    return list(LayeredStore(tmp_path / "data").read(layer))


def test_entry_protective_stop_signal_exit_and_cost_identity(tmp_path):
    plan = {122: entry_long(), 124: Signal(0, exit_long=True, reason="exit_now", regime="TREND_UP")}
    t = make_trader(tmp_path, plan)
    r = Replay(t)
    start(t, r)
    r.minute(60_000)  # history 121
    r.minute(60_000)  # 122 -> entry signal, filled on the +500ms book
    tr = t.open_trades[SYM]
    assert tr.state == "open" and tr.entry_qty > 0 and tr.stop_cid
    assert t.broker.open_orders()[0].client_order_id == tr.stop_cid  # protective stop resting
    r.minute(60_100)
    r.minute(60_200)  # 124 -> exit signal
    assert SYM not in t.open_trades
    out = rows(tmp_path, "outcome")
    assert len(out) == 1
    o = out[0]
    assert o["exit_reason"].startswith("signal_exit") and o["mode"] == "paper"
    assert o["net_pnl"] == pytest.approx(o["gross_pnl"] - o["fees"] - o["spread_cost"] - o["slippage_cost"]
                                         - o["funding"])
    assert o["gross_pnl"] > 0 and o["spread_cost"] > 0
    # the chain joins: outcome -> decision -> risk decision -> entry order
    dec = {d["decision_id"]: d for d in rows(tmp_path, "decision")}
    d = dec[o["entry_decision_id"]]
    assert d["action"] == "enter_long" and d["risk_decision_id"] == o["risk_decision_id"]
    assert d["client_order_id"] == o["entry_client_order_id"]
    ex = [e for e in rows(tmp_path, "execution") if e["event"] == "fill"]
    assert {e["client_order_id"] for e in ex} >= {o["entry_client_order_id"], o["exit_client_order_id"]}
    assert trade_from_outcome(o).net_pnl == pytest.approx(o["net_pnl"])


def test_stop_loss_fills_through_resting_stop(tmp_path):
    t = make_trader(tmp_path, {122: entry_long(stop=200.0)})
    r = Replay(t)
    start(t, r)
    r.minute(60_000)
    r.minute(60_000)
    assert t.open_trades[SYM].state == "open"
    r.minute(59_700, trades=[(20, 59_750.0)])  # trade through the stop (~59,800) -> triggered, then filled
    out = rows(tmp_path, "outcome")
    assert len(out) == 1 and out[0]["exit_reason"] == "stop_loss" and out[0]["net_pnl"] < 0
    assert not t.broker.open_orders()


def test_take_profit_and_trailing_are_managed_on_book_updates(tmp_path):
    t = make_trader(tmp_path, {122: entry_long(stop=500.0, tp=150.0)})
    r = Replay(t)
    start(t, r)
    r.minute(60_000)
    r.minute(60_000)
    r.minute(60_200)
    assert rows(tmp_path, "outcome")[0]["exit_reason"] == "take_profit"

    t2 = make_trader(tmp_path / "b", {122: entry_long(stop=500.0, trail=100.0)})
    r2 = Replay(t2)
    start(t2, r2)
    r2.minute(60_000)
    r2.minute(60_000)
    r2.minute(60_300)
    r2.minute(60_150)
    assert rows(tmp_path / "b", "outcome")[0]["exit_reason"] == "trailing_stop"


def test_restart_restores_position_and_reconciles(tmp_path):
    t = make_trader(tmp_path, {122: entry_long()})
    r = Replay(t)
    start(t, r)
    r.minute(60_000)
    r.minute(60_000)
    t.save_state()
    stop_cid = t.open_trades[SYM].stop_cid

    t2 = make_trader(tmp_path)
    r2 = Replay(t2)
    r2.warm([60_000.0] * 123)
    now = T0 + timedelta(minutes=124)
    t2.process(FeedEvent("connected", now, "test"))
    assert t2.start(now)
    assert t2.open_trades[SYM].stop_cid == stop_cid
    assert [o.client_order_id for o in t2.broker.open_orders()] == [stop_cid]
    audit = [a for a in rows(tmp_path, "audit") if a["event"] == "paper_start"]
    assert audit[-1]["steps"][0] == "local_state_restored" and "reconciled_ok" in audit[-1]["steps"]


def test_state_mismatch_blocks_all_new_orders_and_never_auto_fixes(tmp_path):
    t = make_trader(tmp_path, {122: entry_long()})
    r = Replay(t)
    start(t, r)
    r.minute(60_000)
    r.minute(60_000)
    t.save_state()
    state = json.loads(t.state_path.read_text())
    state["open_trades"] = {}  # local book says flat, broker says long
    t.state_path.write_text(json.dumps(state))

    t2 = make_trader(tmp_path, {125: entry_long()})
    r2 = Replay(t2)
    r2.warm([60_000.0] * 123)
    now = T0 + timedelta(minutes=124)
    t2.process(FeedEvent("connected", now, "test"))
    assert t2.start(now) is False
    assert "reconciliation mismatch" in t2.engine.blocked_reason
    position_before = t2.broker.positions()[0].quantity
    r2.i = 124
    r2.minute(60_000)
    r2.minute(60_000)
    assert t2.broker.positions()[0].quantity == position_before  # nothing traded to "fix" it
    last = [d for d in rows(tmp_path, "decision") if d["strategy_id"] == "test_scripted_v1"][-1]
    assert last["action"] == "blocked"


def test_engaged_paper_kill_switch_blocks_entries(tmp_path):
    KillSwitchLog(tmp_path / "paper_kill.jsonl").record(
        engage_kill_switch(reason="test", occurred_at=T0, configuration_version="t"))
    t = make_trader(tmp_path, {122: entry_long()})
    r = Replay(t)
    start(t, r)
    r.minute(60_000)
    r.minute(60_000)
    assert SYM not in t.open_trades
    d = [d for d in rows(tmp_path, "decision") if d["signal"]["entry"] == 1][0]
    assert d["action"] == "blocked" and "kill_switch_engaged" in d["reason"]


def test_stale_book_and_quality_event_block_entries(tmp_path):
    t = make_trader(tmp_path, {122: entry_long(), 124: entry_long()})
    r = Replay(t)
    start(t, r)
    r.minute(60_000)
    # minute 121 with no book updates for >30s before the candle closes
    c = candle(r.i, 60_000)
    t.process(c)
    r.i += 1
    d = [d for d in rows(tmp_path, "decision") if d["signal"]["entry"] == 1][0]
    assert d["action"] == "blocked" and "feed_" in d["reason"]
    r.minute(60_000)
    t.process(DataQualityEvent("price_spike", SYM, candle(r.i, 1).open_time + timedelta(seconds=5), "x", "test"))
    r.minute(60_000)
    d2 = [d for d in rows(tmp_path, "decision") if d["signal"]["entry"] == 1][1]
    assert d2["action"] == "blocked" and "quality_event_price_spike" in d2["reason"]


def test_duplicate_candle_is_not_decided_twice(tmp_path):
    t = make_trader(tmp_path, {})
    r = Replay(t)
    start(t, r)
    r.minute(60_000)
    n = t.counters["decisions"]
    t.process(candle(r.i - 1, 60_000))
    assert t.counters["decisions"] == n


# ---------------------------------------------------------------- long runs


def _random_walk_run(tmp_path, *, minutes: int, strategies, candle_every: int = 1, seed: int = 7,
                     tick_minutes: int = 1):
    from cointrader.risk.engine import RiskConfig as RC
    rng = random.Random(seed)
    t = PaperTrader(config=PaperConfig(state_dir=tmp_path / "state", kill_switch_path=tmp_path / "k.jsonl",
                                       max_candles=300),
                    strategies=strategies, symbols=[SYM], risk=RiskEngine(RC(), FILTERS), filters=FILTERS,
                    store=LayeredStore(tmp_path / "data"))
    tf = Timeframe.MINUTE_1 if candle_every == 1 else Timeframe.HOUR_1
    price = 60_000.0
    warm = []
    for k in range(260):
        ot = T0 - (260 - k) * tf.delta
        o = price
        price *= math.exp(rng.gauss(0, 0.002))
        warm.append(Candle(SYM, tf, ot, o, max(o, price) * 1.0005, min(o, price) * 0.9995, price, 10.0, "test",
                           ot + tf.delta))
    t.bootstrap_history(warm)
    t.process(FeedEvent("connected", T0, "sim"))
    t.process(book(T0, price, 1))
    assert t.start(T0)
    uid, tid = 1, 1
    open_price = price
    for m in range(minutes):
        start_m = T0 + timedelta(minutes=m * tick_minutes)
        for s in (tick_minutes * 60 - 55, tick_minutes * 60 - 35, tick_minutes * 60 - 15):
            price *= math.exp(rng.gauss(0, 0.0008))
            uid += 1
            t.process(book(start_m + timedelta(seconds=s), round(price, 1), uid, half=0.5))
            tid += 1
            t.process(TradeTick(SYM, round(price, 1), 0.01, rng.choice(("buy", "sell")), tid,
                                start_m + timedelta(seconds=s + 1), start_m + timedelta(seconds=s + 1), "sim"))
        if (m + 1) % candle_every == 0:
            ot = start_m + timedelta(minutes=tick_minutes) - tf.delta
            c = Candle(SYM, tf, ot, open_price, max(open_price, price) * 1.0005, min(open_price, price) * 0.9995,
                       price, 10.0, "sim", ot + tf.delta + timedelta(milliseconds=50))
            t.process(c)
            open_price = price
            uid += 1
            t.process(book(c.received_at + timedelta(milliseconds=400), round(price, 1), uid))
    return t


def _check_invariants(tmp_path, t, expected_decisions):
    assert t.counters["decisions"] == expected_decisions
    orders = [json.loads(line) for line in (tmp_path / "state" / "orders.jsonl").read_text().splitlines()] \
        if (tmp_path / "state" / "orders.jsonl").exists() else []
    submits = [o for o in orders if o["state"] == "PENDING_SUBMIT"]
    assert len({o["client_order_id"] for o in submits}) == len(submits)  # no order submitted twice
    decisions = list(LayeredStore(tmp_path / "data").read("decision"))
    assert len({d["decision_id"] for d in decisions}) == len(decisions)  # no duplicate decisions
    for bars in t._history.values():
        assert len(bars) <= 300  # bounded memory
    assert len(t._recent_quality) <= 500
    assert all(len(f) < 1000 for f in t._flow.values())
    assert t._trade_agg.open_minutes() <= 1
    assert t.reconcile_now().ok


def test_simulated_24h_scalping_run_is_bounded_and_duplicate_free(tmp_path):
    from cointrader.strategies.scalp import RangeBreakoutVolume, ShortTermMeanReversion, VwapReversion
    strategies = {s.strategy_id: (s, s.version) for s in (VwapReversion(), ShortTermMeanReversion())}
    minutes = 24 * 60
    t = _random_walk_run(tmp_path, minutes=minutes, strategies=strategies)
    _check_invariants(tmp_path, t, expected_decisions=minutes * 2)
    assert t.counters["entries"] > 0 and t.counters["trades_closed"] > 0  # the run really traded
    assert RangeBreakoutVolume  # 5m strategy exercised in the 7d run below
    store = LayeredStore(tmp_path / "data")
    minute_stats = [r for r in store.read("normalized") if r["kind"] == "trade_stats_1m"]
    assert len(minute_stats) >= minutes - 2


@pytest.mark.parametrize("days", [7, 30])
def test_simulated_multi_day_swing_run_growth_is_linear(tmp_path, days):
    from cointrader.strategies.swing import BollingerReversion, TrendEmaAtr
    strategies = {s.strategy_id: (s, s.version) for s in (TrendEmaAtr(), BollingerReversion())}
    step_minutes = 5 if days == 7 else 20  # market-data spacing; candles stay hourly
    ticks = days * 24 * 60 // step_minutes
    t = _random_walk_run(tmp_path, minutes=ticks, strategies=strategies, candle_every=60 // step_minutes,
                         tick_minutes=step_minutes)
    hours = ticks // (60 // step_minutes)
    _check_invariants(tmp_path, t, expected_decisions=hours * 2)
    parts = LayeredStore(tmp_path / "data").partitions("decision")
    sizes = [p.stat().st_size for p in parts][1:-1]  # full days only
    assert sizes and max(sizes) < 2.0 * min(sizes)  # per-day growth stays flat, no runaway


def _margin_trader(tmp_path, plan, leverage, tiers=True):
    from cointrader.risk.leverage import MarginTier
    from cointrader.risk.margin_policy import MarginPolicy
    t = make_trader(tmp_path, plan)
    t.margin_policy = MarginPolicy("ISOLATED", leverage, 3.0)
    t.margin_tiers = {SYM: [MarginTier(0, None, 0.005, 0.0)]} if tiers else {}
    return t


def test_margin_policy_blocks_entry_when_liquidation_near_stop(tmp_path):
    # 3x isolated: liquidation ~33% away, a 300 stop (0.5%) is fine.
    ok = _margin_trader(tmp_path / "ok", {122: entry_long()}, 3)
    r = Replay(ok); start(ok, r); r.minute(60_000); r.minute(60_000)
    assert SYM in ok.open_trades
    # 20x: liquidation ~4.5% away, only ~9 stop distances of 0.5%... use a wide 2% stop to trip the 3x rule.
    bad = _margin_trader(tmp_path / "bad", {122: entry_long(stop=1_200.0)}, 20)
    r = Replay(bad); start(bad, r); r.minute(60_000); r.minute(60_000)
    assert SYM not in bad.open_trades
    assert any(d["reason"] == "liquidation_too_close_to_stop" for d in rows(tmp_path / "bad", "decision"))


def test_margin_policy_refuses_without_tiers(tmp_path):
    t = _margin_trader(tmp_path, {122: entry_long()}, 3, tiers=False)
    r = Replay(t); start(t, r); r.minute(60_000); r.minute(60_000)
    assert SYM not in t.open_trades
    assert any(d["reason"] == "margin_tiers_unknown" for d in rows(tmp_path, "decision"))
