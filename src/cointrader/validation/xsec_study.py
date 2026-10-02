"""Validation study for cross-sectional portfolio candidates (ADR-0017).

Same order of operations as `validation.signal_study` (CLAUDE.md rule 1),
adapted to a basket of coins:

1. hypothesis pre-registered and verified; the universe is part of the
   registered statement (`universe_statement`), so changing the coin list
   changes the hash;
2. chronological split; warm-up, TRAIN+VALIDATION and TEST are checked
   against the locked-window registry for the basket name AND every
   coin in it -- a portfolio observes each coin's prices, so a range
   locked for one coin is off limits to the basket;
3. determinism check per candidate on TRAIN+VALIDATION data (lookahead is
   structurally impossible: the engine passes only closed-bar history);
4. walk-forward over the SWING fold lengths; each fold's net return is one
   CSCV observation;
5. PBO over the fold matrix, DSR deflated against every candidate ever
   registered;
6. TEST once per candidate, then `lock_test_window` locks the range for
   the basket and for every coin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from cointrader.backtest.event_engine import ExecutionCosts
from cointrader.backtest.xsec_engine import run_xsec_backtest
from cointrader.data.models import Candle
from cointrader.evolution.status import Evidence
from cointrader.risk.engine import RiskConfig
from cointrader.validation.locked_windows import (
    DEFAULT_REGISTRY,
    LockedWindow,
    append_locked_window,
    assert_not_locked,
    load_locked_windows,
)
from cointrader.validation.pbo_dsr import compute_dsr_for_all_candidates, compute_pbo
from cointrader.validation.policies import ValidationPolicy
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog
from cointrader.validation.walk_forward import build_chronological_split, generate_walk_forward_windows

XSEC_POLICY = ValidationPolicy("xsec", fold_train=timedelta(days=60), fold_test=timedelta(days=30))
UNIVERSE_MARKER = " | universe: "


def universe_statement(statement: str, universe: Sequence[str]) -> str:
    return f"{statement}{UNIVERSE_MARKER}{','.join(universe)}"


def universe_of(hypothesis: Hypothesis) -> tuple[str, ...]:
    if UNIVERSE_MARKER not in hypothesis.statement:
        raise ValueError("cross-sectional hypothesis statement must end with its registered universe")
    return tuple(hypothesis.statement.rsplit(UNIVERSE_MARKER, 1)[1].split(","))


def assert_basket_not_locked(locked: Sequence[LockedWindow], basket: str, universe: Sequence[str],
                             start: datetime, end: datetime) -> None:
    for market in (basket, *universe):
        assert_not_locked(locked, market, start, end)


@dataclass(frozen=True)
class XsecCandidateReport:
    strategy_id: str
    integrity_passed: bool
    integrity_findings: tuple[str, ...]
    fold_returns: tuple[float, ...]
    fold_rebalances: tuple[int, ...]
    mean_fold_return: float
    deflated_sharpe: float  # NaN when fold returns were constant
    test_return: float
    test_buy_and_hold_return: float
    test_summary: dict

    @property
    def test_excess_return(self) -> float:
        return self.test_return - self.test_buy_and_hold_return


@dataclass(frozen=True)
class XsecStudyReport:
    hypothesis_id: str
    basket: str
    universe: tuple[str, ...]
    timeframe: str
    train_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime
    fold_count: int
    pbo: float
    trials_deflated_against: int
    candidates: tuple[XsecCandidateReport, ...]
    test_coins_with_data: tuple[str, ...] = ()
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


def equal_weight_buy_and_hold(candles: Mapping[str, Sequence[Candle]], start: datetime, end: datetime) -> float:
    """Equal-weight basket of the coins trading at `start`, held to their
    last bar before `end` (no rebalancing, no costs)."""
    rets = []
    for bars in candles.values():
        inside = [c for c in bars if start <= c.open_time < end]
        if inside and inside[0].open_time == start:
            rets.append(inside[-1].close / inside[0].open - 1)
    return sum(rets) / len(rets) if rets else 0.0


def run_xsec_study(
    hypothesis: Hypothesis,
    log: PreregistrationLog,
    candles: Mapping[str, Sequence[Candle]],
    candidates: Sequence,
    locked: Sequence[LockedWindow],
    *,
    risk: RiskConfig,
    funding: Optional[Mapping[str, Mapping[datetime, float]]] = None,
    assume_no_funding: bool = False,
    policy: ValidationPolicy = XSEC_POLICY,
    costs: ExecutionCosts = ExecutionCosts(),
    initial_equity: float = 10_000.0,
    before_test: Optional[Callable[[datetime, datetime], None]] = None,
) -> XsecStudyReport:
    log.verify(hypothesis)
    if tuple(c.strategy_id for c in candidates) != hypothesis.candidates:
        raise ValueError("candidates differ from the pre-registered list")
    if any(c.family != policy.family for c in candidates):
        raise ValueError(f"every candidate must be a {policy.family} strategy under this policy")
    universe = universe_of(hypothesis)
    if sorted(candles) != sorted(universe):
        raise ValueError("loaded coins differ from the registered universe")
    all_bars = [c for bars in candles.values() for c in bars]
    if not all_bars:
        raise ValueError("no candles")
    timeframe = all_bars[0].timeframe
    if timeframe.value != hypothesis.timeframe:
        raise ValueError(f"candles are {timeframe.value}, hypothesis says {hypothesis.timeframe}")

    split = build_chronological_split(hypothesis.data_start, hypothesis.data_end, align_to=timeframe.delta)
    basket = hypothesis.market
    first = min(c.open_time for c in all_bars)
    assert_basket_not_locked(locked, basket, universe, min(first, split.train_start), split.validation_end)
    assert_basket_not_locked(locked, basket, universe, split.test_start, split.test_end)
    if max(c.open_time for c in all_bars) >= hypothesis.data_end:
        raise ValueError("candles reach past the registered data_end")

    windows = generate_walk_forward_windows(split.train_start, split.validation_end, train=policy.fold_train,
                                            test=policy.fold_test, step=policy.fold_test)
    if len(windows) < policy.num_groups:
        raise ValueError(f"only {len(windows)} walk-forward folds; need >= {policy.num_groups} for PBO")

    def run(c, start: datetime, end: datetime):
        warm_from = start - (c.warmup + 1) * timeframe.delta
        sliced = {s: [b for b in bars if warm_from <= b.open_time < end] for s, bars in candles.items()}
        return run_xsec_backtest(sliced, c, risk, score_from=start, end=end, funding=funding,
                                 assume_no_funding=assume_no_funding, costs=costs, initial_equity=initial_equity)

    integrity: dict[str, list[str]] = {}
    for c in candidates:
        a, b = run(c, split.train_start, split.validation_end), run(c, split.train_start, split.validation_end)
        integrity[c.strategy_id] = [] if a.equity_curve == b.equity_curve else ["determinism: two runs differ"]

    fold_returns: dict[str, list[float]] = {}
    fold_rebal: dict[str, list[int]] = {}
    for c in candidates:
        results = [run(c, w.test_start, w.test_end) for w in windows]
        fold_returns[c.strategy_id] = [r.total_return for r in results]
        fold_rebal[c.strategy_id] = [r.rebalances for r in results]

    pbo = compute_pbo(fold_returns, num_groups=policy.num_groups)
    extra_trials = max(0, log.total_registered_candidates() - len(candidates))
    varying = {n: r for n, r in fold_returns.items() if len(set(r)) > 1}
    constant = len(fold_returns) - len(varying)
    dsr = compute_dsr_for_all_candidates(varying, zero_sharpe_trials=extra_trials + constant) if varying else {}
    trials = next(iter(dsr.values())).num_trials if dsr else extra_trials + constant

    # Lock before TEST runs, so a crash inside the TEST run cannot leave a seen range unlocked.
    if before_test is not None:
        before_test(split.test_start, split.test_end)
    bh = equal_weight_buy_and_hold(candles, split.test_start, split.test_end)
    reports = []
    summaries = []
    for c in candidates:
        res = run(c, split.test_start, split.test_end)
        summary = res.summary()
        summaries.append(summary)
        returns = fold_returns[c.strategy_id]
        reports.append(XsecCandidateReport(
            strategy_id=c.strategy_id, integrity_passed=not integrity[c.strategy_id],
            integrity_findings=tuple(integrity[c.strategy_id]), fold_returns=tuple(returns),
            fold_rebalances=tuple(fold_rebal[c.strategy_id]), mean_fold_return=sum(returns) / len(returns),
            deflated_sharpe=dsr[c.strategy_id].deflated_sharpe_ratio if c.strategy_id in dsr else math.nan,
            test_return=res.total_return, test_buy_and_hold_return=bh, test_summary=summary,
        ))
    with_data = tuple(sorted(s for s, bars in candles.items()
                             if any(split.test_start <= b.open_time < split.test_end for b in bars)))
    return XsecStudyReport(
        hypothesis_id=hypothesis.hypothesis_id, basket=basket, universe=universe, timeframe=timeframe.value,
        train_start=split.train_start, validation_end=split.validation_end, test_start=split.test_start,
        test_end=split.test_end, fold_count=len(windows), pbo=pbo.probability, trials_deflated_against=trials,
        candidates=tuple(reports), test_coins_with_data=with_data,
        assumptions=tuple(summaries[0]["assumptions"]) if summaries else (),
    )


def lock_test_window(report: XsecStudyReport, note: str, path: Path = DEFAULT_REGISTRY) -> list[LockedWindow]:
    """Locks the TEST range for the basket (`TEST-<n>`) and for every coin
    of the universe (`TEST-<n>:<symbol>`). Call immediately after the TEST run."""
    return lock_range(report.hypothesis_id, report.basket, report.universe, report.test_start, report.test_end,
                      tuple(c.strategy_id for c in report.candidates), note, path)


def lock_range(hypothesis_id: str, basket: str, universe: Sequence[str], start: datetime, end: datetime,
               observed: tuple[str, ...], note: str, path: Path = DEFAULT_REGISTRY) -> list[LockedWindow]:
    base = f"TEST-{hypothesis_id.split('-')[-1].lstrip('0')}"
    existing = {w.name for w in load_locked_windows(path)}
    names = [base] + [f"{base}:{s}" for s in universe]
    clash = [n for n in names if n in existing]
    if clash:
        raise ValueError(f"locked window(s) already exist: {clash}")
    windows = [LockedWindow(base, basket, start, end, observed, note)]
    windows += [LockedWindow(f"{base}:{s}", s, start, end, observed, f"coin of {basket}; {note}") for s in universe]
    for w in windows:
        append_locked_window(w, path)
    return windows
