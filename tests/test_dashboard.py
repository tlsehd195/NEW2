import json
from datetime import datetime, timedelta, timezone

from cointrader.journal.store import LayeredStore
from cointrader.monitoring.dashboard import read_snapshot

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path):
    store = LayeredStore(tmp_path / "data")
    t0 = NOW - timedelta(minutes=45)
    for i in range(3):
        t = t0 + timedelta(minutes=15 * i)
        row = {"kind": "candle", "symbol": "ETHUSDT", "source": "t", "via": "ws", "timeframe": "15m",
               "open_time": t.isoformat(), "o": 100 + i, "h": 102 + i, "l": 99 + i, "c": 101 + i, "v": 1.0}
        store.append("normalized", row, at=t + timedelta(minutes=15))
        store.append("normalized", row, at=t + timedelta(minutes=16))  # duplicate bar must collapse
    store.append("normalized", {"kind": "candle", "symbol": "BTCUSDT", "source": "t", "timeframe": "15m",
                                "open_time": t0.isoformat(), "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}, at=NOW)
    store.append("normalized", {"kind": "book_stats_1m", "symbol": "ETHUSDT", "source": "t",
                                "interval_start": (NOW - timedelta(minutes=1)).isoformat(), "mid_price": 110.0}, at=NOW)
    store.append("execution", {"event": "fill", "client_order_id": "c1", "symbol": "ETHUSDT", "mode": "paper",
                               "side": "BUY", "quantity": 2.0, "price": 100.5}, at=NOW - timedelta(minutes=30))
    store.append("decision", {"decision_id": "d1", "symbol": "ETHUSDT", "strategy_id": "s", "strategy_version": "1",
                              "action": "enter_long", "reason": "vote", "mode": "paper",
                              "bar_open_time": (NOW - timedelta(minutes=30)).isoformat()}, at=NOW)
    store.append("outcome", {"trade_id": "x", "symbol": "ETHUSDT", "strategy_id": "s", "net_pnl": -1.5, "mode": "paper",
                             "exit_time": NOW.isoformat(), "direction": 1, "exit_reason": "stop"}, at=NOW)
    return tmp_path / "state"


def _state(state_dir, **trade):
    state_dir.mkdir(parents=True, exist_ok=True)
    state = {"saved_at": NOW.isoformat(), "broker": {"balance": 9990.0, "positions": {}, "resting": []},
             "open_trades": trade}
    (state_dir / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")


def test_snapshot_collects_chart_position_and_history(tmp_path):
    state_dir = _store(tmp_path)
    _state(state_dir, ETHUSDT={"direction": 1, "state": "open", "entry_qty": 2.0, "exit_qty": 0.0,
                               "entry_notional": 201.0, "stop_price": 95.0, "strategy_id": "s", "entry_time": None})
    snap = read_snapshot(state_dir, tmp_path / "data", "ETHUSDT", now=NOW)
    assert [c["close"] for c in snap["candles"]] == [101, 102, 103]  # deduped, ETH only, time ordered
    assert snap["last_price"] == 110.0
    pos = snap["position"]
    assert pos["direction"] == "long" and pos["entry_price"] == 100.5 and pos["stop_price"] == 95.0
    assert abs(pos["unrealized_pnl"] - 2.0 * (110.0 - 100.5)) < 1e-9
    assert snap["account"]["balance"] == 9990.0
    assert snap["fills"][0]["side"] == "BUY" and snap["decisions"][0]["action"] == "enter_long"
    assert snap["closed_trades"][0]["net_pnl"] == -1.5
    assert len(snap["overlays"]["ema20"]) == 3 and snap["overlays"]["ema20"][0] is None  # too few bars for a 20-bar line
    assert len(snap["overlays"]["obv"]) == 3 and snap["overlays"]["obv"][-1] > snap["overlays"]["obv"][0]  # rising bars
    assert snap["rules"]["enter_confidence"] == 0.6
    assert snap["votes"][0]["p_long"] is None  # a decision without vote features shows no numbers, not a crash


def test_snapshot_shows_each_indicators_probability_from_the_latest_decision(tmp_path):
    state_dir = _store(tmp_path)
    store = LayeredStore(tmp_path / "data")
    for i, p_long in enumerate((0.40, 0.62)):  # the later bar wins
        store.append("decision", {"decision_id": f"v{i}", "symbol": "ETHUSDT", "strategy_id": "s16", "strategy_version": "1",
                                  "action": "hold", "reason": "vote_no_entry", "mode": "paper",
                                  "bar_open_time": (NOW - timedelta(minutes=30 - 15 * i)).isoformat(),
                                  "signal": {"features": {"p_long": p_long, "agree_long": 4, "agree_short": 2, "vol_ratio": 1.1,
                                                          "p_rsi": 0.7, "p_obv_slope": 0.4}}}, at=NOW)
    votes = read_snapshot(state_dir, tmp_path / "data", "ETHUSDT", now=NOW)["votes"]
    vote = next(v for v in votes if v["strategy"] == "s16")
    assert vote["strategy"] == "s16" and vote["p_long"] == 0.62 and vote["agree_long"] == 4
    assert vote["per_indicator"] == {"rsi": 0.7, "obv_slope": 0.4}


def test_snapshot_without_state_or_position(tmp_path):
    state_dir = _store(tmp_path)
    assert read_snapshot(state_dir, tmp_path / "data", "ETHUSDT", now=NOW)["account"] is None
    _state(state_dir)
    snap = read_snapshot(state_dir, tmp_path / "data", "ETHUSDT", now=NOW)
    assert snap["position"] is None and snap["account"]["open_positions"] == 0
    assert read_snapshot(state_dir, tmp_path / "empty", "ETHUSDT", now=NOW)["candles"] == []


def test_dashboard_reads_only_the_journal():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("src/cointrader/monitoring/dashboard.py").read_text(encoding="utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    # only the journal reader, the same pure indicator math the strategy uses, and the strategy's default numbers
    assert {m for m in mods if m.startswith("cointrader")} == {"cointrader.journal.store", "cointrader.features",
                                                              "cointrader.strategies.daytrade"}


def test_dashboard_page_is_built_and_served_only_from_its_folder():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("run_dashboard", Path("scripts/run_dashboard.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    index = mod.static_file("/")
    assert index is not None and "<div id=\"root\">" in index.read_text(encoding="utf-8")  # committed build of dashboard-ui/
    assert mod.static_file("/../run_dashboard.py") is None
    assert mod.static_file("/assets/../../run_dashboard.py") is None
    assert mod.static_file("/nope.js") is None


def test_snapshot_scrolls_back_over_finished_days_and_reuses_them(tmp_path):
    store = LayeredStore(tmp_path / "data")
    for d in range(1, 4):  # three finished days before NOW, two bars each
        for i in range(2):
            t = (NOW - timedelta(days=d)).replace(hour=10, minute=15 * i)
            store.append("normalized", {"kind": "candle", "symbol": "ETHUSDT", "source": "t", "timeframe": "15m",
                                        "open_time": t.isoformat(), "o": d, "h": d, "l": d, "c": d, "v": 1.0}, at=t + timedelta(minutes=15))
    first = read_snapshot(tmp_path / "state", tmp_path / "data", "ETHUSDT", bars=50, days=4, now=NOW)
    assert [c["close"] for c in first["candles"]] == [3, 3, 2, 2, 1, 1]
    # the finished days now come from the cache: removing their files must not change the answer
    for p in (tmp_path / "data" / "normalized").glob("*.jsonl"):
        if p.name[:10] < (NOW - timedelta(days=1)).date().isoformat():
            p.unlink()
    again = read_snapshot(tmp_path / "state", tmp_path / "data", "ETHUSDT", bars=50, days=4, now=NOW)
    assert [c["close"] for c in again["candles"]][:4] == [3, 3, 2, 2]
