"""Read-only paper-trading dashboard data (ADR-0048).

Reads only what the paper process already persists: `paper_state.json` and the layered journal
(`normalized` candles + 1m book stats, `execution` fills, `decision`, `outcome`). It never imports
trading code and has no write path, so it cannot affect trading.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from cointrader.features import indicators as ind
from cointrader.journal.store import LayeredStore
from cointrader.strategies.daytrade import DayTradeVote

EXTRA_BARS = 60  # history before the first drawn bar, so the indicator lines start at the left edge

# Finished days never change, so parse each one's candles once and reuse them on the 5-second refreshes.
# Today and yesterday are always re-read (the paper process is still appending, and late records land there).
_DAY_CACHE: dict[tuple[str, str], dict[str, dict[str, dict]]] = {}
_DAY_LOCK = threading.Lock()


def _day_candles(store: LayeredStore, day: date) -> dict[str, dict[str, dict]]:
    """{symbol: {open_time: candle}} for the 15m candles recorded on `day`."""
    out: dict[str, dict[str, dict]] = {}
    for r in store.read("normalized", start=day, end=day):
        if r.get("kind") == "candle" and r.get("timeframe") == "15m":
            out.setdefault(r["symbol"], {})[r["open_time"]] = _candle(r)
    return out


def _candle(r: dict) -> dict:
    return {"time": _epoch(r["open_time"]), "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "volume": r["v"]}


def _epoch(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())


def _load_state(state_dir: Path) -> Optional[dict]:
    path = Path(state_dir) / "paper_state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _krw_account(state_dir: Path, balance: float) -> dict:
    """KRW view of the account from `krw_live.json` (ADR-0041, written by the paper trader from the Upbit
    KRW-USDT ticker). Missing/unreadable file -> no KRW fields; `krw_as_of` lets the page show staleness."""
    try:
        snap = json.loads((Path(state_dir) / "krw_live.json").read_text(encoding="utf-8"))
        rate = float(snap["rate_krw_per_usdt"])
        return {"rate_krw": rate, "balance_krw": balance * rate, "equity_krw": snap.get("value_krw"),
                "krw_as_of": snap.get("as_of")}
    except (OSError, ValueError, KeyError, TypeError):
        return {"rate_krw": None, "balance_krw": None, "equity_krw": None, "krw_as_of": None}


def _position(state: dict, symbol: str, last_price: Optional[float], leverage: Optional[int] = None) -> Optional[dict]:
    t = (state.get("open_trades") or {}).get(symbol)
    if not t:
        return None
    qty = (t.get("entry_qty") or 0.0) - (t.get("exit_qty") or 0.0)
    entry = t["entry_notional"] / t["entry_qty"] if t.get("entry_qty") else None
    pos = {"direction": "long" if t["direction"] > 0 else "short", "state": t["state"], "quantity": qty,
           "entry_price": entry, "stop_price": t.get("stop_price"), "strategy": t["strategy_id"],
           "entry_time": t.get("entry_time"), "unrealized_pnl": None,
           # Notional = open quantity at the entry price. Margin is the isolated-margin estimate notional / exchange
           # leverage (configs/margin_policy.json); None when the leverage is not known.
           "notional": qty * entry if entry is not None else None, "leverage": leverage,
           "margin": qty * entry / leverage if entry is not None and leverage else None}
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


def _trade(r: dict) -> dict:
    """One closed trade for the list. `price_return` is entry price -> exit price in the trade's direction
    (before fees, no leverage); `equity_return` is the net result after fees, spread, slippage and funding
    as a fraction of the account at entry. Either is None when the stored row lacks the inputs."""
    entry, exit_, direction = r.get("entry_fill"), r.get("exit_fill"), r.get("direction")
    price_return = direction * (exit_ - entry) / entry if entry and exit_ and direction else None
    return {"entry_time": r.get("entry_time"), "exit_time": r.get("exit_time"), "direction": direction, "net_pnl": r["net_pnl"],
            "exit_reason": r.get("exit_reason"), "entry_price": entry, "exit_price": exit_,
            "price_return": price_return, "equity_return": r.get("return_on_equity")}


def _fill_kind(side: str, purpose: Optional[str]) -> Optional[str]:
    """What a fill did to the position: long_entry / long_exit / short_entry / short_exit (None if the order is unknown).
    A BUY opens a long or closes a short; a SELL opens a short or closes a long. Stop and exit orders are exits."""
    if not purpose:
        return None
    buy = side.lower() == "buy"
    entry = purpose == "entry"
    return f"{'long' if buy == entry else 'short'}_{'entry' if entry else 'exit'}"


def _rules() -> dict:
    p = DayTradeVote().parameters
    return {k: p.get(k) for k in ("enter_confidence", "exit_confidence", "min_agree", "vol_gate_lo", "vol_gate_hi")}


def read_snapshot(state_dir: Path, data_root: Path, symbol: str, *, bars: int = 200, days: int = 3,
                  now: Optional[datetime] = None, leverage: Optional[int] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    since: date = (now - timedelta(days=days)).date()
    fresh_from: date = max(since, (now - timedelta(days=1)).date())
    store = LayeredStore(data_root)

    candles: dict[str, dict] = {}
    day = since
    while day < fresh_from:
        key = (str(Path(data_root).resolve()), day.isoformat())
        with _DAY_LOCK:
            parsed = _DAY_CACHE.get(key)
            if parsed is None:
                parsed = _day_candles(store, day)
                if parsed:  # an empty day may still get data later, so it is not remembered
                    _DAY_CACHE[key] = parsed
        candles.update(parsed.get(symbol, {}))
        day += timedelta(days=1)
    last_book: Optional[dict] = None
    for r in store.read("normalized", start=fresh_from):
        if r.get("symbol") != symbol:
            continue
        if r.get("kind") == "candle" and r.get("timeframe") == "15m":
            candles[r["open_time"]] = _candle(r)
        elif r.get("kind") == "book_stats_1m" and r.get("mid_price"):
            last_book = r
    full = [candles[k] for k in sorted(candles)][-(bars + EXTRA_BARS):]
    series = full[-bars:]
    overlays, latest = _overlays(full, len(series))
    last_price = last_book["mid_price"] if last_book else (series[-1]["close"] if series else None)
    last_price_time = last_book["interval_start"] if last_book else None

    execution = [r for r in store.read("execution", start=since) if r.get("symbol") == symbol]
    purpose = {r["client_order_id"]: (r.get("intent") or {}).get("purpose") for r in execution if r.get("event") == "order_submit"}
    fills = [{"time": _epoch(r["recorded_at"]), "side": r["side"], "price": r["price"], "quantity": r["quantity"],
              "kind": _fill_kind(r["side"], purpose.get(r.get("client_order_id")))}
             for r in execution if r.get("event") == "fill"]
    symbol_decisions = [r for r in store.read("decision", start=since) if r.get("symbol") == symbol]
    decisions = [{"time": r["bar_open_time"], "action": r["action"], "reason": r["reason"]} for r in symbol_decisions]
    trades = [_trade(r) for r in store.read("outcome", start=since) if r.get("symbol") == symbol]

    state = _load_state(state_dir)
    out = {"symbol": symbol, "as_of": now.isoformat(), "candles": series, "last_price": last_price,
           "last_price_time": last_price_time, "fills": fills[-300:], "decisions": decisions[-12:],
           "closed_trades": trades[-10:], "account": None, "position": None, "overlays": overlays, "latest": latest,
           "votes": _votes(symbol_decisions), "rules": _rules()}
    if state:
        out["position"] = _position(state, symbol, last_price, leverage)
        out["account"] = {"balance": state["broker"]["balance"], "saved_at": state["saved_at"],
                          "open_positions": len(state.get("open_trades") or {}),
                          **_krw_account(state_dir, state["broker"]["balance"])}
    return out
