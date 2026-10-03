import json
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.accounting.krw_rates import collect, ensure_paper_start, load_series, read_points
from cointrader.data.upbit_rest import UpbitRestCandles

NOW = datetime(2026, 10, 3, 3, 0, 30, tzinfo=timezone.utc)


def row(open_time: datetime, close: float, market="KRW-USDT"):
    return {"market": market, "candle_date_time_utc": open_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "opening_price": close, "high_price": close, "low_price": close, "trade_price": close,
            "candle_acc_trade_volume": 1000.0}


class Fake:
    """Replays a recorded Upbit response once (newest first), then empty pages."""

    def __init__(self, rows):
        self.rows, self.urls = list(rows), []

    def __call__(self, url):
        self.urls.append(url)
        body, self.rows = json.dumps(self.rows).encode(), []
        return body, {}


def client(rows):
    f = Fake(rows)
    return UpbitRestCandles(transport=f, now=lambda: NOW), f


def candles_before_now(n, base=1400.0):
    # last fully closed 15m candle opened at 02:45 (closed 03:00 <= NOW)
    opens = [datetime(2026, 10, 3, 2, 45, tzinfo=timezone.utc) - timedelta(minutes=15 * i) for i in range(n)]
    return [row(t, base + i) for i, t in enumerate(opens)]  # newest first, like Upbit


def test_collect_writes_close_time_and_is_incremental(tmp_path):
    path = tmp_path / "krw_usdt.csv"
    c, f = client(candles_before_now(4))
    assert collect(c, path, now=NOW) == 4
    pts = read_points(path)
    assert pts[-1] == (datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc), 1400.0)  # close time, not open time
    assert "KRW-USDT" in f.urls[0] and "candles/minutes/15" in f.urls[0]
    # same data again adds nothing
    c2, _ = client(candles_before_now(4))
    assert collect(c2, path, now=NOW) == 0 and len(read_points(path)) == 4


def test_unclosed_candle_is_never_stored(tmp_path):
    rows = [row(datetime(2026, 10, 3, 3, 0, tzinfo=timezone.utc), 1500.0)] + candles_before_now(1)
    c, _ = client(rows)
    assert collect(c, tmp_path / "r.csv", now=NOW) == 1  # the 03:00 candle closes at 03:15


@pytest.mark.parametrize("bad", [0.0, 12.0, 90_000.0])
def test_implausible_rate_raises_and_writes_nothing(tmp_path, bad):
    rows = candles_before_now(3)
    rows[1] = row(datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc), bad)
    c, _ = client(rows)
    with pytest.raises(ValueError, match="implausible"):
        collect(c, tmp_path / "r.csv", now=NOW)
    assert not (tmp_path / "r.csv").exists()


def test_network_failure_propagates_and_writes_nothing(tmp_path):
    def boom(url):
        raise OSError("blocked")
    with pytest.raises(OSError):
        collect(UpbitRestCandles(transport=boom, now=lambda: NOW), tmp_path / "r.csv", now=NOW)
    assert not (tmp_path / "r.csv").exists()


def test_series_and_paper_start_once(tmp_path):
    c, _ = client(candles_before_now(4))
    collect(c, tmp_path / "r.csv", now=NOW)
    s = load_series(tmp_path / "r.csv", timedelta(hours=1))
    flows = tmp_path / "flows.jsonl"
    assert ensure_paper_start(flows, s, usdt=10_000, now=NOW, fee_rate=0.0005)
    assert not ensure_paper_start(flows, s, usdt=10_000, now=NOW, fee_rate=0.0005)  # only once
    d = json.loads(flows.read_text())
    assert d["type"] == "usdt_purchase" and d["mode"] == "paper" and d["krw_gross"] == pytest.approx(10_000 * 1400)
    assert d["fee_krw"] == pytest.approx(d["krw_gross"] * 0.0005)
    with pytest.raises(ValueError):
        load_series(tmp_path / "missing.csv", timedelta(hours=1))


def test_end_to_end_report_from_paper_files(tmp_path):
    from cointrader.accounting.krw_report import build_krw_report, one_line
    c, _ = client(candles_before_now(8))
    collect(c, tmp_path / "r.csv", now=NOW)
    s = load_series(tmp_path / "r.csv", timedelta(hours=1))
    first = s.points[0][0]
    flows = tmp_path / "flows.jsonl"
    ensure_paper_start(flows, s, usdt=1000, now=first, fee_rate=0.0005)
    rep, _ = build_krw_report(mode="paper", flows_path=flows, rates_path=tmp_path / "r.csv", data_root=tmp_path / "d")
    assert rep.usdt_held == pytest.approx(1000) and rep.trade_count == 0
    assert rep.components["domestic_fees"] < 0 and rep.fx_unrealized is not None
    assert "paper" in one_line(rep)
    with pytest.raises(ValueError, match="no flows file"):
        build_krw_report(mode="paper", flows_path=tmp_path / "nope", rates_path=tmp_path / "r.csv",
                         data_root=tmp_path / "d")


def test_ws_quality_summary_flags_malformed_and_missing_decisions(tmp_path):
    import importlib.util
    from pathlib import Path

    from cointrader.journal.store import LayeredStore
    spec = importlib.util.spec_from_file_location(
        "check_ws_quality", Path(__file__).resolve().parents[1] / "scripts" / "check_ws_quality.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    store = LayeredStore(tmp_path)
    store.append("quality", {"kind": "malformed_message", "symbol": "BTCUSDT", "detail": "kline: KeyError: 'k'", "blocks_trading": True}, at=NOW)
    store.append("quality", {"kind": "unknown_event", "symbol": "BTCUSDT", "detail": "x", "blocks_trading": False}, at=NOW)
    out = mod.summarize(store, NOW - timedelta(hours=1))
    assert out["malformed"] == 1 and out["decisions"] == 0 and "KeyError" in out["malformed_samples"][0]
    assert out["quality_events"][("unknown_event", "BTCUSDT")] == 1
