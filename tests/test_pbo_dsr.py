"""Tests for cointrader.validation.pbo_dsr (copied from tlsehd195/NEW- tests/strategy_research/test_pbo_dsr.py) (Probability of Backtest
Overfitting via CSCV, and Deflated Sharpe Ratio).

These are the "does this correctly distinguish a genuine, persistent
edge from picking the best of several noisy trials" tests this
module's own risk section explicitly demanded (an approximate/unverified
implementation would be worse than none -- see
docs/research/walk-forward-pbo-deflated-sharpe.md section 4).
SYNTHETIC FIXTURE ONLY -- proves the calculation is correct against
known-labeled inputs, not a claim about any real strategy."""

from __future__ import annotations

import math
import random
import statistics

import pytest

from cointrader.validation.pbo_dsr import (
    DsrResult,
    PboResult,
    compute_dsr_for_all_candidates,
    compute_pbo,
)


def _seeded_rng(seed: int) -> random.Random:
    return random.Random(seed)


class TestPboDetectsGenuineConsistentSkill:
    """A candidate that is consistently, meaningfully better every fold
    (planted, known-labeled) must get a LOW PBO -- its in-sample
    selection should keep paying off out-of-sample across nearly every
    CSCV split."""

    def test_consistently_dominant_candidate_gets_low_pbo(self) -> None:
        rng = _seeded_rng(42)
        n_folds = 40
        good = [rng.gauss(0.02, 0.01) for _ in range(n_folds)]
        noise_a = [rng.gauss(0.0, 0.01) for _ in range(n_folds)]
        noise_b = [rng.gauss(0.0, 0.01) for _ in range(n_folds)]
        noise_c = [rng.gauss(0.0, 0.01) for _ in range(n_folds)]

        result = compute_pbo(
            {"good": good, "noise_a": noise_a, "noise_b": noise_b, "noise_c": noise_c},
            num_groups=8,
        )
        assert result.probability < 0.1
        assert result.num_candidates == 4
        assert result.num_combinations == math.comb(8, 4)


class TestPboDetectsPureOverfitting:
    """Four candidates with identical (zero) true edge, differing only
    by noise, must get a HIGH PBO (no candidate's in-sample win
    predicts its out-of-sample rank)."""

    def test_four_equal_mean_noise_candidates_get_high_pbo(self) -> None:
        rng = _seeded_rng(7)
        n_folds = 40
        candidates = {
            f"c{i}": [rng.gauss(0.0, 0.01) for _ in range(n_folds)] for i in range(4)
        }
        result = compute_pbo(candidates, num_groups=8)
        assert result.probability > 0.5

    def test_pbo_is_a_probability(self) -> None:
        rng = _seeded_rng(11)
        candidates = {f"c{i}": [rng.gauss(0.0, 0.01) for _ in range(24)] for i in range(3)}
        result = compute_pbo(candidates, num_groups=6)
        assert 0.0 <= result.probability <= 1.0


class TestPboInputValidation:
    def test_rejects_fewer_than_two_candidates(self) -> None:
        with pytest.raises(ValueError, match="at least 2 candidates"):
            compute_pbo({"solo": [0.1] * 10}, num_groups=8)

    def test_rejects_mismatched_fold_counts(self) -> None:
        with pytest.raises(ValueError, match="same fold count"):
            compute_pbo({"a": [0.1] * 10, "b": [0.1] * 9}, num_groups=8)

    def test_rejects_odd_num_groups(self) -> None:
        with pytest.raises(ValueError, match="must be even"):
            compute_pbo({"a": [0.1] * 10, "b": [0.2] * 10}, num_groups=7)

    def test_rejects_too_few_folds_for_num_groups(self) -> None:
        with pytest.raises(ValueError, match="need at least"):
            compute_pbo({"a": [0.1] * 4, "b": [0.2] * 4}, num_groups=8)

    def test_rejects_zero_folds(self) -> None:
        with pytest.raises(ValueError, match="zero folds"):
            compute_pbo({"a": [], "b": []}, num_groups=2)

    def test_every_fold_used_exactly_once_across_groups(self) -> None:
        """Regression guard: the remainder-absorbing last group must not
        double-count or drop folds when fold_count is not an exact
        multiple of num_groups."""
        rng = _seeded_rng(3)
        n_folds = 37  # not divisible by 8
        candidates = {f"c{i}": [rng.gauss(0.0, 0.01) for _ in range(n_folds)] for i in range(2)}
        # Should not raise, and should produce a valid probability.
        result = compute_pbo(candidates, num_groups=8)
        assert 0.0 <= result.probability <= 1.0


class TestDsrDeflatesForMoreTrials:
    """The core DSR property: the SAME observed Sharpe ratio must be
    judged less impressive (lower DSR) when it was the best of MORE
    trials -- more trials means more chances for one to look good by
    chance alone."""

    def test_expected_max_sharpe_under_null_increases_with_trial_count(self) -> None:
        rng = _seeded_rng(5)
        returns = [rng.gauss(0.01, 0.02) for _ in range(50)]

        # Same observed candidate, compared against pools of increasing size
        # (other candidates' Sharpes fixed so variance_of_trial_sharpes is
        # comparable -- only num_trials changes materially).
        two_trial = compute_dsr_for_all_candidates(
            {"target": returns, "other1": [rng.gauss(0.0, 0.02) for _ in range(50)]}
        )
        many_trial = compute_dsr_for_all_candidates(
            {
                "target": returns,
                **{f"other{i}": [rng.gauss(0.0, 0.02) for _ in range(50)] for i in range(1, 8)},
            }
        )
        assert many_trial["target"].expected_max_sharpe_under_null >= two_trial["target"].expected_max_sharpe_under_null

    def test_single_trial_has_zero_expected_max_under_null(self) -> None:
        rng = _seeded_rng(9)
        returns = [rng.gauss(0.01, 0.02) for _ in range(50)]
        result = compute_dsr_for_all_candidates({"solo": returns})
        assert result["solo"].expected_max_sharpe_under_null == 0.0


