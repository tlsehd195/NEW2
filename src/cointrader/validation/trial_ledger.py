"""Cumulative trial count for the Deflated Sharpe Ratio.

Ported idea from tlsehd195/NEW- (`strategy_research/trial_ledger.py`;
Harvey-Liu-Zhu 2016, Bailey et al. 2014): the right N is every candidate
ever tried, not just this run's. NEW- derived it from committed reports;
NEW2 already keeps an append-only record of every pre-registered
candidate (`PreregistrationLog`), so the ledger reads that instead and
adds no new state to drift.

Earlier trials' fold returns are not stored for this hypothesis, so they
enter as zero-Sharpe trials (the same convention as
`zero_sharpe_trials` in `compute_dsr_for_all_candidates`). Informational:
it never replaces the per-run DSR a hypothesis's own criteria use.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from cointrader.validation.pbo_dsr import DsrResult, compute_dsr_for_all_candidates
from cointrader.validation.preregistration import PreregistrationLog


def prior_trials(log: PreregistrationLog, current_candidates: Sequence[str]) -> int:
    """Registered candidates (across all hypotheses) beyond this run's."""
    return max(0, log.total_registered_candidates() - len(set(current_candidates)))


def compute_cumulative_dsr(
    fold_returns_by_candidate: Mapping[str, Sequence[float]], log: PreregistrationLog,
) -> dict[str, DsrResult]:
    extra = prior_trials(log, list(fold_returns_by_candidate))
    return compute_dsr_for_all_candidates(fold_returns_by_candidate, zero_sharpe_trials=extra)
