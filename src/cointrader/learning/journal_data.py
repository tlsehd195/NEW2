"""Turn the paper/live journal back into training data.

The journal stores what the trader actually saw: closed candles in the
`normalized` layer (stream bars and bootstrap bars, each with its real
`source`) and the feature snapshot of every decision in the `feature`
layer. These readers return them per symbol and timeframe, oldest first,
deduplicated by bar time (a restart re-journals its bootstrap bars; the
first copy wins), and only for bars that had closed by `until`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from cointrader._time import require_aware
from cointrader.data.models import Candle, Timeframe
from cointrader.journal.store import LayeredStore


def _days(start: Optional[datetime], until: datetime) -> tuple[Optional[date], date]:
    # Records are partitioned by receipt time, which is at or just after the bar's close.
    return (start.date() if start else None), (until + timedelta(days=1)).date()


def load_candles(store: LayeredStore, symbol: str, timeframe: str, *, until: datetime,
                 start: Optional[datetime] = None) -> list[Candle]:
    """Closed candles with `start <= open_time` and `close_time <= until`."""
    require_aware("until", until)
    tf = Timeframe(timeframe)
    first, last = _days(start, until)
    by_time: dict[datetime, Candle] = {}
    for r in store.read("normalized", start=first, end=last):
        if r.get("kind") != "candle" or r.get("symbol") != symbol or r.get("timeframe") != timeframe:
            continue
        t = datetime.fromisoformat(r["open_time"])
        if t in by_time or t + tf.delta > until or (start is not None and t < start):
            continue
        by_time[t] = Candle(symbol, tf, t, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]),
                            float(r["v"]), r["source"], datetime.fromisoformat(r["recorded_at"]))
    return [by_time[t] for t in sorted(by_time)]


def missing_bars(candles: list[Candle]) -> int:
    """Bars absent between the first and last candle (a hole the stream or a restart left)."""
    if len(candles) < 2:
        return 0
    step = candles[0].timeframe.delta
    return int((candles[-1].open_time - candles[0].open_time) / step) + 1 - len(candles)


def load_feature_rows(store: LayeredStore, symbol: str, timeframe: str, *, start: datetime,
                      until: datetime) -> list[dict]:
    """Feature snapshots with `start <= as_of < until`, one per bar (several strategies log the same
    bar; the first row wins)."""
    require_aware("start", start)
    require_aware("until", until)
    rows: dict[str, dict] = {}
    for r in store.read("feature", start=start.date(), end=(until + timedelta(days=1)).date()):
        if r.get("symbol") != symbol or r.get("timeframe") != timeframe or "as_of" not in r:
            continue
        at = datetime.fromisoformat(r["as_of"])
        if start <= at < until and r["as_of"] not in rows:
            rows[r["as_of"]] = r["features"]
    return [rows[k] for k in sorted(rows)]
