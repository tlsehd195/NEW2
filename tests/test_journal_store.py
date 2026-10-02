from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.data.market_events import TradeTick
from cointrader.data.models import OrderBookLevel, OrderBookSnapshot, Timeframe
from cointrader.journal.aggregation import MinuteBookAggregator, MinuteTradeAggregator, book_row
from cointrader.journal.store import (
    LAYERS,
    PROTECTED_LAYERS,
    LayeredStore,
    RetentionPolicy,
    StoreError,
    apply_retention,
    storage_report,
)
from cointrader.notifications.notifier import Notifier, Severity, redact
from cointrader.research.dataset import build_dataset
from cointrader.validation.locked_windows import LockedWindow
from tests.helpers import make_candles

T0 = datetime(2026, 3, 1, tzinfo=timezone.utc)
REPO_RETENTION = __import__("pathlib").Path(__file__).resolve().parents[1] / "configs" / "retention.json"


def _policy(**over):
    layers = {name: {"compress_after_days": 2, "delete_after_days": None} for name in LAYERS}
    layers["raw"] = {"compress_after_days": 1, "delete_after_days": 3}
    layers.update(over)
    return RetentionPolicy.from_dict({"layers": layers, "warn_total_bytes": 10**9, "critical_total_bytes": 10**10})


def test_append_partitions_by_day_and_stamps_schema_version(tmp_path):
    s = LayeredStore(tmp_path)
    s.append("audit", {"event": "start"}, at=T0)
    s.append("audit", {"event": "tick"}, at=T0 + timedelta(days=1))
    assert [p.name for p in s.partitions("audit")] == ["2026-03-01.jsonl", "2026-03-02.jsonl"]
    rows = list(s.read("audit"))
    assert rows[0]["data_schema_version"] and rows[0]["layer"] == "audit"


def test_record_missing_required_fields_is_refused(tmp_path):
    with pytest.raises(StoreError):
        LayeredStore(tmp_path).append("decision", {"decision_id": "x"}, at=T0)
    with pytest.raises(StoreError):
        LayeredStore(tmp_path).append("nonsense", {}, at=T0)


def test_retention_compresses_and_only_deletes_unprotected_layers(tmp_path):
    s = LayeredStore(tmp_path)
    for d in range(6):
        s.append("raw", {"source": "t", "stream": "x"}, at=T0 + timedelta(days=d))
        s.append("execution", {"event": "fill", "client_order_id": "c", "symbol": "B", "mode": "paper"},
                 at=T0 + timedelta(days=d))
    today = (T0 + timedelta(days=5)).date()
    rep = apply_retention(s, _policy(), today=today)
    raw_names = [p.name for p in s.partitions("raw")]
    assert "2026-03-01.jsonl" not in " ".join(raw_names)  # older than 3 days: deleted
    exec_names = [p.name for p in s.partitions("execution")]
    assert len(exec_names) == 6  # protected: never deleted
    assert exec_names[0].endswith(".gz") and exec_names[-1] == "2026-03-06.jsonl"  # today untouched
    assert rep.deleted and rep.compressed
    # compressed data reads back identically
    assert len(list(s.read("execution"))) == 6


def test_protected_layer_delete_config_is_rejected():
    with pytest.raises(StoreError):
        _policy(outcome={"compress_after_days": 2, "delete_after_days": 30})


def test_repo_retention_config_loads_and_protects_required_layers():
    p = RetentionPolicy.load(REPO_RETENTION)
    for layer in PROTECTED_LAYERS:
        assert p.layers[layer].delete_after_days is None


def test_late_record_for_archived_day_goes_to_sibling_file(tmp_path):
    s = LayeredStore(tmp_path)
    s.append("audit", {"event": "a"}, at=T0)
    apply_retention(s, _policy(), today=(T0 + timedelta(days=5)).date())
    s.append("audit", {"event": "late"}, at=T0)
    names = [p.name for p in s.partitions("audit")]
    assert "2026-03-01.jsonl.gz" in names and "2026-03-01.late.jsonl" in names
    with gzip.open(tmp_path / "audit" / "2026-03-01.jsonl.gz", "rt") as f:
        assert json.loads(f.readline())["event"] == "a"


def test_maintenance_is_bounded_per_run(tmp_path):
    s = LayeredStore(tmp_path)
    for d in range(5):
        s.append("audit", {"event": "a"}, at=T0 + timedelta(days=d))
    rep = apply_retention(s, _policy(), today=(T0 + timedelta(days=10)).date(), max_files=2)
    assert len(rep.compressed) == 2 and len(rep.skipped) == 3


def test_storage_report_levels(tmp_path):
    s = LayeredStore(tmp_path)
    s.append("audit", {"event": "x" * 500}, at=T0)
    policy = RetentionPolicy.from_dict({"layers": {n: {"compress_after_days": None, "delete_after_days": None}
                                                   for n in LAYERS}, "warn_total_bytes": 100,
                                        "critical_total_bytes": 10**9})
    rep = storage_report(s, policy, min_free_bytes=0)
    assert rep.level == "warning" and rep.bytes_by_layer["audit"] > 100


def _trade(i, sec, side="buy", qty=1.0, price=100.0):
    at = T0 + timedelta(seconds=sec)
    return TradeTick("BTCUSDT", price, qty, side, i, at, at, "test")


