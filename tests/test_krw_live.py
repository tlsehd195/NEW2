import json
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.accounting.krw_ledger import ExitCostConfig, KrwTaxConfig
from cointrader.accounting.krw_live import (UpbitTicker, live_snapshot, one_line, refresh_snapshot, write_snapshot)

NOW = datetime(2027, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
EXITS = ExitCostConfig(1.0, 0.0005, 1000.0)


def ticker_body(price=1450.0, age_s=2.0, market="KRW-USDT"):
    ts = int((NOW - timedelta(seconds=age_s)).timestamp() * 1000)
    return json.dumps([{"market": market, "trade_price": price, "trade_timestamp": ts}]).encode()


def ticker(body):
    return UpbitTicker(transport=lambda url: body, now=lambda: NOW)


def test_ticker_returns_fresh_rate_and_refuses_bad_ones():
    q = ticker(ticker_body()).rate()
    assert q.rate == 1450.0 and q.fetched_at == NOW
    with pytest.raises(ValueError, match="stale"):
        ticker(ticker_body(age_s=900)).rate()
    with pytest.raises(ValueError, match="implausible"):
        ticker(ticker_body(price=14.5)).rate()
    with pytest.raises(ValueError, match="unexpected"):
        ticker(ticker_body(market="KRW-BTC")).rate()
    with pytest.raises(ValueError):
        ticker(b"[]").rate()

    def boom(url):
        raise OSError("blocked")
    with pytest.raises(OSError):
        UpbitTicker(transport=boom, now=lambda: NOW).rate()


def snap(equity=10_300.0, rate=1450.0, **kw):
    q = ticker(ticker_body(price=rate)).rate()
    return live_snapshot(equity_usdt=equity, start_usdt=10_000.0, krw_gross=14_000_000.0, fee_krw=7_000.0, quote=q,
                         tax=KrwTaxConfig(), exit_costs=kw.get("exits", EXITS))


def test_snapshot_split_adds_up_and_reacts_to_rate():
    s = snap()
    p = s["pnl_krw"]
    assert p["total"] == pytest.approx(10_300 * 1450 - 14_007_000)
    assert p["trading"] + p["fx"] + p["domestic_fee"] == pytest.approx(p["total"])
    assert p["trading"] == pytest.approx(300 * 1450) and p["fx"] == pytest.approx(10_000 * 50)
    lower = snap(rate=1350.0)
    assert lower["pnl_krw"]["fx"] == pytest.approx(10_000 * -50)   # FX moved with the rate...
    assert lower["pnl_krw"]["total"] < s["pnl_krw"]["total"]        # ...and so did the total, immediately


def test_cash_out_and_tax_lines():
    assert snap()["cash_out"]["estimated_tax_krw"] == 0.0  # +928,000 KRW is under the 2.5M deduction
    s = snap(equity=11_500.0)  # +2,668,000 KRW
    exit_cost = 1.0 * 1450 + 11_499 * 1450 * 0.0005 + 1000
    assert s["cash_out"]["exit_cost_krw"] == pytest.approx(exit_cost)
    assert s["cash_out"]["estimated_tax_krw"] == pytest.approx((2_668_000 - 2_500_000) * 0.22)
    assert s["cash_out"]["net_after_cash_out_and_tax_krw"] == pytest.approx(
        s["pnl_krw"]["total"] - exit_cost - s["cash_out"]["estimated_tax_krw"])
    assert not s["tax_verified"] and "세무사" in s["note"]
    assert snap(exits=ExitCostConfig())["cash_out"]["net_after_cash_out_and_tax_krw"] is None
    assert "회수·세금 후" in one_line(s)
    with pytest.raises(ValueError):
        snap(equity=0.0)


def test_refresh_writes_atomically_and_failure_keeps_old_file(tmp_path):
    flows, out = tmp_path / "flows.jsonl", tmp_path / "live.json"
    kw = dict(ticker=ticker(ticker_body()), equity_usdt_fn=lambda: 10_300.0, flows_path=flows, out_path=out,
              tax=KrwTaxConfig(), exit_costs=EXITS)
    with pytest.raises(ValueError, match="no paper start purchase"):
        refresh_snapshot(**kw)
    assert not out.exists()
    flows.write_text(json.dumps({"type": "usdt_purchase", "at": NOW.isoformat(), "mode": "paper",
                                 "krw_gross": 14_000_000.0, "usdt": 10_000.0, "fee_krw": 7_000.0}) + "\n")
    first = refresh_snapshot(**kw)
    assert json.loads(out.read_text())["pnl_krw"]["total"] == pytest.approx(first["pnl_krw"]["total"])
    assert not list(tmp_path.glob("*.tmp"))
    # transport dies: the previous file is untouched, the error propagates
    bad = {**kw, "ticker": UpbitTicker(transport=lambda u: (_ for _ in ()).throw(OSError("x")), now=lambda: NOW)}
    before = out.read_text()
    with pytest.raises(OSError):
        refresh_snapshot(**bad)
    assert out.read_text() == before
    # a live-mode refresh does not pick up the paper start purchase
    with pytest.raises(ValueError, match="no live start purchase"):
        refresh_snapshot(**kw, mode="live")


def test_write_snapshot_replaces_existing(tmp_path):
    p = tmp_path / "s.json"
    write_snapshot(p, {"a": 1})
    write_snapshot(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 2}


def test_watch_script_once_reads_file(tmp_path, capsys, monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path
    f = tmp_path / "live.json"
    s = snap()
    s["as_of"] = datetime.now(timezone.utc).isoformat()
    f.write_text(json.dumps(s))
    spec = importlib.util.spec_from_file_location(
        "watch_krw_pnl", Path(__file__).resolve().parents[1] / "scripts" / "watch_krw_pnl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(sys, "argv", ["watch", "--file", str(f), "--once"])
    assert mod.main() == 0
    assert "환율 1,450.0" in capsys.readouterr().out
