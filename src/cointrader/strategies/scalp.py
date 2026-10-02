"""Scalping strategy family (1m / 3m / 5m / 15m).

Separate from swing on purpose: its own timeframes, its own candidate
grid, its own validation policy (`validation.policies.SCALP_POLICY`) and
its own costs (maker/taker, spread, latency, missed fills), which decide
most of any scalping result. Every class here is a research candidate
with no claimed edge.

Two kinds, never mixed within one candidate:

- **candle-confirmed** candidates use only closed bars, so they can be
  backtested on archived klines (`data.binance_vision`).
- **book-confirmed** candidates need a `MarketContext` (book imbalance,
  trade imbalance, microprice). With no context they never enter
  (fail-closed). They can only be evaluated on RECORDED microstructure
  (the research store collects it while paper trading) -- a candle-only
  backtest of them would silently be a different strategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from cointrader.data.models import Candle
from cointrader.features import indicators as ind
from cointrader.features.regime import Regime, RegimeConfig, classify_regime
from cointrader.strategies.base import MarketContext, Signal, flat

_REGIME = RegimeConfig()


def _tail(history: Sequence[Candle], n: int) -> Sequence[Candle]:
    return history[len(history) - n:] if len(history) > n else history


@dataclass(frozen=True)
class VwapReversion:
    """Hypothesis: on 1m-5m bars, a close more than `k_atr` ATRs below
    (above) the rolling VWAP, followed by a reversal bar (close back above
    open), reverts toward VWAP before a 1-ATR stop; not in trends or
    high volatility."""

    vwap_period: int = 30
    k_atr: float = 2.0
    atr_period: int = 14
    stop_atr: float = 1.0
    timeframe: str = "1m"
    family: str = "scalp"
    version: str = "1"
    requires_context: bool = False

    @property
    def strategy_id(self) -> str:
        return f"scalp_vwap_reversion_{self.vwap_period}_{self.k_atr:g}_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(_REGIME.warmup, self.vwap_period, 4 * self.atr_period + 1)

    @property
    def parameters(self) -> dict:
        return {"vwap_period": self.vwap_period, "k_atr": self.k_atr, "atr_period": self.atr_period,
                "stop_atr": self.stop_atr}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        h = _tail(history, self.warmup)
        regime = classify_regime(h, _REGIME)
        v, atr = ind.vwap(h, self.vwap_period), ind.atr(h, self.atr_period)
        last = h[-1]
        feats = {"vwap": v, "atr": atr, "close": last.close, **regime.values}
        if v is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", regime=regime.regime.value, features=feats)
        dev_atr = (last.close - v) / atr
        feats["vwap_dev_atr"] = dev_atr
        exit_long, exit_short = last.close >= v, last.close <= v
        ok_regime = regime.regime in (Regime.RANGE, Regime.LOW_VOLATILITY)
        common = dict(stop_distance=self.stop_atr * atr, take_profit_distance=abs(last.close - v) or None,
                      regime=regime.regime.value, features=feats, exit_long=exit_long, exit_short=exit_short)
        if ok_regime and dev_atr <= -self.k_atr and last.close > last.open:
            return Signal(1, strength=min(1.0, -dev_atr / (2 * self.k_atr)), reason="below_vwap_reversal_bar", **common)
        if ok_regime and dev_atr >= self.k_atr and last.close < last.open:
            return Signal(-1, strength=min(1.0, dev_atr / (2 * self.k_atr)), reason="above_vwap_reversal_bar", **common)
        return Signal(0, exit_long, exit_short, 0.0, "no_deviation" if ok_regime else f"blocked_{regime.regime.value}",
                      regime=regime.regime.value, features=feats)


@dataclass(frozen=True)
class RangeBreakoutVolume:
    """Hypothesis: a 5m close beyond the prior `lookback`-bar range with
    relative volume >= `min_rel_volume` continues at least 1.5 ATR
    (take-profit) more often than it returns 1 ATR (stop)."""

    lookback: int = 30
    min_rel_volume: float = 2.0
    atr_period: int = 14
    stop_atr: float = 1.0
    take_profit_atr: float = 1.5
    timeframe: str = "5m"
    family: str = "scalp"
    version: str = "1"
    requires_context: bool = False

    @property
    def strategy_id(self) -> str:
        return f"scalp_breakout_volume_{self.lookback}_{self.min_rel_volume:g}_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(_REGIME.warmup, self.lookback + 2, 4 * self.atr_period + 1)

    @property
    def parameters(self) -> dict:
        return {"lookback": self.lookback, "min_rel_volume": self.min_rel_volume, "stop_atr": self.stop_atr,
                "take_profit_atr": self.take_profit_atr}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        h = _tail(history, self.warmup)
        regime = classify_regime(h, _REGIME)
        dc, rel, atr = ind.donchian(h, self.lookback), ind.relative_volume(h, self.lookback), ind.atr(h, self.atr_period)
        close = h[-1].close
        feats = {"range_high": dc and dc[0], "range_low": dc and dc[1], "relative_volume": rel, "atr": atr,
                 "close": close, **regime.values}
        if dc is None or rel is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", regime=regime.regime.value, features=feats)
        tradable = regime.regime not in (Regime.HIGH_VOLATILITY, Regime.UNDEFINED)
        common = dict(stop_distance=self.stop_atr * atr, take_profit_distance=self.take_profit_atr * atr,
                      regime=regime.regime.value, features=feats)
        if tradable and rel >= self.min_rel_volume and close > dc[0]:
            return Signal(1, strength=min(1.0, rel / (2 * self.min_rel_volume)), reason="range_break_up_volume", **common)
        if tradable and rel >= self.min_rel_volume and close < dc[1]:
            return Signal(-1, strength=min(1.0, rel / (2 * self.min_rel_volume)), reason="range_break_down_volume", **common)
        return flat("no_breakout" if tradable else f"blocked_{regime.regime.value}", regime=regime.regime.value,
                    features=feats)


@dataclass(frozen=True)
class ShortTermMeanReversion:
    """Hypothesis: a 1m close more than `z_entry` sigma from its 20-bar
    mean, when realised volatility is not in its top decile, snaps back
    to the mean before a 1-ATR stop."""

    period: int = 20
    z_entry: float = 3.0
    atr_period: int = 14
    stop_atr: float = 1.0
    timeframe: str = "1m"
    family: str = "scalp"
    version: str = "1"
    requires_context: bool = False

    @property
    def strategy_id(self) -> str:
        return f"scalp_short_mean_reversion_{self.period}_{self.z_entry:g}_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(_REGIME.warmup, self.period, 4 * self.atr_period + 1)

    @property
    def parameters(self) -> dict:
        return {"period": self.period, "z_entry": self.z_entry, "stop_atr": self.stop_atr}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        h = _tail(history, self.warmup)
        regime = classify_regime(h, _REGIME)
        closes = [c.close for c in h]
        bb, atr = ind.bollinger(closes, self.period), ind.atr(h, self.atr_period)
        feats = {"zscore": bb and bb["zscore"], "mean": bb and bb["mid"], "atr": atr, **regime.values}
        if bb is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", regime=regime.regime.value, features=feats)
        calm = regime.regime not in (Regime.HIGH_VOLATILITY, Regime.UNDEFINED)
        exit_long, exit_short = bb["zscore"] >= 0, bb["zscore"] <= 0
        common = dict(stop_distance=self.stop_atr * atr, take_profit_distance=abs(closes[-1] - bb["mid"]) or None,
                      regime=regime.regime.value, features=feats, exit_long=exit_long, exit_short=exit_short)
        if calm and bb["zscore"] <= -self.z_entry:
            return Signal(1, strength=min(1.0, -bb["zscore"] / (2 * self.z_entry)), reason="extreme_low_deviation", **common)
        if calm and bb["zscore"] >= self.z_entry:
            return Signal(-1, strength=min(1.0, bb["zscore"] / (2 * self.z_entry)), reason="extreme_high_deviation", **common)
        return Signal(0, exit_long, exit_short, 0.0, "no_extreme" if calm else f"blocked_{regime.regime.value}",
                      regime=regime.regime.value, features=feats)


@dataclass(frozen=True)
class MicrostructureMomentum:
    """Hypothesis: when recent trade-flow imbalance, top-of-book imbalance
    and microprice-vs-mid all point the same way (each beyond its
    threshold), the next few minutes move that way by more than costs.
    BOOK-CONFIRMED: needs a MarketContext; never enters without one."""

    min_trade_imbalance: float = 0.3
    min_book_imbalance: float = 0.3
    atr_period: int = 14
    stop_atr: float = 0.75
    take_profit_atr: float = 1.0
    timeframe: str = "1m"
    family: str = "scalp"
    version: str = "1"
    requires_context: bool = True

    @property
    def strategy_id(self) -> str:
        return f"scalp_microstructure_momentum_v{self.version}"

    @property
    def warmup(self) -> int:
        return 4 * self.atr_period + 2

    @property
    def parameters(self) -> dict:
        return {"min_trade_imbalance": self.min_trade_imbalance, "min_book_imbalance": self.min_book_imbalance,
                "stop_atr": self.stop_atr, "take_profit_atr": self.take_profit_atr}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        atr = ind.atr(_tail(history, self.warmup), self.atr_period)
        if context is None or None in (context.trade_imbalance, context.book_imbalance, context.microprice,
                                       context.mid_price):
            return flat("microstructure_unavailable", features={"atr": atr})
        feats = {"atr": atr, "trade_imbalance": context.trade_imbalance, "book_imbalance": context.book_imbalance,
                 "microprice": context.microprice, "mid": context.mid_price, "spread": context.spread}
        if atr is None or atr <= 0:
            return flat("indicator_unavailable", features=feats)
        up = (context.trade_imbalance >= self.min_trade_imbalance and context.book_imbalance >= self.min_book_imbalance
              and context.microprice > context.mid_price)
        down = (context.trade_imbalance <= -self.min_trade_imbalance and context.book_imbalance <= -self.min_book_imbalance
                and context.microprice < context.mid_price)
        common = dict(stop_distance=self.stop_atr * atr, take_profit_distance=self.take_profit_atr * atr, features=feats,
                      exit_long=context.trade_imbalance < 0, exit_short=context.trade_imbalance > 0)
        if up:
            return Signal(1, strength=min(1.0, context.trade_imbalance), reason="flow_book_microprice_up", **common)
        if down:
            return Signal(-1, strength=min(1.0, -context.trade_imbalance), reason="flow_book_microprice_down", **common)
        return flat("no_alignment", features=feats, exit_long=context.trade_imbalance < 0,
                    exit_short=context.trade_imbalance > 0)


def scalp_candidate_grid_v1() -> list:
    """Candle-confirmed scalp candidates, fixed before any result is seen.
    `MicrostructureMomentum` is deliberately absent: it needs recorded
    microstructure, which does not exist yet (ADR-0015)."""
    return [VwapReversion(), RangeBreakoutVolume(), ShortTermMeanReversion()]