def test_minute_trade_aggregation_emits_on_next_minute_and_counts_late():
    agg = MinuteTradeAggregator(large_trade_quantity={"BTCUSDT": 5.0})
    assert agg.add(_trade(1, 1)) == []
    agg.add(_trade(2, 30, "sell", 6.0, 101.0))
    out = agg.add(_trade(3, 61))
    assert len(out) == 1
    s = out[0]
    assert s.trade_count == 2 and s.buy_volume == 1.0 and s.sell_volume == 6.0 and s.large_trade_count == 1
    assert agg.add(_trade(4, 10)) == [] and agg.late_trades == 1
    assert len(agg.flush_before(T0 + timedelta(minutes=2))) == 1 and agg.open_minutes() == 0


def test_minute_book_aggregation_keeps_last_snapshot_and_liquidity_change():
    agg = MinuteBookAggregator()

    def book(at, q):
        return OrderBookSnapshot("BTCUSDT", at, tuple(OrderBookLevel(100 - i, q) for i in range(10)),
                                 tuple(OrderBookLevel(101 + i, q) for i in range(10)))

    agg.add("BTCUSDT", T0, book_row(book(T0, 1.0)))
    first = agg.add("BTCUSDT", T0 + timedelta(seconds=70), book_row(book(T0, 2.0)))
    assert first["updates"] == 1 and first["liquidity_change_5"] is None
    second = agg.flush_before(T0 + timedelta(minutes=5))[0]
    assert second["liquidity_change_5"] == pytest.approx(1.0)
    assert first["top5_imbalance"] == pytest.approx(0.0)


def test_dataset_targets_are_future_only_and_locked_windows_excluded():
    candles = make_candles(400, timeframe=Timeframe.MINUTE_1, market="BTCUSDT", start=T0)
    rep = build_dataset({"BTCUSDT": candles}, locked=(), horizons={"5m": timedelta(minutes=5)}, warmup=50,
                        feature_fn=lambda h: {"close": h[-1].close})
    row = rep.rows[0]
    i = 49
    assert row["as_of"] == candles[i].close_time.isoformat()
    import math
    assert row["future_return_5m"] == pytest.approx(math.log(candles[i + 5].close / candles[i].close))
    assert rep.excluded["incomplete_target_window"] == 5
    lock = LockedWindow("L", "BTCUSDT", T0 + timedelta(minutes=100), T0 + timedelta(minutes=150), ("x",), "t")
    rep2 = build_dataset({"BTCUSDT": candles}, locked=(lock,), horizons={"5m": timedelta(minutes=5)}, warmup=50,
                         feature_fn=lambda h: {"close": h[-1].close})
    assert rep2.excluded["locked_window"] > 0
    for r in rep2.rows:
        t = datetime.fromisoformat(r["as_of"])
        assert not (t - timedelta(minutes=1) < lock.end and t + timedelta(minutes=5) > lock.start)


def test_dataset_rejects_feature_computed_after_row_time_and_reports_survivorship():
    a = make_candles(120, timeframe=Timeframe.MINUTE_1, market="A", start=T0)
    b = make_candles(80, timeframe=Timeframe.MINUTE_1, market="B", start=T0)
    leaky = lambda h: {"as_of": (h[-1].close_time + timedelta(minutes=1)).isoformat()}  # noqa: E731
    rep = build_dataset({"A": a, "B": b}, locked=(), horizons={"1m": timedelta(minutes=1)}, warmup=10,
                        feature_fn=leaky)
    assert rep.rows == [] and rep.excluded["feature_after_row_time"] > 0
    assert rep.symbols_ending_early == ["B"]


def test_notifier_redacts_secrets_and_never_raises():
    env = {"BINANCE_API_SECRET": "supersecretvalue123", "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/abc"}
    sent = []
    n = Notifier(mode="paper", sink=sent.append, env=env)
    n.notify(Severity.CRITICAL, "err", "failed with supersecretvalue123 and api_key=ABCDEF at "
             "https://discord.com/api/webhooks/1/abc", at=T0)
    assert "supersecretvalue123" not in sent[0] and "ABCDEF" not in sent[0] and "webhooks/1/abc" not in sent[0]
    assert "[CRITICAL] [PAPER]" in sent[0]

    def boom(_):
        raise OSError("network down")

    bad = Notifier(mode="paper", sink=boom, env={})
    assert bad.notify(Severity.WARNING, "x", "y", at=T0) is False and len(bad.failures) == 1


def test_notifier_rate_limits_repeats_but_not_critical():
    sent = []
    n = Notifier(mode="paper", sink=sent.append, env={}, min_severity=Severity.TRADE)
    assert not n.notify(Severity.INFO, "a", "b", at=T0)
    n.notify(Severity.WARNING, "stale", "b", at=T0)
    n.notify(Severity.WARNING, "stale", "b", at=T0 + timedelta(minutes=1))
    n.notify(Severity.CRITICAL, "kill", "b", at=T0)
    n.notify(Severity.CRITICAL, "kill", "b", at=T0 + timedelta(seconds=1))
    assert len(sent) == 3 and n.suppressed == 1
    assert redact("secret=abc", {}) == "<redacted>"
