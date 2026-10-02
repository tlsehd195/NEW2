"""FIFO realized-gain accounting for tax reporting.

Pure lot accounting: every fill (buy or sell) is a `Fill`, already priced
in KRW (this module does no currency conversion — a caller trading on
Binance in USDT must convert to KRW at each fill's own time before
building a `Fill`, and say how it did so; that conversion is out of this
module's scope on purpose, same "one job" discipline as the rest of this
package).

**Korean crypto gains tax status is uncertain and this module does not
encode a tax rate.** As of this session's knowledge, 가상자산 양도소득세
has been postponed multiple times and its current effective date/rate is
not something this code can assert reliably — confirm with an accountant
or the current 소득세법 before filing anything. This module only computes
realized gain/loss per lot (FIFO) and totals by year; it is a bookkeeping
aid, not tax advice.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

from cointrader._time import require_aware


class FillSide(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class Fill:
    """One executed trade, already expressed in KRW."""

    asset: str  # e.g. "BTC"
    side: FillSide
    quantity: float
    price_krw: float  # per unit, before fee
    fee_krw: float
    executed_at: datetime
    venue: str  # "upbit" | "binance_futures" | ... — provenance, never blank
    external_id: str = ""  # exchange's own trade id, if any

    def __post_init__(self) -> None:
        require_aware("Fill.executed_at", self.executed_at)
        if self.quantity <= 0:
            raise ValueError("Fill.quantity must be positive")
        if self.price_krw < 0 or self.fee_krw < 0:
            raise ValueError("Fill.price_krw and Fill.fee_krw must be >= 0")
        if not self.asset:
            raise ValueError("Fill.asset must not be empty")
        if not self.venue:
            raise ValueError("Fill.venue must not be empty")


@dataclass(frozen=True)
class RealizedGain:
    asset: str
    quantity: float
    proceeds_krw: float  # sell price * quantity, minus the sell's pro-rated fee
    cost_basis_krw: float  # FIFO lot cost, plus that lot's pro-rated buy fee
    gain_krw: float
    opened_at: datetime
    closed_at: datetime
    sell_external_id: str = ""


class ShortSaleNotSupported(ValueError):
    """A sell with no matching prior lot. Spot accounting only; a short
    position (selling before buying, e.g. a futures short) needs its own
    accounting and must not be silently matched against the wrong lot."""


def compute_fifo_realized_gains(fills: Sequence[Fill]) -> list[RealizedGain]:
    """Processes `fills` in `executed_at` order (ties broken by input
    order), matching each SELL against the oldest open BUY lots first
    (FIFO). Raises `ShortSaleNotSupported` rather than guessing when a
    SELL has no open lot to match."""
    ordered = sorted(enumerate(fills), key=lambda pair: (pair[1].executed_at, pair[0]))
    lots: dict[str, deque] = {}  # asset -> deque[[qty_remaining, unit_cost, opened_at]]
    gains: list[RealizedGain] = []

    for _, fill in ordered:
        book = lots.setdefault(fill.asset, deque())
        if fill.side is FillSide.BUY:
            unit_cost = fill.price_krw + fill.fee_krw / fill.quantity
            book.append([fill.quantity, unit_cost, fill.executed_at])
            continue

        remaining = fill.quantity
        unit_proceeds = fill.price_krw - fill.fee_krw / fill.quantity
        while remaining > 1e-12:
            if not book:
                raise ShortSaleNotSupported(
                    f"{fill.asset}: sell of {fill.quantity} at {fill.executed_at.isoformat()} "
                    "has no open lot to match (FIFO spot accounting only)"
                )
            lot = book[0]
            take = min(remaining, lot[0])
            gains.append(RealizedGain(
                asset=fill.asset, quantity=take,
                proceeds_krw=take * unit_proceeds, cost_basis_krw=take * lot[1],
                gain_krw=take * (unit_proceeds - lot[1]),
                opened_at=lot[2], closed_at=fill.executed_at,
                sell_external_id=fill.external_id,
            ))
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-12:
                book.popleft()
    return gains


@dataclass(frozen=True)
class YearlySummary:
    year: int
    realized_gain_krw: float
    realized_loss_krw: float  # positive number = total losses
    net_krw: float
    trade_count: int


def summarize_by_year(gains: Sequence[RealizedGain]) -> list[YearlySummary]:
    """One row per calendar year (KST), ascending. A year with no closed
    lots is simply absent — never fabricated as zero."""
    from zoneinfo import ZoneInfo
    kst = ZoneInfo("Asia/Seoul")
    by_year: dict[int, list[RealizedGain]] = {}
    for g in gains:
        by_year.setdefault(g.closed_at.astimezone(kst).year, []).append(g)
    out = []
    for year in sorted(by_year):
        rows = by_year[year]
        gain = sum(g.gain_krw for g in rows if g.gain_krw > 0)
        loss = -sum(g.gain_krw for g in rows if g.gain_krw < 0)
        out.append(YearlySummary(year, gain, loss, gain - loss, len(rows)))
    return out
