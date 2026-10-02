"""Adaptive weighted ensemble of RSI, Bollinger %B, volume z-score and
VWAP deviation.

User's own idea (2026-09-28): combine several standard indicators and
adjust their weights as data accumulates, instead of committing to one
fixed rule. For that to be pre-registerable (CLAUDE.md rule 1:
walk-forward -> PBO/DSR -> one held-out TEST, no shortcuts), "adjust
weights" cannot mean free-form tuning after seeing a result -- it has to
be ONE fixed, deterministic procedure, decided before any result is
seen. Otherwise every weight choice is a hidden extra trial that PBO/DSR
never gets to deflate against.

The fixed procedure: at each bar, each indicator's "weight" is its own
Pearson correlation with next-bar returns over the trailing
`fit_lookback` bars of ALREADY-REALIZED history (the bar being decided
is never in that window); today's four indicator values are combined
with those correlations and thresholded at zero. Correlation-weighting
(rather than a fitted multi-feature regression) is a deliberate
simplification: it needs no matrix solve, cannot be singular, and is
computable from four small running sums per feature -- important since
src/ is stdlib-only (no numpy) and this runs across tens of thousands of
bars per walk-forward fold.

Efficiency note: the four correlations' running sums are prefix sums
over the whole visible history, cached per underlying candle sequence
(keyed by id(), since `backtest.engine.PrefixView` shares one growing
list across a single sequential run) and extended by one entry per call
-- O(1) amortized per bar instead of re-scanning `fit_lookback` bars
every call. A different underlying sequence (a new walk-forward fold or
TEST run) is detected by identity and rebuilds from scratch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from cointrader.data.models import Candle


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: Sequence[float], mean: float) -> float:
    if len(xs) < 2:
        return 0.0
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    return math.sqrt(var)


def _rsi(closes: Sequence[float]) -> float:
    """Simple (non-Wilder) RSI over `len(closes) - 1` changes, scaled to
    [-1, 1] via (RSI-50)/50."""
    gains = losses = 0.0
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    n = len(closes) - 1
    avg_gain, avg_loss = gains / n, losses / n
    if avg_gain + avg_loss == 0:
        return 0.0
    rsi = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return (rsi - 50.0) / 50.0


def _bollinger_feature(closes: Sequence[float], *, k: float = 2.0) -> float:
    mean = _mean(closes)
    std = _stdev(closes, mean)
    if std == 0:
        return 0.0
    upper, lower = mean + k * std, mean - k * std
    pct_b = (closes[-1] - lower) / (upper - lower)
    return _clip((pct_b - 0.5) * 2.0, -2.0, 2.0)


def _volume_feature(volumes: Sequence[float]) -> float:
    mean = _mean(volumes)
    std = _stdev(volumes, mean)
    if std == 0:
        return 0.0
    return _clip((volumes[-1] - mean) / std / 3.0, -2.0, 2.0)


def _vwap_feature(window: Sequence[Candle]) -> float:
    total_volume = sum(c.volume for c in window)
    if total_volume == 0:
        return 0.0
    vwap = sum(c.close * c.volume for c in window) / total_volume
    if vwap == 0:
        return 0.0
    return _clip((window[-1].close - vwap) / vwap * 20.0, -2.0, 2.0)


def _features(window: Sequence[Candle]) -> tuple[float, float, float, float]:
    closes = [c.close for c in window]
    volumes = [c.volume for c in window]
    return (_rsi(closes), _bollinger_feature(closes), _volume_feature(volumes), _vwap_feature(window))


_NUM_FEATURES = 4


class _RunningSums:
    """Prefix sums of (x, x^2, y, y^2, x*y) for each of the 4 features,
    indexed by row number (row j = the training pair built from bar
    `w - 1 + j`). Index 0 is the all-zero baseline so a window
    [a, b) sums to prefix[b] - prefix[a]."""

    __slots__ = ("sum_x", "sum_x2", "sum_y", "sum_y2", "sum_xy", "first_bar")

    def __init__(self, first_bar: int) -> None:
        self.first_bar = first_bar  # the history index the first row (row 0) corresponds to
        self.sum_x = [[0.0] for _ in range(_NUM_FEATURES)]
        self.sum_x2 = [[0.0] for _ in range(_NUM_FEATURES)]
        self.sum_y = [0.0]
        self.sum_y2 = [0.0]
        self.sum_xy = [[0.0] for _ in range(_NUM_FEATURES)]

    def append(self, feats: tuple[float, float, float, float], y: float) -> None:
        self.sum_y.append(self.sum_y[-1] + y)
        self.sum_y2.append(self.sum_y2[-1] + y * y)
        for k in range(_NUM_FEATURES):
            x = feats[k]
            self.sum_x[k].append(self.sum_x[k][-1] + x)
            self.sum_x2[k].append(self.sum_x2[k][-1] + x * x)
            self.sum_xy[k].append(self.sum_xy[k][-1] + x * y)

    @property
    def n_rows(self) -> int:
        return len(self.sum_y) - 1

    def correlations(self, a: int, b: int) -> tuple[float, float, float, float]:
        """Pearson correlation of each feature with `y` over rows [a, b)."""
        count = b - a
        if count < 2:
            return (0.0, 0.0, 0.0, 0.0)
        sy, sy2 = self.sum_y[b] - self.sum_y[a], self.sum_y2[b] - self.sum_y2[a]
        var_y = count * sy2 - sy * sy
        out = []
        for k in range(_NUM_FEATURES):
            sx = self.sum_x[k][b] - self.sum_x[k][a]
            sx2 = self.sum_x2[k][b] - self.sum_x2[k][a]
            sxy = self.sum_xy[k][b] - self.sum_xy[k][a]
            var_x = count * sx2 - sx * sx
            denom = var_x * var_y
            out.append(0.0 if denom <= 0 else (count * sxy - sx * sy) / math.sqrt(denom))
        return tuple(out)


@dataclass(frozen=True)
class AdaptiveIndicatorEnsemble:
    """Long when the sum of (rolling correlation x today's indicator
    value), over RSI/Bollinger %B/volume z-score/VWAP deviation, is
    positive; flat otherwise. `fit_lookback` and `indicator_window` are
    fixed hyperparameters, part of what gets pre-registered -- never
    tuned after seeing a result."""

    fit_lookback: int
    indicator_window: int = 20
    hysteresis: float = 0.0
    _cache: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.indicator_window < 5:
            raise ValueError("indicator_window must be >= 5")
        if self.fit_lookback < 5 * self.indicator_window:
            raise ValueError("fit_lookback must be well above indicator_window to have enough training rows")
        if self.hysteresis < 0:
            raise ValueError("hysteresis must be >= 0")

    @property
    def name(self) -> str:
        suffix = f"_h{self.hysteresis:g}" if self.hysteresis else ""
        return f"adaptive_ensemble_{self.fit_lookback}{suffix}"

    @property
    def warmup(self) -> int:
        return self.fit_lookback + self.indicator_window + 1

    def __call__(self, history: Sequence[Candle]) -> float:
        n = len(history)
        if n <= self.warmup:
            return 0.0
        w = self.indicator_window
        underlying = getattr(history, "_candles", history)
        if self._cache.get("id") != id(underlying):
            self._cache.clear()
            self._cache["id"] = id(underlying)
            self._cache["sums"] = _RunningSums(first_bar=w - 1)
            self._cache["last_exposure"] = 0.0
        sums: _RunningSums = self._cache["sums"]
        # Extend to cover every row i (i = w-1+row) with i <= n - 2 (its
        # target uses close[i+1], which must already be visible).
        next_i = sums.first_bar + sums.n_rows
        while next_i <= n - 2:
            window = history[next_i - w + 1 : next_i + 1]
            feats = _features(window)
            target = history[next_i + 1].close / history[next_i].close - 1.0
            sums.append(feats, target)
            next_i += 1

        row_b = sums.n_rows  # rows [0, row_b) available, corresponding to bars [w-1, w-1+row_b) = [w-1, n-1)
        row_a = max(0, row_b - self.fit_lookback)
        if row_b - row_a < 5 * w:
            return 0.0
        weights = sums.correlations(row_a, row_b)

        today = _features(history[n - w : n])
        score = sum(wt * ft for wt, ft in zip(weights, today))

        if self.hysteresis == 0.0:
            exposure = 1.0 if score > 0 else 0.0
        else:
            # Only flip when the signal clears the band; otherwise hold
            # the previous bar's position. This is what turns a
            # bar-to-bar sign flip into a real regime change before it's
            # traded, so a noisy score near zero doesn't churn the
            # position (and its costs) every bar.
            previous = self._cache["last_exposure"]
            if score > self.hysteresis:
                exposure = 1.0
            elif score < -self.hysteresis:
                exposure = 0.0
            else:
                exposure = previous
        self._cache["last_exposure"] = exposure
        return exposure


def adaptive_candidate_grid() -> list:
    """Three fixed lookback variants -- a family of the same one
    procedure, each its own pre-registered trial (like
    `momentum_candidate_grid`), not free tuning."""
    return [AdaptiveIndicatorEnsemble(n) for n in (240, 500, 720)]


def adaptive_hysteresis_candidate_grid() -> list:
    """Same procedure, with a fixed hysteresis band added after H-0003
    showed the un-banded version churns position every time the
    correlation-weighted score's sign flips, bleeding capital to costs.
    Band width (0.3, on a score whose per-feature terms are each bounded
    to [-2, 2]) is a fixed design choice made before this hypothesis was
    run, not tuned on its result."""
    return [AdaptiveIndicatorEnsemble(n, hysteresis=0.3) for n in (240, 500, 720)]
