"""Screening vs. final exam (ADR-0051, from NEW-'s ADR-0222).

Screening runs the same integrity checks, walk-forward folds, PBO and
DSR as the study, but over TRAIN+VALIDATION only: the held-out TEST is
never read, so screening needs no registration and has no count limit.
Its cost is paid elsewhere:

- every screening run is appended to `research/screening.jsonl`
  (append-only), and every screened candidate counts as a trial in the
  final exam's DSR (`run_signal_study(screened_trials=...)`);
- the first screening of a market reserves the range's last 20% (the
  split's TEST) in `configs/reserved_windows.json`; no later screening
  of that market may touch it, so the final exam's TEST stays unseen;
- the final exam (`scripts/run_validation.py`) takes at most
  `MAX_FINALISTS` candidates, each screened on the same market and
  timeframe, its TEST must be exactly the market's reservation, and only
  a human registers it. After the TEST run the reservation becomes a
  locked window as before.

Screening results are never validation evidence and never move a
candidate's lifecycle status.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from cointrader._time import require_aware
from cointrader.backtest.event_engine import ExecutionCosts, FuturesTerms
from cointrader.data.models import Candle
from cointrader.research.hypotheses import AUTOMATED_ACTORS
from cointrader.risk.engine import RiskEngine
from cointrader.validation.locked_windows import LockedWindow, assert_not_locked
from cointrader.validation.pbo_dsr import compute_pbo
from cointrader.validation.policies import ValidationPolicy
from cointrader.validation.preregistration import Hypothesis
from cointrader.validation.signal_study import deflated_sharpes, walk_forward_folds
from cointrader.validation.walk_forward import build_chronological_split

_REPO = Path(__file__).resolve().parents[3]
DEFAULT_LEDGER = _REPO / "research" / "screening.jsonl"
DEFAULT_RESERVED = _REPO / "configs" / "reserved_windows.json"
MAX_FINALISTS = 3


class ScreeningRefused(ValueError):
    pass


@dataclass(frozen=True)
class ReservedWindow:
    market: str
    start: datetime
    end: datetime
    note: str

    def __post_init__(self) -> None:
        require_aware("ReservedWindow.start", self.start)
        require_aware("ReservedWindow.end", self.end)
        if self.end <= self.start:
            raise ValueError("ReservedWindow.end must be after start")


def load_reserved(path: Path = DEFAULT_RESERVED) -> tuple[ReservedWindow, ...]:
    if not path.exists():
        return ()
    return tuple(ReservedWindow(r["market"], datetime.fromisoformat(r["start"]), datetime.fromisoformat(r["end"]),
                                r["note"]) for r in json.loads(path.read_text(encoding="utf-8")))


def _write_reserved(windows: Sequence[ReservedWindow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"market": w.market, "start": w.start.isoformat(), "end": w.end.isoformat(), "note": w.note}
            for w in windows]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reserved_for(windows: Sequence[ReservedWindow], market: str) -> Optional[ReservedWindow]:
    return next((w for w in windows if w.market == market), None)


def reserve(window: ReservedWindow, path: Path = DEFAULT_RESERVED) -> None:
    existing = load_reserved(path)
    if reserved_for(existing, window.market):
        raise ScreeningRefused(f"{window.market} already has a reserved TEST window")
    _write_reserved((*existing, window), path)


def release(market: str, path: Path = DEFAULT_RESERVED) -> None:
    """After the final exam: the reservation has just become a locked window."""
    _write_reserved(tuple(w for w in load_reserved(path) if w.market != market), path)


class ScreeningLedger:
    """Append-only record of every screening run."""

    def __init__(self, path: Path = DEFAULT_LEDGER) -> None:
        self._path = path

    def rows(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(l) for l in self._path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def append(self, row: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def screened(self, market: str, timeframe: str) -> set[str]:
        return {c["strategy_id"] for r in self.rows() if r["market"] == market and r["timeframe"] == timeframe
                for c in r["candidates"]}

    def total_candidates(self) -> int:
        """Every screened candidate run, across all screenings: trials for DSR."""
        return sum(len(r["candidates"]) for r in self.rows())


@dataclass(frozen=True)
class ScreenedCandidate:
    strategy_id: str
    integrity_passed: bool
    fold_returns: tuple[float, ...]
    fold_trade_counts: tuple[int, ...]
    mean_fold_return: float
    deflated_sharpe: Optional[float]


@dataclass(frozen=True)
class ScreeningReport:
    market: str
    timeframe: str
    family: str
    train_start: datetime
    validation_end: datetime
    reservation: ReservedWindow  # new (to write) or the market's existing one
    new_reservation: bool
    fold_count: int
    pbo: float
    trials_deflated_against: int
    candidates: tuple[ScreenedCandidate, ...]
    label: str = "SCREENING (walk-forward only; TEST not read; not validation evidence)"


def run_screening(
    candles: Sequence[Candle],
    candidates: Sequence,
    market: str,
    start: datetime,
    end: datetime,
    locked: Sequence[LockedWindow],
    reserved: Sequence[ReservedWindow],
    *,
    policy: ValidationPolicy,
    risk: RiskEngine,
    prior_trials: int,
    costs: ExecutionCosts = ExecutionCosts(),
    futures: FuturesTerms = FuturesTerms(),
    initial_equity: float = 10_000.0,
) -> ScreeningReport:
    """Walk-forward over the split's TRAIN+VALIDATION of `[start, end)`.
    The split's TEST is reserved if the market has no reservation yet;
    otherwise every bar read must stay clear of the existing one."""
    if not candles:
        raise ValueError("no candles")
    if any(c.family != policy.family for c in candidates):
        raise ValueError(f"every candidate must be a {policy.family} strategy under this policy")
    timeframe = candles[0].timeframe
    split = build_chronological_split(start, end, align_to=timeframe.delta)
    read_from = min(candles[0].open_time, split.train_start)
    assert_not_locked(locked, market, read_from, split.validation_end)
    held = reserved_for(reserved, market)
    if held is None:
        assert_not_locked(locked, market, split.test_start, split.test_end)
        held = ReservedWindow(market, split.test_start, split.test_end,
                              f"final-exam TEST reserved by screening {timeframe.value} {start.date()}..{end.date()}")
        new = True
    else:
        if read_from < held.end and split.validation_end > held.start:
            raise ScreeningRefused(f"{market} screening range overlaps its reserved TEST "
                                   f"[{held.start.isoformat()}, {held.end.isoformat()})")
        new = False
    if any(c.open_time >= split.validation_end for c in candles):
        raise ScreeningRefused("candles reach past VALIDATION; screening must not load TEST bars")

    wf = walk_forward_folds(candles, candidates, split.train_start, split.validation_end, policy=policy, risk=risk,
                            costs=costs, futures=futures, initial_equity=initial_equity)
    pbo = compute_pbo(wf.fold_returns, num_groups=policy.num_groups)
    dsr, trials = deflated_sharpes(wf.fold_returns, prior_trials)
    reports = tuple(ScreenedCandidate(
        strategy_id=c.strategy_id, integrity_passed=wf.integrity[c.strategy_id].passed,
        fold_returns=tuple(wf.fold_returns[c.strategy_id]), fold_trade_counts=tuple(wf.fold_trades[c.strategy_id]),
        mean_fold_return=sum(wf.fold_returns[c.strategy_id]) / len(wf.windows),
        deflated_sharpe=dsr[c.strategy_id].deflated_sharpe_ratio if c.strategy_id in dsr else None,
    ) for c in candidates)
    return ScreeningReport(market=market, timeframe=timeframe.value, family=policy.family,
                           train_start=split.train_start, validation_end=split.validation_end, reservation=held,
                           new_reservation=new, fold_count=len(wf.windows), pbo=pbo.probability,
                           trials_deflated_against=trials, candidates=reports)


def check_finalists(hypothesis: Hypothesis, ledger: ScreeningLedger, reserved: Sequence[ReservedWindow],
                    test_start: datetime, test_end: datetime) -> None:
    """Final-exam gate, called before registration."""
    problems = []
    if hypothesis.registered_by.strip().upper() in AUTOMATED_ACTORS:
        problems.append("only a human registers a final exam; automated actors may only screen")
    if len(hypothesis.candidates) > MAX_FINALISTS:
        problems.append(f"{len(hypothesis.candidates)} finalists; at most {MAX_FINALISTS}")
    unscreened = sorted(set(hypothesis.candidates) - ledger.screened(hypothesis.market, hypothesis.timeframe))
    if unscreened:
        problems.append(f"{unscreened} never screened on {hypothesis.market} {hypothesis.timeframe}")
    held = reserved_for(reserved, hypothesis.market)
    if held is None:
        problems.append(f"{hypothesis.market} has no reserved TEST window; screen first")
    elif (held.start, held.end) != (test_start, test_end):
        problems.append(f"TEST [{test_start.isoformat()}, {test_end.isoformat()}) is not {hypothesis.market}'s "
                        f"reserved [{held.start.isoformat()}, {held.end.isoformat()}); use the screening's start/end")
    if problems:
        raise ScreeningRefused("; ".join(problems))

