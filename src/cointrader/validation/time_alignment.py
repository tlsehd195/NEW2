"""Multi-timeframe alignment without look-ahead.

When a strategy decides on a higher timeframe (e.g. a 1h trend filter)
but executes on a lower one (e.g. 5m), the 1h bar that is still forming
must never be visible. `closed_higher_bars` builds higher-timeframe bars
from lower-timeframe bars and returns ONLY those whose close time is at
or before `as_of` AND whose every constituent lower bar is present. An
incomplete bucket (a gap inside it) is dropped and reported, not built
from partial data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from cointrader._time import require_aware
from cointrader.data.models import Candle, Timeframe


@dataclass(frozen=True)
class AlignmentResult:
    bars: tuple[Candle, ...]
    incomplete_buckets: tuple[datetime, ...]


def _bucket_start(t: datetime, higher: Timeframe) -> datetime:
    step = higher.delta.total_seconds()
    return datetime.fromtimestamp((t.timestamp() // step) * step, tz=t.tzinfo)


def closed_higher_bars(lower: Sequence[Candle], higher: Timeframe, as_of: datetime) -> AlignmentResult:
    require_aware("as_of", as_of)
    if not lower:
        return AlignmentResult((), ())
    lower_tf = lower[0].timeframe
    if higher.delta <= lower_tf.delta or higher.delta % lower_tf.delta:
        raise ValueError(f"{higher.value} is not a whole multiple of {lower_tf.value}")
    per_bucket = int(higher.delta / lower_tf.delta)
    buckets: dict[datetime, list[Candle]] = {}
    for c in lower:
        if c.close_time > as_of:
            continue  # not closed yet at as_of
        buckets.setdefault(_bucket_start(c.open_time, higher), []).append(c)
    out, incomplete = [], []
    for start in sorted(buckets):
        members = sorted(buckets[start], key=lambda c: c.open_time)
        if start + higher.delta > as_of:
            continue  # higher bar still forming
        if len(members) != per_bucket or len({m.open_time for m in members}) != per_bucket:
            incomplete.append(start)
            continue
        latest_receipt = max(m.received_at for m in members)
        out.append(Candle(
            market=members[0].market, timeframe=higher, open_time=start, open=members[0].open,
            high=max(m.high for m in members), low=min(m.low for m in members), close=members[-1].close,
            volume=sum(m.volume for m in members), source=f"resampled:{members[0].source}",
            received_at=latest_receipt,
        ))
    return AlignmentResult(tuple(out), tuple(incomplete))
