"""Isolated-margin liquidation price for a single one-way futures position.

Grounded in Binance's own USDⓈ-M futures documentation (see
docs/decisions/ADR-0004-binance-futures-leverage-mechanics.md for the
source URLs and the exact relationships quoted from there):

    Maintenance Margin = Position Notional * Maintenance Margin Rate
                          - Maintenance Amount

Binance publishes the maintenance margin rate and maintenance amount as a
table of tiers keyed by position notional value, and only as an image for
the exact liquidation-price algebra -- so the formula below is this
session's own derivation from the relationship above, not a verbatim copy.
For an isolated-margin, one-way position, at the liquidation price the
wallet balance plus the position's own unrealized PNL equals the
maintenance margin:

    WB + UPNL(liq_price) = liq_price * size * MMR - maintenance_amount

Solving that for `liq_price` on each side gives the two formulas below.
Cross margin (maintenance/PNL shared across several open positions) is out
of scope -- ADR-0004 defers it until this project actually opens more than
one futures position at a time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True)
class MarginTier:
    """One row of Binance's per-symbol maintenance margin bracket table.
    `notional_floor`/`notional_cap` bound the position notional value
    (in quote currency, e.g. USDT) this tier applies to; `notional_cap`
    is exclusive, `None` means unbounded above."""

    notional_floor: float
    notional_cap: "float | None"
    maintenance_margin_rate: float
    maintenance_amount: float

    def __post_init__(self) -> None:
        if self.notional_floor < 0:
            raise ValueError("notional_floor must be >= 0")
        if self.notional_cap is not None and self.notional_cap <= self.notional_floor:
            raise ValueError("notional_cap must be > notional_floor")
        if not 0.0 <= self.maintenance_margin_rate < 1.0:
            raise ValueError("maintenance_margin_rate must be in [0, 1)")
        if self.maintenance_amount < 0:
            raise ValueError("maintenance_amount must be >= 0")

    def covers(self, notional: float) -> bool:
        if notional < self.notional_floor:
            return False
        return self.notional_cap is None or notional < self.notional_cap


class MarginTierNotFound(ValueError):
    """No supplied tier covers this position's notional value. Never
    guessed at or defaulted -- the caller must supply a bracket table
    that actually covers the size being traded (fail-closed, ADR-0004)."""


def find_maintenance_tier(tiers: "list[MarginTier]", notional: float) -> MarginTier:
    if not tiers:
        raise MarginTierNotFound("no margin tiers supplied")
    if not math.isfinite(notional) or notional < 0:
        raise ValueError(f"invalid notional: {notional!r}")
    for tier in tiers:
        if tier.covers(notional):
            return tier
    raise MarginTierNotFound(f"no tier covers notional {notional}")


def estimate_liquidation_price(
    *,
    side: PositionSide,
    entry_price: float,
    position_size: float,
    wallet_balance: float,
    tiers: "list[MarginTier]",
) -> float:
    """Isolated margin, one-way mode. `position_size` is a positive
    quantity of the base asset; `wallet_balance` is the isolated margin
    posted for this position (initial margin plus any extra added).

    Fail-closed: raises ValueError on any non-finite/non-positive input,
    and raises MarginTierNotFound rather than silently picking a nearby
    tier when none of the supplied tiers covers this position's notional.
    """
    if not math.isfinite(entry_price) or entry_price <= 0:
        raise ValueError(f"invalid entry_price: {entry_price!r}")
    if not math.isfinite(position_size) or position_size <= 0:
        raise ValueError(f"invalid position_size: {position_size!r}")
    if not math.isfinite(wallet_balance) or wallet_balance <= 0:
        raise ValueError(f"invalid wallet_balance: {wallet_balance!r}")

    notional = entry_price * position_size
    tier = find_maintenance_tier(tiers, notional)
    mmr = tier.maintenance_margin_rate
    maint_amount = tier.maintenance_amount

    if side is PositionSide.LONG:
        liq_price = (entry_price * position_size - wallet_balance - maint_amount) / (
            position_size * (1 - mmr)
        )
    else:
        liq_price = (entry_price * position_size + wallet_balance + maint_amount) / (
            position_size * (1 + mmr)
        )

    if liq_price <= 0:
        # Wallet balance alone (no leverage risk) exceeds what the tier's
        # maintenance requirement could ever trigger at a positive price.
        raise ValueError("wallet_balance is large enough that this position cannot be liquidated at a positive price")
    if side is PositionSide.LONG and liq_price >= entry_price:
        raise ValueError("computed long liquidation price is not below entry price; check wallet_balance/leverage")
    if side is PositionSide.SHORT and liq_price <= entry_price:
        raise ValueError("computed short liquidation price is not above entry price; check wallet_balance/leverage")
    return liq_price


def margin_ratio(
    *,
    side: PositionSide,
    entry_price: float,
    mark_price: float,
    position_size: float,
    wallet_balance: float,
    tiers: "list[MarginTier]",
) -> float:
    """Current maintenance-margin-to-equity ratio, 1.0 at liquidation.
    Used for monitoring/alerting only -- never for an automatic kill
    switch trigger (ADR-0004 explicitly defers that, and
    live/kill_switch.py stays a protected, human-only file)."""
    if not math.isfinite(mark_price) or mark_price <= 0:
        raise ValueError(f"invalid mark_price: {mark_price!r}")
    notional_at_entry = entry_price * position_size
    tier = find_maintenance_tier(tiers, notional_at_entry)
    maintenance_margin = mark_price * position_size * tier.maintenance_margin_rate - tier.maintenance_amount
    upnl = (
        (mark_price - entry_price) * position_size
        if side is PositionSide.LONG
        else (entry_price - mark_price) * position_size
    )
    equity = wallet_balance + upnl
    if equity <= 0:
        return math.inf
    return maintenance_margin / equity
