"""Swing strategy family (1h / 4h / 1d).

Each class is ONE stated hypothesis with a small fixed parameter set.
None of them is the dead 2/14/28-day momentum grid (H-0005..H-0010) or
the 3/9/21 funding-carry grid (H-0011/H-0012) -- see
`docs/PROJECT_STATUS.md`. What is new here vs. those failed attempts:

- every entry is conditioned on the deterministic regime
  (`features.regime`), so a trend rule is not asked to trade a range and
  a reversion rule is not asked to fight a trend;
- every entry carries an ATR-based stop, so a loser is cut at a known
  size and the risk engine can size by risk instead of by conviction;
- they are research candidates. Nothing here is claimed to have an
  edge; each must go pre-registration -> walk-forward -> PBO/DSR ->
  one held-out TEST like every earlier hypothesis.
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
class TrendEmaAtr:
    """Hypothesis: in a classified trend regime, holding in the trend's
    direction while the fast EMA stays on the right side of the slow EMA
    earns more than it pays in costs, with a 2.5-ATR stop and 3-ATR trail."""

    fast: int = 20
    slow: int = 50
    atr_period: int = 14
    stop_atr: float = 2.5
    trail_atr: float = 3.0
    allow_short: bool = True
    timeframe: str = "1h"
    family: str = "swing"
    version: str = "1"

    @property
    def strategy_id(self) -> str:
        return f"swing_trend_ema_atr_{self.fast}_{self.slow}_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(_REGIME.warmup, 4 * self.slow + 1)

    @property
    def parameters(self) -> dict:
        return {"fast": self.fast, "slow": self.slow, "atr_period": self.atr_period, "stop_atr": self.stop_atr,
                "trail_atr": self.trail_atr, "allow_short": self.allow_short}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        h = _tail(history, self.warmup)
        regime = classify_regime(h, _REGIME)
        closes = [c.close for c in h]
        fast, slow, atr = ind.ema(closes, self.fast), ind.ema(closes, self.slow), ind.atr(h, self.atr_period)
        feats = {"ema_fast": fast, "ema_slow": slow, "atr": atr, "close": closes[-1], **regime.values}
        if fast is None or slow is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", regime=regime.regime.value, features=feats)
        exit_long, exit_short = fast < slow, fast > slow
        common = dict(stop_distance=self.stop_atr * atr, trailing_distance=self.trail_atr * atr,
                      regime=regime.regime.value, features=feats, exit_long=exit_long, exit_short=exit_short)
        if regime.regime is Regime.TREND_UP and fast > slow:
            return Signal(1, strength=min(1.0, abs(regime.values["distance_atr"]) / 3), reason="trend_up_ema_aligned", **common)
        if self.allow_short and regime.regime is Regime.TREND_DOWN and fast < slow:
            return Signal(-1, strength=min(1.0, abs(regime.values["distance_atr"]) / 3), reason="trend_down_ema_aligned", **common)
        return Signal(0, exit_long, exit_short, 0.0, f"no_entry_{regime.regime.value}", regime=regime.regime.value,
                      features=feats)


@dataclass(frozen=True)
class BreakoutVolume:
    """Hypothesis: a close beyond the prior `entry`-bar range on relative
    volume >= `min_rel_volume` (participation confirms the break), outside
    HIGH_VOLATILITY, continues far enough to pay for a 2-ATR stop. Exit
    on a close back through the prior `exit`-bar opposite extreme."""

    entry: int = 20
    exit: int = 10
    min_rel_volume: float = 1.5
    atr_period: int = 14
    stop_atr: float = 2.0
    allow_short: bool = True
    timeframe: str = "4h"
    family: str = "swing"
    version: str = "1"

    @property
    def strategy_id(self) -> str:
        return f"swing_breakout_volume_{self.entry}_{self.exit}_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(_REGIME.warmup, self.entry + 2, 4 * self.atr_period + 2)

    @property
    def parameters(self) -> dict:
        return {"entry": self.entry, "exit": self.exit, "min_rel_volume": self.min_rel_volume,
                "atr_period": self.atr_period, "stop_atr": self.stop_atr, "allow_short": self.allow_short}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        h = _tail(history, self.warmup)
        regime = classify_regime(h, _REGIME)
        dc_entry, dc_exit = ind.donchian(h, self.entry), ind.donchian(h, self.exit)
        rel_vol, atr = ind.relative_volume(h, self.entry), ind.atr(h, self.atr_period)
        close = h[-1].close
        feats = {"donchian_high": dc_entry and dc_entry[0], "donchian_low": dc_entry and dc_entry[1],
                 "exit_high": dc_exit and dc_exit[0], "exit_low": dc_exit and dc_exit[1],
                 "relative_volume": rel_vol, "atr": atr, "close": close, **regime.values}
        if dc_entry is None or dc_exit is None or rel_vol is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", regime=regime.regime.value, features=feats)
        exit_long, exit_short = close < dc_exit[1], close > dc_exit[0]
        tradable = regime.regime not in (Regime.HIGH_VOLATILITY, Regime.UNDEFINED)
        common = dict(stop_distance=self.stop_atr * atr, regime=regime.regime.value, features=feats,
                      exit_long=exit_long, exit_short=exit_short)
        if tradable and rel_vol >= self.min_rel_volume:
            if close > dc_entry[0]:
                return Signal(1, strength=min(1.0, rel_vol / (2 * self.min_rel_volume)), reason="upside_breakout_on_volume", **common)
            if self.allow_short and close < dc_entry[1]:
                return Signal(-1, strength=min(1.0, rel_vol / (2 * self.min_rel_volume)), reason="downside_breakout_on_volume", **common)
        return Signal(0, exit_long, exit_short, 0.0, "no_breakout" if tradable else f"blocked_{regime.regime.value}",
                      regime=regime.regime.value, features=feats)


@dataclass(frozen=True)
class BollingerReversion:
    """Hypothesis: in RANGE / LOW_VOLATILITY regimes only, a close beyond
    `z_entry` standard deviations of the 20-bar mean with RSI confirming
    exhaustion reverts toward the mean before it hits a 1.5-ATR stop.
    Exit when the z-score is back to 0 or the regime turns into a trend."""

    period: int = 20
    z_entry: float = 2.0
    rsi_period: int = 14
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    stop_atr: float = 1.5
    atr_period: int = 14
    timeframe: str = "1h"
    family: str = "swing"
    version: str = "1"

    @property
    def strategy_id(self) -> str:
        return f"swing_bollinger_reversion_{self.period}_{self.z_entry:g}_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(_REGIME.warmup, 4 * self.rsi_period + 1, self.period, 4 * self.atr_period + 1)

    @property
    def parameters(self) -> dict:
        return {"period": self.period, "z_entry": self.z_entry, "rsi_period": self.rsi_period,
                "rsi_low": self.rsi_low, "rsi_high": self.rsi_high, "stop_atr": self.stop_atr}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        h = _tail(history, self.warmup)
        regime = classify_regime(h, _REGIME)
        closes = [c.close for c in h]
        bb, r, atr = ind.bollinger(closes, self.period), ind.rsi(closes, self.rsi_period), ind.atr(h, self.atr_period)
        feats = {"bb_zscore": bb and bb["zscore"], "bb_mid": bb and bb["mid"], "rsi": r, "atr": atr, **regime.values}
        if bb is None or r is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", regime=regime.regime.value, features=feats)
        trending = regime.regime in (Regime.TREND_UP, Regime.TREND_DOWN, Regime.HIGH_VOLATILITY)
        exit_long = bb["zscore"] >= 0 or trending
        exit_short = bb["zscore"] <= 0 or trending
        ranging = regime.regime in (Regime.RANGE, Regime.LOW_VOLATILITY)
        tp = abs(closes[-1] - bb["mid"]) or None
        common = dict(stop_distance=self.stop_atr * atr, take_profit_distance=tp, regime=regime.regime.value,
                      features=feats, exit_long=exit_long, exit_short=exit_short)
        if ranging and bb["zscore"] <= -self.z_entry and r <= self.rsi_low:
            return Signal(1, strength=min(1.0, abs(bb["zscore"]) / (2 * self.z_entry)), reason="oversold_in_range", **common)
        if ranging and bb["zscore"] >= self.z_entry and r >= self.rsi_high:
            return Signal(-1, strength=min(1.0, abs(bb["zscore"]) / (2 * self.z_entry)), reason="overbought_in_range", **common)
        return Signal(0, exit_long, exit_short, 0.0, "no_extreme" if ranging else f"blocked_{regime.regime.value}",
                      regime=regime.regime.value, features=feats)


@dataclass(frozen=True)
class RegimeHybrid:
    """Hypothesis: switching rule by regime (trend rule in TREND_*,
    reversion rule in RANGE/LOW_VOLATILITY, flat in HIGH_VOLATILITY)
    beats either rule alone. The components are fixed instances, so this
    is one candidate, not a search over combinations."""

    trend: TrendEmaAtr = TrendEmaAtr()
    reversion: BollingerReversion = BollingerReversion()
    timeframe: str = "1h"
    family: str = "swing"
    version: str = "1"

    @property
    def strategy_id(self) -> str:
        return f"swing_regime_hybrid_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(self.trend.warmup, self.reversion.warmup)

    @property
    def parameters(self) -> dict:
        return {"trend": self.trend.parameters, "reversion": self.reversion.parameters}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        t = self.trend.signal(history)
        m = self.reversion.signal(history)
        regime = t.regime
        # Exit by the rule that owns the current regime; outside both, exit.
        if regime in (Regime.TREND_UP.value, Regime.TREND_DOWN.value):
            exit_long, exit_short = t.exit_long, t.exit_short
        elif regime in (Regime.RANGE.value, Regime.LOW_VOLATILITY.value):
            exit_long, exit_short = m.exit_long, m.exit_short
        else:
            exit_long = exit_short = True
        if regime in (Regime.TREND_UP.value, Regime.TREND_DOWN.value) and t.entry:
            return Signal(t.entry, exit_long, exit_short, t.strength, f"hybrid_trend:{t.reason}", t.stop_distance,
                          t.take_profit_distance, t.trailing_distance, regime, t.features)
        if regime in (Regime.RANGE.value, Regime.LOW_VOLATILITY.value) and m.entry:
            return Signal(m.entry, exit_long, exit_short, m.strength, f"hybrid_reversion:{m.reason}", m.stop_distance,
                          m.take_profit_distance, m.trailing_distance, regime, m.features)
        if regime == Regime.HIGH_VOLATILITY.value:
            return flat("hybrid_flat_high_volatility", regime=regime, features=t.features, exit_long=True, exit_short=True)
        return flat(f"hybrid_no_entry_{regime}", regime=regime, features=t.features, exit_long=exit_long,
                    exit_short=exit_short)


@dataclass(frozen=True)
class SmaTrendFilter:
    """Hypothesis (H-0015, low turnover): long-only on daily bars while the
    close is above a rising `period`-day SMA; flat otherwise. Price-to-MA
    ratios predict bitcoin returns in Detzel, Liu, Strauss, Zhou & Zhu
    (2021, Financial Management). No regime gate and no trailing stop, so
    the position changes only when the close crosses the average; the
    ATR stop is a catastrophe stop and the risk engine's sizing input."""

    period: int = 100
    slope_bars: int = 20
    atr_period: int = 20
    stop_atr: float = 3.0
    timeframe: str = "1d"
    family: str = "swing"
    version: str = "1"

    @property
    def strategy_id(self) -> str:
        return f"swing_daily_sma_trend_{self.period}_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(_REGIME.warmup, self.period + self.slope_bars + 1, 4 * self.atr_period + 1)

    @property
    def parameters(self) -> dict:
        return {"period": self.period, "slope_bars": self.slope_bars, "atr_period": self.atr_period,
                "stop_atr": self.stop_atr}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        h = _tail(history, self.warmup)
        rg = classify_regime(h, _REGIME).regime.value
        closes = [c.close for c in h]
        ma, ma_before = ind.sma(closes, self.period), ind.sma(closes[:-self.slope_bars], self.period)
        atr = ind.atr(h, self.atr_period)
        feats = {"sma": ma, "sma_before": ma_before, "atr": atr, "close": closes[-1]}
        if ma is None or ma_before is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", regime=rg, features=feats)
        exit_long = closes[-1] < ma
        if closes[-1] > ma and ma > ma_before:
            return Signal(1, strength=min(1.0, (closes[-1] - ma) / (3 * atr)), reason="above_rising_sma",
                          stop_distance=self.stop_atr * atr, regime=rg, features=feats)
        return flat("below_or_flat_sma", regime=rg, features=feats, exit_long=exit_long)


