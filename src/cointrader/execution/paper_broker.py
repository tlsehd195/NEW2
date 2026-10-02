"""PaperBroker: simulated exchange driven by real market data.

Same `Broker` interface as `LiveBroker`; the engine, risk and journal
code above it cannot tell them apart except by `mode`.

Fill model (conservative, stated):

- MARKET orders are not filled on submit. They fill on the first book
  update that arrives at least `latency` after submission (latency), at
  the book at that moment: against a full depth snapshot if one is
  available (`backtest.costs.simulate_market_fill`, level by level), else
  only up to the best level's size at the best price (the rest is
  cancelled, IOC-style: partial fill). Taker fee.
- LIMIT orders rest; they fill (maker fee) only when a trade prints
  THROUGH the limit (strictly better than it), for at most that trade's
  size -- partial fills and missed fills both happen.
- STOP_MARKET / TAKE_PROFIT_MARKET (reduce-only) trigger on the trade
  price crossing the stop and then fill like a MARKET order.
- No market data yet, or the last book older than `max_book_age`: the
  order is REJECTED ("no_fresh_market_data"), never filled at a guess.
- Funding: at each funding time from the mark-price stream, an open
  position pays/receives notional * rate.

State (balances, positions, resting orders, processed ids) round-trips
through `to_dict` / `from_dict` for restart recovery.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from cointrader.backtest.costs import Side, simulate_market_fill
from cointrader.data.market_events import BookTicker, MarkPriceUpdate, TradeTick
from cointrader.data.models import OrderBookSnapshot
from cointrader.execution.models import AccountSnapshot, Fill, OrderIntent, OrderState, OrderStatus, PositionSnapshot
from cointrader.live.config import HealthStatus


@dataclass
class _Resting:
    intent: OrderIntent
    submitted_at: datetime
    filled: float = 0.0
    notional: float = 0.0
    triggered: bool = False
    fills: list = field(default_factory=list)


@dataclass
class _Pos:
    quantity: float = 0.0  # signed
    entry_price: float = 0.0
    realized: float = 0.0
    fees: float = 0.0
    funding: float = 0.0


class PaperBroker:
    mode = "paper"

    def __init__(self, *, initial_balance: float, taker_fee: float = 0.0005, maker_fee: float = 0.0002,
                 latency: timedelta = timedelta(milliseconds=250), max_book_age: timedelta = timedelta(seconds=5)) -> None:
        self.balance = initial_balance
        self.taker_fee = taker_fee
        self.maker_fee = maker_fee
        self.latency = latency
        self.max_book_age = max_book_age
        self._book: dict[str, BookTicker] = {}
        self._depth: dict[str, OrderBookSnapshot] = {}
        self._mark: dict[str, float] = {}
        self._next_funding: dict[str, datetime] = {}
        self._orders: dict[str, _Resting] = {}
        self._status: dict[str, OrderStatus] = {}
        self._pos: dict[str, _Pos] = {}
        self.fill_log: list[Fill] = []
        self.now: Optional[datetime] = None

    # --- market data -------------------------------------------------------
    def on_book(self, t: BookTicker) -> list[Fill]:
        self._book[t.symbol] = t
        self.now = t.received_at
        return self._fill_market_orders(t.symbol)

    def on_depth(self, snapshot: OrderBookSnapshot) -> None:
        self._depth[snapshot.market] = snapshot

    def on_trade(self, t: TradeTick) -> list[Fill]:
        self.now = t.received_at
        fills = []
        for cid, o in list(self._orders.items()):
            it = o.intent
            if it.symbol != t.symbol or self._status[cid].state.terminal:
                continue
            if it.order_type == "LIMIT":
                through = t.price < it.price if it.side == "BUY" else t.price > it.price
                if through and t.exchange_time >= o.submitted_at + self.latency:
                    qty = min(it.quantity - o.filled, t.quantity)
                    fills.append(self._fill(cid, qty, it.price, "maker", t.received_at, it.price))
            elif it.order_type in ("STOP_MARKET", "TAKE_PROFIT_MARKET") and not o.triggered:
                stop = it.stop_price
                if it.order_type == "STOP_MARKET":
                    hit = t.price <= stop if it.side == "SELL" else t.price >= stop
                else:
                    hit = t.price >= stop if it.side == "SELL" else t.price <= stop
                if hit:
                    o.triggered = True
                    o.submitted_at = t.received_at  # becomes a market order now
        return fills

    def on_mark_price(self, m: MarkPriceUpdate) -> Optional[float]:
        """Applies funding when a funding time has passed; returns the
        amount paid (+) / received (-) or None."""
        self._mark[m.symbol] = m.mark_price
        due = self._next_funding.get(m.symbol)
        paid = None
        if due is not None and m.exchange_time >= due and m.funding_rate is not None:
            pos = self._pos.get(m.symbol)
            if pos and pos.quantity:
                paid = pos.quantity * m.mark_price * m.funding_rate  # long pays when rate > 0
                pos.funding += paid
                self.balance -= paid
        if m.next_funding_time is not None:
            self._next_funding[m.symbol] = m.next_funding_time
        return paid

    # --- broker interface --------------------------------------------------
    def health(self) -> HealthStatus:
        return HealthStatus.HEALTHY if self._book else HealthStatus.UNKNOWN

    def submit(self, intent: OrderIntent) -> OrderStatus:
        cid = intent.client_order_id
        if cid in self._status:
            return self._status[cid]  # idempotent at the "exchange" too
        book = self._book.get(intent.symbol)
        now = self.now
        if book is None or now is None or now - book.received_at > self.max_book_age:
            st = OrderStatus(cid, OrderState.REJECTED, detail="no_fresh_market_data")
            self._status[cid] = st
            return st
        pos = self._pos.get(intent.symbol, _Pos())
        if intent.reduce_only:
            closing = -1 if intent.side == "SELL" else 1
            if pos.quantity == 0 or (pos.quantity > 0) == (closing > 0):
                st = OrderStatus(cid, OrderState.REJECTED, detail="reduce_only_would_increase_position")
                self._status[cid] = st
                return st
        self._orders[cid] = _Resting(intent, now)
        st = OrderStatus(cid, OrderState.NEW, detail="accepted")
        self._status[cid] = st
        return st

    def cancel(self, symbol: str, client_order_id: str) -> OrderStatus:
        st = self._status.get(client_order_id)
        if st is None:
            return OrderStatus(client_order_id, OrderState.REJECTED, detail="unknown order")
        if st.state.terminal:
            return st
        self._orders.pop(client_order_id, None)
        st = OrderStatus(client_order_id, OrderState.CANCELED, st.filled_quantity, st.average_price, detail="canceled",
                         fills=st.fills)
        self._status[client_order_id] = st
        return st

    def query(self, symbol: str, client_order_id: str) -> OrderStatus:
        return self._status.get(client_order_id) or OrderStatus(client_order_id, OrderState.REJECTED,
                                                                 detail="order not found (never placed)")

    def open_orders(self, symbol: Optional[str] = None) -> list[OrderStatus]:
        return [self._status[c] for c, o in self._orders.items()
                if not self._status[c].state.terminal and (symbol is None or o.intent.symbol == symbol)]

    def positions(self) -> list[PositionSnapshot]:
        return [PositionSnapshot(s, p.quantity, p.entry_price if p.quantity else None) for s, p in sorted(self._pos.items())]

    def unrealized(self) -> float:
        total = 0.0
        for s, p in self._pos.items():
            if p.quantity:
                px = self._mark.get(s) or (self._book[s].mid if s in self._book else p.entry_price)
                total += p.quantity * (px - p.entry_price)
        return total

    def account(self) -> AccountSnapshot:
        eq = self.balance + self.unrealized()
        return AccountSnapshot(self.balance, eq, eq, self.now or datetime.min)

    # --- internals -----------------------------------------------------------
    def _fill_market_orders(self, symbol: str) -> list[Fill]:
        book = self._book[symbol]
        fills = []
        for cid, o in list(self._orders.items()):
            it = o.intent
            if it.symbol != symbol or self._status[cid].state.terminal:
                continue
            is_market = it.order_type == "MARKET" or o.triggered
            if not is_market or book.received_at < o.submitted_at + self.latency:
                continue
            remaining = it.quantity - o.filled
            if it.reduce_only:
                remaining = min(remaining, abs(self._pos.get(symbol, _Pos()).quantity))
            if remaining <= 0:
                self._finish(cid, OrderState.CANCELED, "nothing left to reduce")
                continue
            depth = self._depth.get(symbol)
            side = Side.BUY if it.side == "BUY" else Side.SELL
            ref = book.mid
            if depth is not None and abs((depth.as_of - book.received_at).total_seconds()) < self.max_book_age.total_seconds():
                r = simulate_market_fill(depth, side, remaining, fee_rate=0.0)
                if r.filled_quantity > 0:
                    fills.append(self._fill(cid, r.filled_quantity, r.average_price, "taker", book.received_at, ref))
            else:
                price, size = (book.ask_price, book.ask_quantity) if it.side == "BUY" else (book.bid_price, book.bid_quantity)
                qty = min(remaining, size)
                if qty > 0:
                    fills.append(self._fill(cid, qty, price, "taker", book.received_at, ref))
            st = self._status[cid]
            if not st.state.terminal:  # IOC: the unfilled rest of a market order is cancelled
                if st.filled_quantity > 0:
                    self._finish(cid, OrderState.CANCELED, "partial fill; remainder cancelled (IOC)")
                else:
                    self._finish(cid, OrderState.EXPIRED, "no liquidity at the book")
        return fills

    def _finish(self, cid: str, state: OrderState, detail: str) -> None:
        st = self._status[cid]
        self._status[cid] = OrderStatus(cid, state, st.filled_quantity, st.average_price, st.exchange_order_id, detail,
                                        st.fills)
        if state.terminal:
            self._orders.pop(cid, None)

    def _fill(self, cid: str, qty: float, price: float, liquidity: str, at: datetime, ref: float) -> Fill:
        o = self._orders[cid]
        it = o.intent
        fee = qty * price * (self.taker_fee if liquidity == "taker" else self.maker_fee)
        fill = Fill(cid, it.symbol, it.side, qty, price, fee, liquidity, at, ref)
        o.filled += qty
        o.notional += qty * price
        o.fills.append(fill)
        self.fill_log.append(fill)
        self._apply_to_position(it.symbol, qty if it.side == "BUY" else -qty, price, fee)
        state = OrderState.FILLED if o.filled >= it.quantity - 1e-12 else OrderState.PARTIALLY_FILLED
        self._status[cid] = OrderStatus(cid, state, o.filled, o.notional / o.filled, None,
                                        "filled" if state is OrderState.FILLED else "partially filled", tuple(o.fills))
        if state is OrderState.FILLED:
            self._orders.pop(cid, None)
        return fill

    def _apply_to_position(self, symbol: str, signed_qty: float, price: float, fee: float) -> None:
        p = self._pos.setdefault(symbol, _Pos())
        p.fees += fee
        self.balance -= fee
        if p.quantity == 0 or (p.quantity > 0) == (signed_qty > 0):
            new_qty = p.quantity + signed_qty
            p.entry_price = (p.entry_price * abs(p.quantity) + price * abs(signed_qty)) / abs(new_qty)
            p.quantity = new_qty
            return
        closing = min(abs(signed_qty), abs(p.quantity))
        pnl = closing * (price - p.entry_price) * (1 if p.quantity > 0 else -1)
        p.realized += pnl
        self.balance += pnl
        remaining = abs(signed_qty) - closing
        p.quantity += math.copysign(closing, signed_qty)
        if abs(p.quantity) < 1e-12:
            p.quantity, p.entry_price = 0.0, 0.0
        if remaining > 1e-12:  # flipped through zero
            p.quantity = math.copysign(remaining, signed_qty)
            p.entry_price = price

    # --- persistence ---------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "balance": self.balance,
            "positions": {s: vars(p) for s, p in self._pos.items()},
            "resting": [{"intent": o.intent.to_dict(), "submitted_at": o.submitted_at.isoformat(), "filled": o.filled,
                         "notional": o.notional, "triggered": o.triggered} for o in self._orders.values()],
            "statuses": {c: {"state": s.state.value, "filled": s.filled_quantity, "avg": s.average_price,
                             "detail": s.detail} for c, s in self._status.items()},
            "next_funding": {s: t.isoformat() for s, t in self._next_funding.items()},
        }

    @classmethod
    def from_dict(cls, d: dict, **kw) -> "PaperBroker":
        b = cls(initial_balance=d["balance"], **kw)
        b._pos = {s: _Pos(**p) for s, p in d["positions"].items()}
        for c, s in d["statuses"].items():
            b._status[c] = OrderStatus(c, OrderState(s["state"]), s["filled"], s["avg"], None, s["detail"])
        for r in d["resting"]:
            i = dict(r["intent"])
            i["signal_timestamp"] = datetime.fromisoformat(i["signal_timestamp"])
            i["data_timestamp"] = datetime.fromisoformat(i["data_timestamp"])
            intent = OrderIntent(**i)
            b._orders[intent.client_order_id] = _Resting(intent, datetime.fromisoformat(r["submitted_at"]), r["filled"],
                                                         r["notional"], r["triggered"])
        b._next_funding = {s: datetime.fromisoformat(t) for s, t in d.get("next_funding", {}).items()}
        return b
