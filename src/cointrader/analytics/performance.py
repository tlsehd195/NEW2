"""Performance metrics and cost decomposition.

Every result is reported as

    Gross PnL - Trading fees - Spread - Slippage - Funding = Net PnL

so a strategy whose gross edge is eaten by costs (the usual scalping
outcome) is visible as exactly that, not as a small net number with no
explanation. Metrics are computed from what happened (per-bar equity
returns and closed trades) and say nothing about the future; a backtest
metric is labelled as such by the caller (`validation-status-guard`).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Optional, Sequence


def _mean(xs: Sequence[float]) -> Optional[float]:
    return math.fsum(xs) / len(xs) if xs else None


def _stdev(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = math.fsum(xs) / len(xs)
    return math.sqrt(math.fsum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def max_drawdown(equity: Sequence[float]) -> float:
    peak, worst = -math.inf, 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            worst = max(worst, 1 - e / peak)
    return worst


def return_metrics(bar_returns: Sequence[float], periods_per_year: float) -> dict:
    growth = 1.0
    equity = [1.0]
    for r in bar_returns:
        growth *= 1 + r
        equity.append(growth)
    total = growth - 1
    n = len(bar_returns)
    ann = (growth ** (periods_per_year / n) - 1) if n and growth > 0 else None
    sd = _stdev(bar_returns)
    mean = _mean(bar_returns)
    downside = [min(0.0, r) for r in bar_returns]
    dd = math.sqrt(math.fsum(d * d for d in downside) / len(downside)) if downside else None
    mdd = max_drawdown(equity)
    return {
        "total_return": total,
        "annualized_return": ann,
        "volatility": sd * math.sqrt(periods_per_year) if sd is not None else None,
        "sharpe": (mean / sd * math.sqrt(periods_per_year)) if sd else None,
        "sortino": (mean / dd * math.sqrt(periods_per_year)) if dd else None,
        "downside_deviation": dd * math.sqrt(periods_per_year) if dd is not None else None,
        "max_drawdown": mdd,
        "calmar": (ann / mdd) if ann is not None and mdd > 0 else None,
        "bars": n,
    }


def trade_metrics(trades: Sequence, *, days: Optional[float] = None) -> dict:
    """`trades` are objects with the `backtest.event_engine.TradeRecord`
    fields (the paper journal's outcomes have the same shape)."""
    nets = [t.net_pnl for t in trades]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    gross = math.fsum(t.gross_pnl for t in trades)
    fees = math.fsum(t.fees for t in trades)
    spread = math.fsum(t.spread_cost for t in trades)
    slip = math.fsum(t.slippage_cost for t in trades)
    funding = math.fsum(t.funding for t in trades)
    net = math.fsum(nets)
    notional = math.fsum(t.quantity * t.entry_reference for t in trades) * 2  # in and out
    abs_gross = math.fsum(abs(t.gross_pnl) for t in trades)
    maker = [t for t in trades if t.entry_liquidity == "maker"]
    taker = [t for t in trades if t.entry_liquidity == "taker"]
    holding = [t.holding_time for t in trades]
    avg_hold = sum(holding, timedelta(0)) / len(holding) if holding else None
    return {
        "trade_count": len(trades),
        "win_rate": len(wins) / len(nets) if nets else None,
        "profit_factor": (math.fsum(wins) / -math.fsum(losses)) if losses and math.fsum(losses) < 0 else None,
        "average_win": _mean(wins),
        "average_loss": _mean(losses),
        "expectancy": _mean(nets),
        "average_holding_time_seconds": avg_hold.total_seconds() if avg_hold is not None else None,
        "turnover_notional": notional,
        "cost_breakdown": {"gross_pnl": gross, "fees": fees, "spread": spread, "slippage": slip,
                           "funding": funding, "net_pnl": net},
        "fee_ratio": fees / abs_gross if abs_gross else None,
        "slippage_ratio": (spread + slip) / abs_gross if abs_gross else None,
        "funding_impact": funding / abs_gross if abs_gross else None,
        "gross_positive_net_negative": gross > 0 and net < 0,
        "average_spread_paid": spread / notional if notional else None,
        "average_slippage": slip / notional if notional else None,
        "trades_per_day": len(trades) / days if days else None,
        "maker_entries": len(maker),
        "taker_entries": len(taker),
        "mean_mfe": _mean([t.mfe for t in trades]),
        "mean_mae": _mean([t.mae for t in trades]),
        # adverse selection proxy: how far maker-filled entries go against
        # us vs. taker-filled ones (maker fills happen when price trades through).
        "adverse_selection_mae_gap": (_mean([t.mae for t in maker]) - _mean([t.mae for t in taker]))
        if maker and taker else None,
        "exit_reasons": dict(_count(t.exit_reason for t in trades)),
    }


def _count(items: Iterable[str]) -> dict:
    out: dict = defaultdict(int)
    for i in items:
        out[i] += 1
    return out


def exposure(trades: Sequence, total_seconds: float) -> Optional[float]:
    if total_seconds <= 0:
        return None
    return min(1.0, sum(t.holding_time.total_seconds() for t in trades) / total_seconds)


def result_summary(result, periods_per_year: float) -> dict:
    """Full summary of an `EventBacktestResult`."""
    span = (result.equity_curve[-1][0] - result.equity_curve[0][0]).total_seconds() if result.equity_curve else 0.0
    days = span / 86400 if span else None
    fills = len(result.trades)
    return {
        "strategy_id": result.strategy_id, "symbol": result.symbol, "timeframe": result.timeframe,
        "label": "BACKTEST (simulated; not paper or live performance)",
        **return_metrics(result.bar_returns, periods_per_year),
        **trade_metrics(result.trades, days=days),
        "exposure": exposure(result.trades, span),
        "signals": result.signals, "rejected_entries": result.rejected_entries,
        "missed_fills": result.missed_fills, "partial_fills": result.partial_fills,
        "fill_rate": fills / (fills + result.missed_fills) if fills + result.missed_fills else None,
        "liquidations": result.liquidations, "assumptions": list(result.assumptions),
        "engine_version": result.engine_version,
    }


@dataclass(frozen=True)
class GroupKey:
    strategy_id: str
    regime: str
    symbol: str
    timeframe: str


def breakdown(trades: Sequence, timeframe_of=lambda t: getattr(t, "timeframe", "?")) -> dict:
    """Strategy x Regime x Symbol x Timeframe -> trade metrics (with the
    sample size, so a thin cell is visibly thin)."""
    groups: dict = defaultdict(list)
    for t in trades:
        groups[GroupKey(t.strategy_id, t.regime_at_entry, t.symbol, timeframe_of(t))].append(t)
    return {f"{k.strategy_id}|{k.regime}|{k.symbol}|{k.timeframe}": trade_metrics(v) for k, v in sorted(
        groups.items(), key=lambda kv: (kv[0].strategy_id, kv[0].regime, kv[0].symbol, kv[0].timeframe))}
