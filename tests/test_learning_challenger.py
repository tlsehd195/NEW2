from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta, timezone

from cointrader.journal.store import LayeredStore
from cointrader.learning.challenger import STATUS, challenger_records, score
from cointrader.learning.cycle import DailyLearningCycle, challenger_step, lag_for

D0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
SYM = "BTCUSDT"
STEP = timedelta(minutes=15)
SID = "daytrade_indicator_vote_h16_c0.6_v1"


def _journal(store, days, *, seed=0, decisions_from=None):
    rng = random.Random(seed)
    p, t = 60_000.0, D0
    while t < D0 + timedelta(days=days):
        o = p
        p *= math.exp(rng.gauss(0, 0.003))
        store.append("normalized", {"kind": "candle", "symbol": SYM, "source": "binance_ws", "via": "stream",
                                    "timeframe": "15m", "open_time": t.isoformat(), "o": o, "h": max(o, p) * 1.001,
                                    "l": min(o, p) * 0.999, "c": p, "v": 10 + rng.random()}, at=t + STEP)
        if decisions_from is not None and t >= decisions_from:
            pl = rng.random()
            store.append("decision", {"decision_id": t.isoformat(), "symbol": SYM, "strategy_id": SID,
                                      "strategy_version": "1", "action": "hold", "reason": "x", "mode": "paper",
                                      "timeframe": "15m", "bar_open_time": t.isoformat(),
                                      "signal": {"entry": 1 if pl > 0.8 else -1 if pl < 0.2 else 0,
                                                 "features": {"p_long": pl}}}, at=t + STEP)
        t += STEP


def test_score_metrics():
    m = score([(0.01, 1, 0.02), (-0.01, -1, -0.01), (0.02, 0, -0.03), (None, 0, 0.01)])
    assert m["n_bars"] == 4 and m["n_entries"] == 2 and m["entry_hit_rate"] == 1.0
    assert m["mean_net_return"] == round((0.02 - 0.002 + 0.01 - 0.002) / 2, 6)
    assert m["direction_hit_rate"] == round(2 / 3, 4)


def test_challenger_scores_a_day_against_the_journaled_champion(tmp_path):
    store = LayeredStore(tmp_path)
    day = D0 + timedelta(days=24)
    _journal(store, 26, decisions_from=day - timedelta(hours=1))
    t = time.time()
    [rec] = challenger_records(store, SYM, "15m", day, {SID: 16})
    elapsed = time.time() - t
    assert rec["result"] == "SCORED" and rec["status"] == STATUS and "pre-registration" in rec["promotion"]
    assert rec["n_eval"] == 96 and rec["n_train"] == 2000
    # no leak: the newest training target bar closed by the start of the evaluated day
    assert datetime.fromisoformat(rec["train_to"]) + 16 * STEP <= day
    assert rec["champion"]["n_journaled"] == 96 and rec["champion"]["ic"] is not None
    assert [c["family"] for c in rec["challengers"]] == ["ridge", "forest"]
    assert all(c["result"] == "SCORED" and c["n_bars"] == 96 for c in rec["challengers"])
    assert elapsed < 60


def test_too_little_history_is_unknown_not_a_score(tmp_path):
    store = LayeredStore(tmp_path)
    _journal(store, 3)
    [rec] = challenger_records(store, SYM, "15m", D0 + timedelta(days=2), {SID: 16})
    assert rec["result"] == "UNKNOWN" and rec["reason"] == "insufficient_history"


def test_cycle_waits_for_the_longest_horizon(tmp_path):
    lag = lag_for({SID: 16, "other_h48": 48})
    assert lag == timedelta(hours=12, minutes=30)
    store = LayeredStore(tmp_path)
    _journal(store, 4)
    cycle = DailyLearningCycle(store, [SYM], steps=[challenger_step({SID: 16})], lag=lag)
    assert cycle.due_day(D0 + timedelta(days=3, hours=12)).isoformat() == "2026-09-02"
    out = cycle.maybe_run(D0 + timedelta(days=3, hours=13))
    assert out and out[0]["day"] == "2026-09-03" and out[0]["event"] == "challenger_eval"


def test_report_summarizes_shadow_scores(tmp_path):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "show_learning_report.py"
    spec = importlib.util.spec_from_file_location("show_learning_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = LayeredStore(tmp_path)
    day = D0 + timedelta(days=24)
    _journal(store, 26, decisions_from=day - timedelta(hours=1))
    recs = challenger_records(store, SYM, "15m", day, {SID: 16})
    recs.append({"event": "feature_drift", "symbol": SYM, "day": "2026-09-25", "status": "DRIFT_DETECTED",
                 "drifted": ["rsi_14"]})
    text = "\n".join(mod.summarize(recs))
    assert "검증 아님" in text and "champion" in text and "forest" in text and "rsi_14(1)" in text
