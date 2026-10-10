#!/usr/bin/env python3
"""Reduce Binance `bookTicker` daily archives to one top-of-book imbalance reading per quarter-hour mark (ADR-0078).

Each day's zip (~140 MB for BTCUSDT) is downloaded to a temp file, streamed, reduced and deleted, so the 45 GB never
sits on disk. For every quarter-hour mark t it keeps the last update in each 30 s slot of [t-5min, t) (the same cadence as
the `bookDepth` snapshots) and writes mean/last of (bid_qty - ask_qty)/(bid_qty + ask_qty). Output is JSONL
`{"t": iso-utc mark, "mean": .., "last": .., "n": slots}`, one line per mark with >= 3 slots. Research only.

    python3 scripts/extract_book_ticker_marks.py --symbol BTCUSDT --start 2023-05-10 --end 2024-03-25 --out marks.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://data.binance.vision/data/futures/um/daily/bookTicker"
SLOT_MS = 30_000
WINDOW_MS = 300_000
QUARTER_MS = 900_000


def process_day(args):
    symbol, day = args
    url = f"{BASE}/{symbol}/{symbol}-bookTicker-{day:%Y-%m-%d}.zip"
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=os.environ.get("TMPDIR"))
    os.close(fd)
    try:
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return day, None
                if attempt == 3:
                    raise
            except Exception:
                if attempt == 3:
                    raise
        slots: dict[int, float] = {}  # slot index (30 s since epoch) -> imbalance of the last update in that slot
        with zipfile.ZipFile(tmp) as z, z.open(z.namelist()[0]) as f:
            f.readline()
            for line in f:
                parts = line.split(b",")
                ts = int(parts[5])  # transaction_time ms
                if ts % QUARTER_MS < QUARTER_MS - WINDOW_MS:
                    continue
                bq, aq = float(parts[2]), float(parts[4])
                tot = bq + aq
                if tot > 0:
                    slots[ts // SLOT_MS] = (bq - aq) / tot
        marks: dict[int, list] = {}
        for s in sorted(slots):
            mark = ((s * SLOT_MS) // QUARTER_MS + 1) * QUARTER_MS  # the quarter hour this slot precedes
            marks.setdefault(mark, []).append(slots[s])
        out = []
        for mark, v in sorted(marks.items()):
            if len(v) >= 3:
                out.append({"t": datetime.fromtimestamp(mark / 1000, tz=timezone.utc).isoformat(),
                            "mean": math.fsum(v) / len(v), "last": v[-1], "n": len(v)})
        return day, out
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True, help="exclusive")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    start = datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc)
    days = []
    d = start
    while d < end:
        days.append((a.symbol, d))
        d += timedelta(days=1)
    done = set()
    if a.out.exists():  # resume: skip days already written
        for line in a.out.read_text().splitlines():
            done.add(line.split('"t": "')[1][:10])
    todo = [x for x in days if f"{x[1]:%Y-%m-%d}" not in done]
    missing = []
    with ProcessPoolExecutor(a.workers) as ex, a.out.open("a") as fh:
        for day, rows in ex.map(process_day, todo):
            if rows is None:
                missing.append(f"{day:%Y-%m-%d}")
            else:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
                fh.flush()
            print(f"{day:%Y-%m-%d} {'missing' if rows is None else len(rows)}", file=sys.stderr, flush=True)
    print(json.dumps({"days": len(days), "already_done": len(done), "missing_days": missing}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
