# Copied verbatim from tlsehd195/NEW- (src/strategy_research/pbo_dsr.py,
# commit 2251c2e). Docstring references to docs/ and scripts/ point into
# that repository. Keep the computation identical to the source so results
# stay comparable across the two projects; see ADR-0001 in this repo.
"""Probability of Backtest Overfitting (PBO) via Combinatorially
Symmetric Cross-Validation (CSCV), and the Deflated Sharpe Ratio (DSR).

Both answer the same underlying question this module exists to answer:
when several candidate strategies have been compared against the same
real walk-forward folds, is the apparent winner's edge real, or is it
what you'd expect from picking the best of several noisy trials?

References (Tier 1 -- primary sources, formulas transcribed directly
from these papers, not from a secondary summary):

- Bailey, D. H., Borwein, J., Lopez de Prado, M., & Zhu, Q. J. (2015).
  "The Probability of Backtest Overfitting." Journal of Computational
  Finance. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Bailey, D. H., & Lopez de Prado, M. (2014). "The Deflated Sharpe
  Ratio: Correcting for Selection Bias, Backtest Overfitting, and
  Non-Normality." Journal of Portfolio Management, 40(5), 94-107.
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551

See docs/research/walk-forward-pbo-deflated-sharpe.md for the full
adoption history (DEFER through Phase 20/25) and
docs/decisions/ADR-0035-pbo-deflated-sharpe-implementation.md for why
this was built now and the specific adaptation decisions below.

**Adaptation from the original papers, stated explicitly rather than
silently assumed**: both papers operate on a matrix of per-PERIOD
(typically daily) returns. This project's walk-forward evaluation
instead produces one aggregated NET return per (strategy, fold) pair,
where each fold is already an independent, non-overlapping,
genuinely out-of-sample test window (Phase 25,
`strategy_research.walk_forward_evaluation.WalkForwardAggregate`).
This module treats each FOLD as one CSCV observation directly, rather
than sub-dividing folds into finer periods. Given this project's folds
are already independent out-of-sample periods (not overlapping
cross-validation folds needing purging/embargo -- see the design doc's
section 2.2), this is a faithful, conservative reading of the same
question ("does the in-sample-best candidate's edge persist
out-of-sample"), not a different one -- it only changes the
granularity of what counts as one observation.

This module is pure computation over already-computed return series --
no network access, no wall-clock reads, no randomness in any
non-test code path. It answers questions about results that already
exist; it does not, and must not, run any backtest itself.
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence

_EULER_MASCHERONI = 0.5772156649015329

_normal = statistics.NormalDist()


@dataclass(frozen=True)
class PboResult:
    """`probability` is the CSCV estimate of the Probability of Backtest
    Overfitting: across every way of splitting the folds into an
    in-sample (IS) half and an out-of-sample (OOS) half, the fraction
    of splits where the candidate that looked best IN-SAMPLE ranked
    below the OOS median. High (near 0.5) means the apparent winner is
    indistinguishable from picking one of several noisy trials at
    random; low (near 0) means the IS-winner's edge tends to persist
    OOS."""

    probability: float
    num_combinations: int
    num_candidates: int
    num_groups: int
    candidate_names: tuple[str, ...]
    logits: tuple[float, ...]


def compute_pbo(
    fold_returns_by_candidate: Mapping[str, Sequence[float]],
    *,
    num_groups: int = 8,
) -> PboResult:
    """CSCV estimate of PBO (Bailey et al. 2015, section 2).

    `fold_returns_by_candidate`: `{candidate_name: [return_per_fold, ...]}`.
    Every candidate must have the identical fold count, in the same
    fold order (fold i must be the same real calendar window for every
    candidate) -- this is the caller's responsibility to guarantee
    (true by construction when every candidate was walk-forward
    evaluated over the same `overall_start`/`overall_end`/window
    parameters, as `scripts/run_long_horizon_validation.py` already
    does).

    `num_groups` (S in the paper, must be even) splits the folds into
    S contiguous groups; every one of the C(S, S/2) ways to pick S/2
    groups as in-sample is evaluated. The paper's own worked examples
    use S=16; this project defaults to 8 (2 folds' worth of caution
    smaller, chosen only because 76 real folds / 16 groups leaves each
    group with <5 folds when the fold count is smaller in a future,
    shorter-history run -- not tuned against any observed result,
    fixed before this function was ever run against real data)."""
    names = tuple(sorted(fold_returns_by_candidate))
    if len(names) < 2:
        raise ValueError(f"PBO requires at least 2 candidates to compare, got {len(names)}")

    fold_count = len(fold_returns_by_candidate[names[0]])
    for name in names:
        if len(fold_returns_by_candidate[name]) != fold_count:
            raise ValueError(
                f"all candidates must have the same fold count in the same order; "
                f"{names[0]!r} has {fold_count}, {name!r} has {len(fold_returns_by_candidate[name])}"
            )
    if fold_count == 0:
        raise ValueError("cannot compute PBO with zero folds")
    if num_groups < 2 or num_groups % 2 != 0:
        raise ValueError(f"num_groups must be even and >= 2, got {num_groups}")
    if fold_count < num_groups:
        raise ValueError(
            f"need at least {num_groups} folds to form {num_groups} CSCV groups, got {fold_count}"
        )

    group_size = fold_count // num_groups
    groups: list[list[int]] = []
    start = 0
    for g in range(num_groups):
        # The last group absorbs any remainder so every fold is used exactly
        # once. NOT the only defensible convention: `purgedcv._pbo.
        # _contiguous_blocks` instead spreads the remainder across the
        # FIRST `remainder` groups -- confirmed (2026-09-26,
        # scripts/cross_verify_pbo_dsr_with_purgedcv.py, ADR-0207) to be
        # the entire cause of a real ~7pp PBO gap between the two
        # implementations on this project's own 58-fold/8-group real
        # data. The Bailey et al. (2015) CSCV paper assumes an evenly
        # divisible fold count and specifies no remainder rule, so
        # neither convention is "more correct" -- this is a disclosed,
        # deliberate choice, not a bug, and is not being changed to
        # match purgedcv without a reason beyond "the other library did
        # it differently."
        size = group_size if g < num_groups - 1 else fold_count - start
        groups.append(list(range(start, start + size)))
        start += size

    half = num_groups // 2
    logits: list[float] = []
    for is_group_ids in itertools.combinations(range(num_groups), half):
        is_group_ids_set = set(is_group_ids)
        is_fold_ids = [i for g in is_group_ids for i in groups[g]]
        oos_fold_ids = [i for g in range(num_groups) if g not in is_group_ids_set for i in groups[g]]

        is_perf = {
            name: statistics.mean(fold_returns_by_candidate[name][i] for i in is_fold_ids)
            for name in names
        }
        oos_perf = {
            name: statistics.mean(fold_returns_by_candidate[name][i] for i in oos_fold_ids)
            for name in names
        }

        best_is_name = max(names, key=lambda n: is_perf[n])

        # Relative OOS rank of the IS-winner: 1 = worst OOS performer among
        # the candidates, len(names) = best. omega in (0, 1); the logit
        # (Bailey et al. eq. 6-7) is <= 0 exactly when the IS-winner's OOS
        # rank is at or below the median -- i.e. when picking the IS-best
        # did NOT pay off out-of-sample.
        oos_ranked = sorted(names, key=lambda n: oos_perf[n])
        rank = oos_ranked.index(best_is_name) + 1  # 1-indexed
        omega = rank / (len(names) + 1)
        logit = math.log(omega / (1 - omega))
        logits.append(logit)

    pbo = sum(1 for lam in logits if lam <= 0) / len(logits)

    return PboResult(
        probability=pbo,
        num_combinations=len(logits),
        num_candidates=len(names),
        num_groups=num_groups,
        candidate_names=names,
        logits=tuple(logits),
    )


def _sample_skewness(values: Sequence[float]) -> float:
    n = len(values)
    mean = statistics.mean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0:
        return 0.0
    return sum((x - mean) ** 3 for x in values) / n / stdev**3


def _sample_kurtosis(values: Sequence[float]) -> float:
    """Raw (non-excess) kurtosis -- a normal distribution has kurtosis 3
    here, NOT 0. This matches Bailey/Lopez de Prado's PSR formula
    exactly as published (the `(gamma4 - 1) / 4` term below only
    reduces correctly for normal returns if gamma4 is the raw, not
    excess, kurtosis) -- deliberately not subtracting 3, documented
    here so a future edit does not "fix" this into a bug."""
    n = len(values)
    mean = statistics.mean(values)
    stdev = statistics.pstdev(values)
    if stdev == 0:
        return 3.0  # degenerate/constant series: treat as normal-shaped, never used (stdev=0 guarded upstream)
    return sum((x - mean) ** 4 for x in values) / n / stdev**4


@dataclass(frozen=True)
class DsrResult:
    """`deflated_sharpe_ratio` is actually a PROBABILITY in [0, 1] (the
    Probabilistic Sharpe Ratio evaluated at the expected-maximum-Sharpe-
    under-the-null benchmark) -- this is exactly what the DSR papers
    themselves call it, kept unrenamed here to match the literature
    rather than invent a less confusing name. Read it as "the
    probability that this candidate's true Sharpe ratio exceeds what
    the best of `num_trials` purely noisy trials would be expected to
    show by chance" -- high (e.g. > 0.95) is the usual bar for
    'probably not just the best of N noisy draws.'"""

    candidate_name: str
    observed_sharpe: float
    deflated_sharpe_ratio: float
    expected_max_sharpe_under_null: float
    num_trials: int
    num_observations: int
    skewness: float
    kurtosis: float


def compute_dsr_for_all_candidates(
    fold_returns_by_candidate: Mapping[str, Sequence[float]],
    *,
    zero_sharpe_trials: int = 0,
) -> dict[str, DsrResult]:
    """Computes the Deflated Sharpe Ratio for every candidate in one
    call, since DSR for any one candidate requires the OTHER
    candidates' Sharpe ratios too (the "expected max Sharpe under the
    null across N trials" term is shared across all of them -- Bailey &
    Lopez de Prado 2014, eq. 10). All candidates must share the same
    fold count and order, exactly like `compute_pbo`.

    A per-candidate `observed_sharpe` is computed here as
    mean(fold_returns) / population_stdev(fold_returns) -- the walk-forward
    FOLD-level Sharpe-like statistic, computed identically for every
    candidate from the same fold-return series `compute_pbo` uses. This
    is a deliberate choice to use exactly one consistent Sharpe
    definition throughout this module rather than mixing it with, say,
    the held-out single-TEST-window Sharpe already in the report --
    that would compare a multi-fold statistic to a single-window one
    inside the same formula, which the papers do not define.

    `zero_sharpe_trials` (ADR-0220): extra trials that were evaluated but
    whose return series is identically zero (e.g. a filter that never
    fired, measured as excess over its baseline). Their Sharpe is 0; they
    still count toward the trial count and the spread of trial Sharpes,
    so leaving them out would under-deflate the winners."""
    names = tuple(sorted(fold_returns_by_candidate))
    if len(names) < 1:
        raise ValueError("need at least 1 candidate")

    fold_count = len(fold_returns_by_candidate[names[0]])
    for name in names:
        if len(fold_returns_by_candidate[name]) != fold_count:
            raise ValueError(
                f"all candidates must have the same fold count in the same order; "
                f"{names[0]!r} has {fold_count}, {name!r} has {len(fold_returns_by_candidate[name])}"
            )
    if fold_count < 2:
        raise ValueError(f"need at least 2 folds per candidate to compute a Sharpe ratio, got {fold_count}")

    observed_sharpe: dict[str, float] = {}
    for name in names:
        returns = fold_returns_by_candidate[name]
        stdev = statistics.pstdev(returns)
        if stdev == 0:
            raise ValueError(
                f"candidate {name!r} has zero variance across its {fold_count} fold returns -- "
                "Sharpe ratio (and therefore DSR) is undefined for a constant return series"
            )
        observed_sharpe[name] = statistics.mean(returns) / stdev

    if zero_sharpe_trials < 0:
        raise ValueError("zero_sharpe_trials must be >= 0")
    num_trials = len(names) + zero_sharpe_trials
    sharpe_values = list(observed_sharpe.values()) + [0.0] * zero_sharpe_trials
    variance_of_trial_sharpes = statistics.pvariance(sharpe_values) if num_trials > 1 else 0.0

    if num_trials <= 1 or variance_of_trial_sharpes <= 0:
        # A single trial (or all trials tied) has no selection-bias
        # pool to deflate against -- the expected max under a
        # 1-candidate "pool" is 0 (Bailey & Lopez de Prado's formula is
        # undefined for N=1; using 0 recovers the plain, undeflated
        # PSR(0), the honest answer when there was no selection at
        # all).
        sr0 = 0.0
    else:
        sr0 = math.sqrt(variance_of_trial_sharpes) * (
            (1 - _EULER_MASCHERONI) * _normal.inv_cdf(1 - 1 / num_trials)
            + _EULER_MASCHERONI * _normal.inv_cdf(1 - 1 / (num_trials * math.e))
        )

    results: dict[str, DsrResult] = {}
    for name in names:
        returns = fold_returns_by_candidate[name]
        sr_hat = observed_sharpe[name]
        skew = _sample_skewness(returns)
        kurt = _sample_kurtosis(returns)
        n = len(returns)

        denom = math.sqrt(max(1e-12, 1 - skew * sr_hat + ((kurt - 1) / 4) * sr_hat**2))
        z = (sr_hat - sr0) * math.sqrt(n - 1) / denom
        psr = _normal.cdf(z)

        results[name] = DsrResult(
            candidate_name=name,
            observed_sharpe=sr_hat,
            deflated_sharpe_ratio=psr,
            expected_max_sharpe_under_null=sr0,
            num_trials=num_trials,
            num_observations=n,
            skewness=skew,
            kurtosis=kurt,
        )

    return results
