#!/usr/bin/env python3
"""FINAL EXAM (ADR-0051): pre-registered walk-forward validation of at
most 3 screened finalists, then the one-time held-out TEST, then the TEST
window is locked immediately. Screen first with scripts/run_screening.py
and use the same --start/--end, so TEST is the market's reserved window.

    python3 scripts/run_validation.py --hypothesis-id H-0014 --family swing \
        --symbol ETHUSDT --timeframe 1h --start 2021-01-01 --end 2023-06-01 \
        --statement "..." --rationale "why this is a new question, >= 40 chars" \
        --registered-by 동동 --out reports/H-0014.json

Order enforced (CLAUDE.md rule 1): finalist checks (screened, <= 3,
reserved TEST, human) -> hypothesis checks (dead families,
budget, locked windows) -> pre-registration -> locked-window check ->
walk-forward + integrity checks -> PBO/DSR -> TEST once -> LOCK the TEST
window in configs/locked_windows.json and release the reservation -> automatic lifecycle steps
(stop at OOS_TESTED; APPROVED/DEPLOYED are human-only).
Commit research/*.jsonl, configs/locked_windows.json and configs/reserved_windows.json afterwards.
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

from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.research.hypotheses import Budget, register_checked  # noqa: E402
from cointrader.research.lifecycle import CandidateLedger  # noqa: E402
from cointrader.research.market_data import load_candles, load_funding, load_futures_terms, load_open_interest  # noqa: E402
from cointrader.risk.engine import RiskEngine  # noqa: E402
from cointrader.settings import load_markets, load_risk  # noqa: E402
from cointrader.strategies.registry import StrategyRegistry  # noqa: E402
from cointrader.validation.locked_windows import load_locked_windows  # noqa: E402
from cointrader.validation.policies import POLICIES  # noqa: E402
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog  # noqa: E402
from cointrader.validation.screening import ScreeningLedger, check_finalists, load_reserved, release  # noqa: E402
from cointrader.validation.signal_study import criteria_from, lock_test_window, run_signal_study  # noqa: E402
from cointrader.validation.walk_forward import build_chronological_split  # noqa: E402


def _utc(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def _safe(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hypothesis-id", required=True)
    ap.add_argument("--family", required=True, choices=sorted(POLICIES))
    ap.add_argument("--strategies", nargs="+", required=True, help="screened finalists (at most 3)")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--statement", required=True)
    ap.add_argument("--rationale", required=True)
    ap.add_argument("--registered-by", required=True)
    ap.add_argument("--replication", action="store_true", help="human-declared replication of an identical set")
    ap.add_argument("--max-per-window", type=int, default=Budget.max_per_window,
                    help="human-approved registration budget per 30 days; raising it above the default needs --budget-adr")
    ap.add_argument("--budget-adr", help="ADR that records the human decision to raise the budget (e.g. ADR-0027)")
    ap.add_argument("--log", type=Path, default=REPO / "research" / "preregistration.jsonl")
    ap.add_argument("--locked-path", type=Path, default=REPO / "configs" / "locked_windows.json")
    ap.add_argument("--ledger", type=Path, default=REPO / "research" / "candidate_status.jsonl")
    ap.add_argument("--screening", type=Path, default=REPO / "research" / "screening.jsonl")
    ap.add_argument("--reserved-path", type=Path, default=REPO / "configs" / "reserved_windows.json")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    if args.max_per_window > Budget.max_per_window and not (args.budget_adr or "").startswith("ADR-"):
        ap.error("--max-per-window above the default needs --budget-adr naming the ADR that records the human approval")
    policy = POLICIES[args.family]
    registry = StrategyRegistry.load()
    ids = args.strategies
    candidates = [registry.build(i, market=args.symbol, family=args.family) for i in ids]
    locked = load_locked_windows(args.locked_path)
    now = datetime.now(timezone.utc)
    hypothesis = Hypothesis(
        hypothesis_id=args.hypothesis_id, statement=args.statement, market=args.symbol, timeframe=args.timeframe,
        data_start=_utc(args.start), data_end=_utc(args.end), candidates=tuple(ids),
        success_criteria=dict(policy.success_criteria), registered_by=args.registered_by, registered_at=now,
    )
    log = PreregistrationLog(args.log)
    screening = ScreeningLedger(args.screening)
    split = build_chronological_split(hypothesis.data_start, hypothesis.data_end, align_to=Timeframe(args.timeframe).delta)
    check_finalists(hypothesis, screening, load_reserved(args.reserved_path), split.test_start, split.test_end)
    register_checked(log, args.log, hypothesis, locked, rationale=args.rationale, replication=args.replication,
                     budget=Budget(max_per_window=args.max_per_window, max_all_kinds=args.max_per_window))

    tf = Timeframe(args.timeframe)
    # Warm-up history before the registered range (indicator input only; never scored, lock-checked).
    warmup_start = hypothesis.data_start - (max(c.warmup for c in candidates) + 1) * tf.delta
    candles, notes = load_candles(args.symbol, tf, warmup_start, hypothesis.data_end)
    fnotes_side: list[str] = []
    if any(getattr(c, "use_side_data", False) for c in candidates):
        # Side-data candidates get funding + open interest over the whole loaded span (warm-up included);
        # each score call filters to records known at its own bar, so nothing from the future is visible.
        funding_map, n1 = load_funding(args.symbol, candles[0].open_time, hypothesis.data_end)
        oi_points, n2 = load_open_interest(args.symbol, candles[0].open_time, hypothesis.data_end)
        from cointrader.data.binance_funding import FundingRateRecord  # noqa: E402
        funding_records = [FundingRateRecord(args.symbol, t, r, float("nan"), "binance_vision_archive")
                           for t, r in funding_map.items()]
        candidates = [c.attach_side_data(funding=funding_records, open_interest=oi_points)
                      if getattr(c, "use_side_data", False) else c for c in candidates]
        fnotes_side = n1 + n2 + [f"side data: {len(funding_records)} funding records, {len(oi_points)} open-interest days"]
    futures, fnotes = load_futures_terms(args.symbol, hypothesis.data_start, hypothesis.data_end)
    filters, _, _ = load_markets()
    report = run_signal_study(hypothesis, log, candles, candidates, locked, policy=policy,
                              risk=RiskEngine(load_risk(), filters), futures=futures,
                              screened_trials=screening.total_candidates())
    window = lock_test_window(report, path=args.locked_path, note=f"{args.hypothesis_id} {args.family} {args.symbol} {args.timeframe}; "
                                           f"TEST used once by run_validation.py at {now.isoformat()}")
    release(args.symbol, args.reserved_path)  # the reservation is now a locked window
    ledger = CandidateLedger(args.ledger)
    criteria = criteria_from(hypothesis, policy)
    transitions = {}
    for c in report.candidates:
        written = ledger.advance(c.strategy_id, report.evidence_for(c.strategy_id), criteria, now,
                                 hypothesis_id=args.hypothesis_id)
        transitions[c.strategy_id] = [f"{t.from_status.value}->{t.to_status.value}: {t.reason}" for t in written]
    out = {
        "label": report.label, "hypothesis_id": report.hypothesis_id, "market": report.market,
        "timeframe": report.timeframe, "fold_count": report.fold_count, "pbo": _safe(report.pbo),
        "trials_deflated_against": report.trials_deflated_against,
        "test_window_locked": {"name": window.name, "start": window.start.isoformat(), "end": window.end.isoformat()},
        "candidates": [{k: _safe(v) for k, v in dataclasses.asdict(c).items() if k != "test_summary"}
                       | {"test_summary": c.test_summary} for c in report.candidates],
        "lifecycle_transitions": transitions, "data_notes": notes + fnotes + fnotes_side,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2, default=_safe)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
