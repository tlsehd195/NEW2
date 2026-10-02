#!/usr/bin/env python3
"""Runs a pre-registered swing study end to end on Upbit hourly candles.

    python3 scripts/run_swing_study.py --hypothesis-id H-0001 \
        --market KRW-BTC --start 2021-01-01 --end 2026-09-01 \
        --statement "Trend-following beats buy-and-hold on KRW-BTC 1h after costs" \
        --registered-by 동동 --out reports/H-0001.json

The first run registers the hypothesis in `research/preregistration.jsonl`
(commit that file). Any later run with the same id must match it exactly.
After the report is written, lock its TEST window by adding an entry to
`configs/locked_windows.json` -- never re-run a study against it.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.data.quality import check_candles  # noqa: E402
from cointrader.data.upbit_rest import UpbitRestCandles  # noqa: E402
from cointrader.strategies.adaptive_ensemble import (  # noqa: E402
    adaptive_candidate_grid,
    adaptive_hysteresis_candidate_grid,
)
from cointrader.strategies.baselines import (  # noqa: E402
    default_candidate_grid,
    momentum_candidate_grid,
    momentum_candidate_grid_daily,
    momentum_candidate_grid_daily_decorrelated,
)

CANDIDATE_GRIDS = {
    "trend": default_candidate_grid,
    "momentum": momentum_candidate_grid,
    "momentum_daily": momentum_candidate_grid_daily,
    "momentum_daily_decorrelated": momentum_candidate_grid_daily_decorrelated,
    "adaptive": adaptive_candidate_grid,
    "adaptive_hysteresis": adaptive_hysteresis_candidate_grid,
}
from cointrader.validation.locked_windows import load_locked_windows  # noqa: E402
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog  # noqa: E402
from cointrader.validation.study import run_study  # noqa: E402


def _utc(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def _json_safe(value):
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--hypothesis-id", required=True)
    p.add_argument("--statement", required=True)
    p.add_argument("--market", default="KRW-BTC")
    p.add_argument("--timeframe", choices=[tf.value for tf in Timeframe], default=Timeframe.HOUR_1.value)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--registered-by", required=True)
    p.add_argument("--candidate-set", choices=sorted(CANDIDATE_GRIDS), default="trend")
    p.add_argument("--fold-train-days", type=int, default=30)
    p.add_argument("--fold-test-days", type=int, default=14)
    p.add_argument("--log", type=Path, default=REPO / "research" / "preregistration.jsonl")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)

    timeframe = Timeframe(args.timeframe)
    candidates = CANDIDATE_GRIDS[args.candidate_set]()
    hypothesis = Hypothesis(
        hypothesis_id=args.hypothesis_id,
        statement=args.statement,
        market=args.market,
        timeframe=timeframe.value,
        data_start=_utc(args.start),
        data_end=_utc(args.end),
        candidates=tuple(c.name for c in candidates),
        success_criteria={"max_pbo": 0.2, "min_dsr": 0.95, "min_test_excess_return": 0.0},
        registered_by=args.registered_by,
        registered_at=datetime.now(timezone.utc),
    )
    log = PreregistrationLog(args.log)
    print(f"pre-registered {args.hypothesis_id}: {log.register(hypothesis)}", file=sys.stderr)

    candles = UpbitRestCandles().fetch(args.market, timeframe, hypothesis.data_start, hypothesis.data_end)
    issues = check_candles(candles)
    print(f"{len(candles)} candles, {len(issues)} quality issue(s)", file=sys.stderr)
    for issue in issues[:20]:
        print(f"  {issue.kind} {issue.at.isoformat()} {issue.detail}", file=sys.stderr)

    report = run_study(
        hypothesis, log, candles, candidates, load_locked_windows(),
        fold_train=timedelta(days=args.fold_train_days), fold_test=timedelta(days=args.fold_test_days),
    )
    payload = _json_safe(dataclasses.asdict(report))
    payload["timeframe"] = hypothesis.timeframe
    payload["quality_issue_count"] = len(issues)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}; lock TEST {report.test_start.isoformat()}..{report.test_end.isoformat()} now",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
