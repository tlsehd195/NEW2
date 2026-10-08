"""Run (or backfill) the daily learning cycle for one UTC day by hand (ADR-0044).

The paper trader runs this on its own once a day; use this script for a day
it missed while it was not running. Reads and writes only the journal
(`data_root` in configs/paper.json); never touches trading.

    python3 scripts/run_learning_cycle.py --day 2026-10-07
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.journal.store import LayeredStore  # noqa: E402
from cointrader.learning.cycle import DailyLearningCycle  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--day", required=True, type=date.fromisoformat, help="UTC day, YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="run even if the day is already marked done")
    args = ap.parse_args()
    cfg = load_paper()
    cycle = DailyLearningCycle(LayeredStore(REPO / cfg["data_root"]), cfg["symbols"], timeframe="15m")
    if args.day.isoformat() in cycle.done and not args.force:
        print(f"{args.day} already done (use --force to add another run)")
        return 0
    for rec in cycle.run_day(args.day, now=datetime.now(timezone.utc)):
        print(json.dumps({k: rec[k] for k in ("event", "symbol", "day", "status") if k in rec}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