@dataclass(frozen=True)
class DonchianTrend:
    """Hypothesis (H-0015, low turnover): the classic channel breakout
    (Turtle 55/20), long-only on daily bars -- enter on a close above the
    prior `entry`-day high, exit on a close below the prior `exit`-day
    low. Technical trading rules of this kind are studied for crypto in
    Hudson & Urquhart (2021, Annals of Operations Research). Unlike
    `BreakoutVolume` there is no volume or regime filter."""

    entry: int = 55
    exit: int = 20
    atr_period: int = 20
    stop_atr: float = 2.0
    timeframe: str = "1d"
    family: str = "swing"
    version: str = "1"

    @property
    def strategy_id(self) -> str:
        return f"swing_daily_donchian_{self.entry}_{self.exit}_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(_REGIME.warmup, self.entry + 2, 4 * self.atr_period + 1)

    @property
    def parameters(self) -> dict:
        return {"entry": self.entry, "exit": self.exit, "atr_period": self.atr_period, "stop_atr": self.stop_atr}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        h = _tail(history, self.warmup)
        rg = classify_regime(h, _REGIME).regime.value
        dc_entry, dc_exit, atr = ind.donchian(h, self.entry), ind.donchian(h, self.exit), ind.atr(h, self.atr_period)
        close = h[-1].close
        feats = {"entry_high": dc_entry and dc_entry[0], "exit_low": dc_exit and dc_exit[1], "atr": atr, "close": close}
        if dc_entry is None or dc_exit is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", regime=rg, features=feats)
        if close > dc_entry[0]:
            return Signal(1, strength=1.0, reason="close_above_channel", stop_distance=self.stop_atr * atr,
                          regime=rg, features=feats)
        return flat("inside_channel", regime=rg, features=feats, exit_long=close < dc_exit[1])


