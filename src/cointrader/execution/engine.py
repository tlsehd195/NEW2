"""Execution engine: idempotent submission over any `Broker`.

Duplicate-order prevention, in order:

1. the `client_order_id` is deterministic (same decision -> same id);
2. before sending, the order is written to the local append-only order
   store as PENDING_SUBMIT (so a crash between "send" and "record" still
   leaves a trace);
3. an id already in the store is never sent again -- its state is
   queried instead;
4. if the send raises (timeout, connection reset), the outcome is
   UNKNOWN: the engine queries the broker by client id; if that also
   fails the order stays UNKNOWN and EVERY new submission is blocked
   until reconciliation resolves it ("do not submit another order");
5. a failed reconciliation (`execution.reconciliation`) also blocks new
   submissions until a clean reconciliation.

Reduce-only exits are subject to the same idempotency but not to the
reconciliation block's "no new risk" intent -- they are still refused
while an order is UNKNOWN, because sending a second exit on top of an
unknown one could over-close or flip the position.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Protocol

from cointrader.execution.models import (
    AccountSnapshot,
    OrderIntent,
    OrderState,
    OrderStatus,
    PositionSnapshot,
)
from cointrader.live.config import HealthStatus


class Broker(Protocol):
    mode: str

    def submit(self, intent: OrderIntent) -> OrderStatus: ...

    def cancel(self, symbol: str, client_order_id: str) -> OrderStatus: ...

    def query(self, symbol: str, client_order_id: str) -> OrderStatus: ...

    def open_orders(self, symbol: Optional[str] = None) -> list[OrderStatus]: ...

    def positions(self) -> list[PositionSnapshot]: ...

    def account(self) -> AccountSnapshot: ...

    def health(self) -> HealthStatus: ...


class OrderBlocked(RuntimeError):
    pass


class OrderStore:
    """Append-only JSONL of order events; the latest event per client id
    is the order's local state. Never rewritten."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._latest: dict[str, dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self._latest[row["client_order_id"]] = row

    def append(self, client_order_id: str, state: OrderState, at: datetime, **extra) -> None:
        row = {"client_order_id": client_order_id, "state": state.value, "at": at.isoformat(), **extra}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        prev = self._latest.get(client_order_id, {})
        self._latest[client_order_id] = {**prev, **row}

    def get(self, client_order_id: str) -> Optional[dict]:
        return self._latest.get(client_order_id)

    def state(self, client_order_id: str) -> Optional[OrderState]:
        row = self._latest.get(client_order_id)
        return OrderState(row["state"]) if row else None

    def unresolved(self) -> list[dict]:
        return [r for r in self._latest.values()
                if OrderState(r["state"]) in (OrderState.UNKNOWN, OrderState.PENDING_SUBMIT)]

    def open(self) -> list[dict]:
        return [r for r in self._latest.values() if not OrderState(r["state"]).terminal]


@dataclass(frozen=True)
class ExecutionResult:
    client_order_id: str
    submitted: bool
    status: Optional[OrderStatus]
    detail: str


class ExecutionEngine:
    def __init__(self, broker: Broker, store: OrderStore, *, now: Callable[[], datetime]) -> None:
        self.broker = broker
        self.store = store
        self._now = now
        self._reconciliation_block: Optional[str] = None

    # reconciliation hooks ---------------------------------------------------
    def block(self, reason: str) -> None:
        self._reconciliation_block = reason

    def unblock_after_clean_reconciliation(self) -> None:
        """Only `reconciliation.reconcile` returning ok, with no unresolved
        orders left, may call this."""
        if self.store.unresolved():
            raise OrderBlocked("unresolved orders remain; reconcile them first")
        self._reconciliation_block = None

    @property
    def blocked_reason(self) -> Optional[str]:
        if self._reconciliation_block:
            return self._reconciliation_block
        unresolved = self.store.unresolved()
        if unresolved:
            return f"{len(unresolved)} order(s) in unknown state: {[r['client_order_id'] for r in unresolved][:3]}"
        return None

    def resolve_unknown(self) -> list[OrderStatus]:
        """Query the broker for every UNKNOWN / PENDING_SUBMIT order and
        record the answer. An order the broker has never seen is recorded
        as REJECTED (it was never placed)."""
        out = []
        for row in self.store.unresolved():
            cid = row["client_order_id"]
            try:
                status = self.broker.query(row.get("symbol", ""), cid)
            except (ConnectionError, TimeoutError, OSError) as exc:
                out.append(OrderStatus(cid, OrderState.UNKNOWN, detail=f"query failed: {exc}"))
                continue
            self.store.append(cid, status.state, self._now(), symbol=row.get("symbol"), detail=status.detail,
                              filled_quantity=status.filled_quantity, average_price=status.average_price,
                              exchange_order_id=status.exchange_order_id)
            out.append(status)
        return out

    # submission ---------------------------------------------------------------
    def execute(self, intent: OrderIntent) -> ExecutionResult:
        cid = intent.client_order_id
        if intent.mode != self.broker.mode:
            raise OrderBlocked(f"intent mode {intent.mode} does not match broker mode {self.broker.mode}")
        existing = self.store.state(cid)
        if existing is not None:
            return ExecutionResult(cid, False, None, f"duplicate: already {existing.value}; not resubmitted")
        reason = self.blocked_reason
        if reason:
            return ExecutionResult(cid, False, None, f"blocked: {reason}")
        preflight = getattr(self.broker, "preflight", None)
        refusal = preflight(intent) if preflight else None
        if refusal:
            return ExecutionResult(cid, False, None, f"refused before sending: {refusal}")
        self.store.append(cid, OrderState.PENDING_SUBMIT, self._now(), symbol=intent.symbol, intent=intent.to_dict())
        try:
            status = self.broker.submit(intent)
        except (ConnectionError, TimeoutError, OSError) as exc:
            self.store.append(cid, OrderState.UNKNOWN, self._now(), symbol=intent.symbol,
                              detail=f"submit outcome unknown: {type(exc).__name__}: {exc}")
            try:
                status = self.broker.query(intent.symbol, cid)
            except (ConnectionError, TimeoutError, OSError) as exc2:
                return ExecutionResult(cid, False, None, f"UNKNOWN after submit error; query failed too: {exc2}")
        self.store.append(cid, status.state, self._now(), symbol=intent.symbol, detail=status.detail,
                          filled_quantity=status.filled_quantity, average_price=status.average_price,
                          exchange_order_id=status.exchange_order_id,
                          fills=[{"qty": f.quantity, "price": f.price, "fee": f.fee, "liquidity": f.liquidity,
                                  "at": f.at.isoformat()} for f in status.fills])
        return ExecutionResult(cid, True, status, status.detail)
