"""Signal-strategy contract shared by the swing and scalp families.

A `SignalStrategy` is a pure function of already-closed history (plus,
for scalping, an optional `MarketContext` of book/trade-flow values at
decision time). It returns a `Signal`: whether to enter long/short, and
whether an open long/short should exit, with the reason and the feature
values it used. It never sizes a position and never talks to an
exchange -- sizing is `risk.engine`, orders are `execution`.

Position state lives in the engine (backtest / paper / live all use the
same semantics), so the strategy stays stateless and both
`validation.integrity` checks (look-ahead, warm-up sensitivity) apply
to it directly:

- flat and `entry != 0`            -> open in that direction
- long and (`exit_long` or entry<0)  -> close (a reversal re-enters on a
  later decision; the engine never flips in one step)
- short and (`exit_short` or entry>0) -> close
- otherwise                          -> hold

`stop_distance` / `take_profit_distance` / `trailing_distance` are price
distances the engine turns into protective exits, and the risk engine
uses `stop_distance` for per-trade risk sizing. A strategy that cannot
compute them returns no entry (fail-closed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence

from cointrader.data.models import Candle
from cointrader.features.regime import Regime


@dataclass(frozen=True)
class MarketContext:
    """Microstructure at decision time. Every field optional; a strategy
    that needs one that is missing must not enter."""

    book_imbalance: Optional[float] = None
    trade_imbalance: Optional[float] = None
    microprice: Optional[float] = None
    mid_price: Optional[float] = None
    spread: Optional[float] = None


@dataclass(frozen=True)
class Signal:
    entry: int = 0  # +1 long, -1 short, 0 none
    exit_long: bool = False
    exit_short: bool = False
    strength: float = 0.0
    reason: str = ""
    stop_distance: Optional[float] = None
    take_profit_distance: Optional[float] = None
    trailing_distance: Optional[float] = None
    trailing_activation: Optional[float] = None  # trail only once the price has moved this far in favour
    regime: str = Regime.UNDEFINED.value
    features: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.entry not in (-1, 0, 1):
            raise ValueError("Signal.entry must be -1, 0 or 1")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError("Signal.strength must be within [0, 1]")
        if self.entry != 0:
            if self.stop_distance is None or not math.isfinite(self.stop_distance) or self.stop_distance <= 0:
                raise ValueError("an entry signal needs a positive finite stop_distance")
        for name in ("take_profit_distance", "trailing_distance", "trailing_activation"):
            v = getattr(self, name)
            if v is not None and (not math.isfinite(v) or v <= 0):
                raise ValueError(f"Signal.{name} must be positive and finite when set")
        if self.trailing_activation is not None and self.trailing_distance is None:
            raise ValueError("Signal.trailing_activation needs a trailing_distance")


NO_SIGNAL = Signal(reason="no_signal")


def flat(reason: str, *, regime: str = Regime.UNDEFINED.value, features: Optional[dict] = None,
         exit_long: bool = False, exit_short: bool = False) -> Signal:
    return Signal(0, exit_long, exit_short, 0.0, reason, regime=regime, features=features or {})


class SignalStrategy(Protocol):
    strategy_id: str  # unique, stable: name + version + parameters
    family: str  # "swing" | "scalp" | "daytrade"
    version: str
    timeframe: str  # Timeframe value it is designed for

    @property
    def warmup(self) -> int: ...

    @property
    def parameters(self) -> dict: ...

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal: ...
