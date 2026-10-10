"""Calibrated indicator-vote strategy (ADR-0023, ADR-0025).

Many indicators each give P(long); the equal-weight log-odds mean decides.
ONE fixed procedure, every knob fixed before any result is seen, so a
candidate grid stays small enough for PBO/DSR to deflate (CLAUDE.md rule
1). Calibration data are only (score_t, sign of the return over the next
`horizon` bars) pairs whose outcome bar is already visible -- the bar being
decided is never in the fit.

v2 adds (ADR-0025): a volatility GATE (no vote: it only blocks entries when
the short/long realized-vol ratio is outside `vol_gate`) and, with
`use_side_data=True`, two extra votes from funding rate and open interest.
Side data is injected by the validation runner (`attach_side_data`) and is
as-of filtered at every bar; a candidate that needs it but has none at
decision time returns flat (fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

from cointrader.data.models import Candle
from cointrader.features import indicators as ind
from cointrader.features.regime import RegimeConfig, classify_regime
from cointrader.features.indicator_votes import DEFAULT_PANEL, MIN_BARS, PlattCalibrator, Verdict, combine_votes, raw_scores
from cointrader.features.side_indicators import (
    funding_crowding_score, known_funding, known_open_interest, oi_confirmation_score, volatility_ratio,
)
from cointrader.strategies.base import MarketContext, Signal, flat

SIDE_PANEL = ("funding_crowding", "oi_confirm")
_REGIME = RegimeConfig()


@dataclass(frozen=True)
class IndicatorVote:
    horizon: int = 5  # bars the calibration outcome looks ahead
    fit_lookback: int = 200  # trailing bars of realized pairs used to calibrate
    enter_confidence: float = 0.60  # combined P needed to enter (hysteresis: exit below exit_confidence)
    exit_confidence: float = 0.52
    min_agree: float = 0.6  # fraction of indicators on the entry side
    stop_atr: float = 2.5
    trail_atr: Optional[float] = None  # trailing stop this many ATRs behind the best price (None = fixed stop)
    trail_activate_atr: Optional[float] = None  # trail only after the price has moved this many ATRs in favour
    atr_period: int = 14
    allow_short: bool = True
    use_side_data: bool = False  # add funding + open-interest votes
    vol_gate_hi: float = 2.0  # block entries when short/long vol ratio exceeds this
    vol_gate_lo: float = 0.5  # ... or falls below this
    timeframe: str = "1d"
    family: str = "swing"
    version: str = "2"
    # Timeframe knobs; the defaults are the daily (swing) behaviour. The 15m day-trade variant sets them (ADR-0032).
    score_scale: float = 1.0  # multiplies the return-sized score saturation constants
    vol_short: int = 10  # bars of the short realized-vol window
    vol_long: int = 60
    bars_per_day: int = 1  # aligns daily open-interest points with the price window
    max_hold_bars: Optional[int] = None  # engine closes a position held this many bars (None = no time stop)
    max_entries_per_day: Optional[int] = None  # engine cap per UTC day (None = none)
    quality_window_bars: Optional[int] = None  # engine data-quality look-back (None = warm-up bars)
    # Injected after construction; never part of id/equality/repr.
    side: dict = field(default_factory=dict, compare=False, repr=False, hash=False)
    _cache: dict = field(default_factory=dict, compare=False, repr=False, hash=False)

    def __post_init__(self) -> None:
        if not 0.5 < self.exit_confidence < self.enter_confidence < 1.0:
            raise ValueError("need 0.5 < exit_confidence < enter_confidence < 1")
        if self.horizon < 1 or self.fit_lookback < 10 * self.horizon:
            raise ValueError("fit_lookback must be >= 10 * horizon")
        if not 0 < self.vol_gate_lo < 1 < self.vol_gate_hi:
            raise ValueError("need 0 < vol_gate_lo < 1 < vol_gate_hi")
        if not self.score_scale > 0 or not 2 <= self.vol_short < self.vol_long or self.bars_per_day < 1:
            raise ValueError("need score_scale > 0, 2 <= vol_short < vol_long, bars_per_day >= 1")
        if self.trail_atr is not None and not self.trail_atr > 0:
            raise ValueError("trail_atr must be > 0 when set")
        if self.trail_activate_atr is not None and (self.trail_atr is None or not self.trail_activate_atr > 0):
            raise ValueError("trail_activate_atr must be > 0 and needs trail_atr")
        for name in ("max_hold_bars", "max_entries_per_day", "quality_window_bars"):
            v = getattr(self, name)
            if v is not None and v < 1:
                raise ValueError(f"{name} must be >= 1 when set")

    @property
    def strategy_id(self) -> str:
        side = "_side" if self.use_side_data else ""
        trail = f"_t{self.trail_atr:g}" if self.trail_atr is not None else ""
        if self.trail_activate_atr is not None:
            trail += f"a{self.trail_activate_atr:g}"
        # Hold-time and stop-width variants (ADR-0071) appear in the id only when they differ from the default.
        stop = f"_s{self.stop_atr:g}" if self.stop_atr != 2.5 else ""
        hold_default = type(self).__dataclass_fields__["max_hold_bars"].default
        hold = f"_m{self.max_hold_bars}" if self.max_hold_bars != hold_default else ""
        return f"{self.family}_indicator_vote{side}_h{self.horizon}_c{self.enter_confidence:g}{stop}{hold}{trail}_v{self.version}"

    @property
    def warmup(self) -> int:
        return MIN_BARS + self.fit_lookback + self.horizon + 1

    @property
    def parameters(self) -> dict:
        params = {"horizon": self.horizon, "fit_lookback": self.fit_lookback, "enter_confidence": self.enter_confidence,
                  "exit_confidence": self.exit_confidence, "min_agree": self.min_agree, "stop_atr": self.stop_atr,
                  "atr_period": self.atr_period, "allow_short": self.allow_short, "use_side_data": self.use_side_data,
                  "vol_gate_hi": self.vol_gate_hi, "vol_gate_lo": self.vol_gate_lo}
        # Listed only when set, so the swing candidates' recorded parameters stay exactly as registered.
        extra = {"score_scale": self.score_scale, "vol_short": self.vol_short, "vol_long": self.vol_long,
                 "bars_per_day": self.bars_per_day, "max_hold_bars": self.max_hold_bars,
                 "max_entries_per_day": self.max_entries_per_day, "quality_window_bars": self.quality_window_bars,
                 "trail_atr": self.trail_atr, "trail_activate_atr": self.trail_activate_atr}
        defaults = {"score_scale": 1.0, "vol_short": 10, "vol_long": 60, "bars_per_day": 1, "max_hold_bars": None,
                    "max_entries_per_day": None, "quality_window_bars": None, "trail_atr": None,
                    "trail_activate_atr": None}
        params.update({k: v for k, v in extra.items() if v != defaults[k]})
        return params

    def attach_side_data(self, *, funding: Sequence, open_interest: Sequence) -> "IndicatorVote":
        """A copy holding the (time-sorted) funding and open-interest records.
        Records are filtered by decision time inside every score call."""
        return replace(self, side={"funding": sorted(funding, key=lambda r: r.funding_time),
                                   "oi": sorted(open_interest, key=lambda p: p.as_of)}, _cache={})

    # ------------------------------------------------------------ scores
    def _scores_at(self, prefix: Sequence[Candle]) -> Optional[dict]:
        sc = raw_scores(prefix, DEFAULT_PANEL, scale=self.score_scale)
        if sc is None or not self.use_side_data:
            return sc
        now = prefix[-1].close_time
        fund = funding_crowding_score(known_funding(self.side.get("funding", ()), now))
        n = len(prefix)
        # One close per day-step, newest last, so the price move spans the same days as the daily OI points
        # (bars_per_day=1 is exactly the previous `prefix[-30:]`).
        closes = [prefix[n - 1 - k * self.bars_per_day].close for k in range(29, -1, -1) if n - 1 - k * self.bars_per_day >= 0]
        oi = oi_confirmation_score(known_open_interest(self.side.get("oi", ()), now), closes)
        if fund is None or oi is None:
            return None  # fail-closed: a side vote is missing, so no panel at this bar
        return {**sc, "funding_crowding": fund, "oi_confirm": oi}

    def _scores_cached(self, history: Sequence[Candle], t: int) -> Optional[dict]:
        """Scores for bar index t. Cached per underlying candle list (a
        PrefixView shares one list across a sequential run); a different
        list rebuilds, and the cache holds the list so its id can't be reused."""
        underlying = getattr(history, "_candles", history)
        if self._cache.get("src") is not underlying:
            self._cache.clear()
            self._cache["src"] = underlying
            self._cache["scores"] = {}
        scores = self._cache["scores"]
        if t not in scores:
            scores[t] = self._scores_at(history[: t + 1])
        return scores[t]

    def verdict(self, history: Sequence[Candle]) -> Optional[Verdict]:
        """Per-indicator and combined P(long) for the last bar, or None
        (fail-closed) when anything needed is missing."""
        n = len(history)
        if n < self.warmup:
            return None
        today = self._scores_cached(history, n - 1)
        if today is None:
            return None
        rows: dict[str, list[tuple[float, int]]] = {k: [] for k in today}
        last = n - 1 - self.horizon  # outcome bar must already be visible
        for t in range(max(MIN_BARS, last - self.fit_lookback), last + 1):
            sc = self._scores_cached(history, t)
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
        atr = ind.atr(list(history[-(self.atr_period * 4 + 1):]), self.atr_period)
        ratio = volatility_ratio(history, short=self.vol_short, long=self.vol_long)
        # The risk engine refuses entries in an UNDEFINED regime; the regime is context only, it is not a vote.
        regime = classify_regime(history[len(history) - _REGIME.warmup:], _REGIME).regime.value
        feats = {"p_long": v.p_long, "agree_long": v.agree_long, "agree_short": v.agree_short, "vol_ratio": ratio,
                 **{f"p_{k}": p for k, p in v.per_indicator.items()}}
        if atr is None or atr <= 0 or ratio is None:
            return flat("atr_or_vol_unavailable", features=feats)
        total = max(1, len(v.per_indicator))
        exit_long, exit_short = v.p_long < self.exit_confidence, v.p_long > 1 - self.exit_confidence
        trail = self.trail_atr * atr if self.trail_atr is not None else None
        activate = self.trail_activate_atr * atr if self.trail_activate_atr is not None else None
        common = dict(stop_distance=self.stop_atr * atr, trailing_distance=trail, trailing_activation=activate,
                      features=feats, exit_long=exit_long, exit_short=exit_short, regime=regime)
        strength = min(1.0, (v.confidence - 0.5) * 4)
        gated = not (self.vol_gate_lo <= ratio <= self.vol_gate_hi)
        if gated:
            return Signal(0, exit_long, exit_short, 0.0, "vol_gate_blocks_entry", regime=regime, features=feats)
        if v.p_long >= self.enter_confidence and v.agree_long / total >= self.min_agree:
            return Signal(1, strength=strength, reason="vote_long", **common)
        if self.allow_short and v.p_long <= 1 - self.enter_confidence and v.agree_short / total >= self.min_agree:
            return Signal(-1, strength=strength, reason="vote_short", **common)
        return Signal(0, exit_long, exit_short, 0.0, "vote_no_entry", regime=regime, features=feats)
