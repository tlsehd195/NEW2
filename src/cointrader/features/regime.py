"""Deterministic market regime classification.

No strategy is assumed to work in every market. Every decision is tagged
with one of `Regime`, and strategies/risk may refuse to act in some.
`UNDEFINED` (not enough history, a non-finite input) never allows a new
entry -- that rule is enforced by `risk.engine.RiskEngine`, not left to
each strategy.

The rules are fixed, simple and written down here so they can be
pre-registered as part of a hypothesis (they are not tuned on results):

1. Volatility level = current 20-bar realised volatility vs. its own
   trailing distribution over the previous `vol_history` bars (the
   current bar is not in its own baseline). Above the
   `high_vol_percentile` -> HIGH_VOLATILITY (takes priority: the most
   dangerous state wins).
2. Trend = fast/slow EMA order plus the slow-EMA distance measured in
   ATRs. |distance| >= `trend_atr_multiple` with agreeing EMA order ->
   TREND_UP / TREND_DOWN.
3. Otherwise below the `low_vol_percentile` -> LOW_VOLATILITY, else RANGE.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from cointrader.data.models import Candle
from cointrader.features import indicators as ind

REGIME_VERSION = "1.0.0"


class Regime(Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNDEFINED = "UNDEFINED"


@dataclass(frozen=True)
class RegimeConfig:
    fast: int = 20
    slow: int = 50
    atr_period: int = 14
    vol_period: int = 20
    vol_history: int = 100
    high_vol_percentile: float = 0.9
    low_vol_percentile: float = 0.1
    trend_atr_multiple: float = 1.0

    @property
    def warmup(self) -> int:
        return max(4 * self.slow, 4 * self.atr_period + 1, self.vol_period + self.vol_history + 1) + 1


@dataclass(frozen=True)
class RegimeReading:
    regime: Regime
    reason: str
    values: dict = field(default_factory=dict)
    version: str = REGIME_VERSION


def _trailing_vols(closes: Sequence[float], period: int, count: int):
    """Realised vol (same definition as `indicators.realized_volatility`)
    of the `count` windows ending just BEFORE the current bar, via one
    pass of rolling sums instead of `count` separate recomputations."""
    need = period + count + 1
    if len(closes) < need:
        return None
    tail = closes[len(closes) - need:]
    if min(tail) <= 0:
        return None
    rets = [math.log(b / a) for a, b in zip(tail, tail[1:])]  # len = period + count
    out = []
    for end in range(period, period + count):  # window rets[end-period:end] ends before the current bar
        w = rets[end - period:end]
        m = math.fsum(w) / period
        out.append(math.sqrt(math.fsum((r - m) ** 2 for r in w) / (period - 1)))
    return out


def classify_regime(candles: Sequence[Candle], config: RegimeConfig = RegimeConfig()) -> RegimeReading:
    if len(candles) < config.warmup:
        return RegimeReading(Regime.UNDEFINED, f"insufficient_history_{len(candles)}<{config.warmup}")
    candles = candles[len(candles) - config.warmup:]  # every input below needs only this tail
    closes = [c.close for c in candles]
    fast, slow = ind.ema(closes, config.fast), ind.ema(closes, config.slow)
    atr = ind.atr(candles, config.atr_period)
    vol_now = ind.realized_volatility(closes, config.vol_period)
    history = _trailing_vols(closes, config.vol_period, config.vol_history)
    if history is None:
        return RegimeReading(Regime.UNDEFINED, "volatility_history_incomplete")
    if fast is None or slow is None or atr is None or vol_now is None or atr <= 0:
        return RegimeReading(Regime.UNDEFINED, "indicator_unavailable")
    vol_pct = sum(1 for h in history if h < vol_now) / len(history)
    distance_atr = (closes[-1] - slow) / atr
    values = {"ema_fast": fast, "ema_slow": slow, "atr": atr, "realized_vol": vol_now,
              "vol_percentile": vol_pct, "distance_atr": distance_atr}
    if vol_pct > config.high_vol_percentile:
        return RegimeReading(Regime.HIGH_VOLATILITY, f"vol_percentile_{vol_pct:.2f}", values)
    if distance_atr >= config.trend_atr_multiple and fast > slow:
        return RegimeReading(Regime.TREND_UP, f"above_slow_ema_{distance_atr:.2f}_atr", values)
    if distance_atr <= -config.trend_atr_multiple and fast < slow:
        return RegimeReading(Regime.TREND_DOWN, f"below_slow_ema_{distance_atr:.2f}_atr", values)
    if vol_pct < config.low_vol_percentile:
        return RegimeReading(Regime.LOW_VOLATILITY, f"vol_percentile_{vol_pct:.2f}", values)
    return RegimeReading(Regime.RANGE, "no_trend_normal_vol", values)