@dataclass(frozen=True)
class TrendPullback:
    """Hypothesis (H-0015, low turnover): inside a long-term uptrend
    (close above the `trend`-day SMA), a close `z_entry` standard
    deviations below the `period`-day mean is a temporary dip that
    recovers to the mean. Long-only; exit at the mean or when the trend
    filter fails. A rule, not taken from a paper: included as the one
    non-trend-following mechanism so the three candidates are not three
    variants of the same signal (the H-0006 PBO lesson)."""

    trend: int = 100
    period: int = 20
    z_entry: float = 1.5
    atr_period: int = 20
    stop_atr: float = 2.0
    timeframe: str = "1d"
    family: str = "swing"
    version: str = "1"

    @property
    def strategy_id(self) -> str:
        return f"swing_daily_trend_pullback_{self.trend}_{self.period}_v{self.version}"

    @property
    def warmup(self) -> int:
        return max(_REGIME.warmup, self.trend + 1, self.period + 1, 4 * self.atr_period + 1)

    @property
    def parameters(self) -> dict:
        return {"trend": self.trend, "period": self.period, "z_entry": self.z_entry, "atr_period": self.atr_period,
                "stop_atr": self.stop_atr}

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        if len(history) < self.warmup:
            return flat("warmup")
        h = _tail(history, self.warmup)
        rg = classify_regime(h, _REGIME).regime.value
        closes = [c.close for c in h]
        ma, bb, atr = ind.sma(closes, self.trend), ind.bollinger(closes, self.period), ind.atr(h, self.atr_period)
        feats = {"sma_trend": ma, "bb_zscore": bb and bb["zscore"], "bb_mid": bb and bb["mid"], "atr": atr,
                 "close": closes[-1]}
        if ma is None or bb is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", regime=rg, features=feats)
        uptrend = closes[-1] > ma
        exit_long = bb["zscore"] >= 0 or not uptrend
        if uptrend and bb["zscore"] <= -self.z_entry:
            return Signal(1, strength=min(1.0, abs(bb["zscore"]) / (2 * self.z_entry)), reason="dip_in_uptrend",
                          stop_distance=self.stop_atr * atr, take_profit_distance=(bb["mid"] - closes[-1]) or None,
                          regime=rg, features=feats)
        return flat("no_dip" if uptrend else "below_trend", regime=rg, features=feats, exit_long=exit_long)


def swing_candidate_grid_v1() -> list:
    """Fixed before any result is seen (ADR-0015). One instance per
    hypothesis family; never extend this function after a run -- a new
    set is a new grid function and a new hypothesis id."""
    return [TrendEmaAtr(), BreakoutVolume(timeframe="1h"), BollingerReversion(), RegimeHybrid()]


def swing_candidate_grid_v2() -> list:
    """H-0015: daily, long-only, low-turnover set. Fixed before any
    result is seen; never extend it after a run."""
    return [SmaTrendFilter(), DonchianTrend(), TrendPullback()]
