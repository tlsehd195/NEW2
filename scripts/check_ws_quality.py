#!/usr/bin/env python3
"""Summarises WebSocket data-quality events from a paper run (ADR-0040).

    python3 scripts/check_ws_quality.py [--data-root var/data] [--hours 24]

The Binance stream parsers were written against the published format and have never
seen a live message (ADR-0015). After the first real paper run this answers one
question: did any message fail to parse? Exit 0 = nothing malformed AND the run
produced decisions (so kline messages were parsed); exit 1 = look at the samples.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.journal.store import LayeredStore  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402


def summarize(store: LayeredStore, since: datetime) -> dict:
    start = since.date()
    quality = [r for r in store.read("quality", start=start)]
    kinds = Counter((r["kind"], r["symbol"]) for r in quality)
    malformed = [r for r in quality if r["kind"] == "malformed_message"]
    decisions = sum(1 for _ in store.read("decision", start=start))
    return {"quality_events": dict(kinds), "malformed": len(malformed),
            "malformed_samples": [r["detail"] for r in malformed[:5]], "decisions": decisions}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", type=Path, default=REPO / load_paper()["data_root"])
    ap.add_argument("--hours", type=float, default=24.0)
    args = ap.parse_args()
    out = summarize(LayeredStore(args.data_root), datetime.now(timezone.utc) - timedelta(hours=args.hours))
    for k, v in out.items():
        print(f"{k}: {v}")
    ok = out["malformed"] == 0 and out["decisions"] > 0
    print("OK: no malformed messages and decisions were made" if ok else
          "CHECK: malformed messages found, or no decisions (kline stream may not be parsing)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
