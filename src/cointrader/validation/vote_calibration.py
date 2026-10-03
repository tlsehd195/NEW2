"""Does the indicator vote's P(long) come true? Measurement only (ADR-0035).

For each decision bar i the vote's `p_long` is computed from closed bars only
(a `PrefixView`), then compared with whether the close `horizon` bars later is
above the close at i. The vote's Platt fit already uses only outcomes that
were visible at i, so every pair here is out of sample for that fit.

Rules that keep this from becoming a hidden parameter search:
- it never changes a threshold, a strategy or a trade, and registers nothing;
- the caller must pass a range that `assert_not_locked` accepted;
- a pair is dropped (and counted) if a candle hole sits between decision and
  outcome bar, or the outcome is an exact tie (the fit drops ties too);
- labels `horizon` bars apart overlap, so `stride` defaults to `horizon`
  (non-overlapping labels); effective samples are reported, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from cointrader.backtest.engine import PrefixView
from cointrader.data.models import Candle
from cointrader.validation.calibration import CalibrationReport, beats_base_rate, calibration_report


@dataclass(frozen=True)
class Forecasts:
    probs: tuple[float, ...]
    outcomes: tuple[int, ...]  # 1 = close at i+horizon above close at i
    dropped: dict  # reason -> count


def collect_forecasts(candles: Sequence[Candle], verdict_p_long: Callable[[Sequence[Candle]], Optional[float]],
                      horizon: int, first_index: int, *, stride: Optional[int] = None) -> Forecasts:
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    step = stride or horizon
    if step < 1:
        raise ValueError("stride must be >= 1")
    probs: list[float] = []
    outcomes: list[int] = []
    dropped = {"no_verdict": 0, "candle_gap": 0, "tie": 0}
    delta = candles[1].open_time - candles[0].open_time if len(candles) > 1 else None
    for i in range(first_index, len(candles) - horizon, step):
        p = verdict_p_long(PrefixView(candles, i + 1))
        if p is None:
            dropped["no_verdict"] += 1
            continue
        if delta is None or candles[i + horizon].open_time - candles[i].open_time != delta * horizon:
            dropped["candle_gap"] += 1
            continue
        a, b = candles[i].close, candles[i + horizon].close
        if a == b:
            dropped["tie"] += 1
            continue
        probs.append(p)
        outcomes.append(1 if b > a else 0)
    return Forecasts(tuple(probs), tuple(outcomes), dropped)


def _side_rate(f: Forecasts, pick: Callable[[float], bool]) -> dict:
    ys = [y for p, y in zip(f.probs, f.outcomes) if pick(p)]
    return {"n": len(ys), "up_rate": round(sum(ys) / len(ys), 4) if ys else None}


def summarize(f: Forecasts, *, horizon: int, stride: int, enter_confidence: float, min_samples: int = 100) -> dict:
    rep: CalibrationReport = calibration_report(f.probs, f.outcomes, min_samples=min_samples)
    return {
        "samples": rep.samples, "dropped": f.dropped,
        "effective_samples_note": "labels are non-overlapping" if stride >= horizon
        else f"labels overlap: effective n about {rep.samples * stride // horizon}",
        "measurable": rep.reason == "", "reason": rep.reason,
        "base_rate_up": rep.base_rate, "brier": rep.brier, "brier_always_base_rate": rep.brier_baseline,
        "beats_base_rate": beats_base_rate(rep), "log_loss": rep.log_loss, "ece": rep.ece,
        # the two regions the entry rule actually acts on: a 60% entry should be followed by up ~60% / ~40% of the time
        "p_long_at_or_above_enter": _side_rate(f, lambda p: p >= enter_confidence),
        "p_long_at_or_below_1_minus_enter": _side_rate(f, lambda p: p <= 1 - enter_confidence),
        "reliability": [{"low": b.low, "high": b.high, "n": b.count,
                         "mean_predicted": None if b.mean_predicted is None else round(b.mean_predicted, 4),
                         "observed_up_rate": None if b.observed_rate is None else round(b.observed_rate, 4)}
                        for b in rep.bins],
    }
