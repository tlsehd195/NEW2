from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from cointrader.journal.store import LayeredStore
from cointrader.learning.cycle import DailyLearningCycle
from cointrader.learning.drift_check import DRIFT_FEATURES, daily_drift_record
from cointrader.learning.journal_data import load_candles, load_feature_rows, missing_bars

D0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
SYM = "BTCUSDT"
STEP = timedelta(minutes=15)


def _candle(store, t, price, via="stream", source="binance_ws"):
    store.append("normalized", {"kind": "candle", "symbol": SYM, "source": source, "via": via, "timeframe": "15m",
                                "open_time": t.isoformat(), "o": price, "h": price, "l": price, "c": price, "v": 1.0},
                 at=t + STEP + timedelta(seconds=1))


def _features(store, start, days, shift=0.0, seed=0):
    rng = random.Random(seed)
    t = start
    while t < start + timedelta(days=days):
        feats = {k: rng.gauss(shift, 1.0) for k in DRIFT_FEATURES}
        for sid in ("a", "b"):  # two strategies log the same bar
            store.append("feature", {"symbol": SYM, "timeframe": "15m", "feature_version": "x", "features": feats,
                                     "as_of": t.isoformat()}, at=t)
        t += STEP


def test_load_candles_dedupes_and_respects_until(tmp_path):
    store = LayeredStore(tmp_path)
    for i in range(10):
        _candle(store, D0 + i * STEP, 100.0 + i)
    _candle(store, D0, 999.0, via="bootstrap", source="binance_rest")  # restart re-journals a bar: first wins
    _candle(store, D0 + 12 * STEP, 112.0)
    got = load_candles(store, SYM, "15m", until=D0 + 10 * STEP)
    assert [c.close for c in got] == [100.0 + i for i in range(10)]
    full = load_candles(store, SYM, "15m", until=D0 + 13 * STEP)
    assert missing_bars(full) == 2
    assert load_candles(store, "ETHUSDT", "15m", until=D0 + 13 * STEP) == []


def test_feature_rows_one_per_bar(tmp_path):
    store = LayeredStore(tmp_path)
    _features(store, D0, 1)
    rows = load_feature_rows(store, SYM, "15m", start=D0, until=D0 + timedelta(days=1))
    assert len(rows) == 96


def test_drift_statuses(tmp_path):
    store = LayeredStore(tmp_path)
    day = D0 + timedelta(days=7)
    assert daily_drift_record(store, SYM, "15m", day)["status"] == "UNKNOWN"  # no data is never "fine"
    _features(store, D0, 7, seed=1)
    _features(store, day, 1, seed=2)
    rec = daily_drift_record(store, SYM, "15m", day)
    assert rec["n_baseline"] == 7 * 96 and rec["n_current"] == 96
    assert rec["status"] in ("NO_DRIFT", "DRIFT_DETECTED")
    shifted = LayeredStore(tmp_path / "shifted")
    _features(shifted, D0, 7, seed=1)
    _features(shifted, day, 1, shift=5.0, seed=2)
    rec = daily_drift_record(shifted, SYM, "15m", day)
    assert rec["status"] == "DRIFT_DETECTED" and set(rec["drifted"]) == set(DRIFT_FEATURES)


class _Notes:
    def __init__(self):
        self.sent = []

    def notify(self, severity, title, body, *, at, key=None):
        self.sent.append(title)
        return True


def test_cycle_runs_each_day_once_and_survives_restart(tmp_path):
    store = LayeredStore(tmp_path)
    _features(store, D0, 7, seed=1)
    _features(store, D0 + timedelta(days=7), 1, shift=5.0, seed=2)
    notes = _Notes()
    cycle = DailyLearningCycle(store, [SYM], notifier=notes)
    early = D0 + timedelta(days=8, minutes=30)  # within the 1h lag: 10-08 is not due yet, 10-07 is
    assert cycle.due_day(early).isoformat() == "2026-10-07"
    now = D0 + timedelta(days=8, hours=2)
    out = cycle.maybe_run(now)
    assert out and out[0]["event"] == "feature_drift" and out[0]["day"] == "2026-10-08"
    assert notes.sent and "feature_drift" in notes.sent[0]
    assert cycle.maybe_run(now + timedelta(minutes=15)) is None
    again = DailyLearningCycle(store, [SYM])  # a restarted process does not repeat the day
    assert again.maybe_run(now) is None
    assert [r["day"] for r in store.read("learning") if r["event"] == "feature_drift"] == ["2026-10-08"]


def test_a_failing_learning_step_never_stops_the_trader(tmp_path):
    from types import SimpleNamespace

    from cointrader.paper.runner import _run_learning

    def boom(store, symbol, timeframe, day_start):
        raise RuntimeError("bad day")

    store = LayeredStore(tmp_path)
    notes = _Notes()
    trader = SimpleNamespace(store=store, notifier=notes, _now=D0 + timedelta(days=2))
    cycle = DailyLearningCycle(store, [SYM], steps=[boom])
    _run_learning(trader, cycle)
    trader._now += timedelta(minutes=30)
    _run_learning(trader, cycle)  # backs off instead of retrying on every feed event
    assert [r["event"] for r in store.read("audit")] == ["learning_cycle_failed"]
    assert notes.sent == ["learning cycle failed"]


def test_cycle_catches_up_missed_days_one_per_call_but_not_on_first_run(tmp_path):
    store = LayeredStore(tmp_path)
    _features(store, D0, 10, seed=3)
    cycle = DailyLearningCycle(store, [SYM])
    first = D0 + timedelta(days=5, hours=2)  # first ever run: only the finished day 10-05
    assert [r["day"] for r in cycle.maybe_run(first)] == ["2026-10-05"]
    assert cycle.maybe_run(first) is None
    later = D0 + timedelta(days=8, hours=2)  # process was down for 10-06..10-07; 10-08 is due
    days = []
    while (out := cycle.maybe_run(later)) is not None:
        days += [r["day"] for r in out]
    assert days == ["2026-10-06", "2026-10-07", "2026-10-08"]
    assert cycle.maybe_run(later) is None
