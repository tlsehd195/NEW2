"""Validation study for grid candidates (ADR-0021).

Same order as `validation.signal_study` (CLAUDE.md rule 1):

1. hypothesis pre-registered and verified; candidate list must match it;
2. chronological split; warm-up, TRAIN+VALIDATION and TEST each checked
   against the locked-window registry (hard stop on overlap);
3. determinism check per candidate on TRAIN+VALIDATION (the engine only
   reads bars that closed before a reset, so look-ahead is structural);
4. walk-forward: each OOS fold runs the grid from the fold's start with
   the fold's own warm-up; fold net return = one CSCV observation;
5. PBO over the fold matrix, DSR deflated against every candidate ever
   registered;
6. the TEST range is locked (`before_test`), then each candidate runs
   ONCE on TEST.

On top of the usual numbers, every reset period (fold and TEST) is
classified AFTER THE FACT by its own daily closes, so the report can say
where the grid made and lost money. The classes are fixed here and in
ADR-0021, not tuned:

- range:      efficiency ratio < 0.35
- trend_up:   efficiency ratio >= 0.6 and the period's price rose
- trend_down: efficiency ratio >= 0.6 and the period's price fell
- mixed:      anything in between
- partial:    fewer than 3 daily moves (a period cut by a fold edge)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from cointrader.backtest.event_engine import ExecutionCosts
from cointrader.backtest.grid_engine import GridPeriod, GridResult, GridTerms, run_grid_backtest
from cointrader.data.models import Candle
from cointrader.evolution.status import Evidence
from cointrader.validation.locked_windows import DEFAULT_REGISTRY, LockedWindow, append_locked_window, assert_not_locked
from cointrader.validation.pbo_dsr import compute_dsr_for_all_candidates, compute_pbo
from cointrader.validation.policies import ValidationPolicy
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog
from cointrader.validation.walk_forward import build_chronological_split, generate_walk_forward_windows

GRID_POLICY = ValidationPolicy("grid", fold_train=timedelta(days=60), fold_test=timedelta(days=30))
RANGE_MAX_ER = 0.35
TREND_MIN_ER = 0.6
REGIMES = ("range", "mixed", "trend_up", "trend_down", "partial")


def classify_period(p: GridPeriod) -> str:
    if (p.end - p.start) < timedelta(days=3):
        return "partial"
    if p.ex_post_efficiency_ratio < RANGE_MAX_ER:
        return "range"
    if p.ex_post_efficiency_ratio >= TREND_MIN_ER:
        return "trend_up" if p.ex_post_price_return > 0 else "trend_down"
    return "mixed"


def regime_breakdown(periods: Sequence[GridPeriod]) -> dict:
    """Per ex-post regime: how many periods, how many the grid deployed
    in, how many it stopped out of, and the sum/mean of period returns."""
    out = {}
    for regime in REGIMES:
        ps = [p for p in periods if classify_period(p) == regime]
        rets = [p.pnl / p.equity_start for p in ps if p.equity_start > 0]
        out[regime] = {
            "periods": len(ps), "deployed": sum(p.deployed for p in ps), "stopped": sum(p.stopped for p in ps),
            "sum_return": sum(rets), "mean_return": sum(rets) / len(rets) if rets else 0.0,
            "winning_periods": sum(r > 0 for r in rets),
        }
    return out


@dataclass(frozen=True)
class GridCandidateReport:
    strategy_id: str
    integrity_passed: bool
    integrity_findings: tuple[str, ...]
    fold_returns: tuple[float, ...]
    fold_round_trips: tuple[int, ...]
    fold_stops: tuple[int, ...]
    mean_fold_return: float
    deflated_sharpe: float  # NaN when fold returns were constant
    fold_regimes: dict
    test_return: float
    test_buy_and_hold_return: float
    test_regimes: dict
    test_summary: dict

    @property
    def test_excess_return(self) -> float:
        return self.test_return - self.test_buy_and_hold_return


@dataclass(frozen=True)
class GridStudyReport:
    hypothesis_id: str
    market: str
    timeframe: str
    train_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    fold_count: int
    pbo: float
    trials_deflated_against: int
    candidates: tuple[GridCandidateReport, ...]
    must_lock_test_window: bool = True
    label: str = "BACKTEST validation study (simulated; not paper or live performance)"
    assumptions: tuple[str, ...] = field(default_factory=tuple)

    def evidence_for(self, strategy_id: str) -> Evidence:
        c = next(c for c in self.candidates if c.strategy_id == strategy_id)
        if not c.integrity_passed:
            return Evidence(backtest_completed=True)
        dsr = None if math.isnan(c.deflated_sharpe) else c.deflated_sharpe
        return Evidence(backtest_completed=True, fold_count=self.fold_count, pbo=self.pbo,
                        deflated_sharpe=dsr if dsr is not None else 0.0, test_excess_return=c.test_excess_return)


def _slice(candles: Sequence[Candle], start: datetime, end: datetime) -> list[Candle]:
    return [c for c in candles if start <= c.open_time < end]


def warmup_bars(candidate, timeframe, costs: ExecutionCosts = ExecutionCosts()) -> int:
    return candidate.lookback_days * int(timedelta(days=1) / timeframe.delta) + costs.impact_vol_window + 2


def run_grid_study(
    hypothesis: Hypothesis,
    log: PreregistrationLog,
    candles: Sequence[Candle],
    candidates: Sequence,
    locked: Sequence[LockedWindow],
    *,
    funding: Optional[Mapping[datetime, float]],
    assume_no_funding: bool = False,
    terms: GridTerms = GridTerms(),
    policy: ValidationPolicy = GRID_POLICY,
    costs: ExecutionCosts = ExecutionCosts(),
    initial_equity: float = 10_000.0,
    before_test: Optional[Callable[[datetime, datetime], None]] = None,
) -> GridStudyReport:
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
    if candles[-1].open_time >= hypothesis.data_end:
        raise ValueError("candles reach past the registered data_end")

    split = build_chronological_split(hypothesis.data_start, hypothesis.data_end, align_to=timeframe.delta)
    assert_not_locked(locked, hypothesis.market, min(candles[0].open_time, split.train_start), split.validation_end)
    assert_not_locked(locked, hypothesis.market, split.test_start, split.test_end)

    windows = generate_walk_forward_windows(split.train_start, split.validation_end, train=policy.fold_train,
                                            test=policy.fold_test, step=policy.fold_test)
    if len(windows) < policy.num_groups:
        raise ValueError(f"only {len(windows)} walk-forward folds; need >= {policy.num_groups} for PBO")

    def run(c, start: datetime, end: datetime) -> GridResult:
        warm_from = start - warmup_bars(c, timeframe, costs) * timeframe.delta
        return run_grid_backtest(_slice(candles, warm_from, end), c, score_from=start, funding=funding,
                                 assume_no_funding=assume_no_funding, costs=costs, terms=terms,
                                 initial_equity=initial_equity)

    integrity: dict[str, list[str]] = {}
    for c in candidates:
        a, b = run(c, split.train_start, split.validation_end), run(c, split.train_start, split.validation_end)
        integrity[c.strategy_id] = [] if a.equity_curve == b.equity_curve else ["determinism: two runs differ"]

    fold_results: dict[str, list[GridResult]] = {c.strategy_id: [run(c, w.test_start, w.test_end) for w in windows]
                                                 for c in candidates}
    fold_returns = {k: [r.total_return for r in rs] for k, rs in fold_results.items()}

    pbo = compute_pbo(fold_returns, num_groups=policy.num_groups)
    extra_trials = max(0, log.total_registered_candidates() - len(candidates))
    varying = {k: r for k, r in fold_returns.items() if len(set(r)) > 1}
    constant = len(fold_returns) - len(varying)
    dsr = compute_dsr_for_all_candidates(varying, zero_sharpe_trials=extra_trials + constant) if varying else {}
    trials = next(iter(dsr.values())).num_trials if dsr else extra_trials + constant

    # Lock before TEST runs, so a crash inside the TEST run cannot leave a seen range unlocked.
    if before_test is not None:
        before_test(split.test_start, split.test_end)
    test_bars = _slice(candles, split.test_start, split.test_end)
    bh = test_bars[-1].close / test_bars[0].open - 1 if len(test_bars) >= 2 else 0.0
    reports = []
    for c in candidates:
        res = run(c, split.test_start, split.test_end)
        rs = fold_results[c.strategy_id]
        returns = fold_returns[c.strategy_id]
        reports.append(GridCandidateReport(
            strategy_id=c.strategy_id, integrity_passed=not integrity[c.strategy_id],
            integrity_findings=tuple(integrity[c.strategy_id]), fold_returns=tuple(returns),
            fold_round_trips=tuple(r.round_trips for r in rs), fold_stops=tuple(r.stops for r in rs),
            mean_fold_return=sum(returns) / len(returns),
            deflated_sharpe=dsr[c.strategy_id].deflated_sharpe_ratio if c.strategy_id in dsr else math.nan,
            fold_regimes=regime_breakdown([p for r in rs for p in r.periods]),
            test_return=res.total_return, test_buy_and_hold_return=bh, test_regimes=regime_breakdown(res.periods),
            test_summary=res.summary(),
        ))
    return GridStudyReport(
        hypothesis_id=hypothesis.hypothesis_id, market=hypothesis.market, timeframe=timeframe.value,
        train_start=split.train_start, validation_end=split.validation_end, test_start=split.test_start,
        test_end=split.test_end, fold_count=len(windows), pbo=pbo.probability, trials_deflated_against=trials,
        candidates=tuple(reports), assumptions=tuple(reports[0].test_summary["assumptions"]) if reports else (),
    )


def lock_range(hypothesis_id: str, market: str, start: datetime, end: datetime, observed: tuple[str, ...],
               note: str, path: Path = DEFAULT_REGISTRY) -> LockedWindow:
    window = LockedWindow(f"TEST-{hypothesis_id.split('-')[-1].lstrip('0')}", market, start, end, observed, note)
    append_locked_window(window, path)
    return window
