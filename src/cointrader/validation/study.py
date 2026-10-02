"""Swing validation study: walk-forward -> PBO/DSR -> one held-out TEST.

Order of operations (none of these steps may be skipped or reordered):

1. The hypothesis is pre-registered (`preregistration`) and verified.
2. The overall range is split chronologically; TRAIN+VALIDATION and TEST
   are each checked against the locked-window registry.
3. Every candidate runs on the same walk-forward test folds inside
   TRAIN+VALIDATION. Each fold's net return is one CSCV observation.
4. PBO and DSR are computed over those fold returns, with the trial
   count taken from the pre-registration log (every candidate ever
   registered, not only this study's).
5. Only then is each candidate run once on TEST. After this the caller
   must lock the TEST range (`locked_windows.append_locked_window`) --
   the report says so explicitly.

The strategies are rule-based and have no fitted parameters, so the
"train" part of each walk-forward window only provides indicator
warm-up history; the test fold is what is scored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from cointrader.backtest.costs import CandleCostModel
from cointrader.backtest.engine import run_backtest
from cointrader.data.models import Candle
from cointrader.validation.locked_windows import LockedWindow, assert_not_locked
from cointrader.validation.pbo_dsr import compute_dsr_for_all_candidates, compute_pbo
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog
from cointrader.validation.walk_forward import build_chronological_split, generate_walk_forward_windows


@dataclass(frozen=True)
class CandidateReport:
    name: str
    fold_returns: tuple[float, ...]
    mean_fold_return: float
    deflated_sharpe: float  # NaN when the candidate's fold returns were constant
    test_return: float
    test_buy_and_hold_return: float
    test_liquidity_capped_bars: int  # bars in the TEST run where desired size exceeded book/bar liquidity


@dataclass(frozen=True)
class StudyReport:
    hypothesis_id: str
    market: str
    test_start: datetime
    test_end: datetime
    fold_count: int
    pbo: float
    trials_deflated_against: int
    candidates: tuple[CandidateReport, ...]
    must_lock_test_window: bool = True


def _slice(candles: Sequence[Candle], start: datetime, end: datetime) -> list[Candle]:
    return [c for c in candles if start <= c.open_time < end]


def _buy_and_hold(candles: Sequence[Candle]) -> float:
    if len(candles) < 2:
        return 0.0
    return candles[-1].open / candles[0].open - 1


def run_study(
    hypothesis: Hypothesis,
    log: PreregistrationLog,
    candles: Sequence[Candle],
    candidates: Sequence,  # objects with .name, .warmup and __call__(history) -> exposure
    locked: Sequence[LockedWindow],
    *,
    fold_train: timedelta,
    fold_test: timedelta,
    cost_model: CandleCostModel = CandleCostModel(),
    num_groups: int = 8,
) -> StudyReport:
    log.verify(hypothesis)
    if tuple(c.name for c in candidates) != hypothesis.candidates:
        raise ValueError("candidates differ from the pre-registered list")

    step = candles[0].timeframe.delta
    split = build_chronological_split(hypothesis.data_start, hypothesis.data_end, align_to=step)
    assert_not_locked(locked, hypothesis.market, split.train_start, split.validation_end)
    assert_not_locked(locked, hypothesis.market, split.test_start, split.test_end)

    windows = generate_walk_forward_windows(
        split.train_start, split.validation_end, train=fold_train, test=fold_test, step=fold_test,
    )
    if len(windows) < num_groups:
        raise ValueError(f"only {len(windows)} walk-forward folds; need >= {num_groups} for PBO")

    def run(candidate, start: datetime, end: datetime, warmup_from: datetime):
        # History from warmup_from feeds the indicators; only bars whose
        # decision time falls inside [start, end) are scored.
        bars = _slice(candles, warmup_from, end)
        warmup = sum(1 for c in bars if c.open_time < start)
        return run_backtest(bars, candidate, cost_model=cost_model, warmup=warmup)

    def score(candidate, start: datetime, end: datetime, warmup_from: datetime) -> float:
        return run(candidate, start, end, warmup_from).total_return

    fold_returns = {
        c.name: [score(c, w.test_start, w.test_end, w.train_start) for w in windows] for c in candidates
    }
    pbo = compute_pbo(fold_returns, num_groups=num_groups)
    # Every candidate ever registered counts as a trial. A candidate whose
    # fold returns are constant (e.g. it never traded) has no defined
    # Sharpe; it still counts, as a zero-Sharpe trial (NEW- ADR-0220).
    extra_trials = max(0, log.total_registered_candidates() - len(candidates))
    varying = {n: r for n, r in fold_returns.items() if len(set(r)) > 1}
    constant = len(fold_returns) - len(varying)
    dsr = compute_dsr_for_all_candidates(varying, zero_sharpe_trials=extra_trials + constant) if varying else {}
    trials = next(iter(dsr.values())).num_trials if dsr else extra_trials + constant

    test_bars = _slice(candles, split.test_start, split.test_end)
    reports = []
    for c in candidates:
        returns = fold_returns[c.name]
        test_result = run(c, split.test_start, split.test_end, split.test_start - fold_train)
        reports.append(CandidateReport(
            name=c.name,
            fold_returns=tuple(returns),
            mean_fold_return=sum(returns) / len(returns),
            deflated_sharpe=dsr[c.name].deflated_sharpe_ratio if c.name in dsr else math.nan,
            test_return=test_result.total_return,
            test_buy_and_hold_return=_buy_and_hold(test_bars),
            test_liquidity_capped_bars=test_result.liquidity_capped_bars,
        ))
    return StudyReport(
        hypothesis_id=hypothesis.hypothesis_id,
        market=hypothesis.market,
        test_start=split.test_start,
        test_end=split.test_end,
        fold_count=len(windows),
        pbo=pbo.probability,
        trials_deflated_against=trials,
        candidates=tuple(reports),
    )
