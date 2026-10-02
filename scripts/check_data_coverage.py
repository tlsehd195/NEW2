#!/usr/bin/env python3
"""Preflight: what does the archive actually hold for a planned study range?

Registers nothing, locks nothing, touches no TEST window -- it only reports
candle / funding / open-interest coverage so a hypothesis range is chosen
from facts instead of guessed (a wrong guess would burn a one-shot run).

    python3 scripts/check_data_coverage.py --symbol BTCUSDT --timeframe 1d \
        --start 2020-09-01 --end 2022-08-17 --out reports/coverage.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.research.market_data import load_candles, load_funding, load_open_interest  # noqa: E402


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _span(times: list[datetime], step: timedelta) -> dict:
    if not times:
        return {"count": 0}
    times = sorted(times)
    holes = [(a, b) for a, b in zip(times, times[1:]) if b - a > step * 1.5]
    return {"count": len(times), "first": times[0].isoformat(), "last": times[-1].isoformat(),
            "holes_over_1_5_steps": len(holes), "longest_hole_days": max(((b - a).days for a, b in holes), default=0)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    start, end, tf = _utc(args.start), _utc(args.end), Timeframe(args.timeframe)
    candles, cnotes = load_candles(args.symbol, tf, start, end)
    funding, fnotes = load_funding(args.symbol, start, end)
    oi, onotes = load_open_interest(args.symbol, start, end)
    out = {
        "symbol": args.symbol, "timeframe": args.timeframe, "start": start.isoformat(), "end": end.isoformat(),
        "candles": _span([c.open_time for c in candles], tf.delta),
        "funding": _span(list(funding), timedelta(hours=8)),
        "open_interest": _span([p.as_of for p in oi], timedelta(days=1)),
        "notes": {"candles": cnotes[:20], "funding": fnotes[:20], "open_interest": onotes[:20],
                  "open_interest_missing_days": len(onotes), "funding_gap_count": len(fnotes)},
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
