"""Fail-closed position sizing for leveraged futures.

Builds on `risk/sizing.py`'s spot sizing (same inverse-volatility idea,
same max-weight cap) and adds a leverage layer, then refuses the trade
outright if the resulting liquidation price sits too close to entry --
a thin buffer means ordinary volatility, not a thesis being wrong, would
wipe the position out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from cointrader.risk.leverage import MarginTier, PositionSide, estimate_liquidation_price
from cointrader.risk.sizing import SizingConfig, size_spot_position


@dataclass(frozen=True)
class FuturesSizingConfig:
    spot: SizingConfig = SizingConfig()
    max_leverage: float = 3.0
    min_liquidation_buffer: float = 0.15  # liq price must be >= 15% away from entry


@dataclass(frozen=True)
class FuturesSizingResult:
    position_size: float  # base-asset quantity, 0.0 when not traded
    notional: float
    wallet_balance: float  # isolated margin posted
    liquidation_price: Optional[float]
    reason: str

    @property
    def trade_allowed(self) -> bool:
        return self.reason == "sized"


def size_futures_position(
    *,
    side: PositionSide,
    signal_exposure: Optional[float],
    equity: Optional[float],
    annual_volatility: Optional[float],
    entry_price: Optional[float],
    tiers: "list[MarginTier]",
    config: FuturesSizingConfig = FuturesSizingConfig(),
) -> FuturesSizingResult:
    def refuse(reason: str) -> FuturesSizingResult:
        return FuturesSizingResult(0.0, 0.0, 0.0, None, reason)

    if config.max_leverage <= 0:
        return refuse("max_leverage_not_configured")
    if not 0.0 < config.min_liquidation_buffer < 1.0:
        return refuse("liquidation_buffer_not_configured")
    if entry_price is None or not math.isfinite(entry_price) or entry_price <= 0:
        return refuse("entry_price_unknown")

    spot = size_spot_position(
        signal_exposure=signal_exposure,
        equity=equity,
        annual_volatility=annual_volatility,
        config=config.spot,
    )
    if not spot.trade_allowed:
        return refuse(spot.reason)
    if spot.target_value == 0.0:
        return FuturesSizingResult(0.0, 0.0, 0.0, None, "sized")

    wallet_balance = spot.target_value
    notional = wallet_balance * config.max_leverage
    position_size = notional / entry_price

    try:
        liq_price = estimate_liquidation_price(
            side=side,
            entry_price=entry_price,
            position_size=position_size,
            wallet_balance=wallet_balance,
            tiers=tiers,
        )
    except ValueError:
        return refuse("liquidation_price_unavailable")

    distance = abs(liq_price - entry_price) / entry_price
    if distance < config.min_liquidation_buffer:
        return refuse("liquidation_too_close")

    return FuturesSizingResult(position_size, notional, wallet_balance, liq_price, "sized")
