#!/usr/bin/env python3
"""Collects Upbit KRW-USDT 15m closes into a CSV for the KRW ledger (ADR-0040).

    python3 scripts/collect_krw_rates.py [--out var/data/krw_usdt.csv] [--loop-minutes 15]

Public REST, no API key. One run appends whatever closed since the last
stored point (first run backfills 2 days). With --loop-minutes it repeats
until stopped; a failed round is reported and retried, never papered over.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.accounting.krw_rates import collect  # noqa: E402
from cointrader.data.upbit_rest import UpbitRestCandles  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / load_paper()["data_root"] / "krw_usdt.csv")
    ap.add_argument("--loop-minutes", type=float, default=0.0)
    args = ap.parse_args()
    client = UpbitRestCandles()
    while True:
        try:
            n = collect(client, args.out, now=datetime.now(timezone.utc))
            print(f"{datetime.now(timezone.utc).isoformat()} added {n} rate points -> {args.out}", flush=True)
        except Exception as exc:  # noqa: BLE001 - report and (in loop mode) retry; one-shot exits non-zero
            print(f"collect failed: {exc}", file=sys.stderr, flush=True)
            if not args.loop_minutes:
                return 1
        if not args.loop_minutes:
            return 0
        time.sleep(args.loop_minutes * 60)


if __name__ == "__main__":
    sys.exit(main())
