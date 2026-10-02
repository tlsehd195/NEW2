#!/usr/bin/env python3
"""Pre-registered walk-forward validation of grid-trading candidates on one
USDⓈ-M perpetual, then the one-time TEST, then the TEST range is locked
(ADR-0021).

    python3 scripts/run_grid_validation.py --hypothesis-id H-0021 --symbol BNBUSDT --timeframe 15m \
        --start 2023-04-22 --end 2026-01-01 --statement "..." --rationale "..." \
        --registered-by 동동 --out reports/grid.json

Order enforced (CLAUDE.md rule 1): the whole range (warm-up included)
checked against the locks -> hypothesis checks and pre-registration ->
walk-forward -> PBO/DSR -> LOCK -> TEST once -> automatic lifecycle steps
(stop at OOS_TESTED). Commit research/*.jsonl and
configs/locked_windows.json afterwards.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.backtest.grid_engine import GridTerms  # noqa: E402
from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.research.hypotheses import register_checked  # noqa: E402
from cointrader.research.lifecycle import CandidateLedger  # noqa: E402
from cointrader.research.market_data import load_candles, load_funding  # noqa: E402
from cointrader.strategies.grid import GRID_FACTORIES  # noqa: E402
from cointrader.validation.grid_study import GRID_POLICY, lock_range, run_grid_study, warmup_bars  # noqa: E402
from cointrader.validation.locked_windows import assert_not_locked, load_locked_windows  # noqa: E402
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog  # noqa: E402
from cointrader.validation.signal_study import criteria_from  # noqa: E402


def _utc(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def _safe(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def main(loader=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hypothesis-id", required=True)
    ap.add_argument("--strategies", nargs="+", required=True, help="grid candidate ids (strategies/grid.py)")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--statement", required=True)
    ap.add_argument("--rationale", required=True)
    ap.add_argument("--registered-by", required=True)
    ap.add_argument("--maintenance-margin-rate", type=float, default=0.01,
                    help="stated maintenance margin rate for the liquidation check (archive has no bracket table)")
    ap.add_argument("--log", type=Path, default=REPO / "research" / "preregistration.jsonl")
    ap.add_argument("--locked-path", type=Path, default=REPO / "configs" / "locked_windows.json")
    ap.add_argument("--ledger", type=Path, default=REPO / "research" / "candidate_status.jsonl")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    ids = list(args.strategies)
    candidates = [GRID_FACTORIES[i] for i in ids]
    tf = Timeframe(args.timeframe)
    start, end = _utc(args.start), _utc(args.end)
    warmup_start = start - max(warmup_bars(c, tf) for c in candidates) * tf.delta
    locked = load_locked_windows(args.locked_path)
    # Before anything is registered or loaded: the whole range may not touch a locked window.
    assert_not_locked(locked, args.symbol, warmup_start, end)

    now = datetime.now(timezone.utc)
    hypothesis = Hypothesis(
        hypothesis_id=args.hypothesis_id, statement=args.statement, market=args.symbol, timeframe=args.timeframe,
        data_start=start, data_end=end, candidates=tuple(ids), success_criteria=dict(GRID_POLICY.success_criteria),
        registered_by=args.registered_by, registered_at=now,
    )
    log = PreregistrationLog(args.log)
    register_checked(log, args.log, hypothesis, locked, rationale=args.rationale)

    load_c, load_f = loader or (load_candles, load_funding)
    candles, notes = load_c(args.symbol, tf, warmup_start, end)
    funding, fnotes = load_f(args.symbol, warmup_start, end)
    note = f"{args.hypothesis_id} grid {args.symbol} {args.timeframe}; TEST used once by run_grid_validation.py at {now.isoformat()}"
    windows: list = []

    def lock_before_test(test_start, test_end):
        windows.append(lock_range(args.hypothesis_id, args.symbol, test_start, test_end, tuple(ids), note,
                                  args.locked_path))

    report = run_grid_study(hypothesis, log, candles, candidates, locked, funding=funding,
                            terms=GridTerms(maintenance_margin_rate=args.maintenance_margin_rate),
                            before_test=lock_before_test)
    ledger = CandidateLedger(args.ledger)
    criteria = criteria_from(hypothesis, GRID_POLICY)
    transitions = {}
    for c in report.candidates:
        written = ledger.advance(c.strategy_id, report.evidence_for(c.strategy_id), criteria, now,
                                 hypothesis_id=args.hypothesis_id)
        transitions[c.strategy_id] = [f"{t.from_status.value}->{t.to_status.value}: {t.reason}" for t in written]
    out = {
        "label": report.label, "hypothesis_id": report.hypothesis_id, "market": report.market,
        "timeframe": report.timeframe, "fold_count": report.fold_count, "pbo": _safe(report.pbo),
        "trials_deflated_against": report.trials_deflated_against,
        "train_start": report.train_start.isoformat(), "validation_end": report.validation_end.isoformat(),
        "test_window_locked": {"name": windows[0].name, "start": windows[0].start.isoformat(),
                               "end": windows[0].end.isoformat()},
        "candidates": [{k: _safe(v) for k, v in dataclasses.asdict(c).items() if k != "test_summary"}
                       | {"test_excess_return": c.test_excess_return, "test_summary": c.test_summary}
                       for c in report.candidates],
        "lifecycle_transitions": transitions, "data_notes": notes + fnotes,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2, default=_safe)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
