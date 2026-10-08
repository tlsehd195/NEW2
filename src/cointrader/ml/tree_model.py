"""Small bagged ensemble of shallow regression trees, pure Python.

Adapted from tlsehd195/NEW- `ml/tree_model.py`. Hyperparameters are
FIXED (not searched) so the model adds no hidden extra trials on top of
what PBO/DSR already deflate. Deterministic: a local `random.Random`
seeded by the caller, never wall-clock time.

`predict_with_confidence` returns `(mean_prediction, agreement)` where
`agreement` in [0, 1] is the share of trees whose prediction has the
same sign as the mean. 1.0 = every tree agrees. It is model agreement,
NOT a calibrated probability of profit.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional, Sequence

MAX_DEPTH = 2
MIN_SAMPLES_LEAF_FRACTION = 0.1
MIN_SAMPLES_LEAF_FLOOR = 5
N_ESTIMATORS = 25
FEATURE_SUBSAMPLE_SIZE = 3
DEFAULT_RANDOM_SEED = 20261002


@dataclass(frozen=True)
class _Node:
    value: Optional[float] = None
    feature_id: Optional[str] = None
    threshold: Optional[float] = None
    left: Optional["_Node"] = None
    right: Optional["_Node"] = None

    @property
    def is_leaf(self) -> bool:
        return self.feature_id is None


def _sse(ys: Sequence[float]) -> float:
    if len(ys) < 2:
        return 0.0
    m = math.fsum(ys) / len(ys)
    return math.fsum((y - m) ** 2 for y in ys)


def _best_split(samples, feature_ids, min_leaf):
    """Best (feature, threshold) by SSE reduction. One sorted pass per feature with running sums
    (O(n log n)); same candidate thresholds (midpoints between distinct values), same leaf-size rule
    and same first-best tie-breaking as the original quadratic scan (ADR-0044)."""
    n = len(samples)
    ys = [s.target for s in samples]
    total, total_sq = math.fsum(ys), math.fsum(y * y for y in ys)
    parent = _sse(ys)
    best, best_gain = None, 0.0
    for f in feature_ids:
        xs = [s.features[f] for s in samples]
        order = sorted(range(n), key=xs.__getitem__)
        left_sum = left_sq = 0.0
        for k in range(n - 1):
            y = ys[order[k]]
            left_sum += y
            left_sq += y * y
            a, b = xs[order[k]], xs[order[k + 1]]
            nl = k + 1
            nr = n - nl
            if a == b or nl < min_leaf or nr < min_leaf:
                continue
            right_sum = total - left_sum
            gain = parent - (left_sq - left_sum * left_sum / nl) - ((total_sq - left_sq) - right_sum * right_sum / nr)
            if gain > best_gain:
                best, best_gain = (f, (a + b) / 2.0), gain
    return best


def _build(samples, feature_ids, depth, max_depth, min_leaf) -> _Node:
    value = math.fsum(s.target for s in samples) / len(samples)
    if depth >= max_depth or len(samples) < 2 * min_leaf:
        return _Node(value=value)
    split = _best_split(samples, feature_ids, min_leaf)
    if split is None:
        return _Node(value=value)
    f, thr = split
    return _Node(
        feature_id=f, threshold=thr,
        left=_build([s for s in samples if s.features[f] <= thr], feature_ids, depth + 1, max_depth, min_leaf),
        right=_build([s for s in samples if s.features[f] > thr], feature_ids, depth + 1, max_depth, min_leaf),
    )


def _walk(node: _Node, features: dict) -> float:
    while not node.is_leaf:
        node = node.left if features[node.feature_id] <= node.threshold else node.right
    return node.value


@dataclass
class BaggedTreeModel:
    feature_ids: Sequence[str]
    n_estimators: int = N_ESTIMATORS
    max_depth: int = MAX_DEPTH
    min_samples_leaf_fraction: float = MIN_SAMPLES_LEAF_FRACTION
    min_samples_leaf_floor: int = MIN_SAMPLES_LEAF_FLOOR
    feature_subsample_size: int = FEATURE_SUBSAMPLE_SIZE
    random_seed: int = DEFAULT_RANDOM_SEED
    _trees: list = field(default_factory=list, init=False, repr=False)

    def fit(self, samples: Sequence) -> None:
        if not samples:
            raise ValueError("cannot fit BaggedTreeModel on an empty sample set")
        samples = list(samples)
        if not all(math.isfinite(s.target) for s in samples):
            raise ValueError("cannot fit BaggedTreeModel on a non-finite target value")
        for s in samples:
            if not all(math.isfinite(s.features[f]) for f in self.feature_ids):
                raise ValueError("cannot fit BaggedTreeModel on a non-finite feature value")
        rng = random.Random(self.random_seed)
        n = len(samples)
        k = min(self.feature_subsample_size, len(self.feature_ids))
        min_leaf = max(self.min_samples_leaf_floor, int(n * self.min_samples_leaf_fraction))
        self._trees = []
        for _ in range(self.n_estimators):
            boot = [samples[rng.randrange(n)] for _ in range(n)]
            ids = rng.sample(list(self.feature_ids), k)
            self._trees.append(_build(boot, ids, 0, self.max_depth, min_leaf))

    def predict(self, features: dict) -> float:
        return self.predict_with_confidence(features)[0]

    def predict_with_confidence(self, features: dict) -> tuple[float, float]:
        if not self._trees:
            raise RuntimeError("fit() must be called before predict()")
        missing = set(self.feature_ids) - set(features)
        if missing:
            raise ValueError(f"predict() missing feature(s): {sorted(missing)}")
        preds = [_walk(t, features) for t in self._trees]
        mean = math.fsum(preds) / len(preds)
        if mean == 0.0:
            return 0.0, 0.0
        agree = sum(1 for p in preds if p * mean > 0) / len(preds)
        return mean, agree
