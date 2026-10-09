"""End-to-end dry run of scripts/run_validation.py on synthetic candles,
so a real-data run cannot die after the TEST was used but before the
window is locked and the report printed."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone

from cointrader.backtest.event_engine import FuturesTerms
from cointrader.data.models import Timeframe
from tests.helpers import make_candles

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]


def _load_script(name="run_validation"):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _screened(tmp_path, market, timeframe, ids, start, end):
    """What a prior run_screening.py leaves behind (ADR-0051): ledger rows and the reserved TEST."""
    from cointrader.validation.screening import ReservedWindow, ScreeningLedger, reserve
    from cointrader.validation.walk_forward import build_chronological_split
    split = build_chronological_split(datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
                                      datetime.fromisoformat(end).replace(tzinfo=timezone.utc),
                                      align_to=Timeframe(timeframe).delta)
    ScreeningLedger(tmp_path / "screening.jsonl").append(
        {"market": market, "timeframe": timeframe, "candidates": [{"strategy_id": i} for i in ids]})
    reserve(ReservedWindow(market, split.test_start, split.test_end, "test"), tmp_path / "reserved.json")
    return ["--screening", str(tmp_path / "screening.jsonl"), "--reserved-path", str(tmp_path / "reserved.json")]


def test_run_validation_end_to_end_locks_test_and_stops_at_oos(tmp_path, monkeypatch, capsys):
    mod = _load_script()
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    candles = make_candles(24 * 900, timeframe=Timeframe.HOUR_1, market="ETHUSDT", start=start, vol=0.006)
    monkeypatch.setattr(mod, "load_candles", lambda s, tf, a, b: ([c for c in candles if a <= c.open_time < b], []))
    monkeypatch.setattr(mod, "load_futures_terms",
                        lambda s, a, b: (FuturesTerms(assume_no_funding=True), ["test: no funding"]))
    locked = tmp_path / "locked.json"
    locked.write_text("[]", encoding="utf-8")
    ids = ["swing_trend_ema_atr_20_50_v1", "swing_bollinger_reversion_20_2_v1"]
    paths = ["--screening", str(tmp_path / "screening.jsonl"), "--reserved-path", str(tmp_path / "reserved.json"),
             "--log", str(tmp_path / "prereg.jsonl"), "--locked-path", str(locked)]
    # screening first: walk-forward only, reserves the TEST, records the run
    scr = _load_script("run_screening")
    seen = []
    monkeypatch.setattr(scr, "load_candles", lambda s, tf, a, b: (seen.append(b) or
                                                                 [c for c in candles if a <= c.open_time < b], []))
    monkeypatch.setattr(scr, "load_futures_terms", lambda s, a, b: (FuturesTerms(assume_no_funding=True), []))
    monkeypatch.setattr(sys, "argv", ["run_screening.py", "--family", "swing", "--symbol", "ETHUSDT",
                                      "--timeframe", "1h", "--start", "2020-01-01", "--end", "2022-06-01",
                                      "--strategies", *ids, "--by", "RESEARCH_LOOP", *paths])
    assert scr.main() == 0
    reserved = json.loads((tmp_path / "reserved.json").read_text(encoding="utf-8"))
    assert len(reserved) == 1 and seen[0].isoformat() == reserved[0]["start"]  # no TEST bar loaded
    assert json.loads(locked.read_text(encoding="utf-8")) == []
    out = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "run_validation.py", "--hypothesis-id", "H-9001", "--family", "swing", "--symbol", "ETHUSDT",
        "--timeframe", "1h", "--start", "2020-01-01", "--end", "2022-06-01",
        "--strategies", "swing_trend_ema_atr_20_50_v1", "swing_bollinger_reversion_20_2_v1",
        "--statement", "synthetic dry run", "--rationale", "synthetic end-to-end dry run of the validation script",
        "--registered-by", "test", "--log", str(tmp_path / "prereg.jsonl"), "--locked-path", str(locked),
        "--ledger", str(tmp_path / "ledger.jsonl"), "--out", str(out), *paths[:4]])
    assert mod.main() == 0
    assert json.loads((tmp_path / "reserved.json").read_text(encoding="utf-8")) == []  # now a locked window
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["trials_deflated_against"] >= 4  # 2 registered + 2 screened
    windows = json.loads(locked.read_text(encoding="utf-8"))
    assert len(windows) == 1 and windows[0]["market"] == "ETHUSDT" and windows[0]["name"] == "TEST-9001"
    assert report["test_window_locked"]["start"] == windows[0]["start"]
    assert report["label"].startswith("BACKTEST")
    rows = [json.loads(l) for l in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] \
        if (tmp_path / "ledger.jsonl").exists() else []
    assert all(r["to_status"] not in ("APPROVED", "DEPLOYED") for r in rows)
    # re-running the same hypothesis now hits the lock
    monkeypatch.setattr(sys, "argv", sys.argv)
    import pytest
    from cointrader.validation.locked_windows import LockedWindowViolation
    with pytest.raises((LockedWindowViolation, ValueError)):
        mod.main()


def test_run_validation_daily_long_holds_with_jittered_funding(tmp_path, monkeypatch):
    """H-0015 shape: daily bars, multi-day holds crossing many settlements
    whose archive stamps sit a few ms off the 8h grid."""
    from datetime import timedelta
    mod = _load_script()
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    from cointrader.data.models import Candle
    # ETH-like daily volatility (ATR ~6% of price), above the unscaled 1h volatility-kill limit
    raw = make_candles(1436, timeframe=Timeframe.DAY_1, market="ETHUSDT", start=start, vol=0.08, drift=0.001)
    candles = [Candle(c.market, c.timeframe, c.open_time, c.open / 20_000, c.high / 20_000, c.low / 20_000,
                      c.close / 20_000, c.volume * 1e4, c.source, c.received_at) for c in raw]  # ETH-like price
    funding = {start + timedelta(hours=8 * k, milliseconds=k % 7): 0.0001 for k in range(1436 * 3 + 3)}
    monkeypatch.setattr(mod, "load_candles", lambda s, tf, a, b: ([c for c in candles if a <= c.open_time < b], []))
    monkeypatch.setattr(mod, "load_futures_terms", lambda s, a, b: (FuturesTerms(funding=funding), []))
    locked = tmp_path / "locked.json"
    locked.write_text("[]", encoding="utf-8")
    out = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "run_validation.py", "--hypothesis-id", "H-9002", "--family", "swing", "--symbol", "ETHUSDT",
        "--timeframe", "1d", "--start", "2020-08-01", "--end", "2023-12-07",
        "--strategies", "swing_daily_sma_trend_100_v1", "swing_daily_donchian_55_20_v1",
        "swing_daily_trend_pullback_100_20_v1",
        "--statement", "synthetic daily dry run", "--rationale", "synthetic end-to-end dry run of daily candidates",
        "--registered-by", "test", "--log", str(tmp_path / "prereg.jsonl"), "--locked-path", str(locked),
        "--ledger", str(tmp_path / "ledger.jsonl"), "--out", str(out),
        *_screened(tmp_path, "ETHUSDT", "1d", ["swing_daily_sma_trend_100_v1", "swing_daily_donchian_55_20_v1",
                                                  "swing_daily_trend_pullback_100_20_v1"],
                   "2020-08-01", "2023-12-07")])
    assert mod.main() == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["fold_count"] >= 16
    trades = [c["test_summary"]["trade_count"] for c in report["candidates"]]
    assert sum(trades) > 0
    assert any(c["test_summary"]["cost_breakdown"]["funding"] != 0 for c in report["candidates"])
    assert not any(k.startswith("volatility_kill") for c in report["candidates"]
                   for k in c["test_summary"]["rejected_entries"])
    # every fold had full indicator history, so no candidate sits out the early folds
    assert all(sum(c["fold_trade_counts"][:3]) > 0 or any(c["fold_returns"][:3]) for c in report["candidates"])


def test_warmup_bars_inside_a_locked_window_are_refused(tmp_path, monkeypatch):
    from datetime import timedelta
    import pytest
    from cointrader.validation.locked_windows import LockedWindowViolation
    mod = _load_script()
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    candles = make_candles(1436, timeframe=Timeframe.DAY_1, market="ETHUSDT", start=start, vol=0.03)
    monkeypatch.setattr(mod, "load_candles", lambda s, tf, a, b: ([c for c in candles if a <= c.open_time < b], []))
    monkeypatch.setattr(mod, "load_futures_terms", lambda s, a, b: (FuturesTerms(assume_no_funding=True), []))
    locked = tmp_path / "locked.json"
    locked.write_text(json.dumps([{"name": "TEST-1", "market": "ETHUSDT", "start": "2020-03-01T00:00:00+00:00",
                                   "end": "2020-04-01T00:00:00+00:00", "observed_by": ["x"], "note": "t"}]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "run_validation.py", "--hypothesis-id", "H-9003", "--family", "swing", "--symbol", "ETHUSDT",
        "--timeframe", "1d", "--start", "2020-08-01", "--end", "2023-12-07",
        "--strategies", "swing_daily_sma_trend_100_v1", "swing_daily_donchian_55_20_v1",
        "--statement", "synthetic", "--rationale", "warm-up bars must not reach into a locked TEST window",
        "--registered-by", "test", "--log", str(tmp_path / "prereg.jsonl"), "--locked-path", str(locked),
        "--ledger", str(tmp_path / "ledger.jsonl"),
        *_screened(tmp_path, "ETHUSDT", "1d", ["swing_daily_sma_trend_100_v1", "swing_daily_donchian_55_20_v1"],
                   "2020-08-01", "2023-12-07")])
    with pytest.raises(LockedWindowViolation):
        mod.main()
