"""Exchange margin settings and the liquidation-vs-stop check (ADR-0037).

Two checks that make paper/live match what the backtest already assumes
(`backtest.event_engine.FuturesTerms`: isolated margin, exchange leverage
3x):

- `liquidation_vs_stop_reason`: an entry whose liquidation price is not
  comfortably beyond its stop is refused -- the stop, not the exchange,
  must be what closes a losing trade.
- `margin_settings_mismatches`: Binance's per-symbol margin type and
  leverage (`/fapi/v1/symbolConfig`, read-only) must equal the policy
  before any live order; Binance's default is cross margin at a high
  leverage, which would silently differ from every backtest.

Neither function changes an exchange setting or places an order. Not yet
wired into the paper/live entry path: that waits on the owner's A/B
choice in ADR-0037.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from cointrader.risk.leverage import MarginTier, MarginTierNotFound, PositionSide, estimate_liquidation_price


@dataclass(frozen=True)
class MarginPolicy:
    margin_type: str = "ISOLATED"  # Binance's spelling: ISOLATED | CROSSED
    exchange_leverage: int = 3
    min_liquidation_to_stop: float = 3.0  # liq distance must be >= this many stop distances

    def __post_init__(self) -> None:
        if self.margin_type not in ("ISOLATED", "CROSSED"):
            raise ValueError(f"margin_type must be ISOLATED or CROSSED: {self.margin_type!r}")
        if not 1 <= self.exchange_leverage <= 20:
            raise ValueError("exchange_leverage must be in [1, 20]")
        if not math.isfinite(self.min_liquidation_to_stop) or self.min_liquidation_to_stop < 1.0:
            raise ValueError("min_liquidation_to_stop must be >= 1")


def liquidation_vs_stop_reason(
    *, direction: int, entry_price: float, stop_price: float, quantity: float,
    tiers: Optional[Sequence[MarginTier]], policy: MarginPolicy,
) -> Optional[str]:
    """None = allowed; otherwise the refusal reason. Isolated margin only:
    the posted margin is notional / exchange_leverage. Unknown tiers,
    bad inputs, or a stop on the wrong side refuse (fail-closed)."""
    if policy.margin_type != "ISOLATED":
        return "liquidation_check_needs_isolated_margin"
    if direction not in (1, -1):
        return "direction_invalid"
    for v in (entry_price, stop_price, quantity):
        if not math.isfinite(v) or v <= 0:
            return "liquidation_inputs_invalid"
    stop_distance = (entry_price - stop_price) * direction
    if stop_distance <= 0:
        return "stop_on_wrong_side"
    if not tiers:
        return "margin_tiers_unknown"
    side = PositionSide.LONG if direction > 0 else PositionSide.SHORT
    try:
        liq = estimate_liquidation_price(
            side=side, entry_price=entry_price, position_size=quantity,
            wallet_balance=entry_price * quantity / policy.exchange_leverage, tiers=list(tiers),
        )
    except MarginTierNotFound:
        return "margin_tiers_unknown"
    except ValueError:
        return "liquidation_price_unavailable"
    if abs(entry_price - liq) < policy.min_liquidation_to_stop * stop_distance:
        return "liquidation_too_close_to_stop"
    return None


def margin_settings_mismatches(rows: Sequence[dict], symbols: Sequence[str], policy: MarginPolicy) -> list[str]:
    """`rows` = `/fapi/v1/symbolConfig` response. Empty list = every symbol
    matches. A symbol missing from the response is a mismatch."""
    by_symbol = {r.get("symbol"): r for r in rows}
    out = []
    for s in symbols:
        r = by_symbol.get(s)
        if r is None:
            out.append(f"{s}: no symbolConfig row")
            continue
        if str(r.get("marginType", "")).upper() != policy.margin_type:
            out.append(f"{s}: marginType {r.get('marginType')!r} != {policy.margin_type}")
        try:
            lev = int(r.get("leverage"))
        except (TypeError, ValueError):
            lev = None
        if lev != policy.exchange_leverage:
            out.append(f"{s}: leverage {r.get('leverage')!r} != {policy.exchange_leverage}")
    return out
