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


_RISK_KEYS = {"risk_per_trade", "max_leverage", "stop_min_fraction", "stop_max_fraction", "max_position_notional", "max_daily_loss", "max_drawdown",
              "max_atr_fraction", "max_spread_fraction", "require_spread", "stoploss_guard", "drawdown_guard",
              "cooldown_minutes", "risk_per_trade_max", "risk_conf_low", "risk_conf_high"}


def risk_from_dict(d: dict) -> RiskConfig:
    d = {k: v for k, v in d.items() if k != "_comment"}
    if set(d) != _RISK_KEYS:
        raise ValueError(f"risk config keys differ: missing {_RISK_KEYS - set(d)}, unknown {set(d) - _RISK_KEYS}")
    sg, dg = d["stoploss_guard"], d["drawdown_guard"]
    return RiskConfig(
        risk_per_trade=d["risk_per_trade"], max_leverage=d["max_leverage"],
        stop_min_fraction=d["stop_min_fraction"], stop_max_fraction=d["stop_max_fraction"],
        max_position_notional=d["max_position_notional"], max_daily_loss=d["max_daily_loss"],
        max_drawdown=d["max_drawdown"], max_atr_fraction=d["max_atr_fraction"],
        max_spread_fraction=d["max_spread_fraction"], require_spread=d["require_spread"],
        stoploss_guard=StoplossGuard(timedelta(hours=sg["lookback_hours"]), sg["max_losses"],
                                     timedelta(hours=sg["pause_hours"])) if sg else None,
        drawdown_guard=MaxDrawdownGuard(timedelta(hours=dg["lookback_hours"]), dg["max_drawdown"],
                                        timedelta(hours=dg["pause_hours"])) if dg else None,
        cooldown=CooldownPeriod(timedelta(minutes=d["cooldown_minutes"])) if d["cooldown_minutes"] else None,
        risk_per_trade_max=d["risk_per_trade_max"], risk_conf_low=d["risk_conf_low"], risk_conf_high=d["risk_conf_high"],
    )


def load_risk(path: Path = CONFIGS / "risk.json") -> RiskConfig:
    return risk_from_dict(_load(path))


def load_paper(path: Path = CONFIGS / "paper.json") -> dict:
    d = _load(path)
    if d.get("environment") != "paper" or d.get("live_trading_enabled") is not False:
        raise ValueError("configs/paper.json must say environment=paper and live_trading_enabled=false")
    return d


def load_krw_accounting(path: Path = CONFIGS / "krw_accounting.json"):
    """-> (KrwTaxConfig, ExitCostConfig, rate max staleness). Strict keys."""
    from cointrader.accounting.krw_ledger import ExitCostConfig, KrwTaxConfig

    d = _load(path)
    top = {"tax", "exit_costs", "exit_costs_source", "rate_max_staleness_minutes"}
    if set(d) != top:
        raise ValueError(f"krw_accounting keys differ: missing {top - set(d)}, unknown {set(d) - top}")
    t = dict(d["tax"])
    if not t.pop("sources", None):
        raise ValueError("krw_accounting.tax.sources must list where the tax values came from")
    tax_keys = {"effective_from_year", "rate", "basic_deduction_krw", "verified"}
    if set(t) != tax_keys:
        raise ValueError(f"krw_accounting.tax keys differ: missing {tax_keys - set(t)}, unknown {set(t) - tax_keys}")
    if not d["exit_costs_source"]:
        raise ValueError("krw_accounting.exit_costs_source must say where the exit costs came from")
    e = d["exit_costs"]
    exit_keys = {"overseas_withdraw_fee_usdt", "domestic_sell_fee_rate", "krw_withdraw_fee_krw"}
    if set(e) != exit_keys:
        raise ValueError(f"krw_accounting.exit_costs keys differ: missing {exit_keys - set(e)}, unknown {set(e) - exit_keys}")
    return (KrwTaxConfig(**t), ExitCostConfig(**e), timedelta(minutes=d["rate_max_staleness_minutes"]))


def load_margin_policy(path: Path = CONFIGS / "margin_policy.json"):
    """-> (MarginPolicy, {symbol: [MarginTier]}, verified). Strict keys."""
    from cointrader.risk.leverage import MarginTier
    from cointrader.risk.margin_policy import MarginPolicy

    d = _load(path)
    keys = {"margin_type", "exchange_leverage", "min_liquidation_to_stop", "verified", "tiers"}
    if set(d) != keys:
        raise ValueError(f"margin_policy keys differ: missing {keys - set(d)}, unknown {set(d) - keys}")
    policy = MarginPolicy(d["margin_type"], d["exchange_leverage"], d["min_liquidation_to_stop"])
    tiers = {s: [MarginTier(t["notional_floor"], t["notional_cap"], t["maintenance_margin_rate"],
                            t["maintenance_amount"]) for t in rows] for s, rows in d["tiers"].items()}
    return policy, tiers, bool(d["verified"])
