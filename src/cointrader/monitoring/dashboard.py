"""Read-only paper-trading dashboard data (ADR-0048).

Reads only what the paper process already persists: `paper_state.json` and the layered journal
(`normalized` candles + 1m book stats, `execution` fills, `decision`, `outcome`). It never imports
trading code and has no write path, so it cannot affect trading.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from cointrader.features import indicators as ind
from cointrader.journal.store import LayeredStore
from cointrader.strategies.daytrade import DayTradeVote

EXTRA_BARS = 60  # history before the first drawn bar, so the indicator lines start at the left edge

def _epoch(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())


def _load_state(state_dir: Path) -> Optional[dict]:
    path = Path(state_dir) / "paper_state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _position(state: dict, symbol: str, last_price: Optional[float]) -> Optional[dict]:
    t = (state.get("open_trades") or {}).get(symbol)
    if not t:
        return None
    qty = (t.get("entry_qty") or 0.0) - (t.get("exit_qty") or 0.0)
    entry = t["entry_notional"] / t["entry_qty"] if t.get("entry_qty") else None
    pos = {"direction": "long" if t["direction"] > 0 else "short", "state": t["state"], "quantity": qty,
           "entry_price": entry, "stop_price": t.get("stop_price"), "strategy": t["strategy_id"],
           "entry_time": t.get("entry_time"), "unrealized_pnl": None}
    if entry is not None and last_price is not None:
        pos["unrealized_pnl"] = t["direction"] * qty * (last_price - entry)
    return pos


def _overlays(full: list[dict], bars: int) -> tuple[dict, dict]:
    """Indicator lines for the last `bars` candles, and the latest indicator values. Uses the same
    indicator functions as the strategy (features/indicators.py), so the numbers match what it sees."""
    closes = [c["close"] for c in full]
    highlow = [SimpleNamespace(high=c["high"], low=c["low"]) for c in full]
    keys = ("ema20", "bb_upper", "bb_lower", "don_high", "don_low", "rsi14", "roc14")
    out: dict[str, list] = {k: [] for k in keys}
    for i in range(max(0, len(full) - bars), len(full)):
        cs = closes[: i + 1]
        bb, don = ind.bollinger(cs, 20), ind.donchian(highlow[: i + 1], 20)
        out["ema20"].append(ind.ema(cs, 20))
        out["bb_upper"].append(bb["upper"] if bb else None)
        out["bb_lower"].append(bb["lower"] if bb else None)
        out["don_high"].append(don[0] if don else None)
        out["don_low"].append(don[1] if don else None)
        out["rsi14"].append(ind.rsi(cs, 14))
        out["roc14"].append(ind.roc(cs, 14))
    # On-balance volume: running total of volume, signed by the close-to-close direction (level is arbitrary).
    obv, acc = [], 0.0
    for i, c in enumerate(full):
        if i:
            acc += (c["close"] > full[i - 1]["close"]) * c["volume"] - (c["close"] < full[i - 1]["close"]) * c["volume"]
        obv.append(acc)
    out["obv"] = obv[len(full) - bars:] if bars else []
    latest: dict = {}
    if full:
        bb, don = ind.bollinger(closes, 20), ind.donchian(highlow, 20)
        latest = {"rsi14": ind.rsi(closes, 14), "ema20_gap": ind.price_vs_ema(closes, 20),
                  "bollinger_z": bb["zscore"] if bb else None, "roc14": ind.roc(closes, 14),
                  "donchian_pos": ((closes[-1] - don[1]) / (don[0] - don[1]) if don and don[0] != don[1] else None)}
    return out, latest


def _votes(decisions: list[dict]) -> list[dict]:
    """Latest vote per strategy: each indicator's probability, the combined P(long) and what was decided."""
    last: dict[str, dict] = {}
    for r in decisions:
        feats = (r.get("signal") or {}).get("features") or {}
        last[r["strategy_id"]] = {
            "strategy": r["strategy_id"], "bar_time": r["bar_open_time"], "action": r["action"], "reason": r["reason"],
            "p_long": feats.get("p_long"), "agree_long": feats.get("agree_long"), "agree_short": feats.get("agree_short"),
            "vol_ratio": feats.get("vol_ratio"),
            "per_indicator": {k[2:]: v for k, v in feats.items() if k.startswith("p_") and k != "p_long"}}
    return sorted(last.values(), key=lambda v: v["strategy"])


def _rules() -> dict:
    p = DayTradeVote().parameters
    return {k: p.get(k) for k in ("enter_confidence", "exit_confidence", "min_agree", "vol_gate_lo", "vol_gate_hi")}


def read_snapshot(state_dir: Path, data_root: Path, symbol: str, *, bars: int = 200, days: int = 3,
                  now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since: date = (now - timedelta(days=days)).date()
    store = LayeredStore(data_root)

    candles: dict[str, dict] = {}
    last_book: Optional[dict] = None
    for r in store.read("normalized", start=since):
        if r.get("symbol") != symbol:
            continue
        if r.get("kind") == "candle" and r.get("timeframe") == "15m":
            candles[r["open_time"]] = {"time": _epoch(r["open_time"]), "open": r["o"], "high": r["h"],
                                       "low": r["l"], "close": r["c"], "volume": r["v"]}
        elif r.get("kind") == "book_stats_1m" and r.get("mid_price"):
            last_book = r
    full = [candles[k] for k in sorted(candles)][-(bars + EXTRA_BARS):]
    series = full[-bars:]
    overlays, latest = _overlays(full, len(series))
    last_price = last_book["mid_price"] if last_book else (series[-1]["close"] if series else None)
    last_price_time = last_book["interval_start"] if last_book else None

    fills = [{"time": _epoch(r["recorded_at"]), "side": r["side"], "price": r["price"], "quantity": r["quantity"]}
             for r in store.read("execution", start=since) if r.get("event") == "fill" and r.get("symbol") == symbol]
    symbol_decisions = [r for r in store.read("decision", start=since) if r.get("symbol") == symbol]
    decisions = [{"time": r["bar_open_time"], "action": r["action"], "reason": r["reason"]} for r in symbol_decisions]
    trades = [{"exit_time": r.get("exit_time"), "direction": r.get("direction"), "net_pnl": r["net_pnl"],
               "exit_reason": r.get("exit_reason")}
              for r in store.read("outcome", start=since) if r.get("symbol") == symbol]

    state = _load_state(state_dir)
    out = {"symbol": symbol, "as_of": now.isoformat(), "candles": series, "last_price": last_price,
           "last_price_time": last_price_time, "fills": fills[-40:], "decisions": decisions[-12:],
           "closed_trades": trades[-10:], "account": None, "position": None, "overlays": overlays, "latest": latest,
           "votes": _votes(symbol_decisions), "rules": _rules()}
    if state:
        out["position"] = _position(state, symbol, last_price)
        out["account"] = {"balance": state["broker"]["balance"], "saved_at": state["saved_at"],
                          "open_positions": len(state.get("open_trades") or {})}
    return out
