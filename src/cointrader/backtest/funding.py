"""Funding fee application for a single futures position.

From Binance's docs (ADR-0004): `Funding Amount = Nominal Value of
Positions * Funding Rate`; a positive funding rate means longs pay
shorts, a negative one means shorts pay longs. This module only applies
a funding rate that is already known -- it never estimates one. Binance's
funding rate is itself derived from the premium index (impact bid/ask
vs. the price index), which this project has no data source for, so
guessing a rate from candles alone would be exactly the kind of silent
fail-open ADR-0004 rules out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from cointrader.risk.leverage import PositionSide


@dataclass(frozen=True)
class FundingPayment:
    notional: float
    funding_rate: float
    amount: float  # positive = paid out by the position holder, negative = received


def apply_funding_payment(
    *,
    side: PositionSide,
    position_size: float,
    mark_price: float,
    funding_rate: float,
) -> FundingPayment:
    """`funding_rate` must be the actual rate for this settlement (e.g.
    from a recorded historical funding-rate series); there is no
    default. Fail-closed on missing/non-finite input rather than
    treating an absent rate as zero."""
    if funding_rate is None or not math.isfinite(funding_rate):
        raise ValueError(f"invalid funding_rate: {funding_rate!r}")
    if not math.isfinite(position_size) or position_size <= 0:
        raise ValueError(f"invalid position_size: {position_size!r}")
    if not math.isfinite(mark_price) or mark_price <= 0:
        raise ValueError(f"invalid mark_price: {mark_price!r}")

    notional = position_size * mark_price
    raw = notional * funding_rate
    # Longs pay when funding_rate is positive; shorts pay when negative.
    amount = raw if side is PositionSide.LONG else -raw
    return FundingPayment(notional=notional, funding_rate=funding_rate, amount=amount)
