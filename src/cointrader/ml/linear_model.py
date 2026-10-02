"""Ridge regression, pure Python (closed form, Gauss-Jordan solve).

Adapted from tlsehd195/NEW- `ml/linear_model.py`. Differences: features
are standardized with the TRAIN mean/std before the penalty is applied
(crypto indicators live on very different scales, so an unstandardized
ridge penalizes them unevenly) and the intercept is never penalized.

`predict_with_confidence` returns `(prediction, confidence)`.
`confidence` in [0, 1] is |prediction| relative to two training-target
standard deviations. It is a SIZE-OF-SIGNAL score, not a calibrated
probability of being right -- do not present it as a win rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

DEFAULT_RIDGE = 1.0
CANDIDATE_RIDGES: tuple[float, ...] = (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    n = len(vector)
    aug = [list(matrix[i]) + [vector[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]) if math.isfinite(aug[r][col]) else -1.0)
        pv = aug[pivot][col]
        if not math.isfinite(pv) or abs(pv) < 1e-12:
            raise ValueError("singular matrix -- cannot fit linear model on this data")
        aug[col], aug[pivot] = aug[pivot], aug[col]
        aug[col] = [v / aug[col][col] for v in aug[col]]
        for row in range(n):
            if row != col and aug[row][col] != 0.0:
                f = aug[row][col]
                aug[row] = [aug[row][k] - f * aug[col][k] for k in range(n + 1)]
    return [aug[i][n] for i in range(n)]


@dataclass
class RidgeModel:
    feature_ids: Sequence[str]
    ridge: float = DEFAULT_RIDGE
    _mean: dict = field(default_factory=dict, init=False, repr=False)
    _std: dict = field(default_factory=dict, init=False, repr=False)
    _coef: Optional[dict] = field(default=None, init=False, repr=False)
    _intercept: Optional[float] = field(default=None, init=False, repr=False)
    _target_std: float = field(default=0.0, init=False, repr=False)

    def fit(self, samples: Sequence) -> None:
        if not samples:
            raise ValueError("cannot fit RidgeModel on an empty sample set")
        ids = list(self.feature_ids)
        ys = [s.target for s in samples]
        if not all(math.isfinite(y) for y in ys):
            raise ValueError("cannot fit RidgeModel on a non-finite target value")
        n = len(samples)
        for f in ids:
            col = [s.features[f] for s in samples]
            if not all(math.isfinite(v) for v in col):
                raise ValueError(f"cannot fit RidgeModel on a non-finite value of {f}")
            m = math.fsum(col) / n
            var = math.fsum((v - m) ** 2 for v in col) / n
            self._mean[f], self._std[f] = m, (math.sqrt(var) if var > 1e-18 else 1.0)
        y_mean = math.fsum(ys) / n
        self._target_std = math.sqrt(math.fsum((y - y_mean) ** 2 for y in ys) / n)
        z = [[(s.features[f] - self._mean[f]) / self._std[f] for f in ids] for s in samples]
        k = len(ids)
        xtx = [[math.fsum(row[a] * row[b] for row in z) for b in range(k)] for a in range(k)]
        for i in range(k):
            xtx[i][i] += self.ridge
        xty = [math.fsum(z[i][a] * (ys[i] - y_mean) for i in range(n)) for a in range(k)]
        w = _solve(xtx, xty)
        if not all(math.isfinite(v) for v in w):
            raise ValueError("cannot fit RidgeModel -- non-finite coefficient")
        self._coef = dict(zip(ids, w))
        self._intercept = y_mean

    def predict(self, features: dict) -> float:
        if self._coef is None or self._intercept is None:
            raise RuntimeError("fit() must be called before predict()")
        missing = set(self.feature_ids) - set(features)
        if missing:
            raise ValueError(f"predict() missing feature(s): {sorted(missing)}")
        return self._intercept + sum(
            self._coef[f] * (features[f] - self._mean[f]) / self._std[f] for f in self.feature_ids
        )

    def predict_with_confidence(self, features: dict) -> tuple[float, float]:
        p = self.predict(features)
        if self._target_std <= 0:
            return p, 0.0
        return p, min(1.0, abs(p - self._intercept) / (2.0 * self._target_std))

    @property
    def coefficients(self) -> Optional[dict]:
        """Coefficients on STANDARDIZED features (comparable across features)."""
        return dict(self._coef) if self._coef is not None else None


def select_ridge_via_expanding_window_cv(
    samples: Sequence, feature_ids: Sequence[str],
    candidate_ridges: Sequence[float] = CANDIDATE_RIDGES, folds: int = 3,
) -> float:
    """Pick the ridge strength with the lowest out-of-fold squared error
    using chronological expanding-window folds (never a random split:
    neighbouring bars are correlated). A training sample is only used for
    a fold if its `target_time` is at or before the fold's test start, so
    no CV training target overlaps the test period. The grid is fixed
    before any result is seen. Falls back to the weakest candidate when
    there is too little history; never raises."""
    ordered = sorted(samples, key=lambda s: s.as_of_time)
    if len(ordered) < (folds + 1) * (len(feature_ids) + 2):
        return candidate_ridges[0]
    cut = [ordered[int(len(ordered) * (i + 1) / (folds + 1))].as_of_time for i in range(folds)]
    best, best_err = candidate_ridges[0], None
    for ridge in candidate_ridges:
        errs: list[float] = []
        for i, start in enumerate(cut):
            end = cut[i + 1] if i + 1 < len(cut) else None
            train = [s for s in ordered if s.target_time <= start]
            test = [s for s in ordered if s.as_of_time >= start and (end is None or s.as_of_time < end)]
            if len(train) < len(feature_ids) + 2 or not test:
                continue
            model = RidgeModel(list(feature_ids), ridge)
            try:
                model.fit(train)
            except ValueError:
                continue
            errs.extend((model.predict(s.features) - s.target) ** 2 for s in test)
        if errs:
            mse = math.fsum(errs) / len(errs)
            if best_err is None or mse < best_err:
                best, best_err = ridge, mse
    return best
