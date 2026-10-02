#!/usr/bin/env python3
"""Runs a pre-registered basis-neutral carry study end to end on real
Binance spot + futures data (ADR-0013).

    python3 scripts/run_basis_carry_study.py --hypothesis-id H-0012 \
        --symbol BTCUSDT --start 2021-01-01 --end 2026-09-01 \
        --statement "Basis-neutral funding carry beats flat on BTCUSDT after costs" \
        --registered-by 동동 --out reports/H-0012.json

Same rules as `run_funding_carry_study.py`: the first run registers the
hypothesis in `research/preregistration.jsonl` (commit that file). Any
later run with the same id must match it exactly. After the report is
written, lock its TEST window in `configs/locked_windows.json` -- never
re-run a study against it.

Data source (ADR-0012/ADR-0013): reads `data.binance.vision`'s static
archive for funding, futures klines AND spot klines -- confirmed
reachable from GitHub Actions for all three (the live REST API is
region-blocked, per ADR-0012). Archive gaps (missing files) are printed,
never silently dropped. Unlike `run_funding_carry_study.py`, there is no
`--data-source live` fallback here yet: the live spot REST fetcher
(`binance_futures.py`'s spot equivalent) does not exist in this repo.
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

from cointrader.data.binance_vision import (  # noqa: E402
    BinanceVisionFundingRateHistory,
    BinanceVisionFuturesCandles,
    BinanceVisionSpotCandles,
    join_mark_price_from_candles,
)
from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.data.quality import check_candles  # noqa: E402
from cointrader.strategies.basis_carry import basis_carry_candidate_grid  # noqa: E402
from cointrader.validation.basis_carry_study import run_basis_carry_study  # noqa: E402
from cointrader.validation.locked_windows import load_locked_windows  # noqa: E402
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog  # noqa: E402

CANDIDATE_GRIDS = {
    "basis_carry": basis_carry_candidate_grid,
}


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
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--registered-by", required=True)
    p.add_argument("--candidate-set", choices=sorted(CANDIDATE_GRIDS), default="basis_carry")
    p.add_argument("--fold-train-days", type=int, default=30)
    p.add_argument("--fold-test-days", type=int, default=14)
    p.add_argument("--log", type=Path, default=REPO / "research" / "preregistration.jsonl")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)

    candidates = CANDIDATE_GRIDS[args.candidate_set]()
    hypothesis = Hypothesis(
        hypothesis_id=args.hypothesis_id,
        statement=args.statement,
        market=args.symbol,
        timeframe="8h",
        data_start=_utc(args.start),
        data_end=_utc(args.end),
        candidates=tuple(c.name for c in candidates),
        success_criteria={"max_pbo": 0.2, "min_dsr": 0.95, "min_test_excess_return": 0.0},
        registered_by=args.registered_by,
        registered_at=datetime.now(timezone.utc),
    )
    log = PreregistrationLog(args.log)
    print(f"pre-registered {args.hypothesis_id}: {log.register(hypothesis)}", file=sys.stderr)

    funding_client = BinanceVisionFundingRateHistory()
    funding = funding_client.fetch(args.symbol, hypothesis.data_start, hypothesis.data_end)
    for gap in funding_client.last_gaps:
        print(f"  funding archive gap: {gap.detail}", file=sys.stderr)
    print(f"{len(funding)} funding records ({len(funding_client.last_gaps)} archive gap(s))", file=sys.stderr)

    futures_client = BinanceVisionFuturesCandles()
    futures_candles = futures_client.fetch(args.symbol, Timeframe.HOUR_1, hypothesis.data_start, hypothesis.data_end)
    for gap in futures_client.last_gaps:
        print(f"  futures klines archive gap: {gap.detail}", file=sys.stderr)
    print(f"{len(futures_candles)} futures candles ({len(futures_client.last_gaps)} archive gap(s))", file=sys.stderr)

    spot_client = BinanceVisionSpotCandles()
    spot_candles = spot_client.fetch(args.symbol, Timeframe.HOUR_1, hypothesis.data_start, hypothesis.data_end)
    for gap in spot_client.last_gaps:
        print(f"  spot klines archive gap: {gap.detail}", file=sys.stderr)
    print(f"{len(spot_candles)} spot candles ({len(spot_client.last_gaps)} archive gap(s))", file=sys.stderr)

    funding = join_mark_price_from_candles(funding, futures_candles)
    gap_count = len(funding_client.last_gaps) + len(futures_client.last_gaps) + len(spot_client.last_gaps)

    futures_issues = check_candles(futures_candles)
    spot_issues = check_candles(spot_candles)
    print(f"{len(futures_issues)} futures quality issue(s), {len(spot_issues)} spot quality issue(s)", file=sys.stderr)
    for issue in (futures_issues + spot_issues)[:20]:
        print(f"  {issue.kind} {issue.at.isoformat()} {issue.detail}", file=sys.stderr)

    report = run_basis_carry_study(
        hypothesis, log, funding, spot_candles, futures_candles, candidates, load_locked_windows(),
        fold_train=timedelta(days=args.fold_train_days), fold_test=timedelta(days=args.fold_test_days),
    )
    payload = _json_safe(dataclasses.asdict(report))
    payload["timeframe"] = hypothesis.timeframe
    payload["funding_record_count"] = len(funding)
    payload["futures_candle_count"] = len(futures_candles)
    payload["spot_candle_count"] = len(spot_candles)
    payload["quality_issue_count"] = len(futures_issues) + len(spot_issues)
    payload["archive_gap_count"] = gap_count
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}; lock TEST {report.test_start.isoformat()}..{report.test_end.isoformat()} now",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
