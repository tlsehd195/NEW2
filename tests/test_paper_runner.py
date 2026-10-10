from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone

from cointrader.data.models import Candle, Timeframe
from cointrader.journal.store import LayeredStore
from cointrader.paper.runner import ReplayFileSource, bootstrap, build_trader, live_stream_urls, run
from cointrader.settings import load_paper

T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
SYM = "BTCUSDT"


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


class History:
    def fetch(self, symbol, timeframe, start, end):
        out, t, p = [], start, 60_000.0
        while t + timeframe.delta <= end:
            p *= math.exp(0.0005 * math.sin(t.timestamp() / 600))
            out.append(Candle(symbol, timeframe, t, p, p * 1.001, p * 0.999, p, 10.0, "test_rest", t + timeframe.delta))
            t += timeframe.delta
        return out


def _messages(minutes: int):
    msgs, uid, aid, p = [], 1, 1, 60_000.0
    for m in range(minutes):
        start = T0 + timedelta(minutes=m)
        o = p
        for s in (5, 20, 35, 50):
            p *= math.exp(0.0004 * math.sin(m + s))
            at = start + timedelta(seconds=s)
            uid += 1
            msgs.append((at, {"stream": f"{SYM.lower()}@bookTicker", "data": {
                "e": "bookTicker", "u": uid, "s": SYM, "b": f"{p - 0.5:.1f}", "B": "3", "a": f"{p + 0.5:.1f}",
                "A": "3", "T": _ms(at), "E": _ms(at)}}))
            aid += 1
            msgs.append((at + timedelta(milliseconds=5), {"stream": f"{SYM.lower()}@aggTrade", "data": {
                "e": "aggTrade", "s": SYM, "a": aid, "p": f"{p:.1f}", "q": "0.01", "m": bool(s % 2), "T": _ms(at),
                "E": _ms(at)}}))
        close_at = start + timedelta(minutes=1)
        msgs.append((close_at + timedelta(milliseconds=50), {"stream": f"{SYM.lower()}@kline_1m", "data": {
            "e": "kline", "E": _ms(close_at), "s": SYM, "k": {
                "t": _ms(start), "T": _ms(close_at) - 1, "s": SYM, "i": "1m", "o": f"{o:.1f}",
                "h": f"{max(o, p) * 1.0005:.1f}", "l": f"{min(o, p) * 0.9995:.1f}", "c": f"{p:.1f}", "v": "12",
                "x": True}}}))
    return msgs


def test_replay_run_end_to_end(tmp_path):
    cfg = dict(load_paper())
    cfg.update({"symbols": [SYM], "strategies": ["scalp_vwap_reversion_30_2_v1", "scalp_short_mean_reversion_20_3_v1"],
                "state_dir": "state", "data_root": "data", "kill_switch_path": "state/kill.jsonl"})
    trader = build_trader(cfg, root=tmp_path)
    urls = live_stream_urls(trader)
    assert len(urls) == 2 and all("fstream.binance.com" in u for u in urls)
    assert "btcusdt@bookTicker" in urls[0] and "btcusdt@kline_1m" in urls[1]
    assert bootstrap(trader, History(), T0) > 0

    msgs = _messages(30)
    path = tmp_path / "replay.jsonl"
    path.write_text("\n".join(json.dumps(d) for _, d in msgs) + "\n", encoding="utf-8")
    current = {"t": T0}
    times = iter(t for t, _ in msgs)

    class ClockedReplay(ReplayFileSource):
        """Replay whose clock is the recorded receipt time of each message."""

        def messages(self):
            for line in super().messages():
                current["t"] = next(times)
                yield line

    n = run(trader, ClockedReplay(path), history=History(), now=lambda: current["t"])
    assert n >= len(msgs)
    assert trader.counters["decisions"] == 30 * 2
    assert (tmp_path / "state" / "paper_state.json").exists()
    store = LayeredStore(tmp_path / "data")
    decisions = list(store.read("decision"))
    assert {d["mode"] for d in decisions} == {"paper"}
    assert any(r["kind"] == "trade_stats_1m" for r in store.read("normalized"))
    status = trader.status()
    assert status["mode"] == "PAPER" and status["ready"] is True
    assert Timeframe.MINUTE_1


def test_repo_configs_default_to_paper_and_load_strictly(tmp_path):
    import pytest

    from cointrader.settings import load_markets, load_risk, risk_from_dict
    cfg = load_paper()
    assert cfg["environment"] == "paper" and cfg["live_trading_enabled"] is False
    filters, large, verified = load_markets()
    assert verified is False  # placeholders until refreshed from exchangeInfo
    assert set(cfg["symbols"]) <= set(filters)
    risk = load_risk()
    assert risk.max_leverage <= 20 and risk.risk_per_trade <= 0.02  # ADR-0061: owner chose 2% (ADR-0059 cap 20x)
    with pytest.raises(ValueError):
        risk_from_dict({"risk_per_trade": 0.01})
    bad = tmp_path / "paper.json"
    bad.write_text(json.dumps({**cfg, "environment": "live"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_paper(bad)


def test_default_paper_strategies_get_their_full_warmup(tmp_path):
    """The 15m day-trade votes need ~1,190 closed bars. A 600-bar buffer kept every decision at
    "warmup" forever, so the buffer and the REST bootstrap must cover the longest warm-up."""
    cfg = dict(load_paper())
    cfg.update({"state_dir": "state", "data_root": "data", "kill_switch_path": "state/kill.jsonl"})
    trader = build_trader(cfg, root=tmp_path)
    need = max(s.warmup for s, _ in trader.strategies.values())
    assert need > 600 and trader.cfg.max_candles >= need
    now = T0 + timedelta(days=20)
    bootstrap(trader, History(), now)
    for sym in trader.symbols:
        assert len(trader._bars(sym, "15m")) >= need
