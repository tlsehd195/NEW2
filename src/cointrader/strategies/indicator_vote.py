"""Calibrated indicator-vote strategy (ADR-0005 appendix (2026-10-02)).

Many popular indicators each give P(long); the equal-weight log-odds
mean decides. ONE fixed procedure, every knob fixed before any result is
seen, so a candidate grid stays small enough for PBO/DSR to deflate
(CLAUDE.md rule 1). Calibration data are only (score_t, sign of the
return over the next `horizon` bars) pairs whose outcome bar is already
visible -- the bar being decided is never in the fit.

Not registered, not run on TEST: it needs its own pre-registration
(new hypothesis id) first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from cointrader.data.models import Candle
from cointrader.features import indicators as ind
from cointrader.features.indicator_votes import DEFAULT_PANEL, MIN_BARS, PlattCalibrator, Verdict, combine_votes, raw_scores
from cointrader.strategies.base import MarketContext, Signal, flat


@dataclass(frozen=True)
class IndicatorVote:
    horizon: int = 12  # bars the calibration outcome looks ahead
    fit_lookback: int = 400  # trailing bars of realized pairs used to calibrate
    enter_confidence: float = 0.60  # combined P needed to enter (hysteresis: exit below exit_confidence)
    exit_confidence: float = 0.52
    min_agree: float = 0.6  # fraction of indicators on the entry side
    stop_atr: float = 2.5
    atr_period: int = 14
    allow_short: bool = True
    timeframe: str = "1d"
    family: str = "swing"
    version: str = "1"

    def __post_init__(self) -> None:
        if not 0.5 < self.exit_confidence < self.enter_confidence < 1.0:
            raise ValueError("need 0.5 < exit_confidence < enter_confidence < 1")
        if self.horizon < 1 or self.fit_lookback < 10 * self.horizon:
            raise ValueError("fit_lookback must be >= 10 * horizon")

    @property
    def strategy_id(self) -> str:
        return f"swing_indicator_vote_h{self.horizon}_c{self.enter_confidence:g}_v{self.version}"

    @property
    def warmup(self) -> int:
        return MIN_BARS + self.fit_lookback + self.horizon + 1

    @property
    def parameters(self) -> dict:
        return {"horizon": self.horizon, "fit_lookback": self.fit_lookback, "enter_confidence": self.enter_confidence,
                "exit_confidence": self.exit_confidence, "min_agree": self.min_agree, "stop_atr": self.stop_atr,
                "atr_period": self.atr_period, "allow_short": self.allow_short}

    def verdict(self, history: Sequence[Candle]) -> Optional[Verdict]:
        """Per-indicator and combined P(long) for the last bar, or None
        (fail-closed) when anything needed is missing."""
        n = len(history)
        if n < self.warmup:
            return None
        today = raw_scores(history, DEFAULT_PANEL)
        if today is None:
            return None
        rows: dict[str, list[tuple[float, int]]] = {k: [] for k in today}
        last = n - 1 - self.horizon  # outcome bar must already be visible
        for t in range(max(MIN_BARS, last - self.fit_lookback), last + 1):
            sc = raw_scores(history[: t + 1], DEFAULT_PANEL)
            if sc is None:
                continue
            ret = history[t + self.horizon].close / history[t].close - 1.0
            if ret == 0:
                continue
            for k, v in sc.items():
                rows[k].append((v, 1 if ret > 0 else 0))
        probs = {}
        for k, pairs in rows.items():
            cal = PlattCalibrator.fit([p[0] for p in pairs], [p[1] for p in pairs])
            probs[k] = cal.predict(today[k])
        return combine_votes(probs)

    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        v = self.verdict(history)
        if v is None:
            return flat("warmup_or_indicator_unavailable")
        atr = ind.atr(list(history[-(self.atr_period * 4):]), self.atr_period)
        feats = {"p_long": v.p_long, "agree_long": v.agree_long, "agree_short": v.agree_short,
                 **{f"p_{k}": p for k, p in v.per_indicator.items()}}
        if atr is None or atr <= 0:
            return flat("atr_unavailable", features=feats)
        total = max(1, len(v.per_indicator))
        exit_long, exit_short = v.p_long < self.exit_confidence, v.p_long > 1 - self.exit_confidence
        common = dict(stop_distance=self.stop_atr * atr, features=feats, exit_long=exit_long, exit_short=exit_short)
        strength = min(1.0, (v.confidence - 0.5) * 4)
        if v.p_long >= self.enter_confidence and v.agree_long / total >= self.min_agree:
            return Signal(1, strength=strength, reason="vote_long", **common)
        if self.allow_short and v.p_long <= 1 - self.enter_confidence and v.agree_short / total >= self.min_agree:
            return Signal(-1, strength=strength, reason="vote_short", **common)
        return Signal(0, exit_long, exit_short, 0.0, "vote_no_entry", features=feats)
