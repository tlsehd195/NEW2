"""Funding-carry validation study: same order of operations as
`validation.study.run_study`, adapted for `funding_carry_engine`'s
settlement-based candidates (ADR-0009) instead of bar-based ones.

1. The hypothesis is pre-registered and verified.
2. TRAIN+VALIDATION and TEST ranges are each checked against locked
   windows.
3. Every candidate runs over the same walk-forward folds; each fold's
   net return (price + funding) is one CSCV observation.
4. PBO and DSR are computed over those fold returns, trial count from
   every candidate ever registered project-wide (momentum candidates
   included -- the pre-registration log does not distinguish signal
   families, so this DSR is deflated against the whole project's
   multiple-testing history, not just funding candidates).
5. Only then does each candidate run once on TEST; the caller must lock
   the TEST range afterward.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from cointrader.backtest.costs import CandleCostModel
from cointrader.backtest.funding_carry_engine import run_funding_carry_backtest
from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle
from cointrader.validation.locked_windows import LockedWindow, assert_not_locked
from cointrader.validation.pbo_dsr import compute_dsr_for_all_candidates, compute_pbo
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog
from cointrader.validation.walk_forward import build_chronological_split, generate_walk_forward_windows


@dataclass(frozen=True)
class FundingCandidateReport:
    name: str
    fold_returns: tuple[float, ...]
    mean_fold_return: float
    deflated_sharpe: float  # NaN when fold returns were constant
    test_return: float
    test_funding_only_return: float  # the funding-PnL-only component of test_return
    test_gaps: int  # settlements skipped in TEST for a missing/invalid mark_price
    test_liquidity_capped_settlements: int  # TEST settlements where the desired size exceeded max_participation


@dataclass(frozen=True)
class FundingStudyReport:
    hypothesis_id: str
    market: str
    test_start: datetime
    test_end: datetime
    fold_count: int
    pbo: float
    trials_deflated_against: int
    candidates: tuple[FundingCandidateReport, ...]
    must_lock_test_window: bool = True


def _slice_funding(records: Sequence[FundingRateRecord], start: datetime, end: datetime) -> list[FundingRateRecord]:
    return [r for r in records if start <= r.funding_time < end]


def _slice_price(candles: Sequence[Candle], start: datetime, end: datetime) -> list[Candle]:
    return [c for c in candles if start <= c.open_time < end]


def run_funding_study(
    hypothesis: Hypothesis,
    log: PreregistrationLog,
    funding_history: Sequence[FundingRateRecord],
    price_history: Sequence[Candle],
    candidates: Sequence,  # objects with .name, .warmup and __call__(price_history, funding_history) -> exposure
    locked: Sequence[LockedWindow],
    *,
    fold_train: timedelta,
    fold_test: timedelta,
    cost_model: CandleCostModel = CandleCostModel(),
    num_groups: int = 8,
) -> FundingStudyReport:
    log.verify(hypothesis)
    if tuple(c.name for c in candidates) != hypothesis.candidates:
        raise ValueError("candidates differ from the pre-registered list")

    step = timedelta(hours=8)  # Binance's funding settlement interval
    split = build_chronological_split(hypothesis.data_start, hypothesis.data_end, align_to=step)
    assert_not_locked(locked, hypothesis.market, split.train_start, split.validation_end)
    assert_not_locked(locked, hypothesis.market, split.test_start, split.test_end)

    windows = generate_walk_forward_windows(
        split.train_start, split.validation_end, train=fold_train, test=fold_test, step=fold_test,
    )
    if len(windows) < num_groups:
        raise ValueError(f"only {len(windows)} walk-forward folds; need >= {num_groups} for PBO")

    def run(candidate, start: datetime, end: datetime, warmup_from: datetime):
        funding = _slice_funding(funding_history, warmup_from, end)
        price = _slice_price(price_history, warmup_from, end)
        warmup = sum(1 for r in funding if r.funding_time < start)
        return run_funding_carry_backtest(
            funding, candidate, price_history=price, cost_model=cost_model, warmup=warmup,
        )

    def score(candidate, start: datetime, end: datetime, warmup_from: datetime) -> float:
        return run(candidate, start, end, warmup_from).total_return

    fold_returns = {
        c.name: [score(c, w.test_start, w.test_end, w.train_start) for w in windows] for c in candidates
    }
    pbo = compute_pbo(fold_returns, num_groups=num_groups)
    extra_trials = max(0, log.total_registered_candidates() - len(candidates))
    varying = {n: r for n, r in fold_returns.items() if len(set(r)) > 1}
    constant = len(fold_returns) - len(varying)
    dsr = compute_dsr_for_all_candidates(varying, zero_sharpe_trials=extra_trials + constant) if varying else {}
    trials = next(iter(dsr.values())).num_trials if dsr else extra_trials + constant

    reports = []
    for c in candidates:
        returns = fold_returns[c.name]
        test_result = run(c, split.test_start, split.test_end, split.test_start - fold_train)
        funding_only_growth = 1.0
        for f in test_result.funding_returns:
            funding_only_growth *= 1 + f
        reports.append(FundingCandidateReport(
            name=c.name,
            fold_returns=tuple(returns),
            mean_fold_return=sum(returns) / len(returns),
            deflated_sharpe=dsr[c.name].deflated_sharpe_ratio if c.name in dsr else math.nan,
            test_return=test_result.total_return,
            test_funding_only_return=funding_only_growth - 1,
            test_gaps=test_result.gaps,
            test_liquidity_capped_settlements=test_result.liquidity_capped_settlements,
        ))
    return FundingStudyReport(
        hypothesis_id=hypothesis.hypothesis_id,
        market=hypothesis.market,
        test_start=split.test_start,
        test_end=split.test_end,
        fold_count=len(windows),
        pbo=pbo.probability,
        trials_deflated_against=trials,
        candidates=tuple(reports),
    )
