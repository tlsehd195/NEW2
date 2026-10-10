"""Paper carry runner: hourly bars + funding settlements in, one equity path and risk numbers out (ADR-0067).

Decision rules (all parameters, none fitted to data):
- enter spot long + perp short at equal coins with `1/leverage` of notional as isolated margin;
- every bar close: if the margin ratio (wallet / perp notional) falls below `topup_trigger * margin_frac`,
  sell spot and buy back perp in equal coins until the ratio is back at `margin_frac` (closed form, fees paid);
- bar high is checked against the maintenance requirement (margin_policy tiers, ASSUMED): breach = liquidation,
  the margin is lost and the spot leg is sold;
- optional funding filter: leave when the trailing mean funding (annualised) is below `exit_apr`, return when it
  is above `reenter_apr`. `funding_filter=False` is the always-on hold.
Capital is whatever the executor holds as cash; the runner never borrows.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from cointrader.carry.executor import PaperCarryExecutor
from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle
from cointrader.risk.leverage import MarginTier, find_maintenance_tier

SETTLEMENTS_PER_YEAR = 3 * 365


@dataclass(frozen=True)
class CarryParams:
    leverage: float = 2.0                  # perp notional / margin; margin_frac = 1 / leverage
    topup_trigger: float = 0.5             # top up when ratio < trigger * margin_frac
    funding_filter: bool = False
    filter_window: int = 21                # settlements (7 days)
    exit_apr: float = 0.0
    reenter_apr: float = 0.05
    capital_cost_apr: float = 0.04         # ASSUMED opportunity cost of the money, reported separately

    def __post_init__(self) -> None:
        if not 1.0 <= self.leverage <= 20:
            raise ValueError("leverage must be in [1, 20]")
        if not 0 < self.topup_trigger < 1:
            raise ValueError("topup_trigger must be in (0, 1)")

    @property
    def margin_frac(self) -> float:
        return 1.0 / self.leverage


def maintenance_requirement(tiers: Sequence[MarginTier], notional: float) -> float:
    t = find_maintenance_tier(list(tiers), notional)
    return notional * t.maintenance_margin_rate - t.maintenance_amount


def solve_shrink_coins(*, q: float, wallet: float, target_ratio: float, spot: float, perp: float,
                       spot_fee: float, perp_fee: float, slippage: float) -> float:
    """Coins x to sell on both legs so (wallet + net proceeds) / ((q - x) * perp) == target_ratio."""
    s_net = spot * (1 - slippage) * (1 - spot_fee)
    p_cost = perp * (1 + slippage) * perp_fee
    denom = target_ratio * perp + s_net - p_cost
    return max(0.0, min(q, (target_ratio * q * perp - wallet) / denom))


class CarryRunner:
    def __init__(self, executor: PaperCarryExecutor, params: CarryParams, tiers: Sequence[MarginTier]) -> None:
        self.ex, self.p, self.tiers = executor, params, tiers
        self.capital0 = executor.cash
        self.equity_curve: list[tuple[datetime, float]] = []
        self.rebalances = 0
        self.liquidations = 0
        self.entries = 0
        self.bars_in_position = 0
        self.min_buffer_close = math.inf    # adverse-move buffer (fraction of perp notional) at bar closes
        self.min_buffer_high = math.inf     # same, evaluated at each bar high (intrabar worst)
        self.peak_perp_notional = 0.0

    def _buffer(self, price: float) -> float:
        ex = self.ex
        notional = ex.q_perp * price
        return (ex.wallet(price) - maintenance_requirement(self.tiers, notional)) / notional

    def run(self, spot: Sequence[Candle], perp: Sequence[Candle], funding: Sequence[FundingRateRecord]) -> dict:
        s_by = {c.open_time: c for c in spot}
        p_by = {c.open_time: c for c in perp}
        times = sorted(set(s_by) & set(p_by))
        if len(times) < 100:
            raise ValueError(f"too few joined bars: {len(times)}")
        fund = sorted(funding, key=lambda r: r.funding_time)
        fi = 0
        trailing: list[float] = []
        p, ex = self.p, self.ex
        hour = timedelta(hours=1)
        for t in times:
            sc, pc = s_by[t], p_by[t]
            liquidated = False
            # 1. funding settled in [t, t+1h) is paid to whoever held the position at the start of the bar
            while fi < len(fund) and fund[fi].funding_time < t + hour:
                r = fund[fi]
                fi += 1
                if r.funding_time < t:
                    continue    # before the data window
                if ex.q_perp > 0:
                    mark = r.mark_price if math.isfinite(r.mark_price) and r.mark_price > 0 else pc.open
                    ex.credit_funding(r.funding_rate, mark)
                trailing.append(r.funding_rate)
            # 2. liquidation check at the bar high (short loses when price rises)
            if ex.q_perp > 0:
                buf_hi = self._buffer(pc.high)
                self.min_buffer_high = min(self.min_buffer_high, buf_hi)
                if buf_hi < 0:
                    self.liquidations += 1
                    ex.liquidate_perp()
                    ex.sell_spot(sc.close)
                    liquidated = True
            # 3. bar-close decisions
            if ex.q_perp > 0:
                notional = ex.q_perp * pc.close
                self.peak_perp_notional = max(self.peak_perp_notional, notional)
                if ex.wallet(pc.close) / notional < p.topup_trigger * p.margin_frac:
                    x = solve_shrink_coins(
                        q=ex.q_perp, wallet=ex.wallet(pc.close), target_ratio=p.margin_frac, spot=sc.close,
                        perp=pc.close, spot_fee=ex.costs.spot_fee, perp_fee=ex.costs.perp_fee,
                        slippage=ex.costs.slippage)
                    if x > 0:
                        ex.shrink_and_top_up(x, sc.close, pc.close)
                        self.rebalances += 1
            mean_apr = None
            if len(trailing) >= p.filter_window:
                mean_apr = sum(trailing[-p.filter_window:]) / p.filter_window * SETTLEMENTS_PER_YEAR
            if ex.q_perp > 0 and p.funding_filter and mean_apr is not None and mean_apr < p.exit_apr:
                ex.close_pair(sc.close, pc.close)
            elif not ex.in_position and not liquidated:
                if not p.funding_filter or (mean_apr is not None and mean_apr > p.reenter_apr):
                    ex.open_pair(ex.cash, p.margin_frac, sc.close, pc.close)
                    self.entries += 1
            if ex.q_perp > 0:
                self.bars_in_position += 1
                self.min_buffer_close = min(self.min_buffer_close, self._buffer(pc.close))
            self.equity_curve.append((t, ex.equity(sc.close, pc.close)))
        return self.summary(times)

    def summary(self, times: Sequence[datetime]) -> dict:
        ex, p = self.ex, self.p
        days = (times[-1] - times[0]).total_seconds() / 86400
        eq = [e for _, e in self.equity_curve]
        end = eq[-1]
        peak, mdd = eq[0], 0.0
        for e in eq:
            peak = max(peak, e)
            mdd = max(mdd, (peak - e) / peak)
        ret = end / self.capital0 - 1
        apr = ret * 365 / days
        fin = lambda v: None if not math.isfinite(v) else round(100 * v, 3)  # noqa: E731
        return {
            "start": times[0].isoformat(), "end": times[-1].isoformat(), "days": round(days, 1),
            "leverage": p.leverage, "funding_filter": p.funding_filter,
            "capital_start": round(self.capital0, 2), "capital_end": round(end, 2),
            "net_return_pct": round(100 * ret, 3), "net_apr_pct": round(100 * apr, 2),
            "net_apr_minus_capital_cost_pct": round(100 * (apr - p.capital_cost_apr), 2),
            "max_drawdown_pct": round(100 * mdd, 3),
            "funding_received_pct_of_capital": round(100 * ex.funding_received / self.capital0, 3),
            "fees_pct_of_capital": round(100 * ex.fees_paid / self.capital0, 3),
            "entries": self.entries, "rebalances": self.rebalances, "liquidations": self.liquidations,
            "time_in_position_pct": round(100 * self.bars_in_position / len(times), 1),
            "min_liq_buffer_close_pct": fin(self.min_buffer_close),
            "min_liq_buffer_at_high_pct": fin(self.min_buffer_high),
            "peak_perp_notional_vs_capital": round(self.peak_perp_notional / self.capital0, 3),
        }
