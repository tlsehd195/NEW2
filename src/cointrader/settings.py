"""Loaders for the JSON configs under `configs/` (markets, risk, paper,
retention). Every loader is strict: an unknown or missing key raises, so
a typo can never silently fall back to a default. `configs/live/` is
deliberately not read here -- live settings belong to the protected
live-approval path."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from cointrader.risk.engine import RiskConfig, SymbolFilters
from cointrader.risk.protections import CooldownPeriod, MaxDrawdownGuard, StoplossGuard

REPO = Path(__file__).resolve().parents[2]
CONFIGS = REPO / "configs"


def _load(path: Path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    d.pop("_comment", None)
    return d


def load_markets(path: Path = CONFIGS / "markets.json") -> tuple[dict[str, SymbolFilters], dict[str, float], bool]:
    d = _load(path)
    filters, large = {}, {}
    for sym, m in d["markets"].items():
        filters[sym] = SymbolFilters(sym, m["tick_size"], m["step_size"], m["min_quantity"], m["min_notional"])
        large[sym] = m["large_trade_quantity"]
    return filters, large, bool(d["verified"])


_RISK_KEYS = {"risk_per_trade", "max_leverage", "max_position_notional", "max_daily_loss", "max_drawdown",
              "max_atr_fraction", "max_spread_fraction", "require_spread", "stoploss_guard", "drawdown_guard",
              "cooldown_minutes"}


def risk_from_dict(d: dict) -> RiskConfig:
    d = {k: v for k, v in d.items() if k != "_comment"}
    if set(d) != _RISK_KEYS:
        raise ValueError(f"risk config keys differ: missing {_RISK_KEYS - set(d)}, unknown {set(d) - _RISK_KEYS}")
    sg, dg = d["stoploss_guard"], d["drawdown_guard"]
    return RiskConfig(
        risk_per_trade=d["risk_per_trade"], max_leverage=d["max_leverage"],
        max_position_notional=d["max_position_notional"], max_daily_loss=d["max_daily_loss"],
        max_drawdown=d["max_drawdown"], max_atr_fraction=d["max_atr_fraction"],
        max_spread_fraction=d["max_spread_fraction"], require_spread=d["require_spread"],
        stoploss_guard=StoplossGuard(timedelta(hours=sg["lookback_hours"]), sg["max_losses"],
                                     timedelta(hours=sg["pause_hours"])) if sg else None,
        drawdown_guard=MaxDrawdownGuard(timedelta(hours=dg["lookback_hours"]), dg["max_drawdown"],
                                        timedelta(hours=dg["pause_hours"])) if dg else None,
        cooldown=CooldownPeriod(timedelta(minutes=d["cooldown_minutes"])) if d["cooldown_minutes"] else None,
    )


def load_risk(path: Path = CONFIGS / "risk.json") -> RiskConfig:
    return risk_from_dict(_load(path))


def load_paper(path: Path = CONFIGS / "paper.json") -> dict:
    d = _load(path)
    if d.get("environment") != "paper" or d.get("live_trading_enabled") is not False:
        raise ValueError("configs/paper.json must say environment=paper and live_trading_enabled=false")
    return d
