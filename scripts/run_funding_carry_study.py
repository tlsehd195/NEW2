#!/usr/bin/env python3
"""Runs a pre-registered funding-carry study end to end on real Binance
futures data (ADR-0009).

    python3 scripts/run_funding_carry_study.py --hypothesis-id H-0011 \
        --symbol BTCUSDT --start 2021-01-01 --end 2026-09-01 \
        --statement "Funding-rate carry beats flat on BTCUSDT after costs" \
        --registered-by 동동 --out reports/H-0011.json

Same rules as `run_swing_study.py`: the first run registers the
hypothesis in `research/preregistration.jsonl` (commit that file). Any
later run with the same id must match it exactly. After the report is
written, lock its TEST window in `configs/locked_windows.json` -- never
re-run a study against it. `market` in the locked-window registry and
pre-registration log is the Binance symbol (e.g. "BTCUSDT"), which never
collides with Upbit's "KRW-*" strings used by the momentum studies.

Data source (ADR-0012): `fapi.binance.com` (the live REST API) returns
HTTP 451 from GitHub Actions -- region-blocked. This script instead reads
`data.binance.vision`'s static archive (`--data-source vision`, the
default), which is reachable and gives the same historical data for any
month/day Binance has already published, at the cost of never being able
to reach the last few unpublished days and never being usable for live
trading. `--data-source live` keeps the old REST path for anywhere the
451 block does not apply (e.g. a non-US self-hosted runner). Archive gaps
(missing files) are printed, never silently dropped.
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

from cointrader.data.binance_funding import BinanceFundingRateHistory  # noqa: E402
from cointrader.data.binance_futures import BinanceFuturesCandles  # noqa: E402
from cointrader.data.binance_vision import (  # noqa: E402
    BinanceVisionFundingRateHistory,
    BinanceVisionFuturesCandles,
    join_mark_price_from_candles,
)
from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.data.quality import check_candles  # noqa: E402
from cointrader.strategies.funding_carry import funding_carry_candidate_grid  # noqa: E402
from cointrader.validation.funding_study import run_funding_study  # noqa: E402
from cointrader.validation.locked_windows import load_locked_windows  # noqa: E402
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog  # noqa: E402

CANDIDATE_GRIDS = {
    "funding_carry": funding_carry_candidate_grid,
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
    p.add_argument("--candidate-set", choices=sorted(CANDIDATE_GRIDS), default="funding_carry")
    p.add_argument("--data-source", choices=("vision", "live"), default="vision",
                    help="'vision' = data.binance.vision archive (default, works around ADR-0012's "
                         "451 block); 'live' = fapi.binance.com REST API")
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

    if args.data_source == "vision":
        funding_client = BinanceVisionFundingRateHistory()
        funding = funding_client.fetch(args.symbol, hypothesis.data_start, hypothesis.data_end)
        for gap in funding_client.last_gaps:
            print(f"  funding archive gap: {gap.detail}", file=sys.stderr)
        print(f"{len(funding)} funding records ({len(funding_client.last_gaps)} archive gap(s)), source=data.binance.vision",
              file=sys.stderr)
        candle_client = BinanceVisionFuturesCandles()
        candles = candle_client.fetch(args.symbol, Timeframe.HOUR_1, hypothesis.data_start, hypothesis.data_end)
        for gap in candle_client.last_gaps:
            print(f"  klines archive gap: {gap.detail}", file=sys.stderr)
        funding = join_mark_price_from_candles(funding, candles)
        gap_count = len(funding_client.last_gaps) + len(candle_client.last_gaps)
    else:
        funding = BinanceFundingRateHistory().fetch(args.symbol, hypothesis.data_start, hypothesis.data_end)
        print(f"{len(funding)} funding records, source=fapi.binance.com live REST", file=sys.stderr)
        candles = BinanceFuturesCandles().fetch(args.symbol, Timeframe.HOUR_1, hypothesis.data_start, hypothesis.data_end)
        gap_count = 0
    issues = check_candles(candles)
    print(f"{len(candles)} futures candles, {len(issues)} quality issue(s)", file=sys.stderr)
    for issue in issues[:20]:
        print(f"  {issue.kind} {issue.at.isoformat()} {issue.detail}", file=sys.stderr)

    report = run_funding_study(
        hypothesis, log, funding, candles, candidates, load_locked_windows(),
        fold_train=timedelta(days=args.fold_train_days), fold_test=timedelta(days=args.fold_test_days),
    )
    payload = _json_safe(dataclasses.asdict(report))
    payload["timeframe"] = hypothesis.timeframe
    payload["data_source"] = args.data_source
    payload["funding_record_count"] = len(funding)
    payload["futures_candle_count"] = len(candles)
    payload["quality_issue_count"] = len(issues)
    payload["archive_gap_count"] = gap_count
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}; lock TEST {report.test_start.isoformat()}..{report.test_end.isoformat()} now",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
