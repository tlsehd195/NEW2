"""White's Reality Check (2000) and Hansen's SPA (2005).

Ported from tlsehd195/NEW- (`strategy_research/reality_check_spa.py`),
rewritten stdlib-only (numpy -> `random.Random`) because `src/` stays
standard-library only. Same math, same fold-level granularity as
`pbo_dsr`.

Question: after trying N candidates, does the best one's excess return
over the benchmark survive a data-snooping-aware test? Supplementary to
PBO/DSR, never a replacement: it adds a field to the report, it does not
change a verdict. Randomness is confined to the bootstrap and always
seeded by the caller.

- White, H. (2000). "A Reality Check for Data Snooping." Econometrica 68(5).
- Hansen, P. R. (2005). "A Test for Superior Predictive Ability." JBES 23(4).
- Politis & Romano (1994). "The Stationary Bootstrap." JASA 89(428).
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class DataSnoopingResult:
    """`p_value` low (< 0.05) = the best candidate's edge is unlikely to
    be a data-snooping artifact. `test` is "white_rc" or "hansen_spa"."""

    test: str
    p_value: float
    observed_statistic: float
    best_candidate: str
    num_bootstrap_samples: int
    num_folds: int
    candidate_names: tuple[str, ...]


def excess_returns_vs_benchmark(
    fold_returns_by_candidate: Mapping[str, Sequence[float]], benchmark_fold_returns: Sequence[float],
) -> dict[str, list[float]]:
    """candidate - benchmark per fold; same fold order for everything."""
    n = len(benchmark_fold_returns)
    for name, returns in fold_returns_by_candidate.items():
        if len(returns) != n:
            raise ValueError(f"candidate {name!r} has {len(returns)} fold returns, benchmark has {n} -- must match")
    return {name: [r - b for r, b in zip(rs, benchmark_fold_returns)] for name, rs in fold_returns_by_candidate.items()}


def _validate(excess: Mapping[str, Sequence[float]]) -> tuple[tuple[str, ...], int]:
    names = tuple(sorted(excess))
    if not names:
        raise ValueError("need at least 1 candidate")
    n = len(excess[names[0]])
    for name in names:
        if len(excess[name]) != n:
            raise ValueError(f"all candidates need the same fold count; {names[0]!r} has {n}, {name!r} has {len(excess[name])}")
    if n < 2:
        raise ValueError(f"need at least 2 folds, got {n}")
    return names, n


def _stationary_indices(n: int, mean_block_length: float, rng: random.Random) -> list[int]:
    """Politis-Romano stationary bootstrap: geometric blocks, circular."""
    p = 1.0 / mean_block_length
    out: list[int] = []
    cur = rng.randrange(n)
    while len(out) < n:
        out.append(cur)
        cur = rng.randrange(n) if rng.random() < p else (cur + 1) % n
    return out


def white_reality_check(
    excess: Mapping[str, Sequence[float]], *, num_bootstrap_samples: int = 2000,
    mean_block_length: float = 4.0, seed: int = 0,
) -> DataSnoopingResult:
    names, n = _validate(excess)
    means = {k: statistics.fmean(excess[k]) for k in names}
    observed = max(math.sqrt(n) * means[k] for k in names)
    rng = random.Random(seed)
    exceed = 0
    for _ in range(num_bootstrap_samples):
        idx = _stationary_indices(n, mean_block_length, rng)
        stat = max(math.sqrt(n) * (statistics.fmean(excess[k][i] for i in idx) - means[k]) for k in names)
        exceed += stat >= observed
    return DataSnoopingResult("white_rc", exceed / num_bootstrap_samples, observed, max(names, key=means.get),
                              num_bootstrap_samples, n, names)


def hansen_spa(
    excess: Mapping[str, Sequence[float]], *, num_bootstrap_samples: int = 2000,
    mean_block_length: float = 4.0, seed: int = 0,
) -> DataSnoopingResult:
    """Consistent-p variant: studentized statistic, poor candidates
    recentred to zero before resampling (more power than White's RC)."""
    names, n = _validate(excess)
    means = {k: statistics.fmean(excess[k]) for k in names}
    rng = random.Random(seed)
    paths = [_stationary_indices(n, mean_block_length, rng) for _ in range(num_bootstrap_samples)]
    boot = {k: [statistics.fmean(excess[k][i] for i in idx) for idx in paths] for k in names}
    sd = {k: (statistics.pstdev(boot[k]) * math.sqrt(n)) or 1e-12 for k in names}
    stud = {k: math.sqrt(n) * means[k] / sd[k] for k in names}
    observed = max(stud.values())
    thresh = {k: -math.sqrt(2 * math.log(math.log(max(n, 3))) / n) * (sd[k] / math.sqrt(n)) for k in names}
    centred = {k: means[k] if means[k] > thresh[k] else 0.0 for k in names}
    exceed = 0
    for b in range(num_bootstrap_samples):
        stat = max(math.sqrt(n) * (boot[k][b] - centred[k]) / sd[k] for k in names)
        exceed += stat >= observed
    return DataSnoopingResult("hansen_spa", exceed / num_bootstrap_samples, observed, max(names, key=stud.get),
                              num_bootstrap_samples, n, names)
