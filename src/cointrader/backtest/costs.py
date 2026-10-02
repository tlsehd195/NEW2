"""Execution cost models.

Only the *frame* comes from tlsehd195/NEW-'s daily-bar `backtest/costs.py`.
For swing and especially scalping, fees + spread + slippage decide most
of the result, so a flat "fill at close" assumption always overstates
returns. Two models here:

- `simulate_market_fill`: walks a real order-book snapshot level by
  level, so the fill price reflects actual depth. Use it wherever book
  snapshots exist (live/paper, and recorded books for short-horizon
  research).
- `CandleCostModel`: for candle-only history (no recorded books), a
  deliberately conservative per-side cost = fee + half-spread + impact
  that grows with order size relative to the bar's traded value. Its
  defaults are assumptions, stated as such; calibrate them against
  `simulate_market_fill` on recorded books before trusting a backtest.

Upbit KRW market fee is 0.05% per side (as of this writing; check the
exchange's current fee page before relying on it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from cointrader.data.models import OrderBookSnapshot

UPBIT_KRW_FEE_RATE = 0.0005


class Side(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class FillResult:
    side: Side
    requested_quantity: float
    filled_quantity: float
    average_price: float  # before fees; NaN when nothing filled
    fee: float  # in quote currency
    slippage_vs_mid: float  # fraction, positive = worse than mid
    levels_consumed: int
    fully_filled: bool


def simulate_market_fill(book: OrderBookSnapshot, side: Side, quantity: float, *, fee_rate: float) -> FillResult:
    """Market order against the visible book. A quantity larger than the
    visible depth is filled only partially and reported as such
    (`fully_filled=False`) -- never extrapolated at the last price."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if fee_rate < 0:
        raise ValueError("fee_rate must be >= 0")
    levels = book.asks if side is Side.BUY else book.bids
    remaining = quantity
    notional = 0.0
    consumed = 0
    for level in levels:
        if remaining <= 0:
            break
        take = min(remaining, level.size)
        notional += take * level.price
        remaining -= take
        consumed += 1
    filled = quantity - remaining
    if filled <= 0:
        return FillResult(side, quantity, 0.0, math.nan, 0.0, math.nan, 0, False)
    avg = notional / filled
    mid = book.mid
    slippage = (avg - mid) / mid if side is Side.BUY else (mid - avg) / mid
    return FillResult(
        side=side,
        requested_quantity=quantity,
        filled_quantity=filled,
        average_price=avg,
        fee=notional * fee_rate,
        slippage_vs_mid=slippage,
        levels_consumed=consumed,
        fully_filled=remaining <= 1e-12 * quantity,
    )


@dataclass(frozen=True)
class CandleCostModel:
    """Per-side cost as a fraction of traded notional:

        fee_rate + half_spread + impact_coefficient * sqrt(order_value / bar_traded_value)

    The square-root impact form is a common empirical approximation, not
    a claim about any specific market. Defaults are conservative
    placeholders for a liquid KRW major (half-spread 2bp, impact 10%*sqrt).
    An order larger than `max_participation` of the bar's traded value is
    refused (fail-closed) rather than priced."""

    fee_rate: float = UPBIT_KRW_FEE_RATE
    half_spread: float = 0.0002
    impact_coefficient: float = 0.1
    max_participation: float = 0.05

    def cost_fraction(self, order_value: float, bar_traded_value: float) -> float:
        if order_value < 0:
            raise ValueError("order_value must be >= 0")
        if order_value == 0:
            return 0.0
        if bar_traded_value <= 0:
            raise ValueError("cannot price an order against a bar with no traded value")
        participation = order_value / bar_traded_value
        if participation > self.max_participation:
            raise ValueError(
                f"order is {participation:.2%} of the bar's traded value, above max_participation "
                f"{self.max_participation:.2%}"
            )
        return self.fee_rate + self.half_spread + self.impact_coefficient * math.sqrt(participation)
