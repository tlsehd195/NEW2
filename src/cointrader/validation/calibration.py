"""Probability calibration metrics for forecasts of an up-move (ADR-0034).

Borrowed idea: buberlo/jev-trader's calibration loop (Brier score, log
loss, expected calibration error, reliability curve). Our indicator vote
(ADR-0023) turns six indicators into a "long probability" in percent, and
`MLStrategy` reports a confidence. Neither number means anything to the
entry threshold unless predicted 70% really comes true about 70% of the
time. These functions measure that from already-realized outcomes.

Pure, deterministic, standard library only. Observation only: nothing here
changes a threshold or a trade. Inputs that cannot be measured (too few
samples, non-finite values, a single outcome class) give `None` fields with
a reason, never a flattering number (fail-closed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

_EPS = 1e-12


@dataclass(frozen=True)
class ReliabilityBin:
    low: float
    high: float
    count: int
    mean_predicted: Optional[float]
    observed_rate: Optional[float]


@dataclass(frozen=True)
class CalibrationReport:
    samples: int
    brier: Optional[float]
    brier_baseline: Optional[float]  # Brier of always predicting the base rate
    log_loss: Optional[float]
    ece: Optional[float]
    base_rate: Optional[float]
    bins: tuple[ReliabilityBin, ...]
    reason: str  # empty when measurable


def _clean(probs: Sequence[float], outcomes: Sequence[int]) -> Optional[tuple[list[float], list[int]]]:
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must have the same length")
    p_out: list[float] = []
    y_out: list[int] = []
    for p, y in zip(probs, outcomes):
        if p is None or y is None or not math.isfinite(p) or not 0.0 <= p <= 1.0 or y not in (0, 1):
            return None  # one bad pair poisons the measurement: say so, do not skip silently
        p_out.append(float(p))
        y_out.append(int(y))
    return p_out, y_out


def brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> Optional[float]:
    cleaned = _clean(probs, outcomes)
    if not cleaned or not cleaned[0]:
        return None
    p, y = cleaned
    return math.fsum((a - b) ** 2 for a, b in zip(p, y)) / len(p)


def log_loss(probs: Sequence[float], outcomes: Sequence[int]) -> Optional[float]:
    cleaned = _clean(probs, outcomes)
    if not cleaned or not cleaned[0]:
        return None
    p, y = cleaned
    total = 0.0
    for a, b in zip(p, y):
        a = min(max(a, _EPS), 1 - _EPS)
        total += -(b * math.log(a) + (1 - b) * math.log(1 - a))
    return total / len(p)


def reliability_bins(probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10) -> tuple[ReliabilityBin, ...]:
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    cleaned = _clean(probs, outcomes)
    if not cleaned:
        return ()
    p, y = cleaned
    sums = [[0, 0.0, 0] for _ in range(n_bins)]  # count, sum of p, sum of y
    for a, b in zip(p, y):
        i = min(int(a * n_bins), n_bins - 1)
        sums[i][0] += 1
        sums[i][1] += a
        sums[i][2] += b
    out = []
    for i, (c, sp, sy) in enumerate(sums):
        out.append(ReliabilityBin(i / n_bins, (i + 1) / n_bins, c,
                                  sp / c if c else None, sy / c if c else None))
    return tuple(out)


def expected_calibration_error(probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10) -> Optional[float]:
    bins = reliability_bins(probs, outcomes, n_bins)
    total = sum(b.count for b in bins)
    if not total:
        return None
    return math.fsum(b.count * abs(b.mean_predicted - b.observed_rate) for b in bins if b.count) / total


def calibration_report(probs: Sequence[float], outcomes: Sequence[int], *, n_bins: int = 10,
                       min_samples: int = 100) -> CalibrationReport:
    """Everything above in one object. `reason` explains any missing numbers."""
    cleaned = _clean(probs, outcomes)
    if cleaned is None:
        return CalibrationReport(len(probs), None, None, None, None, None, (), "non-finite or out-of-range input")
    p, y = cleaned
    n = len(p)
    if n < min_samples:
        return CalibrationReport(n, None, None, None, None, None, (), f"only {n} samples, need {min_samples}")
    base = sum(y) / n
    if base in (0.0, 1.0):
        return CalibrationReport(n, None, None, None, None, base, (), "outcomes are all one class")
    return CalibrationReport(
        n, brier_score(p, y), base * (1 - base), log_loss(p, y),
        expected_calibration_error(p, y, n_bins), base, reliability_bins(p, y, n_bins), "")


def beats_base_rate(report: CalibrationReport) -> Optional[bool]:
    """True only if the forecast's Brier score is strictly below always guessing the base rate."""
    if report.brier is None or report.brier_baseline is None:
        return None
    return report.brier < report.brier_baseline