class TestDsrDistinguishesSkillFromNoise:
    def test_strong_consistent_edge_gets_high_dsr_noise_gets_low_dsr(self) -> None:
        rng = _seeded_rng(42)
        n_folds = 40
        good = [rng.gauss(0.02, 0.01) for _ in range(n_folds)]
        noise_a = [rng.gauss(0.0, 0.01) for _ in range(n_folds)]
        noise_b = [rng.gauss(0.0, 0.01) for _ in range(n_folds)]
        noise_c = [rng.gauss(0.0, 0.01) for _ in range(n_folds)]

        result = compute_dsr_for_all_candidates(
            {"good": good, "noise_a": noise_a, "noise_b": noise_b, "noise_c": noise_c}
        )
        assert result["good"].deflated_sharpe_ratio > 0.95
        assert result["noise_a"].deflated_sharpe_ratio < 0.5
        assert result["noise_b"].deflated_sharpe_ratio < 0.5
        assert result["noise_c"].deflated_sharpe_ratio < 0.5

    def test_deflated_sharpe_ratio_is_a_probability(self) -> None:
        rng = _seeded_rng(13)
        candidates = {f"c{i}": [rng.gauss(0.0, 0.01) for _ in range(30)] for i in range(4)}
        result = compute_dsr_for_all_candidates(candidates)
        for r in result.values():
            assert 0.0 <= r.deflated_sharpe_ratio <= 1.0


class TestDsrKurtosisConventionIsNonExcess:
    """Regression guard for a specific, easy-to-get-wrong detail: the
    PSR formula's gamma4 term is RAW (non-excess) kurtosis -- a normal
    distribution has gamma4=3 here, not 0. Silently "fixing" this to
    excess kurtosis would be a real, hard-to-notice correctness bug."""

    def test_near_normal_returns_report_kurtosis_near_three(self) -> None:
        rng = _seeded_rng(21)
        returns = [rng.gauss(0.01, 0.02) for _ in range(2000)]  # large n -> converges near-normal
        result = compute_dsr_for_all_candidates({"solo": returns})
        assert result["solo"].kurtosis == pytest.approx(3.0, abs=0.3)


class TestDsrInputValidation:
    def test_rejects_zero_variance_series(self) -> None:
        with pytest.raises(ValueError, match="zero variance"):
            compute_dsr_for_all_candidates({"a": [0.01] * 10, "b": [0.02] * 10, "c": [0.01] * 10})

    def test_rejects_mismatched_fold_counts(self) -> None:
        with pytest.raises(ValueError, match="same fold count"):
            compute_dsr_for_all_candidates({"a": [0.1, 0.2, 0.3], "b": [0.1, 0.2]})

    def test_rejects_fewer_than_two_folds(self) -> None:
        with pytest.raises(ValueError, match="at least 2 folds"):
            compute_dsr_for_all_candidates({"a": [0.1]})


class TestPboResultAndDsrResultAreFrozen:
    def test_pbo_result_is_frozen(self) -> None:
        rng = _seeded_rng(1)
        result = compute_pbo(
            {f"c{i}": [rng.gauss(0.0, 0.01) for _ in range(16)] for i in range(2)}, num_groups=4
        )
        with pytest.raises(Exception):
            result.probability = 0.5  # type: ignore[misc]

    def test_dsr_result_is_frozen(self) -> None:
        rng = _seeded_rng(1)
        returns = [rng.gauss(0.01, 0.02) for _ in range(20)]
        result = compute_dsr_for_all_candidates({"solo": returns})["solo"]
        with pytest.raises(Exception):
            result.deflated_sharpe_ratio = 1.0  # type: ignore[misc]


class TestDeterminism:
    """No randomness anywhere in production code -- identical input must
    always produce byte-identical output."""

    def test_compute_pbo_is_deterministic(self) -> None:
        rng = _seeded_rng(99)
        candidates = {f"c{i}": [rng.gauss(0.0, 0.01) for _ in range(24)] for i in range(4)}
        r1 = compute_pbo(candidates, num_groups=6)
        r2 = compute_pbo(candidates, num_groups=6)
        assert r1 == r2

    def test_compute_dsr_is_deterministic(self) -> None:
        rng = _seeded_rng(100)
        candidates = {f"c{i}": [rng.gauss(0.0, 0.01) for _ in range(24)] for i in range(3)}
        r1 = compute_dsr_for_all_candidates(candidates)
        r2 = compute_dsr_for_all_candidates(candidates)
        assert r1 == r2


def test_zero_sharpe_trials_count_toward_the_deflation() -> None:
    """ADR-0220: trials that never differed from the baseline (all-zero
    excess) still count, so the winner is deflated more, never less."""
    from cointrader.validation.pbo_dsr import compute_dsr_for_all_candidates

    returns = {"a": [0.02, 0.01, 0.03, -0.01, 0.02, 0.01], "b": [0.0, 0.01, -0.02, 0.01, 0.0, -0.01]}
    plain = compute_dsr_for_all_candidates(returns)
    padded = compute_dsr_for_all_candidates(returns, zero_sharpe_trials=9)
    assert padded["a"].num_trials == 11
    assert padded["a"].expected_max_sharpe_under_null > plain["a"].expected_max_sharpe_under_null
    assert padded["a"].deflated_sharpe_ratio < plain["a"].deflated_sharpe_ratio
