"""Validation study for `SignalStrategy` candidates (swing or scalp),
on the event engine with full costs, risk engine and futures terms.

Same order of operations as `validation.study` (none may be skipped or
reordered; CLAUDE.md rule 1):

1. hypothesis pre-registered and verified; candidate list must match it;
2. chronological split; TRAIN+VALIDATION and TEST each checked against
   the locked-window registry (hard stop on overlap);
3. integrity checks (look-ahead, warm-up, determinism) on
   TRAIN+VALIDATION data only -- a failing candidate is still scored (it
   counts as a trial) but can never be promoted;
4. walk-forward: each OOS fold runs the full event backtest with
   decisions only inside the fold; fold net return = one CSCV observation;
5. PBO over the fold matrix, DSR per candidate deflated against every
   candidate ever registered (the log's trial count, not just this run);
6. only then each candidate runs ONCE on TEST; the report says the TEST
   window must be locked now (`must_lock_test_window`), and
   `lock_test_window` does it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from cointrader.analytics.performance import result_summary
from cointrader.backtest.event_engine import ExecutionCosts, FuturesTerms, run_event_backtest
from cointrader.data.models import Candle
from cointrader.evolution.status import Evidence, PromotionCriteria
from cointrader.risk.engine import RiskEngine
from cointrader.validation.integrity import check_signal_strategy
from cointrader.validation.locked_windows import (
    DEFAULT_REGISTRY,
    LockedWindow,
    append_locked_window,
    assert_not_locked,
)
from cointrader.validation.pbo_dsr import compute_dsr_for_all_candidates, compute_pbo
from cointrader.validation.policies import ValidationPolicy
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog
from cointrader.validation.walk_forward import build_chronological_split, generate_walk_forward_windows


@dataclass(frozen=True)
class SignalCandidateReport:
    strategy_id: str
    integrity_passed: bool
    integrity_findings: tuple[str, ...]
    fold_returns: tuple[float, ...]
    fold_trade_counts: tuple[int, ...]
    mean_fold_return: float
    deflated_sharpe: float  # NaN when fold returns were constant
    test_return: float
    test_buy_and_hold_return: float
    test_summary: dict

    @property
    def test_excess_return(self) -> float:
        return self.test_return - self.test_buy_and_hold_return


@dataclass(frozen=True)
class SignalStudyReport:
    hypothesis_id: str
    family: str
    market: str
    timeframe: str
    train_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    fold_count: int
    pbo: float
    trials_deflated_against: int
    candidates: tuple[SignalCandidateReport, ...]
    must_lock_test_window: bool = True
    label: str = "BACKTEST validation study (simulated; not paper or live performance)"
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    def evidence_for(self, strategy_id: str) -> Evidence:
        """What `evolution.status.next_automatic_status` may use. A
        candidate that failed integrity checks gets no walk-forward
        evidence, so it can never pass BACKTESTED."""
        c = next(c for c in self.candidates if c.strategy_id == strategy_id)
        if not c.integrity_passed:
            return Evidence(backtest_completed=True)
        dsr = None if math.isnan(c.deflated_sharpe) else c.deflated_sharpe
        return Evidence(backtest_completed=True, fold_count=self.fold_count, pbo=self.pbo,
                        deflated_sharpe=dsr if dsr is not None else 0.0, test_excess_return=c.test_excess_return)


def _slice(candles: Sequence[Candle], start: datetime, end: datetime) -> list[Candle]:
    return [c for c in candles if start <= c.open_time < end]


def _buy_and_hold(candles: Sequence[Candle]) -> float:
    return candles[-1].close / candles[0].open - 1 if len(candles) >= 2 else 0.0


def run_signal_study(
    hypothesis: Hypothesis,
    log: PreregistrationLog,
    candles: Sequence[Candle],
    candidates: Sequence,
    locked: Sequence[LockedWindow],
    *,
    policy: ValidationPolicy,
    risk: RiskEngine,
    costs: ExecutionCosts = ExecutionCosts(),
    futures: FuturesTerms = FuturesTerms(),
    initial_equity: float = 10_000.0,
) -> SignalStudyReport:
    log.verify(hypothesis)
    if tuple(c.strategy_id for c in candidates) != hypothesis.candidates:
        raise ValueError("candidates differ from the pre-registered list")
    if any(c.family != policy.family for c in candidates):
        raise ValueError(f"every candidate must be a {policy.family} strategy under this policy")
    if not candles:
        raise ValueError("no candles")
    timeframe = candles[0].timeframe
    if timeframe.value != hypothesis.timeframe:
        raise ValueError(f"candles are {timeframe.value}, hypothesis says {hypothesis.timeframe}")

    split = build_chronological_split(hypothesis.data_start, hypothesis.data_end, align_to=timeframe.delta)
    assert_not_locked(locked, hypothesis.market, split.train_start, split.validation_end)
    if candles[0].open_time < split.train_start:  # warm-up bars loaded before the range
        assert_not_locked(locked, hypothesis.market, candles[0].open_time, split.train_start)
    assert_not_locked(locked, hypothesis.market, split.test_start, split.test_end)

    windows = generate_walk_forward_windows(split.train_start, split.validation_end, train=policy.fold_train,
                                            test=policy.fold_test, step=policy.fold_test)
    if len(windows) < policy.num_groups:
        raise ValueError(f"only {len(windows)} walk-forward folds; need >= {policy.num_groups} for PBO")

    in_sample = _slice(candles, split.train_start, split.validation_end)
    integrity = {c.strategy_id: check_signal_strategy(in_sample, c, samples=policy.integrity_samples)
                 for c in candidates}

    # Indicator history may reach back before a fold's own train start (a
    # 201-bar warm-up is 8 days of 1h bars but 201 days of 1d bars); it is
    # never scored, and any bar it touches was checked against the locks above.
    def run(c, warm_from: datetime, start: datetime, end: datetime):
        warm_from = min(warm_from, start - (c.warmup + 1) * timeframe.delta)
        return run_event_backtest(_slice(candles, warm_from, end), c, risk, costs=costs, futures=futures,
                                  initial_equity=initial_equity, score_from=start)

    fold_returns: dict[str, list[float]] = {}
    fold_trades: dict[str, list[int]] = {}
    for c in candidates:
        results = [run(c, w.train_start, w.test_start, w.test_end) for w in windows]
        fold_returns[c.strategy_id] = [r.total_return for r in results]
        fold_trades[c.strategy_id] = [len(r.trades) for r in results]

    pbo = compute_pbo(fold_returns, num_groups=policy.num_groups)
    extra_trials = max(0, log.total_registered_candidates() - len(candidates))
    varying = {n: r for n, r in fold_returns.items() if len(set(r)) > 1}
    constant = len(fold_returns) - len(varying)
    dsr = compute_dsr_for_all_candidates(varying, zero_sharpe_trials=extra_trials + constant) if varying else {}
    trials = next(iter(dsr.values())).num_trials if dsr else extra_trials + constant

    test_bars = _slice(candles, split.test_start, split.test_end)
    reports = []
    for c in candidates:
        warm_from = split.test_start - policy.fold_train
        res = run(c, warm_from, split.test_start, split.test_end)
        rep = integrity[c.strategy_id]
        returns = fold_returns[c.strategy_id]
        reports.append(SignalCandidateReport(
            strategy_id=c.strategy_id, integrity_passed=rep.passed,
            integrity_findings=tuple(f"{f.check}@{f.bar_index}: {f.detail}" for f in rep.findings[:10]),
            fold_returns=tuple(returns), fold_trade_counts=tuple(fold_trades[c.strategy_id]),
            mean_fold_return=sum(returns) / len(returns),
            deflated_sharpe=dsr[c.strategy_id].deflated_sharpe_ratio if c.strategy_id in dsr else math.nan,
            test_return=res.total_return, test_buy_and_hold_return=_buy_and_hold(test_bars),
            test_summary=result_summary(res, policy.periods_per_year(timeframe)),
        ))
    return SignalStudyReport(
        hypothesis_id=hypothesis.hypothesis_id, family=policy.family, market=hypothesis.market,
        timeframe=timeframe.value, train_start=split.train_start, validation_end=split.validation_end,
        test_start=split.test_start, test_end=split.test_end, fold_count=len(windows), pbo=pbo.probability,
        trials_deflated_against=trials, candidates=tuple(reports),
        assumptions=tuple(reports[0].test_summary["assumptions"]) if reports else (),
    )


def lock_test_window(report: SignalStudyReport, note: str, path: Path = DEFAULT_REGISTRY,
                     name: Optional[str] = None) -> LockedWindow:
    """Append the report's TEST range to the locked-window registry --
    call immediately after the TEST run, before anything else looks at
    the result."""
    window = LockedWindow(
        name=name or f"TEST-{report.hypothesis_id.split('-')[-1].lstrip('0')}",
        market=report.market, start=report.test_start, end=report.test_end,
        observed_by=tuple(c.strategy_id for c in report.candidates), note=note,
    )
    append_locked_window(window, path)
    return window


def criteria_from(hypothesis: Hypothesis, policy: ValidationPolicy) -> PromotionCriteria:
    sc = hypothesis.success_criteria
    return PromotionCriteria(min_folds=policy.min_folds, max_pbo=sc["max_pbo"], min_deflated_sharpe=sc["min_dsr"],
                             min_test_excess_return=sc["min_test_excess_return"])
