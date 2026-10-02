"""Record shapes for the decision -> execution -> outcome chain.

The chain `Market Data -> Feature -> Regime -> Signal -> Risk -> Intent ->
Order -> Fill -> Position -> Exit -> Outcome` is kept joinable by ids:

    decision.decision_id          one strategy evaluation on one closed bar
    decision.risk_decision_id     RiskEngine's id (also on the OrderIntent)
    decision.client_order_id      the entry intent's deterministic id
    execution.client_order_id     every order event and fill
    outcome.entry_decision_id     closed trade -> the decision that opened it
    outcome.entry/exit_client_order_id

Backtest, paper and live all emit these shapes (`mode` says which), so
one analytics path reads them all and a backtest row can never be
mistaken for a live one.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from cointrader.backtest.event_engine import TradeRecord

MODES = ("backtest", "paper", "live")


def decision_id(strategy_id: str, symbol: str, bar_open_time: datetime, mode: str) -> str:
    raw = f"{strategy_id}|{symbol}|{bar_open_time.isoformat()}|{mode}"
    return "dc_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def decision_record(
    *,
    mode: str,
    symbol: str,
    timeframe: str,
    bar_open_time: datetime,
    strategy_id: str,
    strategy_version: str,
    feature_version: str,
    regime: str,
    regime_reason: str,
    features: dict,
    signal: dict,
    quality: dict,
    action: str,  # "enter_long" | "enter_short" | "exit" | "hold" | "blocked"
    reason: str,
    risk: Optional[dict] = None,
    client_order_id: Optional[str] = None,
    position_before: float = 0.0,
) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    return {
        "decision_id": decision_id(strategy_id, symbol, bar_open_time, mode),
        "mode": mode,
        "symbol": symbol,
        "timeframe": timeframe,
        "bar_open_time": bar_open_time.isoformat(),
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "feature_version": feature_version,
        "regime": regime,
        "regime_reason": regime_reason,
        "features": features,
        "signal": signal,
        "quality": quality,
        "risk": risk,
        "risk_decision_id": (risk or {}).get("decision_id"),
        "client_order_id": client_order_id,
        "position_before": position_before,
        "action": action,
        "reason": reason,
    }


def outcome_record(
    trade: TradeRecord,
    *,
    mode: str,
    timeframe: str,
    strategy_version: str,
    feature_version: str,
    entry_decision_id: Optional[str] = None,
    entry_client_order_id: Optional[str] = None,
    exit_client_order_id: Optional[str] = None,
    expected_entry_slippage: Optional[float] = None,
) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    d = asdict(trade)
    d["entry_time"] = trade.entry_time.isoformat()
    d["exit_time"] = trade.exit_time.isoformat()
    ref = trade.entry_reference
    actual_slip = (trade.entry_fill - ref) / ref * trade.direction if ref else None
    d.update({
        "trade_seq": trade.trade_id,
        "trade_id": f"{mode}:{trade.strategy_id}:{trade.symbol}:{trade.trade_id}",
        "mode": mode,
        "timeframe": timeframe,
        "strategy_version": strategy_version,
        "feature_version": feature_version,
        "entry_decision_id": entry_decision_id,
        "entry_client_order_id": entry_client_order_id,
        "exit_client_order_id": exit_client_order_id,
        "return_on_equity": trade.return_on_equity,
        "holding_seconds": trade.holding_time.total_seconds(),
        "success": trade.net_pnl > 0,
        "expected_entry_slippage": expected_entry_slippage,
        "actual_entry_slippage": actual_slip,
    })
    return d


def trade_from_outcome(row: dict) -> TradeRecord:
    """Inverse of `outcome_record` for analytics over stored outcomes."""
    fields = TradeRecord.__dataclass_fields__
    kw = {k: row[k] for k in fields if k != "trade_id"}
    kw["entry_time"] = datetime.fromisoformat(row["entry_time"])
    kw["exit_time"] = datetime.fromisoformat(row["exit_time"])
    kw["trade_id"] = row["trade_seq"]
    return TradeRecord(**kw)
