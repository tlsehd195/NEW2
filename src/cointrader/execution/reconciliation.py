"""Local vs. exchange state reconciliation.

Run at start-up (before trading resumes) and periodically. Any mismatch
-- a position size that differs by more than one exchange step, a
position the other side does not know about, an order open on one side
only -- blocks new orders (`ExecutionEngine.block`) and is reported as
CRITICAL. Nothing is ever "fixed" by sending an order to make the numbers
agree: the mismatch is a fact to investigate, not a quantity to trade.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from cointrader.execution.models import OrderStatus, PositionSnapshot


@dataclass(frozen=True)
class ReconciliationReport:
    at: datetime
    ok: bool
    mismatches: tuple[str, ...] = field(default_factory=tuple)
    checked_symbols: tuple[str, ...] = field(default_factory=tuple)


def reconcile(
    *,
    at: datetime,
    local_positions: dict[str, float],
    exchange_positions: Iterable[PositionSnapshot],
    local_open_order_ids: Iterable[str],
    exchange_open_orders: Iterable[OrderStatus],
    step_sizes: dict[str, float],
    own_order_prefix: str = "ct_",
) -> ReconciliationReport:
    mismatches: list[str] = []
    exch = {p.symbol: p.quantity for p in exchange_positions}
    symbols = sorted(set(local_positions) | {s for s, q in exch.items() if q != 0})
    for s in symbols:
        local, remote = local_positions.get(s, 0.0), exch.get(s, 0.0)
        tol = step_sizes.get(s)
        if tol is None:
            mismatches.append(f"{s}: no step size known, cannot compare positions")
            continue
        if abs(local - remote) > tol / 2:
            mismatches.append(f"{s}: local position {local} != exchange {remote}")
    local_ids = set(local_open_order_ids)
    remote_ids = {o.client_order_id for o in exchange_open_orders}
    for cid in sorted(local_ids - remote_ids):
        mismatches.append(f"order {cid} open locally but not on the exchange")
    for cid in sorted(remote_ids - local_ids):
        who = "ours" if cid.startswith(own_order_prefix) else "NOT placed by this system"
        mismatches.append(f"order {cid} open on the exchange but not locally ({who})")
    return ReconciliationReport(at, not mismatches, tuple(mismatches), tuple(symbols))


def apply_to_engine(report: ReconciliationReport, engine) -> Optional[str]:
    """Block on mismatch; unblock only on a clean report."""
    if report.ok:
        engine.unblock_after_clean_reconciliation()
        return None
    reason = "reconciliation mismatch: " + "; ".join(report.mismatches[:5])
    engine.block(reason)
    return reason
