"""15-minute taker-flow strategy (ADR-0064): a NEW candidate next to `DayTradeVote`.

Direction comes from ONE input, the taker buy/sell imbalance of the last `flow_window` bars
(Kim & Hansen 2026, grade B: aggressive-flow imbalance predicts the next 4-12 h at 15m resolution):

    flow_t  = (2 * sum(taker_buy_volume) - sum(volume)) / sum(volume)      over the last W bars, in [-1, 1]
    z_t     = (flow_t - mean(flow)) / std(flow)                            over the last `z_window` flows
    score_t = tanh(z_t / 2)

Long when z >= z_in, short when z <= -z_in; an open position exits when z crosses back through 0.
`p_long` for confidence-scaled sizing is the same ridge-Platt map as the indicator vote (score -> P(return over
`flow_window` bars > 0), fitted on already-realized pairs only), so P stays near 0.5 when the flow carries no
information and the risk engine then sizes at its 1% floor.

Two optional entry FILTERS (never votes; graded A/B papers say they predict volatility/crowding, not direction):

- `vpin_gate`: bar-time VPIN proxy = mean |2*taker_buy/volume - 1| over `vpin_window` bars (Easley et al. 2012
  use volume buckets; this is the bar-time approximation). Entries are blocked while it sits in the top decile of
  its own trailing `z_window` values (toxic, volatile flow).
- `funding_gate`: block longs when funding has been persistently high (crowded longs) and shorts when it has
  been persistently negative (same crowding score as the indicator vote's funding vote, |score| >= 0.5).

Stops, sizing, the 12 h time stop and the volatility gate are the same as `DayTradeVote`. Missing taker volume,
funding data (when the gate needs it) or ATR means no trade (fail-closed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

from cointrader.data.models import Candle
from cointrader.features import indicators as ind
from cointrader.features.indicator_votes import PlattCalibrator
from cointrader.features.regime import RegimeConfig, classify_regime
from cointrader.features.side_indicators import funding_crowding_score, known_funding, volatility_ratio
from cointrader.strategies.base import MarketContext, Signal, flat

_REGIME = RegimeConfig()
CROWDED = 0.5  # |funding crowding score| at or above this blocks the crowded side


@dataclass(frozen=True)
class FlowVote:
    flow_window: int = 16  # bars of taker flow summed; also the calibration horizon (16 = 4 h, 48 = 12 h)
    z_window: int = 480  # bars the flow is standardised against (5 days; keeps warm-up inside the unlocked data)
    z_in: float = 1.0
    fit_lookback: int = 480
    stop_atr: float = 2.5
    atr_period: int = 14
    allow_short: bool = True
    vpin_gate: bool = False
    vpin_window: int = 48
    vpin_block_pct: float = 0.9
    funding_gate: bool = False
    vol_gate_hi: float = 2.0
    vol_gate_lo: float = 0.5
    vol_short: int = 96
    vol_long: int = 960
    timeframe: str = "15m"
    family: str = "daytrade"
    version: str = "1"
    max_hold_bars: int = 48
    max_entries_per_day: int = 100
    quality_window_bars: int = 192
    side: dict = field(default_factory=dict, compare=False, repr=False, hash=False)
    _cache: dict = field(default_factory=dict, compare=False, repr=False, hash=False)

    def __post_init__(self) -> None:
        if self.flow_window < 2 or self.z_window < 10 * self.flow_window or self.fit_lookback < 10 * self.flow_window:
            raise ValueError("need flow_window >= 2, z_window and fit_lookback >= 10 * flow_window")
        if not self.z_in > 0 or not 0.5 < self.vpin_block_pct < 1.0:
            raise ValueError("need z_in > 0 and 0.5 < vpin_block_pct < 1")
        if not 0 < self.vol_gate_lo < 1 < self.vol_gate_hi or not 2 <= self.vol_short < self.vol_long:
            raise ValueError("bad volatility gate")

    @property
    def strategy_id(self) -> str:
        gates = ("_vpin" if self.vpin_gate else "") + ("_fund" if self.funding_gate else "")
        return f"{self.family}_flow_vote{gates}_w{self.flow_window}_z{self.z_in:g}_v{self.version}"

    @property
    def warmup(self) -> int:
        return self.z_window + self.flow_window + self.fit_lookback + self.flow_window + 1

    @property
    def parameters(self) -> dict:
        params = {"flow_window": self.flow_window, "z_window": self.z_window, "z_in": self.z_in,
                  "fit_lookback": self.fit_lookback, "stop_atr": self.stop_atr, "atr_period": self.atr_period,
                  "allow_short": self.allow_short, "vpin_gate": self.vpin_gate, "funding_gate": self.funding_gate,
                  "vol_gate_hi": self.vol_gate_hi, "vol_gate_lo": self.vol_gate_lo}
        if self.vpin_gate:
            params.update({"vpin_window": self.vpin_window, "vpin_block_pct": self.vpin_block_pct})
        return params

    def attach_side_data(self, *, funding: Sequence, open_interest: Sequence = ()) -> "FlowVote":
        return replace(self, side={"funding": sorted(funding, key=lambda r: r.funding_time)}, _cache={})

    @property
    def use_side_data(self) -> bool:
        return self.funding_gate

    # ------------------------------------------------------------ series (prefix-sum caches, no look-ahead)
    def _series(self, history: Sequence[Candle]) -> dict:
        """Per-bar flow and |imbalance| for bars 0..len-1, extended in place. Entry t uses bars <= t. Each window is
        summed directly (no running totals), so the value never depends on where the history starts."""
        underlying = getattr(history, "_candles", history)
        c = self._cache
        if c.get("src") is not underlying:
            c.clear()
            c.update(src=underlying, imb=[], flow=[])
        n, w = len(history), self.flow_window
        while len(c["flow"]) < n:
            i = len(c["flow"])
            bar = underlying[i]
            ok = bar.taker_buy_volume is not None and bar.volume > 0
            c["imb"].append(abs(2 * bar.taker_buy_volume / bar.volume - 1) if ok else None)
            win = underlying[i + 1 - w:i + 1] if i + 1 >= w else []
            if len(win) < w or any(b.taker_buy_volume is None or b.volume <= 0 for b in win):
                c["flow"].append(None)
            else:
                vol = math.fsum(b.volume for b in win)
                c["flow"].append((2 * math.fsum(b.taker_buy_volume for b in win) - vol) / vol)
        return c

    def _z(self, c: dict, t: int) -> Optional[float]:
        lo = t + 1 - self.z_window
        if lo < 0:
            return None
        win = c["flow"][lo:t + 1]
        if any(f is None for f in win):
            return None
        m = math.fsum(win) / len(win)
        var = math.fsum((f - m) ** 2 for f in win) / (len(win) - 1)
        return None if var <= 1e-18 else (win[-1] - m) / math.sqrt(var)

    def _vpin(self, c: dict, t: int) -> Optional[float]:
        lo = t + 1 - self.vpin_window
        if lo < 0:
            return None
        win = c["imb"][lo:t + 1]
        return None if any(x is None for x in win) else math.fsum(win) / self.vpin_window

    def _vpin_blocked(self, c: dict, t: int) -> Optional[bool]:
        """True when the VPIN proxy is in the top (1 - vpin_block_pct) of its trailing z_window values; None = unknown."""
        now = self._vpin(c, t)
        if now is None:
            return None
        past = [self._vpin(c, k) for k in range(t - self.z_window + 1, t + 1, 4)]  # every 4th bar: cheap, deterministic
        past = [p for p in past if p is not None]
        if len(past) < 50:
            return None
        return sum(1 for p in past if p <= now) / len(past) >= self.vpin_block_pct

    def _score_cached(self, c: dict, t: int) -> Optional[float]:
        memo = c.setdefault("score", {})
        if t not in memo:
            z = self._z(c, t)
            memo[t] = None if z is None else (z, math.tanh(z / 2.0))
        return memo[t]

    def p_long(self, history: Sequence[Candle]) -> Optional[tuple[float, float]]:
        """(z, calibrated P(long)) for the last bar, or None (fail-closed)."""
        n = len(history)
        if n < self.warmup:
            return None
        c = self._series(history)
        today = self._score_cached(c, n - 1)
        if today is None:
            return None
        xs, ys = [], []
        last = n - 1 - self.flow_window  # the outcome bar must already be closed
        underlying = c["src"]
        for t in range(max(0, last - self.fit_lookback), last + 1):
            sc = self._score_cached(c, t)
            if sc is None:
                continue
            ret = underlying[t + self.flow_window].close / underlying[t].close - 1.0
            if ret == 0:
                continue
            xs.append(sc[1])
            ys.append(1 if ret > 0 else 0)
        return today[0], PlattCalibrator.fit(xs, ys).predict(today[1])

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        zp = self.p_long(history)
        if zp is None:
            return flat("warmup_or_flow_unavailable")
        z, p = zp
        atr = ind.atr(list(history[-(self.atr_period * 4 + 1):]), self.atr_period)
        ratio = volatility_ratio(history, short=self.vol_short, long=self.vol_long)
        regime = classify_regime(history[len(history) - _REGIME.warmup:], _REGIME).regime.value
        feats = {"p_long": p, "flow_z": z, "vol_ratio": ratio}
        if atr is None or atr <= 0 or ratio is None:
            return flat("atr_or_vol_unavailable", features=feats)
        exit_long, exit_short = z < 0.0, z > 0.0
        if not self.vol_gate_lo <= ratio <= self.vol_gate_hi:
            return Signal(0, exit_long, exit_short, 0.0, "vol_gate_blocks_entry", regime=regime, features=feats)
        want = 1 if z >= self.z_in else (-1 if z <= -self.z_in and self.allow_short else 0)
        if want == 0:
            return Signal(0, exit_long, exit_short, 0.0, "flow_no_entry", regime=regime, features=feats)
        c = self._cache
        if self.vpin_gate:
            blocked = self._vpin_blocked(c, len(history) - 1)
            feats["vpin_blocked"] = blocked
            if blocked is None or blocked:
                return Signal(0, exit_long, exit_short, 0.0, "vpin_gate_blocks_entry", regime=regime, features=feats)
        if self.funding_gate:
            crowd = funding_crowding_score(known_funding(self.side.get("funding", ()), history[-1].close_time))
            feats["funding_crowding"] = crowd
            if crowd is None:
                return flat("funding_unavailable", features=feats)  # fail-closed
            if (want > 0 and crowd <= -CROWDED) or (want < 0 and crowd >= CROWDED):
                return Signal(0, exit_long, exit_short, 0.0, "funding_gate_blocks_entry", regime=regime, features=feats)
        strength = min(1.0, max(0.0, (abs(z) - self.z_in) / 2.0 + 0.25))
        return Signal(want, exit_long, exit_short, strength, "flow_long" if want > 0 else "flow_short",
                      stop_distance=self.stop_atr * atr, features=feats, regime=regime)
