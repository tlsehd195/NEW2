"""Indicator "votes": each indicator -> signed score -> calibrated P(long).

Design (docs/decisions/ADR-0005, appendix 2026-10-02):

1. `raw_scores` turns popular indicators into signed scores in (-1, 1)
   (positive = bullish). Pure functions of the visible history.
2. `PlattCalibrator` maps one indicator's score to P(next-horizon return
   > 0) using ONLY already-realized (score, outcome) pairs. Two
   parameters, L2-shrunk toward "no information" (p = 0.5), so a
   noisy indicator stays near 50% instead of claiming confidence it has
   not earned (Niculescu-Mizil & Caruana 2005; Guo et al. 2017).
3. `combine_votes` averages the calibrated log-odds with equal weights
   by default (forecast combination, Neely-Rapach-Tu-Zhou 2014). Learned
   per-indicator weights are opt-in, not the default: every free weight
   is a hidden trial for PBO/DSR (Sullivan-Timmermann-White 1999;
   Bailey et al. PBO).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from cointrader.data.models import Candle
from cointrader.features import indicators as ind

# Trend / momentum / mean-reversion / volume groups. Group is metadata
# for reporting and for the equal-weight-per-group option.
INDICATOR_GROUPS: dict[str, str] = {
    "ema_trend": "trend", "ma_alignment": "trend", "donchian_pos": "trend", "macd_hist": "trend",
    "roc": "momentum", "rsi": "momentum", "stoch": "momentum", "cci": "momentum",
    "bollinger_b": "reversion", "vwap_dev": "reversion",
    "obv_slope": "volume", "mfi": "volume",
}
MIN_BARS = 140

# One vote per distinct role. The wider set above is computed too (for
# research), but overlapping price-derived ones (macd/ma_alignment ~ ema_trend,
# stoch/cci ~ rsi, vwap_dev ~ bollinger_b, mfi ~ obv_slope) stay OUT of the
# default panel so correlated indicators don't count as independent votes.
# Volatility has no direction -> it belongs in a gate, not a vote. Funding /
# open interest are crypto-specific roles that need data outside the candle.
DEFAULT_PANEL: tuple[str, ...] = ("ema_trend", "donchian_pos", "roc", "rsi", "bollinger_b", "obv_slope")


def redundancy(scores_series: Sequence[dict[str, float]], names: Sequence[str]) -> float:
    """Mean absolute pairwise Pearson correlation of the named indicators'
    scores over a series of panels (0 = independent, 1 = duplicates)."""
    cols = {k: [d[k] for d in scores_series] for k in names}
    vals = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            x, y = cols[a], cols[b]
            mx, my = math.fsum(x) / len(x), math.fsum(y) / len(y)
            sxy = math.fsum((u - mx) * (v - my) for u, v in zip(x, y))
            sxx, syy = math.fsum((u - mx) ** 2 for u in x), math.fsum((v - my) ** 2 for v in y)
            vals.append(0.0 if sxx <= 0 or syy <= 0 else abs(sxy / math.sqrt(sxx * syy)))
    return math.fsum(vals) / len(vals) if vals else 0.0


def _squash(x: float, scale: float = 1.0) -> float:
    return math.tanh(x / scale)


def _stoch(window: Sequence[Candle]) -> float:
    hi, lo = max(c.high for c in window), min(c.low for c in window)
    return 0.0 if hi == lo else (window[-1].close - lo) / (hi - lo) * 2.0 - 1.0


def _cci(window: Sequence[Candle]) -> float:
    tp = [(c.high + c.low + c.close) / 3.0 for c in window]
    mean = math.fsum(tp) / len(tp)
    md = math.fsum(abs(v - mean) for v in tp) / len(tp)
    return 0.0 if md == 0 else (tp[-1] - mean) / (0.015 * md)


def _mfi(window: Sequence[Candle]) -> float:
    pos = neg = 0.0
    prev = None
    for c in window:
        tp = (c.high + c.low + c.close) / 3.0
        if prev is not None:
            flow = tp * c.volume
            if tp > prev:
                pos += flow
            elif tp < prev:
                neg += flow
        prev = tp
    return 0.0 if pos + neg == 0 else (pos - neg) / (pos + neg)


def _obv_slope(window: Sequence[Candle]) -> float:
    obv, series = 0.0, []
    for i, c in enumerate(window):
        if i:
            obv += c.volume if c.close > window[i - 1].close else -c.volume if c.close < window[i - 1].close else 0.0
        series.append(obv)
    total = math.fsum(c.volume for c in window)
    return 0.0 if total == 0 else (series[-1] - series[0]) / total


def raw_scores(history: Sequence[Candle], panel: Optional[Sequence[str]] = None) -> Optional[dict[str, float]]:
    """Signed score in (-1, 1) per indicator, or None when any input is
    missing (fail-closed: an incomplete panel is not a vote)."""
    if len(history) < MIN_BARS:
        return None
    h = history[len(history) - MIN_BARS:]
    closes = [c.close for c in h]
    w14, w20 = h[-14:], h[-20:]
    parts = {
        "ema_trend": ind.price_vs_ema(closes, 20),
        "roc": ind.roc(closes, 14),
        "rsi": ind.rsi(closes, 14),
        "vwap_dev": ind.vwap_deviation(h, 20),
        "ma_alignment": ind.ma_alignment(closes, (5, 10, 20)),
    }
    macd = ind.macd(closes)
    bb = ind.bollinger(closes, 20)
    don = ind.donchian(h, 20)
    atr = ind.atr(h, 14)
    if any(v is None for v in parts.values()) or macd is None or bb is None or don is None or not atr or atr <= 0:
        return None
    px = closes[-1]
    lo, hi = don
    out = {
        "ema_trend": _squash(parts["ema_trend"], 0.03),
        "roc": _squash(parts["roc"], 0.05),
        "rsi": (parts["rsi"] - 50.0) / 50.0,
        "ma_alignment": float(parts["ma_alignment"]),
        "macd_hist": _squash(macd[2] / atr, 0.25),
        "donchian_pos": 0.0 if hi == lo else max(-1.0, min(1.0, (px - lo) / (hi - lo) * 2.0 - 1.0)),
        "stoch": _stoch(w14),
        "cci": _squash(_cci(w20), 100.0),
        # Mean-reversion indicators vote AGAINST stretch: high %B -> bearish.
        "bollinger_b": -_squash(bb["zscore"], 2.0),
        "vwap_dev": -_squash(parts["vwap_dev"], 0.02),
        "obv_slope": _squash(_obv_slope(h), 0.3),
        "mfi": _mfi(w14),
    }
    if not all(math.isfinite(v) for v in out.values()):
        return None
    return out if panel is None else {k: out[k] for k in panel}


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


@dataclass(frozen=True)
class PlattCalibrator:
    """p = sigmoid(a * score + b); a=b=0 means "no information"."""

    a: float = 0.0
    b: float = 0.0
    n: int = 0

    def predict(self, score: float) -> float:
        return _sigmoid(self.a * score + self.b)

    @staticmethod
    def fit(scores: Sequence[float], outcomes: Sequence[int], *, l2: float = 5.0, iters: int = 25,
            min_rows: int = 30) -> "PlattCalibrator":
        """Ridge logistic regression via Newton steps. Too few rows -> the
        uninformative calibrator (never a confident one)."""
        n = len(scores)
        if n != len(outcomes):
            raise ValueError("scores and outcomes must have the same length")
        if n < min_rows:
            return PlattCalibrator(n=n)
        a = b = 0.0
        for _ in range(iters):
            ga = gb = haa = hab = hbb = 0.0
            for x, y in zip(scores, outcomes):
                p = _sigmoid(a * x + b)
                r, wgt = p - y, p * (1.0 - p)
                ga += r * x
                gb += r
                haa += wgt * x * x
                hab += wgt * x
                hbb += wgt
            ga += l2 * a  # the intercept is not penalised
            haa += l2
            det = haa * hbb - hab * hab
            if det <= 1e-12:
                break
            da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
            a, b = a - da, b - db
            if abs(da) + abs(db) < 1e-8:
                break
        return PlattCalibrator(a=a, b=b, n=n)


@dataclass(frozen=True)
class Verdict:
    p_long: float  # combined probability, 0.5 = no view
    per_indicator: dict  # name -> calibrated P(long)
    agree_long: int
    agree_short: int

    @property
    def side(self) -> int:
        return 1 if self.p_long > 0.5 else -1 if self.p_long < 0.5 else 0

    @property
    def confidence(self) -> float:
        """Confidence in the chosen side, in [0.5, 1)."""
        return max(self.p_long, 1.0 - self.p_long)


def combine_votes(probs: dict[str, float], weights: Optional[dict[str, float]] = None) -> Verdict:
    """Weighted mean of log-odds (equal weights by default)."""
    if not probs:
        return Verdict(0.5, {}, 0, 0)
    eps = 1e-6
    wts = {k: (weights or {}).get(k, 1.0) for k in probs}
    total = sum(wts.values())
    if total <= 0:
        return Verdict(0.5, dict(probs), 0, 0)
    z = sum(wts[k] * math.log(min(max(p, eps), 1 - eps) / (1 - min(max(p, eps), 1 - eps))) for k, p in probs.items()) / total
    return Verdict(
        _sigmoid(z), dict(probs),
        sum(1 for p in probs.values() if p > 0.5), sum(1 for p in probs.values() if p < 0.5),
    )
