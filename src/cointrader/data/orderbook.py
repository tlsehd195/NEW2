"""Local order book maintained from a REST snapshot + diff-depth deltas.

Synchronisation follows Binance's documented procedure for USDⓈ-M
futures ("How to manage a local order book correctly"), as known to this
session (not re-verified live here, see `data.binance_ws`):

1. Buffer deltas from the stream, then fetch a REST snapshot
   (`lastUpdateId`).
2. Drop every buffered delta with `u < lastUpdateId`.
3. The first applied delta must satisfy `U <= lastUpdateId <= u`.
4. Every later delta's `pu` must equal the previous delta's `u`;
   otherwise the book is out of sync.

Out of sync is fail-closed: the book goes to `synced = False`, refuses to
answer `snapshot()`, and records a `DataQualityEvent("orderbook_sequence_gap")`.
The caller must re-snapshot; nothing is patched over. Memory is bounded
by `max_levels` per side (levels beyond it are dropped from the far end,
which never affects the top-of-book features).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from cointrader.data.market_events import DataQualityEvent, DepthDelta
from cointrader.data.models import OrderBookLevel, OrderBookSnapshot


@dataclass(frozen=True)
class DepthSnapshot:
    symbol: str
    last_update_id: int
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    as_of: datetime


class LocalOrderBook:
    def __init__(self, symbol: str, *, max_levels: int = 1000) -> None:
        self.symbol = symbol
        self._max_levels = max_levels
        self._bids: dict[float, float] = {}
        self._asks: dict[float, float] = {}
        self._last_u: Optional[int] = None
        self._snapshot_id: Optional[int] = None
        self._buffer: list[DepthDelta] = []
        self.synced = False
        self.last_update_at: Optional[datetime] = None

    @property
    def needs_snapshot(self) -> bool:
        """True until a REST snapshot is installed (and again after a sequence gap reset the book)."""
        return self._snapshot_id is None

    def reset(self) -> None:
        self._bids.clear()
        self._asks.clear()
        self._last_u = None
        self._snapshot_id = None
        self._buffer.clear()
        self.synced = False

    def _apply_levels(self, delta: DepthDelta) -> None:
        for book, levels in ((self._bids, delta.bids), (self._asks, delta.asks)):
            for price, qty in levels:
                if qty == 0:
                    book.pop(price, None)
                else:
                    book[price] = qty
        self._trim()

    def _trim(self) -> None:
        if len(self._bids) > self._max_levels:
            for p in sorted(self._bids)[: len(self._bids) - self._max_levels]:
                del self._bids[p]
        if len(self._asks) > self._max_levels:
            for p in sorted(self._asks, reverse=True)[: len(self._asks) - self._max_levels]:
                del self._asks[p]

    def _gap(self, at: datetime, detail: str) -> DataQualityEvent:
        self.reset()
        return DataQualityEvent("orderbook_sequence_gap", self.symbol, at, detail, "orderbook")

    def load_snapshot(self, snapshot: DepthSnapshot) -> list[DataQualityEvent]:
        """Install a REST snapshot and replay buffered deltas onto it."""
        if snapshot.symbol != self.symbol:
            raise ValueError("snapshot symbol mismatch")
        buffered = list(self._buffer)
        self.reset()
        self._bids = {p: q for p, q in snapshot.bids if q > 0}
        self._asks = {p: q for p, q in snapshot.asks if q > 0}
        self._snapshot_id = snapshot.last_update_id
        self.last_update_at = snapshot.as_of
        events: list[DataQualityEvent] = []
        for d in buffered:
            ev = self.apply(d)
            if ev is not None:
                events.append(ev)
                break
        return events

    def apply(self, delta: DepthDelta) -> Optional[DataQualityEvent]:
        if delta.symbol != self.symbol:
            raise ValueError("delta symbol mismatch")
        if self._snapshot_id is None:
            self._buffer.append(delta)  # waiting for a snapshot
            if len(self._buffer) > 10_000:
                self._buffer = self._buffer[-10_000:]
            return None
        if self._last_u is None:
            if delta.final_update_id < self._snapshot_id:
                return None  # older than the snapshot: drop (step 2)
            if not delta.first_update_id <= self._snapshot_id <= delta.final_update_id:
                return self._gap(delta.received_at,
                                 f"first delta U={delta.first_update_id} u={delta.final_update_id} does not bracket "
                                 f"snapshot lastUpdateId={self._snapshot_id}")
        else:
            expected_prev = self._last_u
            prev = delta.previous_final_update_id
            if prev is None:
                # Spot-style stream without `pu`: require contiguity via U.
                if delta.first_update_id != expected_prev + 1:
                    return self._gap(delta.received_at, f"U={delta.first_update_id} after u={expected_prev}")
            elif prev != expected_prev:
                return self._gap(delta.received_at, f"pu={prev} but previous u={expected_prev}")
        self._apply_levels(delta)
        self._last_u = delta.final_update_id
        self.last_update_at = delta.exchange_time
        if self._bids and self._asks and max(self._bids) >= min(self._asks):
            return self._gap(delta.received_at, "book crossed after applying delta")
        self.synced = True
        return None

    def snapshot(self, as_of: datetime, depth: int = 20) -> OrderBookSnapshot:
        if not self.synced:
            raise RuntimeError(f"{self.symbol} order book is not synchronised")
        bids = tuple(OrderBookLevel(p, self._bids[p]) for p in sorted(self._bids, reverse=True)[:depth])
        asks = tuple(OrderBookLevel(p, self._asks[p]) for p in sorted(self._asks)[:depth])
        return OrderBookSnapshot(self.symbol, as_of, bids, asks)
