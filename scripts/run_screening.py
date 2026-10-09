#!/usr/bin/env python3
"""Walk-forward SCREENING of strategies, without the held-out TEST (ADR-0051).

    python3 scripts/run_screening.py --family daytrade --symbol BTCUSDT --timeframe 15m \
        --start 2023-04-06 --end 2024-06-18 --strategies a b c --by 동동 --out reports/screen.json

No registration and no count limit: the TEST part of `[start, end)` is
never loaded. The first screening of a market reserves that TEST part
for the final exam (configs/reserved_windows.json); later screenings of
the market must stay clear of it. Every run is appended to
research/screening.jsonl and its candidates count as trials in the final
exam's DSR. To take finalists to the final exam, run
scripts/run_validation.py with the SAME --start/--end, at most 3
screened candidates, registered by a human.
Commit research/screening.jsonl and configs/reserved_windows.json afterwards.
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
from cointrader.research.market_data import load_candles, load_futures_terms  # noqa: E402
from cointrader.risk.engine import RiskEngine  # noqa: E402
from cointrader.settings import load_markets, load_risk  # noqa: E402
from cointrader.strategies.registry import StrategyRegistry  # noqa: E402
from cointrader.validation.locked_windows import load_locked_windows  # noqa: E402
from cointrader.validation.policies import POLICIES  # noqa: E402
from cointrader.validation.preregistration import PreregistrationLog  # noqa: E402
from cointrader.validation.screening import ScreeningLedger, load_reserved, reserve, run_screening  # noqa: E402
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
    ap.add_argument("--family", required=True, choices=sorted(POLICIES))
    ap.add_argument("--strategies", nargs="+", required=True, help="registered strategy ids")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--by", required=True, help="who ran it (a person, or RESEARCH_LOOP)")
    ap.add_argument("--note", default="")
    ap.add_argument("--log", type=Path, default=REPO / "research" / "preregistration.jsonl")
    ap.add_argument("--screening", type=Path, default=REPO / "research" / "screening.jsonl")
    ap.add_argument("--locked-path", type=Path, default=REPO / "configs" / "locked_windows.json")
    ap.add_argument("--reserved-path", type=Path, default=REPO / "configs" / "reserved_windows.json")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    policy = POLICIES[args.family]
    registry = StrategyRegistry.load()
    candidates = [registry.build(i, market=args.symbol, family=args.family) for i in args.strategies]
    start, end, tf = _utc(args.start), _utc(args.end), Timeframe(args.timeframe)
    split = build_chronological_split(start, end, align_to=tf.delta)
    warmup_start = start - (max(c.warmup for c in candidates) + 1) * tf.delta
    candles, notes = load_candles(args.symbol, tf, warmup_start, split.validation_end)  # TEST bars never loaded
    futures, fnotes = load_futures_terms(args.symbol, start, split.validation_end)
    filters, _, _ = load_markets()
    ledger = ScreeningLedger(args.screening)
    prior = PreregistrationLog(args.log).total_registered_candidates() + ledger.total_candidates()
    report = run_screening(candles, candidates, args.symbol, start, end, load_locked_windows(args.locked_path),
                           load_reserved(args.reserved_path), policy=policy, risk=RiskEngine(load_risk(), filters),
                           prior_trials=prior, futures=futures)
    if report.new_reservation:
        reserve(report.reservation, args.reserved_path)
    now = datetime.now(timezone.utc)
    row = {"at": now.isoformat(), "by": args.by, "note": args.note, "family": report.family,
           "market": report.market, "timeframe": report.timeframe, "start": start.isoformat(),
           "end": end.isoformat(), "train_start": report.train_start.isoformat(),
           "validation_end": report.validation_end.isoformat(), "fold_count": report.fold_count,
           "pbo": _safe(report.pbo), "trials_deflated_against": report.trials_deflated_against,
           "candidates": [{k: _safe(v) for k, v in dataclasses.asdict(c).items()} for c in report.candidates]}
    ledger.append(row)
    out = {"label": report.label, **row,
           "reserved_test": {"start": report.reservation.start.isoformat(), "end": report.reservation.end.isoformat(),
                             "new": report.new_reservation},
           "data_notes": notes + fnotes}
    text = json.dumps(out, ensure_ascii=False, indent=2, default=_safe)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
