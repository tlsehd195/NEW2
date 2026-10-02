"""Deterministic drift detectors (mean shift, variance shift, bucket
frequency shift), adapted from tlsehd195/NEW- `monitoring/drift.py`.

Observation only: a result is `NO_DRIFT`, `DRIFT_DETECTED` or `UNKNOWN`.
Nothing here replaces a model, edits a risk limit or stops trading --
drift is a prompt for a human to re-validate (fail-closed means
`UNKNOWN` is reported with a reason, never read as "fine").

Thresholds are explicit arguments with fixed defaults, chosen before any
result is seen; `distribution_shift` is a simplified PSI-like statistic
(sum of absolute bucket-frequency differences, buckets from the
baseline's own range), not a textbook PSI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Sequence

MIN_SAMPLES = 30
MEAN_SHIFT_Z = 3.0
VARIANCE_RATIO = 2.0
DISTRIBUTION_SHIFT = 0.3
BUCKETS = 10


class DriftStatus(Enum):
    NO_DRIFT = "NO_DRIFT"
    DRIFT_DETECTED = "DRIFT_DETECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DriftResult:
    metric: str
    test: str
    status: DriftStatus
    statistic: Optional[float]
    threshold: Optional[float]
    n_baseline: int
    n_current: int
    reason: str


def _clean(values: Sequence[float]) -> list[float]:
    return [v for v in values if v is not None and math.isfinite(v)]


def _mean(xs):
    return math.fsum(xs) / len(xs)


def _var(xs):
    m = _mean(xs)
    return math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def _prep(metric, test, baseline, current, min_samples):
    b, c = _clean(baseline), _clean(current)
    if len(b) < min_samples or len(c) < min_samples:
        return b, c, DriftResult(metric, test, DriftStatus.UNKNOWN, None, None, len(b), len(c), "insufficient_sample")
    return b, c, None


def _result(metric, test, stat, thr, b, c) -> DriftResult:
    status = DriftStatus.DRIFT_DETECTED if stat >= thr else DriftStatus.NO_DRIFT
    return DriftResult(metric, test, status, stat, thr, len(b), len(c), f"{test}={stat:.4f}_threshold={thr}")


def mean_shift(metric: str, baseline: Sequence[float], current: Sequence[float], *,
               z_threshold: float = MEAN_SHIFT_Z, min_samples: int = MIN_SAMPLES) -> DriftResult:
    b, c, early = _prep(metric, "mean_shift_z", baseline, current, min_samples)
    if early:
        return early
    sd = math.sqrt(_var(b))
    if sd == 0:
        return DriftResult(metric, "mean_shift_z", DriftStatus.UNKNOWN, None, None, len(b), len(c), "zero_baseline_stdev")
    return _result(metric, "mean_shift_z", abs(_mean(c) - _mean(b)) / sd, z_threshold, b, c)


def variance_shift(metric: str, baseline: Sequence[float], current: Sequence[float], *,
                   ratio_threshold: float = VARIANCE_RATIO, min_samples: int = MIN_SAMPLES) -> DriftResult:
    b, c, early = _prep(metric, "variance_ratio", baseline, current, min_samples)
    if early:
        return early
    vb, vc = _var(b), _var(c)
    if vb == 0 or vc == 0:
        return DriftResult(metric, "variance_ratio", DriftStatus.UNKNOWN, None, None, len(b), len(c), "zero_variance")
    return _result(metric, "variance_ratio", max(vb, vc) / min(vb, vc), ratio_threshold, b, c)


def distribution_shift(metric: str, baseline: Sequence[float], current: Sequence[float], *,
                       threshold: float = DISTRIBUTION_SHIFT, buckets: int = BUCKETS,
                       min_samples: int = MIN_SAMPLES) -> DriftResult:
    b, c, early = _prep(metric, "bucket_frequency_difference", baseline, current, min_samples)
    if early:
        return early
    lo, hi = min(b), max(b)
    if lo == hi:
        return DriftResult(metric, "bucket_frequency_difference", DriftStatus.UNKNOWN, None, None,
                           len(b), len(c), "degenerate_baseline_range")
    width = (hi - lo) / buckets

    def idx(v: float) -> int:  # out-of-range values go to the edge buckets, never dropped
        return 0 if v <= lo else buckets - 1 if v >= hi else min(buckets - 1, int((v - lo) / width))

    fb, fc = [0] * buckets, [0] * buckets
    for v in b:
        fb[idx(v)] += 1
    for v in c:
        fc[idx(v)] += 1
    stat = math.fsum(abs(x / len(b) - y / len(c)) for x, y in zip(fb, fc))
    return _result(metric, "bucket_frequency_difference", stat, threshold, b, c)


def feature_drift(baseline_rows: Sequence[Mapping[str, float]], current_rows: Sequence[Mapping[str, float]],
                  feature_ids: Sequence[str]) -> list[DriftResult]:
    """Run the mean and distribution tests on every ML feature, so a model
    trained on `baseline_rows` can be checked against recent live/paper
    feature vectors. Returns one result per (feature, test)."""
    out: list[DriftResult] = []
    for f in feature_ids:
        b, c = [r[f] for r in baseline_rows if f in r], [r[f] for r in current_rows if f in r]
        out.append(mean_shift(f, b, c))
        out.append(distribution_shift(f, b, c))
    return out


def any_drift(results: Sequence[DriftResult]) -> bool:
    return any(r.status is DriftStatus.DRIFT_DETECTED for r in results)
