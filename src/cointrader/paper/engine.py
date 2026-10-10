"""Always-on paper trader: the live pipeline with a simulated broker.

    feed item -> (health, quality) -> closed candle -> features -> regime
      -> strategy.signal -> quality gate -> kill switch -> RiskEngine
      -> OrderIntent -> ExecutionEngine(PaperBroker) -> fills -> position
      -> protective stop / take-profit / trailing / signal exit -> outcome

Every step writes to the layered journal (`journal.store`), so each
closed trade joins back to the exact features, regime, signal, risk
decision and orders that produced it.

The same `ExecutionEngine` and `RiskEngine` classes run in live mode;
only the broker differs. This module never constructs a `LiveBroker`
(an AST test enforces that), never releases a kill switch and never
changes a strategy's lifecycle status.

Paper kill switch: the paper trader reads its OWN kill-switch log
(`paper.kill_switch_path`, separate from live). A log that exists and
says engaged -- or that cannot be read -- blocks entries. A log that
does not exist yet counts as "not engaged" for PAPER only, because
paper risks no money; live keeps the stricter "missing = engaged" rule
of `live.kill_switch.KillSwitchLog`. The paper trader engages its kill
switch on a max-drawdown breach; only a human can release it.

Start-up recovery order (ADR-0015): restore local state -> connect feed
(the run loop) -> broker health -> account -> open orders -> positions
-> reconcile local vs broker -> only then allow entries. A mismatch
blocks all new orders until a later clean reconciliation; nothing is
auto-corrected.
"""

from __future__ import annotations

import json
import os
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from cointrader.backtest.event_engine import TradeRecord
from cointrader.data.feed import FeedEvent
from cointrader.data.market_events import BookTicker, DataQualityEvent, DepthDelta, MarkPriceUpdate, TradeTick
from cointrader.data.models import Candle
from cointrader.data.orderbook import DepthSnapshot, LocalOrderBook
from cointrader.data.quality_gate import QualityLimits, evaluate_data_quality
from cointrader.data.realtime import FeedHealthMonitor
from cointrader.execution.engine import ExecutionEngine, OrderStore
from cointrader.execution.models import Fill, OrderIntent, OrderState
from cointrader.execution.paper_broker import PaperBroker
from cointrader.execution.reconciliation import apply_to_engine, reconcile
from cointrader.features.indicators import FEATURE_VERSION
from cointrader.features.microstructure import TradeFlowWindow, microprice, ticker_imbalance
from cointrader.features.regime import classify_regime
from cointrader.features.snapshot import candle_features
from cointrader.journal.aggregation import MinuteBookAggregator, MinuteTradeAggregator, ticker_row, trade_stats_record
from cointrader.journal.records import decision_record, outcome_record
from cointrader.journal.store import LayeredStore
from cointrader.live.kill_switch import KillSwitchLog, engage_kill_switch
from cointrader.notifications.notifier import Notifier, Severity
from cointrader.risk.engine import EntryRequest, RiskEngine, SymbolFilters, account_state_from_history
from cointrader.risk.protections import ClosedTrade, EquityPoint
from cointrader.strategies.base import MarketContext

STATE_VERSION = 1


def _time_stop_reason(max_hold_bars: int, bar_length: timedelta) -> str:
    hours = max_hold_bars * bar_length.total_seconds() / 3600
    return f"시간손절({int(hours) if hours == int(hours) else round(hours, 1)}시간)"


@dataclass(frozen=True)
class PaperConfig:
    state_dir: Path
    kill_switch_path: Path
    initial_balance: float = 10_000.0
    max_candles: int = 600  # per (symbol, timeframe); build_trader raises it to the longest strategy warm-up
    quality_window: int = 50
    save_every: timedelta = timedelta(minutes=1)
    reconcile_every: timedelta = timedelta(minutes=5)
    history_keep: timedelta = timedelta(days=7)
    trade_flow_window: timedelta = timedelta(minutes=1)
    quality_hold: timedelta = timedelta(minutes=5)
    record_raw: bool = False
    large_trade_quantity: dict = field(default_factory=dict)
    quality_limits: QualityLimits = QualityLimits()
    depth_levels: int = 20  # order-book levels handed to the broker for market-order fills
    depth_every: timedelta = timedelta(milliseconds=200)  # at most one book snapshot to the broker per interval
    depth_resync_every: timedelta = timedelta(seconds=30)  # wait between REST snapshot attempts of an unsynced book


@dataclass
class OpenTrade:
    seq: int
    strategy_id: str
    strategy_version: str
    timeframe: str
    symbol: str
    direction: int
    entry_cid: str
    decision_id: str
    risk_decision_id: str
    signal_reason: str
    regime: str
    features: dict
    stop_distance: float
    tp_distance: Optional[float]
    trail_distance: Optional[float]
    equity_at_entry: float
    decided_at: str
    expected_entry_slippage: Optional[float]
    state: str = "entering"  # entering | open | closing
    entry_qty: float = 0.0
    entry_notional: float = 0.0
    entry_ref_notional: float = 0.0
    entry_fees: float = 0.0
    entry_spread: float = 0.0
    entry_time: Optional[str] = None
    exit_qty: float = 0.0
    exit_notional: float = 0.0
    exit_ref_notional: float = 0.0
    exit_fees: float = 0.0
    exit_spread: float = 0.0
    exit_time: Optional[str] = None
    funding: float = 0.0
    best: Optional[float] = None
    worst: Optional[float] = None
    stop_price: Optional[float] = None
    tp_price: Optional[float] = None
    stop_cid: Optional[str] = None
    exit_cid: Optional[str] = None
    exit_reason: Optional[str] = None
    bars_held: int = 0
    pending_exit: Optional[str] = None  # reason, when an exit must be (re)tried
    trail_activation: Optional[float] = None  # trail only once best has moved this far from the entry average

    @property
    def open_quantity(self) -> float:
        return max(self.entry_qty - self.exit_qty, 0.0)


