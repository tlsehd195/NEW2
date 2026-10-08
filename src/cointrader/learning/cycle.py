"""Once-a-day learning cycle run inside the paper (later live) process.

For each finished UTC day D it runs every step once, after `lag` past
midnight (so the targets the steps need have been realized), writes the
results to the `learning` layer and a `daily_cycle_done` marker. Days
already marked are skipped, so a restart never repeats a day; days missed
while the process was down are not backfilled automatically (run
`scripts/run_learning_cycle.py --day` for that).

It runs synchronously between feed events: the journal files are only
ever written by this same thread, so a read never sees a half-written line.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Iterable, Optional

from cointrader.data.models import Timeframe
from cointrader.journal.store import LayeredStore
from cointrader.learning.challenger import challenger_records
from cointrader.learning.drift_check import daily_drift_record
from cointrader.notifications.notifier import Notifier, Severity

# A step gets (store, symbol, timeframe, day_start) and returns learning-layer records.
Step = Callable[[LayeredStore, str, str, datetime], list[dict]]


def drift_step(store: LayeredStore, symbol: str, timeframe: str, day_start: datetime) -> list[dict]:
    return [daily_drift_record(store, symbol, timeframe, day_start)]


def challenger_step(champions: dict[str, int]) -> Step:
    """Shadow retrain-and-compare against the given champions (strategy_id -> horizon in bars)."""
    def step(store: LayeredStore, symbol: str, timeframe: str, day_start: datetime) -> list[dict]:
        return challenger_records(store, symbol, timeframe, day_start, champions)
    return step


def lag_for(champions: dict[str, int], timeframe: str = "15m") -> timedelta:
    """Wait until the longest champion horizon after midnight has been realized (+30 min slack)."""
    longest = max(champions.values(), default=0)
    return max(timedelta(hours=1), Timeframe(timeframe).delta * longest + timedelta(minutes=30))


class DailyLearningCycle:
    def __init__(self, store: LayeredStore, symbols: Iterable[str], *, timeframe: str = "15m",
                 steps: Optional[list[Step]] = None, lag: timedelta = timedelta(hours=1),
                 notifier: Optional[Notifier] = None) -> None:
        self.store = store
        self.symbols = tuple(symbols)
        self.timeframe = timeframe
        self.steps = list(steps) if steps is not None else [drift_step]
        self.lag = lag
        self.notifier = notifier
        self.next_try: Optional[datetime] = None  # set by the caller after a failure (retry back-off)
        self.done: set[str] = {r["day"] for r in store.read("learning")
                               if r.get("event") == "daily_cycle_done" and r.get("timeframe") == timeframe}

    def due_day(self, now: datetime) -> date:
        return (now - self.lag).date() - timedelta(days=1)

    def maybe_run(self, now: datetime) -> Optional[list[dict]]:
        day = self.due_day(now)
        if day.isoformat() in self.done or (self.next_try is not None and now < self.next_try):
            return None
        return self.run_day(day, now=now)

    def run_day(self, day: date, *, now: datetime) -> list[dict]:
        day_start = datetime.combine(day, time(0), tzinfo=timezone.utc)
        out: list[dict] = []
        for symbol in self.symbols:
            for step in self.steps:
                for rec in step(self.store, symbol, self.timeframe, day_start):
                    out.append(self.store.append("learning", rec, at=now))
                    if rec.get("status") == "DRIFT_DETECTED" and self.notifier is not None:
                        self.notifier.notify(Severity.WARNING, f"{rec['event']} {symbol} {rec['day']}",
                                             f"drifted: {', '.join(rec.get('drifted', []))} (observation only)",
                                             at=now, key=f"{rec['event']}:{symbol}")
        self.store.append("learning", {"event": "daily_cycle_done", "symbol": "*", "timeframe": self.timeframe,
                                       "day": day.isoformat(), "records": len(out)}, at=now)
        self.done.add(day.isoformat())
        return out
