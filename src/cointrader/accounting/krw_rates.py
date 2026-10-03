"""KRW per USDT, collected from Upbit's public REST candles (ADR-0040).

The KRW ledger values every USDT movement at the rate of its own moment,
so it needs a history of that rate. `collect` appends the closes of
Upbit's KRW-USDT 15-minute candles to a CSV (`time,rate,source`, time =
the candle's CLOSE time, so a rate is only used after it is known).
Re-running only appends what is newer. A bad or implausible rate raises
and nothing is written: a wrong rate would silently corrupt every KRW
figure, and a gap is already handled downstream (`KrwRateSeries` refuses
a time with no rate within `max_staleness`).

No API key is needed (public quotation endpoint). Paper and live use the
same rates; only the flows file differs.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from cointrader._time import require_aware
from cointrader.accounting.krw_ledger import KrwRateSeries
from cointrader.data.models import Timeframe
from cointrader.data.upbit_rest import SOURCE, UpbitRestCandles

MARKET = "KRW-USDT"
# USDT has traded between roughly 1,000 and 1,700 KRW; anything outside this band is a bad
# tick or a wrong market, not a price. Generous on purpose: it only has to catch garbage.
PLAUSIBLE_KRW_PER_USDT = (500.0, 5000.0)
_BAR = Timeframe.MINUTE_15


def read_points(path: Path) -> list[tuple[datetime, float]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [(datetime.fromisoformat(r["time"]), float(r["rate"])) for r in csv.DictReader(f)]


def collect(client: UpbitRestCandles, path: Path, *, now: datetime,
            backfill: timedelta = timedelta(days=2)) -> int:
    """Appends closed 15m KRW-USDT candles newer than the file's last point
    (or `backfill` back when the file is new). Returns how many were added."""
    require_aware("now", now)
    have = read_points(path)
    last = have[-1][0] if have else None
    start = (last - _BAR.delta) if last else now - backfill
    candles = client.fetch(MARKET, _BAR, start, now)
    lo, hi = PLAUSIBLE_KRW_PER_USDT
    new = []
    for c in candles:
        if c.source != SOURCE:
            raise ValueError(f"unexpected candle source {c.source!r}; expected {SOURCE}")
        if not math.isfinite(c.close) or not lo <= c.close <= hi:
            raise ValueError(f"implausible {MARKET} close {c.close!r} at {c.close_time.isoformat()}; nothing written")
        if last is None or c.close_time > last:
            new.append(c)
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if fresh:
            w.writerow(["time", "rate", "source"])
        for c in new:
            w.writerow([c.close_time.isoformat(), repr(c.close), c.source])
    return len(new)


def load_series(path: Path, max_staleness: timedelta) -> KrwRateSeries:
    pts = read_points(path)
    if not pts:
        raise ValueError(f"no KRW/USDT rates in {path}; run scripts/collect_krw_rates.py first")
    return KrwRateSeries(tuple(pts), f"upbit_{MARKET}_15m_close", max_staleness)


def ensure_paper_start(flows_path: Path, series: KrwRateSeries, *, usdt: float, now: datetime,
                       fee_rate: float) -> bool:
    """Paper only. Writes the simulated KRW -> USDT purchase that funds the paper account, once,
    at the rate of the moment (so later FX moves show up as 환차손익). Returns True if written.
    A live ledger must come from real exchange records, never from this."""
    if flows_path.exists() and flows_path.read_text(encoding="utf-8").strip():
        return False
    rate = series.at(now)
    gross = usdt * rate
    row = {"type": "usdt_purchase", "at": now.isoformat(), "mode": "paper", "krw_gross": gross, "usdt": usdt,
           "fee_krw": gross * fee_rate, "venue": "paper_simulated"}
    flows_path.parent.mkdir(parents=True, exist_ok=True)
    flows_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return True
