"""Bar-by-bar backtest of a grid candidate on one USDⓈ-M perpetual
(ADR-0021).

Fills
    The grid rests a buy limit one step below the current level and a sell
    limit one step above it. A limit fills at its own price, with the maker
    fee, only when the bar path trades STRICTLY through it (a touch is not
    a fill, as in `event_engine`). The bar path is unknown inside a bar, so
    it is taken as open -> nearer extreme -> other extreme -> close (ties:
    low first). Each fill immediately re-arms the opposite order one step
    away, so a bar that sweeps down and back up can complete round trips;
    those bars are counted (`intrabar_round_trip_bars`) so their share of
    the result is visible. The move from the previous close to this open
    is walked too. With zero costs on a simulated martingale aggregated
    into proper OHLC bars, 1m/5m/15m runs all average ~0 (checked while
    building ADR-0021), so the path rule adds no free edge; the study runs
    on 15m bars.

Holes
    If bars are missing (the previous bar did not close at this bar's
    open), the grid is flattened at this bar's open and stays flat until
    the next reset (`data_gap_halts`); settlements inside the hole are
    still charged on the position that was held through it.

Market orders (re-centring at a reset, stops, fail-closed flattening)
    fill at the reference price moved against the order by half the spread
    plus square-root impact (impact_coefficient * sigma of the previous
    `impact_vol_window` bar log returns * sqrt(order value / bar traded
    value), participation capped at 1), plus the taker fee. A stop that
    the bar gaps through fills from where the path was, not at the stop.

Funding
    Every recorded settlement since the previous bar closed is charged on
    the position held then, at this bar's open price (long pays a positive
    rate). If a settlement on the `funding_interval` grid is missing while
    a position is open, the grid is flattened and stays flat until the
    next reset (fail-closed; counted as `funding_gap_halts`).
    `funding=None` is accepted only with `assume_no_funding=True`, which is
    listed in the assumptions.

Liquidation
    The whole account backs the grid (cross margin). At every path point,
    if equity <= `maintenance_margin_rate` * position notional, the account
    is liquidated: equity goes to zero and the run stops. The maintenance
    rate is an explicit input (the archive has no bracket table); it must
    be given, or `assume_no_liquidation=True` must be passed and is reported.

Accounting identity (tested)
    final equity - initial = grid_realized + market_realized + unrealized
                             - maker_fees - taker_fees - slippage - funding
    grid_realized is P&L realised by the grid's own limit fills (the round
    trips); market_realized is P&L realised by resets, stops and
    flattening (the inventory a trend left behind). Lots are matched LIFO.

Periods
    Each reset period is reported with its equity change and, for
    attribution only, the ex-post efficiency ratio and return of the
    period's own daily closes (`ex_post_*`, never used by the grid).
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional, Sequence

from cointrader.backtest.event_engine import ExecutionCosts
from cointrader.data.models import Candle
from cointrader.strategies.grid import GridCandidate

ENGINE_VERSION = "1.0.0"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FUNDING_TIME_TOLERANCE = timedelta(seconds=60)


@dataclass(frozen=True)
class GridTerms:
    maintenance_margin_rate: Optional[float] = None
    assume_no_liquidation: bool = False
    funding_interval: timedelta = timedelta(hours=8)


@dataclass(frozen=True)
class GridPeriod:
    start: datetime
    end: datetime
    deployed: bool
    reason: str
    centre: float
    step: float
    equity_start: float
    equity_end: float
    stopped: bool
    ex_post_efficiency_ratio: float
    ex_post_price_return: float

    @property
    def pnl(self) -> float:
        return self.equity_end - self.equity_start


@dataclass(frozen=True)
class GridResult:
    strategy_id: str
    initial_equity: float
    final_equity: float
    equity_curve: tuple[tuple[datetime, float], ...]
    periods: tuple[GridPeriod, ...]
    maker_fills: int
    market_orders: int
    round_trips: int
    intrabar_round_trip_bars: int
    stops: int
    liquidations: int
    funding_gap_halts: int
    data_gap_halts: int
    thin_market_orders: int
    grid_realized: float
    market_realized: float
    unrealized: float
    maker_fees: float
    taker_fees: float
    slippage: float
    funding: float  # net paid (negative = received)
    max_abs_exposure: float  # max |position notional| / equity seen at a bar close
    assumptions: tuple[str, ...]
    engine_version: str = ENGINE_VERSION

    @property
    def total_return(self) -> float:
        return self.final_equity / self.initial_equity - 1

    @property
    def max_drawdown(self) -> float:
        peak, dd = -math.inf, 0.0
        for _, e in self.equity_curve:
            peak = max(peak, e)
            if peak > 0:
                dd = max(dd, 1 - e / peak)
        return dd

    def summary(self) -> dict:
        eq0 = self.initial_equity
        return {
            "total_return": self.total_return, "max_drawdown": self.max_drawdown,
            "periods": len(self.periods), "periods_deployed": sum(p.deployed for p in self.periods),
            "maker_fills": self.maker_fills, "round_trips": self.round_trips,
            "intrabar_round_trip_bars": self.intrabar_round_trip_bars, "market_orders": self.market_orders,
            "stops": self.stops, "liquidations": self.liquidations, "funding_gap_halts": self.funding_gap_halts,
            "data_gap_halts": self.data_gap_halts,
            "thin_market_orders": self.thin_market_orders, "max_abs_exposure": self.max_abs_exposure,
            "pnl_breakdown_pct_of_initial": {
                "grid_round_trips": self.grid_realized / eq0, "inventory_resets_and_stops": self.market_realized / eq0,
                "open_inventory": self.unrealized / eq0, "maker_fees": -self.maker_fees / eq0,
                "taker_fees": -self.taker_fees / eq0, "spread_and_impact": -self.slippage / eq0,
                "funding": -self.funding / eq0,
            },
            "assumptions": list(self.assumptions), "engine_version": self.engine_version,
        }


def _efficiency_ratio(closes: Sequence[float]) -> float:
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
    return abs(closes[-1] - closes[0]) / path if path > 0 else 0.0


def _funding_slots(funding: Mapping[datetime, float], interval: timedelta) -> dict[datetime, float]:
    """Snap archive stamps (a few ms after the hour) onto the minute grid."""
    out: dict[datetime, float] = {}
    tol = FUNDING_TIME_TOLERANCE.total_seconds()
    for when, rate in funding.items():
        snapped = EPOCH + timedelta(seconds=round((when - EPOCH).total_seconds() / 60) * 60)
        if abs((when - snapped).total_seconds()) > tol:
            continue
        if snapped in out:
            raise ValueError(f"two funding records on {snapped.isoformat()}")
        out[snapped] = rate
    return out


def run_grid_backtest(
    candles: Sequence[Candle],
    candidate: GridCandidate,
    *,
    score_from: datetime,
    funding: Optional[Mapping[datetime, float]] = None,
    assume_no_funding: bool = False,
    costs: ExecutionCosts = ExecutionCosts(),
    terms: GridTerms = GridTerms(),
    initial_equity: float = 10_000.0,
) -> GridResult:
    if not candles:
        raise ValueError("no candles")
    tf = candles[0].timeframe
    if any(c.timeframe != tf for c in candles):
        raise ValueError("mixed timeframes")
    if any(b.open_time <= a.open_time for a, b in zip(candles, candles[1:])):
        raise ValueError("candles must be strictly increasing in time")
    bars_per_day = timedelta(days=1) / tf.delta
    if bars_per_day != int(bars_per_day):
        raise ValueError("timeframe must divide a day")
    bpd = int(bars_per_day)
    if funding is None and not assume_no_funding:
        raise ValueError("funding series required (or assume_no_funding=True, reported as an assumption)")
    mmr = terms.maintenance_margin_rate
    if mmr is None and not terms.assume_no_liquidation:
        raise ValueError("maintenance_margin_rate required (or assume_no_liquidation=True)")
    if mmr is not None and not 0 <= mmr < 1:
        raise ValueError("maintenance_margin_rate must be in [0, 1)")

    assumptions = [
        f"grid engine {ENGINE_VERSION}; bar path open->nearer extreme->other extreme->close; limits fill only "
        f"when traded strictly through, at the limit price, maker fee {costs.maker_fee}",
        f"market orders: taker fee {costs.taker_fee}, half spread {costs.half_spread}, impact "
        f"{costs.impact_coefficient}*sigma(prev {costs.impact_vol_window} bars, floor {costs.impact_vol_floor})"
        f"*sqrt(participation)",
        "cross margin: the whole account backs the grid; lot prices not rounded to exchange tick/step",
        "funding and liquidation use the traded (last) price, not the mark price (archive mark klines not loaded)",
        "a hole in the bars (missing candles) flattens the grid at the next bar's open until the next reset",
    ]
    if funding is None:
        assumptions.append("NO FUNDING CHARGED (assume_no_funding=True)")
    if mmr is None:
        assumptions.append("LIQUIDATION NOT MODELLED (assume_no_liquidation=True)")
    else:
        assumptions.append(f"liquidation when equity <= {mmr} * position notional (stated maintenance rate)")
    slots = _funding_slots(funding, terms.funding_interval) if funding is not None else {}
    slot_times = sorted(slots)
    interval_s = terms.funding_interval.total_seconds()

    closes = [c.close for c in candles]
    logret = [0.0] + [math.log(b / a) if a > 0 and b > 0 else 0.0 for a, b in zip(closes, closes[1:])]
    w = costs.impact_vol_window

    def sigma_before(i: int) -> float:
        xs = logret[max(1, i - w):i]
        if len(xs) < 2:
            return costs.impact_vol_floor
        m = sum(xs) / len(xs)
        return max(math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)), costs.impact_vol_floor)

    reset_span = timedelta(days=candidate.reset_days)
    n = candidate.levels_per_side
    offset = n if candidate.mode == "spot" else 0
    need = candidate.lookback_days * bpd + 1

    cash = initial_equity
    q = 0.0  # signed base quantity
    lots: list[tuple[float, float]] = []  # LIFO (signed qty, entry price)
    grid_realized = market_realized = 0.0
    maker_fees = taker_fees = slippage = funding_paid = 0.0
    maker_fills = market_orders = round_trips = intrabar_rt = stops = liquidations = gap_halts = thin = 0
    data_gap_halts = 0
    max_exposure = 0.0
    curve: list[tuple[datetime, float]] = []
    periods: list[GridPeriod] = []

    active = False
    centre = step = lot = 0.0
    level = 0
    period: Optional[dict] = None
    dead = False

    def realize(dq: float, price: float) -> float:
        """Match `dq` against open lots LIFO; returns realised P&L at `price`."""
        nonlocal q
        pnl = 0.0
        rest = dq
        while rest != 0 and lots and (lots[-1][0] > 0) != (rest > 0):
            lq, lp = lots[-1]
            take = min(abs(rest), abs(lq))
            sign = 1.0 if lq > 0 else -1.0
            pnl += sign * take * (price - lp)
            left = lq - sign * take
            if abs(left) < 1e-12:
                lots.pop()
            else:
                lots[-1] = (left, lp)
            rest += sign * take
            if abs(rest) < 1e-12:
                rest = 0.0
        if rest != 0:
            lots.append((rest, price))
        q += dq
        if abs(q) < 1e-12:
            q = 0.0
            lots.clear()
        return pnl

    def market(dq: float, ref: float, i: int) -> None:
        nonlocal cash, market_realized, taker_fees, slippage, market_orders, thin
        if dq == 0:
            return
        bar = candles[i]
        value = abs(dq) * ref
        bar_value = bar.close * bar.volume
        part = value / bar_value if bar_value > 0 else math.inf
        if part > costs.max_participation:
            thin += 1
        impact = costs.impact_coefficient * sigma_before(i) * math.sqrt(min(part, 1.0))
        fill = ref * (1 + (costs.half_spread + impact) * (1 if dq > 0 else -1))
        market_realized += realize(dq, ref)
        slippage += abs(dq) * abs(fill - ref)
        fee = abs(dq) * fill * costs.taker_fee
        taker_fees += fee
        cash -= dq * fill + fee
        market_orders += 1

    def limit(dq: float, price: float) -> None:
        nonlocal cash, grid_realized, maker_fees, maker_fills, round_trips
        reducing = q != 0 and (q > 0) != (dq > 0)
        grid_realized += realize(dq, price)
        fee = abs(dq) * price * costs.maker_fee
        maker_fees += fee
        cash -= dq * price + fee
        maker_fills += 1
        round_trips += reducing

    def equity(price: float) -> float:
        return cash + q * price

    def close_period(end: datetime, price: float) -> None:
        nonlocal period
        if period is None:
            return
        daily = period["daily"] + [price]
        periods.append(GridPeriod(
            start=period["start"], end=end, deployed=period["deployed"], reason=period["reason"],
            centre=period["centre"], step=period["step"], equity_start=period["equity"], equity_end=equity(price),
            stopped=period["stopped"], ex_post_efficiency_ratio=_efficiency_ratio(daily) if len(daily) > 2 else 0.0,
            ex_post_price_return=daily[-1] / daily[0] - 1,
        ))
        period = None

    def reset(i: int) -> None:
        nonlocal active, centre, step, lot, level, period
        bar = candles[i]
        close_period(bar.open_time, bar.open)
        eq = equity(bar.open)
        reason, deploy = "deployed", True
        if i < need:
            reason, deploy = "insufficient_history", False
        else:
            vol_n = candidate.vol_lookback_days * bpd
            xs = logret[i - vol_n:i]
            m = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
            if not math.isfinite(sd) or sd <= 0:
                reason, deploy = "no_volatility", False
            elif candidate.max_efficiency_ratio is not None:
                daily = [closes[i - 1 - k * bpd] for k in range(candidate.er_lookback_days, -1, -1)]
                if _efficiency_ratio(daily) >= candidate.max_efficiency_ratio:
                    reason, deploy = "trend_filter", False
        if deploy:
            centre = closes[i - 1]
            half = candidate.width_sigmas * sd * math.sqrt(bpd) * math.sqrt(candidate.reset_days) * centre
            step = half / n
            if step <= 0 or centre - (n + 1) * step <= 0:
                reason, deploy = "range_below_zero", False
        if deploy and eq > 0:
            lot = candidate.exposure * eq / ((n + offset) * centre)
            level = 0
            market(offset * lot - q, bar.open, i)
            active = True
        else:
            market(-q, bar.open, i)
            active = False
        period = {"start": bar.open_time, "deployed": deploy, "reason": reason,
                  "centre": centre if deploy else math.nan, "step": step if deploy else math.nan,
                  "equity": eq, "stopped": False, "daily": []}

    def price_at(j: int) -> float:
        return centre + j * step

    def walk(a: float, b: float, i: int) -> tuple[bool, bool]:
        """Fills along one path segment; -> (bought, sold)."""
        nonlocal level, active, stops
        bought = sold = False
        if not active:
            return bought, sold
        if b < a:
            while level - 1 >= -n and b < price_at(level - 1):
                limit(lot, price_at(level - 1))
                level -= 1
                bought = True
            stop = price_at(-n - 1)
            if q > 0 and b < stop:
                market(-q, min(stop, a), i)
                stops += 1
                active = False
                period["stopped"] = True
        elif b > a:
            while level + 1 <= n and b > price_at(level + 1):
                limit(-lot, price_at(level + 1))
                level += 1
                sold = True
            stop = price_at(n + 1)
            if q < 0 and b > stop:
                market(-q, max(stop, a), i)
                stops += 1
                active = False
                period["stopped"] = True
        return bought, sold

    started = False
    for i, bar in enumerate(candles):
        if bar.open_time < score_from:
            continue
        if dead:
            curve.append((bar.close_time, 0.0))
            continue
        # funding at settlements since the previous bar closed, on the position held then
        t0 = bar.open_time
        t1 = bar.close_time
        prev_bar = candles[i - 1] if started else None
        since = prev_bar.close_time if prev_bar is not None else t0
        if q != 0:
            for k in range(bisect.bisect_left(slot_times, since), bisect.bisect_left(slot_times, t1)):
                paid = q * bar.open * slots[slot_times[k]]
                funding_paid += paid
                cash -= paid
        if prev_bar is not None and since != t0:
            # missing bars: what happened inside the hole is unknown -> flatten, stay flat until the next reset
            if q != 0 or active:
                market(-q, bar.open, i)
                active = False
                data_gap_halts += 1
        elif prev_bar is not None and active:
            walk(prev_bar.close, bar.open, i)  # the move between the previous close and this open
        if funding is not None and q != 0:
            first = math.ceil((since - EPOCH).total_seconds() / interval_s)
            s = EPOCH + timedelta(seconds=first * interval_s)
            missing = False
            while s < t1:
                missing = missing or s not in slots
                s += terms.funding_interval
            if missing:
                market(-q, bar.open, i)
                active = False
                gap_halts += 1
        if not started or (bar.open_time - EPOCH) % reset_span == timedelta(0):
            reset(i)
            started = True
        if period is not None and (bar.open_time - EPOCH) % timedelta(days=1) == timedelta(0):
            period["daily"].append(bar.open)
        low_first = abs(bar.open - bar.low) <= abs(bar.high - bar.open)
        path = [bar.open, bar.low, bar.high, bar.close] if low_first else [bar.open, bar.high, bar.low, bar.close]
        bought = sold = False
        prev = path[0]
        for p in path[1:]:
            if bar.volume > 0:
                b, s_ = walk(prev, p, i)
                bought, sold = bought or b, sold or s_
            if mmr is not None and q != 0 and equity(p) <= mmr * abs(q) * p:
                liquidations += 1
                dead = True
                cash, q = 0.0, 0.0
                lots.clear()
                active = False
                if period is not None:
                    period["stopped"] = True
                break
            prev = p
        intrabar_rt += bought and sold
        eq = equity(bar.close)
        if eq > 0:
            max_exposure = max(max_exposure, abs(q) * bar.close / eq)
        curve.append((bar.close_time, eq))

    if not started:
        raise ValueError("no bar at or after score_from")
    last = candles[-1]
    close_period(last.close_time, last.close)
    unrealized = sum(lq * (last.close - lp) for lq, lp in lots)
    return GridResult(
        strategy_id=candidate.strategy_id, initial_equity=initial_equity, final_equity=equity(last.close),
        equity_curve=tuple(curve), periods=tuple(periods), maker_fills=maker_fills, market_orders=market_orders,
        round_trips=round_trips, intrabar_round_trip_bars=intrabar_rt, stops=stops, liquidations=liquidations,
        funding_gap_halts=gap_halts, data_gap_halts=data_gap_halts, thin_market_orders=thin, grid_realized=grid_realized,
        market_realized=market_realized, unrealized=unrealized, maker_fees=maker_fees, taker_fees=taker_fees,
        slippage=slippage, funding=funding_paid, max_abs_exposure=max_exposure, assumptions=tuple(assumptions),
    )