class PaperTrader:
    def __init__(
        self,
        *,
        config: PaperConfig,
        strategies: dict[str, tuple[object, str]],  # strategy_id -> (instance, version)
        symbols: Iterable[str],
        risk: RiskEngine,
        filters: dict[str, SymbolFilters],
        store: LayeredStore,
        notifier: Optional[Notifier] = None,
        broker: Optional[PaperBroker] = None,
        margin_policy=None,  # risk.margin_policy.MarginPolicy | None (ADR-0037)
        margin_tiers: Optional[dict] = None,  # symbol -> [MarginTier]
        depth_snapshot: Optional[Callable[[str], DepthSnapshot]] = None,  # public REST /fapi/v1/depth; None = no book
    ) -> None:
        self._depth_snapshot = depth_snapshot
        self._order_books: dict[str, LocalOrderBook] = {}
        self._depth_last_sync_try: dict[str, datetime] = {}
        self._depth_last_push: dict[str, datetime] = {}
        self._entries_by_day: dict[str, int] = {}  # "strategy|symbol|UTC date" -> entries (max_entries_per_day)
        self.margin_policy = margin_policy
        self.margin_tiers = margin_tiers or {}
        self.cfg = config
        self.strategies = strategies
        self.symbols = tuple(symbols)
        self.risk = risk
        self.filters = filters
        self.store = store
        self.notifier = notifier or Notifier(mode="paper", sink=lambda _msg: None)
        config.state_dir.mkdir(parents=True, exist_ok=True)
        self.broker = broker or PaperBroker(initial_balance=config.initial_balance)
        self.orders = OrderStore(config.state_dir / "orders.jsonl")
        self.engine = ExecutionEngine(self.broker, self.orders, now=self._clock)
        self.health = FeedHealthMonitor(quality_hold=timedelta(seconds=60))
        self.kill_log = KillSwitchLog(config.kill_switch_path)
        self._now: Optional[datetime] = None
        self._history: dict[tuple[str, str], deque] = {}
        self._book: dict[str, BookTicker] = {}
        self._flow: dict[str, TradeFlowWindow] = {s: TradeFlowWindow(config.trade_flow_window) for s in self.symbols}
        self._recent_quality: deque = deque(maxlen=500)
        self._trade_agg = MinuteTradeAggregator(large_trade_quantity=dict(config.large_trade_quantity))
        self._book_agg = MinuteBookAggregator()
        self.open_trades: dict[str, OpenTrade] = {}
        self.equity_points: list[EquityPoint] = []
        self.closed_trades: list[ClosedTrade] = []
        self.trade_seq = 0
        self._last_save: Optional[datetime] = None
        self._last_reconcile: Optional[datetime] = None
        self.ready = False  # False until start-up reconciliation passed
        self._started = False
        self.counters = {"decisions": 0, "entries": 0, "exits": 0, "blocked": 0, "trades_closed": 0}

    # ------------------------------------------------------------------ utils
    def _clock(self) -> datetime:
        if self._now is None:
            raise RuntimeError("paper trader has no market time yet")
        return self._now

    @property
    def state_path(self) -> Path:
        return self.cfg.state_dir / "paper_state.json"

    def kill_switch_engaged(self) -> tuple[bool, str]:
        if not self.cfg.kill_switch_path.exists():
            return False, "no paper kill-switch log (paper only: not engaged)"
        try:
            latest = self.kill_log.latest()
        except (OSError, ValueError, KeyError) as exc:
            return True, f"kill-switch log unreadable: {type(exc).__name__}"
        if latest is None:
            return False, "empty paper kill-switch log"
        return latest.engaged, latest.reason

    def _safety(self, event: str, detail: str, severity: Severity = Severity.WARNING, **extra) -> None:
        self.store.append("safety", {"event": event, "detail": detail, "mode": "paper", **extra}, at=self._clock())
        self.notifier.notify(severity, event, detail, at=self._clock(), key=f"{event}:{extra.get('symbol', '')}")

    def _bars(self, symbol: str, timeframe: str) -> deque:
        return self._history.setdefault((symbol, timeframe), deque(maxlen=self.cfg.max_candles))

    # -------------------------------------------------------------- lifecycle
    def bootstrap_history(self, candles: Iterable[Candle]) -> None:
        """Load closed candles fetched over REST before the stream starts
        (warmup). Not treated as decision points."""
        for c in sorted(candles, key=lambda c: c.open_time):
            bars = self._bars(c.market, c.timeframe.value)
            if not bars or c.open_time > bars[-1].open_time:
                bars.append(c)
                self._journal_candle(c, via="bootstrap")

    def start(self, now: datetime) -> bool:
        """Recovery sequence. Returns True when entries may resume."""
        self._now = now
        self._started = True
        steps = []
        if self.state_path.exists():
            self._restore(json.loads(self.state_path.read_text(encoding="utf-8")))
            steps.append("local_state_restored")
        else:
            steps.append("fresh_start")
        broker_health = self.broker.health()
        steps.append(f"broker_health={broker_health.value}")
        account = self.broker.account()
        steps.append(f"account_equity={account.equity:.2f}")
        open_orders = self.broker.open_orders()
        steps.append(f"open_orders={len(open_orders)}")
        positions = [p for p in self.broker.positions() if p.quantity]
        steps.append(f"positions={len(positions)}")
        resolved = self.engine.resolve_unknown()
        if resolved:
            steps.append(f"resolved_unknown={len(resolved)}")
        report = self.reconcile_now()
        steps.append("reconciled_ok" if report.ok else "reconciliation_mismatch")
        self.ready = report.ok
        self.store.append("audit", {"event": "paper_start", "steps": steps, "state_version": STATE_VERSION,
                                    "feature_version": FEATURE_VERSION, "risk_config_version": self.risk.config_version,
                                    "strategies": {k: v[1] for k, v in self.strategies.items()}}, at=now)
        self.notifier.notify(Severity.INFO if report.ok else Severity.CRITICAL, "paper trader started",
                             ", ".join(steps), at=now)
        return report.ok

    def reconcile_now(self):
        now = self._clock()
        local_positions = {s: t.direction * t.open_quantity for s, t in self.open_trades.items() if t.open_quantity}
        report = reconcile(
            at=now, local_positions=local_positions, exchange_positions=self.broker.positions(),
            local_open_order_ids=[r["client_order_id"] for r in self.orders.open()],
            exchange_open_orders=self.broker.open_orders(),
            step_sizes={s: f.step_size for s, f in self.filters.items()},
        )
        reason = apply_to_engine(report, self.engine)
        self._last_reconcile = now
        self.store.append("safety", {"event": "reconciliation", "detail": "ok" if report.ok else reason,
                                     "ok": report.ok, "mismatches": list(report.mismatches), "mode": "paper"}, at=now)
        if not report.ok:
            self.notifier.notify(Severity.CRITICAL, "reconciliation mismatch", reason or "", at=now,
                                 key="reconciliation")
        self.ready = report.ok
        return report

    # ----------------------------------------------------------- persistence
    def save_state(self) -> None:
        state = {
            "state_version": STATE_VERSION,
            "saved_at": self._clock().isoformat(),
            "broker": self.broker.to_dict(),
            "open_trades": {s: asdict(t) for s, t in self.open_trades.items()},
            "equity_points": [[p.at.isoformat(), p.equity] for p in self.equity_points],
            "closed_trades": [[t.closed_at.isoformat(), t.net_return] for t in self.closed_trades],
            "trade_seq": self.trade_seq,
            "entries_by_day": self._entries_by_day,
            "last_candle": {f"{s}|{tf}": b[-1].open_time.isoformat() for (s, tf), b in self._history.items() if b},
        }
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)  # atomic: a crash leaves the old or the new file, never half of one
        self._last_save = self._clock()

    def _restore(self, state: dict) -> None:
        if state.get("state_version") != STATE_VERSION:
            raise RuntimeError(f"paper state version {state.get('state_version')} != {STATE_VERSION}; refusing to guess")
        self.broker = PaperBroker.from_dict(state["broker"], taker_fee=self.broker.taker_fee,
                                            maker_fee=self.broker.maker_fee, latency=self.broker.latency,
                                            max_book_age=self.broker.max_book_age)
        self.engine.broker = self.broker
        self.open_trades = {s: OpenTrade(**t) for s, t in state["open_trades"].items()}
        self.equity_points = [EquityPoint(datetime.fromisoformat(a), e) for a, e in state["equity_points"]]
        self.closed_trades = [ClosedTrade(datetime.fromisoformat(a), r) for a, r in state["closed_trades"]]
        self.trade_seq = state["trade_seq"]
        self._entries_by_day = {k: int(v) for k, v in state.get("entries_by_day", {}).items()}

    # ------------------------------------------------------------- the loop
    def process(self, item) -> None:
        at = item.at if isinstance(item, (FeedEvent, DataQualityEvent)) else item.received_at
        if self._now is None or at > self._now:
            self._now = at
        self.health.observe(item)
        if not self._started:
            # before recovery nothing may be decided, filled or saved (saving would overwrite the state to restore)
            if isinstance(item, BookTicker):
                self._book[item.symbol] = item
            return
        if isinstance(item, FeedEvent):
            self.store.append("audit", {"event": f"feed_{item.kind}", "detail": item.detail}, at=self._now)
            if item.kind in ("disconnected", "backfill_incomplete", "missing_candle"):
                self._safety(f"feed_{item.kind}", item.detail)
        elif isinstance(item, DataQualityEvent):
            self._recent_quality.append(item)
            self.store.append("quality", {"kind": item.kind, "symbol": item.symbol, "detail": item.detail,
                                          "blocks_trading": item.blocks_trading, "source": item.source,
                                          "event_type": "DATA_QUALITY_EVENT"}, at=self._now)
        elif isinstance(item, BookTicker):
            self._on_book(item)
        elif isinstance(item, TradeTick):
            self._on_trade(item)
        elif isinstance(item, DepthDelta):
            self._on_depth(item)
        elif isinstance(item, MarkPriceUpdate):
            paid = self.broker.on_mark_price(item)
            t = self.open_trades.get(item.symbol)
            if paid is not None and t is not None:
                t.funding += paid
                self.store.append("execution", {"event": "funding", "client_order_id": t.entry_cid,
                                                "symbol": item.symbol, "mode": "paper", "amount": paid}, at=self._now)
        elif isinstance(item, Candle):
            self._on_candle(item)
        self._periodic()

    def _periodic(self) -> None:
        now = self._clock()
        for s in self._trade_agg.flush_before(now):
            self.store.append("normalized", trade_stats_record(s, "paper_aggregator"), at=now)
        for row in self._book_agg.flush_before(now):
            self.store.append("normalized", {**row, "source": "paper_aggregator"}, at=now)
        if self._last_reconcile is None or now - self._last_reconcile >= self.cfg.reconcile_every:
            self.reconcile_now()
        if self._last_save is None or now - self._last_save >= self.cfg.save_every:
            self.save_state()

    # ---------------------------------------------------------- market data
    def _on_book(self, t: BookTicker) -> None:
        self._book[t.symbol] = t
        if self.cfg.record_raw:
            self.store.append("raw", {"source": t.source, "stream": "bookTicker", "symbol": t.symbol,
                                      "b": t.bid_price, "B": t.bid_quantity, "a": t.ask_price, "A": t.ask_quantity,
                                      "u": t.update_id, "E": t.exchange_time.isoformat()}, at=t.received_at)
        row = self._book_agg.add(t.symbol, t.received_at, ticker_row(t))
        if row:
            self.store.append("normalized", {**row, "source": t.source}, at=t.received_at)
        self._handle_fills(self.broker.on_book(t))
        trade = self.open_trades.get(t.symbol)
        if trade is not None and trade.state == "open":
            self._manage_open_trade(trade, t)

    def _on_depth(self, d: DepthDelta) -> None:
        """Keeps a local order book per symbol and hands the broker a snapshot, so market orders fill through
        the real depth instead of the best quote only. Deltas are never journaled (100 ms stream); only a
        sequence gap is, and a missing or unsynced book simply leaves the best-quote fallback in place."""
        if d.symbol not in self.symbols or self._depth_snapshot is None:
            return
        book = self._order_books.setdefault(d.symbol, LocalOrderBook(d.symbol))
        if book.needs_snapshot:  # no snapshot yet: buffer, then fetch one (throttled)
            book.apply(d)
            last = self._depth_last_sync_try.get(d.symbol)
            if last is None or d.received_at - last >= self.cfg.depth_resync_every:
                self._depth_last_sync_try[d.symbol] = d.received_at
                try:
                    snapshot = self._depth_snapshot(d.symbol)
                except Exception as exc:  # noqa: BLE001 -- REST trouble must never stop the paper loop
                    self.store.append("audit", {"event": "depth_snapshot_failed", "symbol": d.symbol,
                                                "detail": f"{type(exc).__name__}: {exc}"}, at=self._now)
                    return
                for ev in book.load_snapshot(snapshot):
                    self._journal_depth_gap(ev)
        else:
            ev = book.apply(d)
            if ev is not None:
                self._journal_depth_gap(ev)  # the book reset itself; the next delta triggers a fresh snapshot
        if not book.synced:
            return
        last_push = self._depth_last_push.get(d.symbol)
        if last_push is not None and d.received_at - last_push < self.cfg.depth_every:
            return
        self._depth_last_push[d.symbol] = d.received_at
        self.broker.on_depth(book.snapshot(d.received_at, depth=self.cfg.depth_levels))

    def _journal_depth_gap(self, ev: DataQualityEvent) -> None:
        # Not added to _recent_quality: a lost depth book only degrades fills to the best quote, it must not block trading.
        self.store.append("quality", {"kind": ev.kind, "symbol": ev.symbol, "detail": ev.detail,
                                      "blocks_trading": False, "source": ev.source,
                                      "event_type": "DATA_QUALITY_EVENT"}, at=self._now)

    def _on_trade(self, t: TradeTick) -> None:
        if self.cfg.record_raw:
            self.store.append("raw", {"source": t.source, "stream": "aggTrade", "symbol": t.symbol, "p": t.price,
                                      "q": t.quantity, "side": t.aggressor_side, "id": t.trade_id,
                                      "E": t.exchange_time.isoformat()}, at=t.received_at)
        flow = self._flow.get(t.symbol)
        if flow is not None:
            try:
                flow.add(t)
            except ValueError:
                self._recent_quality.append(DataQualityEvent("trade_out_of_order", t.symbol, t.received_at,
                                                             "trade older than the flow window head", t.source))
        for s in self._trade_agg.add(t):
            self.store.append("normalized", trade_stats_record(s, t.source), at=t.received_at)
        self._handle_fills(self.broker.on_trade(t))

    def _context(self, symbol: str, now: datetime) -> MarketContext:
        b = self._book.get(symbol)
        flow = self._flow.get(symbol)
        return MarketContext(
            book_imbalance=ticker_imbalance(b) if b else None,
            trade_imbalance=flow.imbalance(now) if flow else None,
            microprice=microprice(b.bid_price, b.ask_price, b.bid_quantity, b.ask_quantity) if b else None,
            mid_price=b.mid if b else None,
            spread=b.spread_fraction if b else None,
        )

    # ----------------------------------------------------------- decisions
    def _on_candle(self, c: Candle) -> None:
        bars = self._bars(c.market, c.timeframe.value)
        if bars and c.open_time <= bars[-1].open_time:
            return  # duplicate or late bar: never re-decide on it
        bars.append(c)
        self._journal_candle(c, via="stream")
        if c.market not in self.symbols:
            return
        history = list(bars)
        for sid, (strategy, version) in self.strategies.items():
            if strategy.timeframe == c.timeframe.value:
                self._evaluate(sid, version, strategy, c, history)
        self._mark_equity()

    def _journal_candle(self, c: Candle, *, via: str) -> None:
        # Bootstrap bars are journaled too (with their real source), so the learning cycle (ADR-0044) can
        # rebuild the exact history the strategies saw; readers dedupe by bar time.
        self.store.append("normalized", {"kind": "candle", "symbol": c.market, "source": c.source, "via": via,
                                         "timeframe": c.timeframe.value, "open_time": c.open_time.isoformat(),
                                         "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume},
                          at=c.received_at)

    def _account_state(self, symbol: str):
        now = self._clock()
        t = self.open_trades.get(symbol)
        book = self._book.get(symbol)
        notional = t.open_quantity * book.mid if (t and book) else 0.0
        return account_state_from_history(equity_points=self.equity_points, closed_trades=self.closed_trades,
                                          now=now, open_position_notional=notional, keep=self.cfg.history_keep)

    def _evaluate(self, sid: str, version: str, strategy, bar: Candle, history: list) -> None:
        now = self._clock()
        symbol = bar.market
        features = candle_features(history)
        regime = classify_regime(history)
        context = self._context(symbol, now)
        signal = strategy.signal(history, context)
        trade = self.open_trades.get(symbol)
        action, reason, risk_info, cid = "hold", signal.reason or "no_signal", None, None
        quality_info = {}

        if trade is not None and trade.strategy_id == sid:
            trade.bars_held += 1
            wants_exit = (trade.direction > 0 and signal.exit_long) or (trade.direction < 0 and signal.exit_short)
            max_hold = getattr(strategy, "max_hold_bars", None)  # same knob the backtest engine enforces
            if trade.state == "open" and wants_exit:
                cid = self._submit_exit(trade, f"signal_exit:{signal.reason}")
                action, reason = "exit", f"signal_exit:{signal.reason}"
            elif (trade.state == "open" and trade.pending_exit is None and max_hold is not None
                  and trade.bars_held >= max_hold):
                stop_reason = _time_stop_reason(max_hold, bar.timeframe.delta)
                cid = self._submit_exit(trade, stop_reason)
                action, reason = "exit", stop_reason
        elif signal.entry != 0:
            cap = getattr(strategy, "max_entries_per_day", None)
            if trade is not None:
                action, reason = "blocked", f"symbol_position_owned_by:{trade.strategy_id}"
            elif cap is not None and self._entries_by_day.get(self._entry_day_key(sid, symbol, now), 0) >= cap:
                action, reason = "blocked", "entry_cap_per_day"  # same key the backtest engine reports
            else:
                action, reason, risk_info, cid, quality_info = self._try_entry(sid, version, strategy, bar, history,
                                                                              signal, features, regime)
        rec = decision_record(
            mode="paper", symbol=symbol, timeframe=bar.timeframe.value, bar_open_time=bar.open_time,
            strategy_id=sid, strategy_version=version, feature_version=FEATURE_VERSION,
            regime=regime.regime.value, regime_reason=regime.reason, features=features,
            signal={"entry": signal.entry, "exit_long": signal.exit_long, "exit_short": signal.exit_short,
                    "strength": signal.strength, "reason": signal.reason, "stop_distance": signal.stop_distance,
                    "take_profit_distance": signal.take_profit_distance,
                    "trailing_distance": signal.trailing_distance, "signal_regime": signal.regime,
                    "context": asdict(context), "features": signal.features or {}},
            quality=quality_info, action=action, reason=reason, risk=risk_info, client_order_id=cid,
            position_before=(trade.direction * trade.open_quantity) if trade else 0.0,
        )
        self.store.append("decision", rec, at=now)
        self.store.append("feature", {"symbol": symbol, "timeframe": bar.timeframe.value,
                                      "feature_version": FEATURE_VERSION, "features": features,
                                      "as_of": bar.close_time.isoformat(), "regime": regime.regime.value,
                                      "decision_id": rec["decision_id"]}, at=now)
        self.counters["decisions"] += 1
        if action == "blocked":
            self.counters["blocked"] += 1
        if action.startswith("enter") and cid:
            self.open_trades[symbol].decision_id = rec["decision_id"]

    @staticmethod
    def _entry_day_key(sid: str, symbol: str, now: datetime) -> str:
        return f"{sid}|{symbol}|{now.astimezone(timezone.utc).date().isoformat()}"

    def _try_entry(self, sid, version, strategy, bar, history, signal, features, regime):
        now = self._clock()
        symbol = bar.market
        book = self._book.get(symbol)
        health, health_reason = self.health.status(symbol, now)
        recent = [e for e in self._recent_quality
                  if e.symbol in (symbol, "*") and now - e.at <= self.cfg.quality_hold]
        gate = evaluate_data_quality(now=now, candles=history[-self.cfg.quality_window:], feed_health=health,
                                     book=book, recent_events=recent, limits=self.cfg.quality_limits)
        quality_info = {"allowed": gate.allowed, "reasons": list(gate.reasons), "feed_health": health.value,
                        "feed_detail": health_reason}
        killed, kill_reason = self.kill_switch_engaged()
        req = EntryRequest(
            now=now, symbol=symbol, direction=signal.entry, reference_price=book.mid if book else None,
            stop_distance=signal.stop_distance, regime=signal.regime, atr=features.get("atr_14"),
            spread_fraction=book.spread_fraction if book else None, feed_health=health,
            data_quality_reasons=tuple(gate.reasons), kill_switch_engaged=killed, strategy_id=sid,
        )
        account = self._account_state(symbol)
        decision = self.risk.evaluate_entry(req, account)
        risk_info = {"decision_id": decision.decision_id, "approved": decision.approved,
                     "reasons": list(decision.reasons), "quantity": decision.quantity, "notional": decision.notional,
                     "leverage": decision.leverage, "risk_amount": decision.risk_amount,
                     "stop_price": decision.stop_price, "config_version": decision.config_version,
                     "kill_switch": kill_reason if killed else "off", "inputs": decision.inputs}
        if not decision.approved:
            return "blocked", ";".join(decision.reasons), risk_info, None, quality_info
        if self.margin_policy is not None:
            from cointrader.risk.margin_policy import liquidation_vs_stop_reason
            liq = liquidation_vs_stop_reason(
                direction=signal.entry, entry_price=book.mid if book else float("nan"),
                stop_price=decision.stop_price if decision.stop_price else float("nan"),
                quantity=decision.quantity, tiers=self.margin_tiers.get(symbol), policy=self.margin_policy)
            if liq:
                risk_info = {**risk_info, "approved": False, "reasons": [liq]}
                return "blocked", liq, risk_info, None, quality_info
        if not self.ready:
            return "blocked", "startup_reconciliation_not_passed", risk_info, None, quality_info
        side = "BUY" if signal.entry > 0 else "SELL"
        filters = self.filters[symbol]
        tp = None
        if signal.take_profit_distance:
            tp = filters.round_price(book.mid + signal.entry * signal.take_profit_distance, up=signal.entry < 0)
        intent = OrderIntent(
            strategy_id=sid, candidate_id=f"{sid}@{version}", symbol=symbol, side=side, position_side="BOTH",
            order_type="MARKET", quantity=decision.quantity, price=None, reduce_only=False,
            stop_price=decision.stop_price, take_profit=tp, reason=signal.reason, signal_timestamp=now,
            data_timestamp=bar.close_time, config_version=self.risk.config_version,
            risk_decision_id=decision.decision_id, purpose="entry", mode="paper",
        )
        result = self.engine.execute(intent)
        self._record_order(intent, result)
        if not result.submitted or result.status is None or result.status.state is OrderState.REJECTED:
            return "blocked", f"execution:{result.detail}", risk_info, intent.client_order_id, quality_info
        self.trade_seq += 1
        half_spread = book.spread_fraction / 2 if book else None
        self.open_trades[symbol] = OpenTrade(
            seq=self.trade_seq, strategy_id=sid, strategy_version=version, timeframe=bar.timeframe.value,
            symbol=symbol, direction=signal.entry, entry_cid=intent.client_order_id, decision_id="",
            risk_decision_id=decision.decision_id, signal_reason=signal.reason, regime=signal.regime,
            features=features, stop_distance=decision.stop_distance, tp_distance=signal.take_profit_distance,
            trail_distance=signal.trailing_distance, trail_activation=signal.trailing_activation,
            equity_at_entry=account.equity,
            decided_at=now.isoformat(), expected_entry_slippage=half_spread,
        )
        self.counters["entries"] += 1
        day_key = self._entry_day_key(sid, symbol, now)
        today = day_key.rsplit("|", 1)[1]
        self._entries_by_day = {k: v for k, v in self._entries_by_day.items() if k.endswith(today)}  # drop older days
        self._entries_by_day[day_key] = self._entries_by_day.get(day_key, 0) + 1
        self.notifier.notify(Severity.TRADE, f"entry {side} {symbol}",
                             f"{sid} qty={decision.quantity} stop={decision.stop_price} ({signal.reason})", at=now)
        return ("enter_long" if signal.entry > 0 else "enter_short"), signal.reason, risk_info, \
            intent.client_order_id, quality_info

    # ------------------------------------------------------------ execution
    def _record_order(self, intent: OrderIntent, result) -> None:
        self.store.append("execution", {"event": "order_submit", "client_order_id": intent.client_order_id,
                                        "symbol": intent.symbol, "mode": "paper", "intent": intent.to_dict(),
                                        "submitted": result.submitted, "detail": result.detail,
                                        "state": result.status.state.value if result.status else None},
                          at=self._clock())

    def _sync(self, cid: str, symbol: str):
        st = self.broker.query(symbol, cid)
        known = self.orders.state(cid)
        if known is not st.state:
            self.orders.append(cid, st.state, self._clock(), symbol=symbol, detail=st.detail,
                               filled_quantity=st.filled_quantity, average_price=st.average_price)
        return st

    def _handle_fills(self, fills: list[Fill]) -> None:
        if not fills:
            return
        touched = set()
        for f in fills:
            self.store.append("execution", {"event": "fill", "client_order_id": f.client_order_id,
                                            "symbol": f.symbol, "mode": "paper", "side": f.side,
                                            "quantity": f.quantity, "price": f.price, "fee": f.fee,
                                            "liquidity": f.liquidity, "reference_price": f.reference_price,
                                            "slippage": f.slippage}, at=f.at)
            t = self.open_trades.get(f.symbol)
            if t is None:
                continue
            book = self._book.get(f.symbol)
            half_spread_abs = (book.ask_price - book.bid_price) / 2 if book else 0.0
            ref = f.reference_price or f.price
            if f.client_order_id == t.entry_cid:
                t.entry_qty += f.quantity
                t.entry_notional += f.quantity * f.price
                t.entry_ref_notional += f.quantity * ref
                t.entry_fees += f.fee
                t.entry_spread += f.quantity * half_spread_abs
                t.entry_time = t.entry_time or f.at.isoformat()
            elif f.client_order_id in (t.stop_cid, t.exit_cid):
                t.exit_qty += f.quantity
                t.exit_notional += f.quantity * f.price
                t.exit_ref_notional += f.quantity * ref
                t.exit_fees += f.fee
                t.exit_spread += f.quantity * half_spread_abs
                t.exit_time = f.at.isoformat()
                if f.client_order_id == t.stop_cid and not t.exit_reason:
                    t.exit_reason = "stop_loss"
            touched.add(f.symbol)
        for symbol in touched:
            self._advance(symbol)

    def _advance(self, symbol: str) -> None:
        t = self.open_trades.get(symbol)
        if t is None:
            return
        for cid in (t.entry_cid, t.stop_cid, t.exit_cid):
            if cid:
                self._sync(cid, symbol)
        entry = self.broker.query(symbol, t.entry_cid)
        if t.state == "entering" and entry.state.terminal:
            if t.entry_qty <= 0:
                self.store.append("execution", {"event": "entry_unfilled", "client_order_id": t.entry_cid,
                                                "symbol": symbol, "mode": "paper", "detail": entry.detail},
                                  at=self._clock())
                del self.open_trades[symbol]
                return
            avg = t.entry_notional / t.entry_qty
            t.best = t.worst = avg
            t.state = "open"
            self._place_protective_stop(t, avg)
        position = next((p.quantity for p in self.broker.positions() if p.symbol == symbol), 0.0)
        if t.state in ("open", "closing") and t.exit_qty > 0 and abs(position) < 1e-12:
            self._close_trade(t)
        elif t.state == "closing" and t.exit_cid and self.broker.query(symbol, t.exit_cid).state.terminal:
            t.state = "open"  # exit only partly filled (IOC remainder cancelled): retry on the next book update
            t.pending_exit = t.exit_reason or "retry_exit"

    def _place_protective_stop(self, t: OpenTrade, avg: float) -> None:
        f = self.filters[t.symbol]
        stop = f.round_price(avg - t.direction * t.stop_distance, up=t.direction < 0)
        t.stop_price = stop
        if t.tp_distance:
            t.tp_price = avg + t.direction * t.tp_distance
        intent = OrderIntent(
            strategy_id=t.strategy_id, candidate_id=f"{t.strategy_id}@{t.strategy_version}", symbol=t.symbol,
            side="SELL" if t.direction > 0 else "BUY", position_side="BOTH", order_type="STOP_MARKET",
            quantity=f.round_quantity(t.entry_qty) or t.entry_qty, price=None, reduce_only=True, stop_price=stop,
            take_profit=None, reason="protective_stop", signal_timestamp=self._clock(),
            data_timestamp=self._clock(), config_version=self.risk.config_version,
            risk_decision_id=t.risk_decision_id, purpose="stop", mode="paper",
        )
        result = self.engine.execute(intent)
        self._record_order(intent, result)
        if result.submitted and result.status and result.status.state is not OrderState.REJECTED:
            t.stop_cid = intent.client_order_id
        else:
            # no protective stop = exposure without a bound: close immediately
            self._safety("protective_stop_failed", f"{t.symbol}: {result.detail}; closing position",
                         Severity.CRITICAL, symbol=t.symbol)
            self._submit_exit(t, "protective_stop_failed")

    def _manage_open_trade(self, t: OpenTrade, book: BookTicker) -> None:
        mid = book.mid
        t.best = max(t.best, mid) if t.direction > 0 else min(t.best, mid)
        t.worst = min(t.worst, mid) if t.direction > 0 else max(t.worst, mid)
        if t.pending_exit:
            self._submit_exit(t, t.pending_exit)
            return
        if t.tp_price is not None and (mid - t.tp_price) * t.direction >= 0:
            self._submit_exit(t, "take_profit")
        elif t.trail_distance is not None and (t.best - mid) * t.direction >= t.trail_distance and (
                t.trail_activation is None or (t.best - t.entry_notional / t.entry_qty) * t.direction >= t.trail_activation):
            self._submit_exit(t, "trailing_stop")

    def _submit_exit(self, t: OpenTrade, reason: str) -> Optional[str]:
        position = next((p.quantity for p in self.broker.positions() if p.symbol == t.symbol), 0.0)
        if abs(position) < 1e-12:
            return None
        now = self._clock()
        intent = OrderIntent(
            strategy_id=t.strategy_id, candidate_id=f"{t.strategy_id}@{t.strategy_version}", symbol=t.symbol,
            side="SELL" if position > 0 else "BUY", position_side="BOTH", order_type="MARKET",
            quantity=abs(position), price=None, reduce_only=True, stop_price=None, take_profit=None, reason=reason,
            signal_timestamp=now, data_timestamp=now, config_version=self.risk.config_version,
            risk_decision_id=t.risk_decision_id, purpose="exit", mode="paper",
        )
        result = self.engine.execute(intent)
        self._record_order(intent, result)
        if result.submitted and result.status and result.status.state is not OrderState.REJECTED:
            t.exit_cid = intent.client_order_id
            t.exit_reason = reason
            t.state = "closing"
            t.pending_exit = None
            self.counters["exits"] += 1
            return intent.client_order_id
        t.pending_exit = reason  # retried on the next book update; the protective stop still stands
        self._safety("exit_not_sent", f"{t.symbol}: {result.detail}", symbol=t.symbol)
        return None

    def _close_trade(self, t: OpenTrade) -> None:
        now = self._clock()
        if t.stop_cid and not self.broker.query(t.symbol, t.stop_cid).state.terminal:
            self.broker.cancel(t.symbol, t.stop_cid)
            self._sync(t.stop_cid, t.symbol)
        qty = t.entry_qty
        entry_avg = t.entry_notional / t.entry_qty
        exit_avg = t.exit_notional / t.exit_qty
        entry_ref = t.entry_ref_notional / t.entry_qty
        exit_ref = t.exit_ref_notional / t.exit_qty
        gross = t.direction * (exit_ref - entry_ref) * min(qty, t.exit_qty)
        fees = t.entry_fees + t.exit_fees
        exec_cost = t.direction * ((entry_avg - entry_ref) * t.entry_qty + (exit_ref - exit_avg) * t.exit_qty)
        spread = t.entry_spread + t.exit_spread
        slippage = exec_cost - spread
        net = gross - fees - spread - slippage - t.funding
        ref = entry_ref
        if t.direction > 0:
            mfe, mae = max(t.best - ref, 0.0) / ref, max(ref - t.worst, 0.0) / ref
        else:
            mfe, mae = max(ref - t.best, 0.0) / ref, max(t.worst - ref, 0.0) / ref
        record = TradeRecord(
            trade_id=t.seq, strategy_id=t.strategy_id, symbol=t.symbol, direction=t.direction,
            entry_time=datetime.fromisoformat(t.entry_time), exit_time=datetime.fromisoformat(t.exit_time or now.isoformat()),
            entry_reference=entry_ref, exit_reference=exit_ref, entry_fill=entry_avg, exit_fill=exit_avg,
            quantity=qty, entry_liquidity="taker", exit_liquidity="taker", filled_fraction=1.0,
            gross_pnl=gross, fees=fees, spread_cost=spread, slippage_cost=slippage, funding=t.funding,
            net_pnl=net, equity_at_entry=t.equity_at_entry, mfe=mfe, mae=mae, holding_bars=t.bars_held,
            exit_reason=t.exit_reason or "unknown", regime_at_entry=t.regime, signal_reason=t.signal_reason,
            features_at_entry=t.features, risk_decision_id=t.risk_decision_id,
        )
        row = outcome_record(record, mode="paper", timeframe=t.timeframe, strategy_version=t.strategy_version,
                             feature_version=FEATURE_VERSION, entry_decision_id=t.decision_id,
                             entry_client_order_id=t.entry_cid, exit_client_order_id=t.exit_cid or t.stop_cid,
                             expected_entry_slippage=t.expected_entry_slippage)
        self.store.append("outcome", row, at=now)
        self.closed_trades.append(ClosedTrade(now, record.return_on_equity))
        del self.open_trades[t.symbol]
        self.counters["trades_closed"] += 1
        self._mark_equity()
        self.notifier.notify(Severity.TRADE, f"exit {t.symbol} ({record.exit_reason})",
                             f"{t.strategy_id} net={net:+.2f} gross={gross:+.2f} fees={fees:.2f}", at=now)
        self._check_drawdown_kill()

    def _mark_equity(self) -> None:
        now = self._clock()
        eq = self.broker.account().equity
        self.equity_points.append(EquityPoint(now, eq))
        cutoff = now - self.cfg.history_keep
        # keep the bounded window but always one point from before it (day-start / peak need it)
        while len(self.equity_points) > 2 and self.equity_points[1].at < cutoff:
            self.equity_points.pop(0)
        self.closed_trades = [c for c in self.closed_trades if c.closed_at >= cutoff]

    def _check_drawdown_kill(self) -> None:
        if not self.equity_points:
            return
        peak = max(p.equity for p in self.equity_points)
        eq = self.equity_points[-1].equity
        if peak > 0 and (peak - eq) / peak >= self.risk.config.max_drawdown:
            killed, _ = self.kill_switch_engaged()
            if not killed:
                self.kill_log.record(engage_kill_switch(reason=f"paper max drawdown {(peak - eq) / peak:.2%}",
                                                        occurred_at=self._clock(),
                                                        configuration_version=self.risk.config_version))
                self._safety("paper_kill_switch_engaged", "max drawdown breached; a human must review and release",
                             Severity.CRITICAL)

    # ------------------------------------------------------------- status
    def status(self) -> dict:
        now = self._now
        acct = self.broker.account()
        killed, kill_reason = self.kill_switch_engaged()
        return {
            "mode": "PAPER",
            "as_of": now.isoformat() if now else None,
            "ready": self.ready,
            "blocked_reason": self.engine.blocked_reason,
            "kill_switch": {"engaged": killed, "detail": kill_reason},
            "equity": acct.equity,
            "balance": acct.wallet_balance,
            "open_trades": {s: {"strategy": t.strategy_id, "direction": t.direction, "qty": t.open_quantity,
                                "state": t.state, "stop": t.stop_price} for s, t in self.open_trades.items()},
            "open_orders": len(self.broker.open_orders()),
            "feed_health": {s: self.health.status(s, now)[0].value for s in self.symbols} if now else {},
            "counters": dict(self.counters),
            "late_trades": self._trade_agg.late_trades,
        }
