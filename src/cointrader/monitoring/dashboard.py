"""Read-only paper-trading dashboard data (ADR-0048).

Reads only what the paper process already persists: `paper_state.json` and the layered journal
(`normalized` candles + 1m book stats, `execution` fills, `decision`, `outcome`). It never imports
trading code and has no write path, so it cannot affect trading.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cointrader.journal.store import LayeredStore

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
                                       "low": r["l"], "close": r["c"]}
        elif r.get("kind") == "book_stats_1m" and r.get("mid_price"):
            last_book = r
    series = [candles[k] for k in sorted(candles)][-bars:]
    last_price = last_book["mid_price"] if last_book else (series[-1]["close"] if series else None)
    last_price_time = last_book["interval_start"] if last_book else None

    fills = [{"time": _epoch(r["recorded_at"]), "side": r["side"], "price": r["price"], "quantity": r["quantity"]}
             for r in store.read("execution", start=since) if r.get("event") == "fill" and r.get("symbol") == symbol]
    decisions = [{"time": r["bar_open_time"], "action": r["action"], "reason": r["reason"]}
                 for r in store.read("decision", start=since) if r.get("symbol") == symbol]
    trades = [{"exit_time": r.get("exit_time"), "direction": r.get("direction"), "net_pnl": r["net_pnl"],
               "exit_reason": r.get("exit_reason")}
              for r in store.read("outcome", start=since) if r.get("symbol") == symbol]

    state = _load_state(state_dir)
    out = {"symbol": symbol, "as_of": now.isoformat(), "candles": series, "last_price": last_price,
           "last_price_time": last_price_time, "fills": fills[-40:], "decisions": decisions[-12:],
           "closed_trades": trades[-10:], "account": None, "position": None}
    if state:
        out["position"] = _position(state, symbol, last_price)
        out["account"] = {"balance": state["broker"]["balance"], "saved_at": state["saved_at"],
                          "open_positions": len(state.get("open_trades") or {})}
    return out
