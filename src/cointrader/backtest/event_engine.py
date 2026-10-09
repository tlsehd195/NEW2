"""Event-driven single-symbol backtest for `SignalStrategy` candidates
(swing and scalp), with leverage, stops and a full cost decomposition.

Same no-look-ahead construction as `backtest.engine` (the strategy sees a
`PrefixView` that ends at the last CLOSED bar), extended with what
realistic execution needs (ADR-0015):

Timing
    A decision made at bar t's close acts no earlier than bar
    t+`latency_bars`'s open. Protective exits (stop, take-profit,
    trailing, liquidation) are checked against each later bar's range.
    When a stop and a take-profit are both touched inside one bar the
    STOP is assumed to have hit first (the bar's path is unknown; the
    pessimistic order is the honest one). A gap through a stop fills at
    the open, not at the stop.

Costs (never "fill at close")
    market/taker: fill = reference * (1 +/- (half_spread + impact)),
    impact = impact_coefficient * sigma_bar * sqrt(order value / bar traded value)
    ("volatility_scaled", default; the square-root law with sigma_bar =
    stdev of the previous `impact_vol_window` bars' log returns, floored at
    `impact_vol_floor` -- known before the fill bar opens, and consistent
    across timeframes because sigma/sqrt(volume) does not depend on the
    bar length). The older "fixed" model (impact_coefficient * sqrt(...),
    no sigma) overstates 1m costs ~20x and is kept only for comparison.
    fee = taker_fee on the filled notional. limit/maker entries fill only
    if a later bar trades THROUGH the limit (strictly beyond it -- a touch
    is not a fill, a crude but conservative adverse-selection model),
    within `limit_ttl_bars`, else they are counted as missed fills.
    Orders above `max_participation` of the bar's traded value are
    partially filled (reported). Quantities are rounded down to the
    exchange step, stop prices to the tick; orders under min quantity /
    min notional are refused.

Futures
    Funding is charged from a recorded settlement series at every
    settlement the position is held through (entry <= settlement < exit).
    A missing settlement inside a holding period fails closed (raises),
    as in `backtest.futures_engine`. `funding=None` is only accepted with
    `assume_no_funding=True`, and that assumption is listed in the report.
    Isolated-margin liquidation uses `risk.leverage.estimate_liquidation_price`
    with the supplied maintenance tiers; a liquidation loses the posted margin.

Risk
    Every entry goes through `risk.engine.RiskEngine` -- the same object
    the paper trader uses -- so protections, daily loss, drawdown,
    volatility and spread limits apply in the backtest exactly as they
    will in paper trading.

Output
    Per-trade records with gross PnL, fees, spread, slippage, funding and
    net PnL, MFE/MAE, holding time, exit reason, regime and features at
    entry; an equity curve; and counters of everything that was refused,
    missed, capped or liquidated.
"""

from __future__ import annotations

import bisect
import math
import re
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence

from cointrader.backtest.engine import PrefixView
from cointrader.data.models import Candle
from cointrader.data.quality import check_candles
from cointrader.features import indicators as ind
from cointrader.live.config import HealthStatus
from cointrader.risk.engine import AccountRiskState, EntryRequest, RiskEngine, utc_day_start
from cointrader.risk.leverage import MarginTier, PositionSide, estimate_liquidation_price
from cointrader.risk.protections import ClosedTrade, EquityPoint

ENGINE_VERSION = "1.0.0"


@dataclass(frozen=True)
class ExecutionCosts:
    """Defaults are stated assumptions (Binance USDⓈ-M VIP0 fees as known
    to this session: taker 0.05%, maker 0.02%), to be replaced by the
    account's real `commissionRate` before a result is trusted."""

    taker_fee: float = 0.0005
    maker_fee: float = 0.0002
    half_spread: float = 0.0001
    impact_coefficient: float = 1.0
    impact_model: str = "volatility_scaled"  # "volatility_scaled" | "fixed"
    impact_vol_window: int = 20
    impact_vol_floor: float = 0.0005
    max_participation: float = 0.05
    latency_bars: int = 1
    entry_order: str = "market"  # "market" | "limit"
    limit_offset: float = 0.0005
    limit_ttl_bars: int = 1

    def __post_init__(self) -> None:
        if self.latency_bars < 1:
            raise ValueError("latency_bars must be >= 1 (no same-bar execution)")
        if self.impact_model not in ("volatility_scaled", "fixed"):
            raise ValueError("impact_model must be volatility_scaled or fixed")
        if self.impact_vol_window < 2 or self.impact_vol_floor < 0:
            raise ValueError("impact_vol_window must be >= 2 and impact_vol_floor >= 0")
        if self.entry_order not in ("market", "limit"):
            raise ValueError("entry_order must be market or limit")
        for name in ("taker_fee", "maker_fee", "half_spread", "impact_coefficient"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        if not 0 < self.max_participation <= 1:
            raise ValueError("max_participation must be in (0, 1]")


@dataclass(frozen=True)
class FuturesTerms:
    margin_leverage: float = 3.0  # isolated-margin leverage setting on the exchange
    tiers: tuple[MarginTier, ...] = ()
    funding: Optional[dict] = None  # settlement datetime -> rate
    funding_interval: timedelta = timedelta(hours=8)
    assume_no_funding: bool = False  # explicit opt-out, reported as an assumption


# The archive's settlement stamps can sit a few milliseconds after the
# nominal grid time (e.g. 16:00:00.003); snap those onto the grid. A stamp
# further off than this is not treated as that settlement.
FUNDING_TIME_TOLERANCE = timedelta(seconds=60)


def _funding_slots(futures: "FuturesTerms") -> dict[int, float]:
    """Grid index (epoch seconds / interval) -> rate. Two records on one
    slot is ambiguous, so it fails closed."""
    if not futures.funding:
        return {}
    step = futures.funding_interval.total_seconds()
    tol = FUNDING_TIME_TOLERANCE.total_seconds()
    slots: dict[int, float] = {}
    for when, rate in futures.funding.items():
        slot = round(when.timestamp() / step)
        if abs(when.timestamp() - slot * step) > tol:
            continue
        if slot in slots:
            raise ValueError(f"two funding records map to the settlement at {when.isoformat()} (fail-closed)")
        slots[slot] = rate
    return slots


@dataclass(frozen=True)
class TradeRecord:
    trade_id: int
    strategy_id: str
    symbol: str
    direction: int
    entry_time: datetime
    exit_time: datetime
    entry_reference: float
    exit_reference: float
    entry_fill: float
    exit_fill: float
    quantity: float
    entry_liquidity: str  # "taker" | "maker"
    exit_liquidity: str
    filled_fraction: float  # of the risk-approved quantity
    gross_pnl: float
    fees: float
    spread_cost: float
    slippage_cost: float
    funding: float  # positive = paid
    net_pnl: float
    equity_at_entry: float
    mfe: float  # max favourable excursion, fraction of entry reference
    mae: float  # max adverse excursion (>= 0), fraction of entry reference
    holding_bars: int
    exit_reason: str
    regime_at_entry: str
    signal_reason: str
    features_at_entry: dict
    risk_decision_id: str

    @property
    def return_on_equity(self) -> float:
        return self.net_pnl / self.equity_at_entry if self.equity_at_entry else 0.0

    @property
    def holding_time(self) -> timedelta:
        return self.exit_time - self.entry_time


@dataclass(frozen=True)
class EventBacktestResult:
    strategy_id: str
    symbol: str
    timeframe: str
    initial_equity: float
    final_equity: float
    trades: tuple[TradeRecord, ...]
    equity_curve: tuple[tuple[datetime, float], ...]
    bar_returns: tuple[float, ...]
    signals: int
    rejected_entries: dict
    missed_fills: int
    partial_fills: int
    liquidations: int
    assumptions: tuple[str, ...]
    engine_version: str = ENGINE_VERSION

    @property
    def total_return(self) -> float:
        return self.final_equity / self.initial_equity - 1

    def cost_breakdown(self) -> dict:
        s = lambda f: math.fsum(getattr(t, f) for t in self.trades)  # noqa: E731
        return {"gross_pnl": s("gross_pnl"), "fees": s("fees"), "spread": s("spread_cost"),
                "slippage": s("slippage_cost"), "funding": s("funding"), "net_pnl": s("net_pnl")}


@dataclass
class _Position:
    direction: int
    quantity: float
    entry_time: datetime
    entry_index: int
    entry_reference: float
    entry_fill: float
    entry_liquidity: str
    filled_fraction: float
    fees: float
    spread_cost: float
    slippage_cost: float
    stop_price: Optional[float]
    take_profit: Optional[float]
    trailing_distance: Optional[float]
    liquidation_price: Optional[float]
    margin: float
    equity_at_entry: float
    regime: str
    signal_reason: str
    features: dict
    risk_decision_id: str
    best: float
    worst: float
    funding: float = 0.0
    trailing_activation: Optional[float] = None


@dataclass
class _PendingEntry:
    direction: int
    quantity: float
    act_index: int
    expires_index: int
    limit_price: Optional[float]
    stop_distance: float
    take_profit_distance: Optional[float]
    trailing_distance: Optional[float]
    regime: str
    reason: str
    features: dict
    risk_decision_id: str
    trailing_activation: Optional[float] = None


def _reason_key(reason: str) -> str:
    """Stable counter key for a rejection reason (drops the numbers)."""
    if reason.startswith("protection:"):
        kind = "stoploss_guard" if "losing" in reason else "drawdown_guard" if "drawdown" in reason else "cooldown"
        return f"protection:{kind}"
    return re.sub(r"[-_@]?[0-9][0-9.e:+-]*.*$", "", reason)


def _bar_value(bar: Candle) -> float:
    return bar.volume * bar.open


def run_event_backtest(
    candles: Sequence[Candle],
    strategy,
    risk: RiskEngine,
    *,
    costs: ExecutionCosts = ExecutionCosts(),
    futures: FuturesTerms = FuturesTerms(),
    initial_equity: float = 10_000.0,
    warmup: Optional[int] = None,
    score_from: Optional[datetime] = None,
    quality_window: Optional[int] = None,
) -> EventBacktestResult:
    """`score_from`: bars before it only feed indicators (walk-forward
    warm-up); no decisions are made before it."""
    if not candles:
        raise ValueError("no candles")
    symbol, timeframe = candles[0].market, candles[0].timeframe
    filters = risk.filters.get(symbol)
    if filters is None:
        raise ValueError(f"no SymbolFilters for {symbol} (fail-closed)")
    if futures.funding is None and not futures.assume_no_funding:
        raise ValueError("funding series missing; pass FuturesTerms(assume_no_funding=True) to accept that explicitly")
    if futures.margin_leverage <= 0:
        raise ValueError("margin_leverage must be > 0")
    warmup = strategy.warmup if warmup is None else warmup
    quality_window = quality_window or getattr(strategy, "quality_window_bars", None) or warmup
    max_hold = getattr(strategy, "max_hold_bars", None)
    max_entries_per_day = getattr(strategy, "max_entries_per_day", None)
    entries_by_day: Counter = Counter()
    assumptions = [
        f"fees taker={costs.taker_fee} maker={costs.maker_fee}",
        f"half_spread={costs.half_spread} (assumed, candles carry no book)",
        (f"impact={costs.impact_coefficient}*sigma_bar(prev {costs.impact_vol_window} bars, floor "
         f"{costs.impact_vol_floor})*sqrt(participation)" if costs.impact_model == "volatility_scaled"
         else f"impact={costs.impact_coefficient}*sqrt(participation) (fixed model)")
        + f", max_participation={costs.max_participation}",
        f"latency={costs.latency_bars} bar(s)", "stop assumed before take-profit when both touched in one bar",
        f"symbol filters for {symbol}: tick={filters.tick_size} step={filters.step_size} "
        f"min_qty={filters.min_quantity} min_notional={filters.min_notional}",
    ]
    if futures.funding is None:
        assumptions.append("NO FUNDING applied (assume_no_funding=True)")
    if not futures.tiers:
        assumptions.append("no maintenance tiers: liquidation NOT modelled")

    issue_times = sorted(i.at for i in check_candles(candles))
    settlements = sorted(futures.funding) if futures.funding else []
    funding_by_slot = _funding_slots(futures)

    equity = initial_equity  # realised
    position: Optional[_Position] = None
    pending: Optional[_PendingEntry] = None
    pending_exit: Optional[tuple[int, str]] = None  # (act_index, reason)
    trades: list[TradeRecord] = []
    curve: list[tuple[datetime, float]] = []
    bar_returns: list[float] = []
    rejected: Counter = Counter()
    missed = partial = liquidations = signals = 0
    closed_for_risk: deque = deque()
    points: deque = deque()
    peak = initial_equity
    day_start_equity = initial_equity
    current_day = None
    prev_mark = initial_equity

    def mark(pos: Optional[_Position], price: float) -> float:
        if pos is None:
            return equity
        return equity + pos.direction * pos.quantity * (price - pos.entry_reference) - pos.fees - pos.spread_cost \
            - pos.slippage_cost - pos.funding

    # sigma of log returns over the `impact_vol_window` bars strictly before bar i (no look-ahead)
    closes_ = [c.close for c in candles]
    rets_ = [0.0] + [math.log(b / a) if a > 0 and b > 0 else 0.0 for a, b in zip(closes_, closes_[1:])]
    sigma_before: list[float] = []
    w = costs.impact_vol_window
    s1 = s2 = 0.0
    for i_ in range(len(candles)):
        # window = rets_[i_-w .. i_-1]
        k = i_ - w
        if i_ - 1 >= 1:
            s1 += rets_[i_ - 1]
            s2 += rets_[i_ - 1] ** 2
        if k >= 1:
            s1 -= rets_[k - 1] if k - 1 >= 1 else 0.0
            s2 -= rets_[k - 1] ** 2 if k - 1 >= 1 else 0.0
        m = min(max(i_ - 1, 0), w)
        var = (s2 - s1 * s1 / m) / (m - 1) if m >= 2 else 0.0
        sigma_before.append(max(math.sqrt(max(var, 0.0)), costs.impact_vol_floor))

    def impact_for(participation: float, bar: Candle) -> float:
        if costs.impact_model == "fixed":
            return costs.impact_coefficient * math.sqrt(participation)
        return costs.impact_coefficient * sigma_by_time[bar.open_time] * math.sqrt(participation)

    sigma_by_time = {c.open_time: sg for c, sg in zip(candles, sigma_before)}

    def market_fill(direction: int, qty: float, reference: float, bar: Candle) -> tuple[float, float, float, float, float]:
        """-> (filled_qty, fill_price, fee, spread_cost, slippage_cost)."""
        bv = _bar_value(bar)
        max_value = costs.max_participation * bv if bv > 0 else 0.0
        value = qty * reference
        filled_qty = qty if value <= max_value else filters.round_quantity(max_value / reference)
        if filled_qty <= 0:
            return 0.0, math.nan, 0.0, 0.0, 0.0
        participation = filled_qty * reference / bv
        impact = impact_for(participation, bar)
        fill = reference * (1 + direction * (costs.half_spread + impact))
        notional = filled_qty * reference
        return filled_qty, fill, filled_qty * fill * costs.taker_fee, notional * costs.half_spread, notional * impact

    def close(pos: _Position, i: int, reference: float, reason: str, liquidity: str, when: datetime) -> None:
        nonlocal equity, position
        bar = candles[i]
        if reason == "liquidation":
            # The isolated margin is lost; entry costs were already paid.
            exit_fill, fee, spread_c, slip_c = reference, 0.0, 0.0, 0.0
            gross = -pos.margin
        elif liquidity == "maker":
            exit_fill, fee, spread_c, slip_c = reference, pos.quantity * reference * costs.maker_fee, 0.0, 0.0
            gross = pos.direction * pos.quantity * (reference - pos.entry_reference)
        else:
            bv = _bar_value(bar)
            participation = min(1.0, pos.quantity * reference / bv) if bv > 0 else 1.0
            impact = impact_for(participation, bar)
            exit_fill = reference * (1 - pos.direction * (costs.half_spread + impact))
            notional = pos.quantity * reference
            fee, spread_c, slip_c = pos.quantity * exit_fill * costs.taker_fee, notional * costs.half_spread, notional * impact
            gross = pos.direction * pos.quantity * (reference - pos.entry_reference)
        fees = pos.fees + fee
        spread_total = pos.spread_cost + spread_c
        slip_total = pos.slippage_cost + slip_c
        net = gross - fees - spread_total - slip_total - pos.funding
        equity += net
        ref = pos.entry_reference
        trades.append(TradeRecord(
            trade_id=len(trades) + 1, strategy_id=strategy.strategy_id, symbol=symbol, direction=pos.direction,
            entry_time=pos.entry_time, exit_time=when, entry_reference=ref, exit_reference=reference,
            entry_fill=pos.entry_fill, exit_fill=exit_fill, quantity=pos.quantity, entry_liquidity=pos.entry_liquidity,
            exit_liquidity=liquidity, filled_fraction=pos.filled_fraction, gross_pnl=gross, fees=fees,
            spread_cost=spread_total, slippage_cost=slip_total, funding=pos.funding, net_pnl=net,
            equity_at_entry=pos.equity_at_entry,
            mfe=max(0.0, pos.direction * (pos.best - ref) / ref),
            mae=max(0.0, -pos.direction * (pos.worst - ref) / ref),
            holding_bars=i - pos.entry_index + (0 if when == bar.open_time else 1), exit_reason=reason,
            regime_at_entry=pos.regime, signal_reason=pos.signal_reason, features_at_entry=pos.features,
            risk_decision_id=pos.risk_decision_id,
        ))
        closed_for_risk.append(ClosedTrade(when, net / pos.equity_at_entry))
        position = None

    def charge_funding(pos: _Position, start: datetime, end: datetime, bar: Candle) -> None:
        """Settlements s with start <= s < end while the position is open."""
        if futures.funding is None:
            return
        # Expected settlement grid (aligned to the epoch in funding_interval steps).
        step = futures.funding_interval.total_seconds()
        first = math.ceil(start.timestamp() / step) * step
        s_ts = first
        while s_ts < end.timestamp():
            s = datetime.fromtimestamp(s_ts, tz=start.tzinfo)
            if s >= pos.entry_time:
                rate = funding_by_slot.get(round(s_ts / step))
                if rate is None or not math.isfinite(rate):
                    raise ValueError(f"funding rate missing at {s.isoformat()} while a position is open (fail-closed)")
                side = PositionSide.LONG if pos.direction > 0 else PositionSide.SHORT
                amount = pos.quantity * bar.open * rate
                pos.funding += amount if side is PositionSide.LONG else -amount
            s_ts += step

    n = len(candles)
    for i in range(n):
        bar = candles[i]
        # 1) pending exit at this bar's open (strategy exit / end)
        if position is not None and pending_exit is not None and pending_exit[0] == i:
            close(position, i, bar.open, pending_exit[1], "taker", bar.open_time)
            pending_exit = None
        # 2) pending entry
        if pending is not None and position is None and i >= pending.act_index:
            if costs.entry_order == "market" or pending.limit_price is None:
                qty, fill, fee, spread_c, slip_c = market_fill(pending.direction, pending.quantity, bar.open, bar)
                reference, liquidity = bar.open, "taker"
            else:
                through = bar.low < pending.limit_price if pending.direction > 0 else bar.high > pending.limit_price
                if through:
                    reference = fill = min(bar.open, pending.limit_price) if pending.direction > 0 else max(bar.open, pending.limit_price)
                    bv = _bar_value(bar)
                    cap_qty = filters.round_quantity(costs.max_participation * bv / reference) if bv > 0 else 0.0
                    qty = min(pending.quantity, cap_qty)
                    fee, spread_c, slip_c, liquidity = qty * fill * costs.maker_fee, 0.0, 0.0, "maker"
                else:
                    qty = 0.0
                    if i >= pending.expires_index:
                        missed += 1
                        pending = None
            if pending is not None and qty > 0:
                if qty < filters.min_quantity or qty * reference < filters.min_notional:
                    rejected["partial_fill_below_exchange_minimum"] += 1
                else:
                    if qty < pending.quantity:
                        partial += 1
                    stop = pending.stop_distance
                    stop_price = filters.round_price(reference - stop if pending.direction > 0 else reference + stop,
                                                     up=pending.direction < 0)
                    tp = pending.take_profit_distance
                    tp_price = None if tp is None else reference + pending.direction * tp
                    margin = qty * reference / futures.margin_leverage
                    liq = None
                    if futures.tiers:
                        try:
                            liq = estimate_liquidation_price(
                                side=PositionSide.LONG if pending.direction > 0 else PositionSide.SHORT,
                                entry_price=reference, position_size=qty, wallet_balance=margin,
                                tiers=list(futures.tiers))
                        except ValueError:
                            liq = None
                    position = _Position(pending.direction, qty, bar.open_time, i, reference, fill, liquidity,
                                         qty / pending.quantity, fee, spread_c, slip_c, stop_price, tp_price,
                                         pending.trailing_distance, liq, margin, mark(None, bar.open),
                                         pending.regime, pending.reason, pending.features, pending.risk_decision_id,
                                         reference, reference, trailing_activation=pending.trailing_activation)
                pending = None
            elif pending is not None and qty <= 0 and costs.entry_order == "market":
                rejected["no_liquidity"] += 1
                pending = None
        # 3) intrabar protective exits, funding, excursions
        if position is not None:
            pos = position
            d = pos.direction
            charge_funding(pos, bar.open_time, bar.close_time, bar)
            hit: Optional[tuple[float, str, str]] = None
            if pos.liquidation_price is not None and (
                    (d > 0 and bar.open <= pos.liquidation_price) or (d < 0 and bar.open >= pos.liquidation_price)):
                hit = (bar.open, "liquidation", "taker")
            elif pos.stop_price is not None and ((d > 0 and bar.open <= pos.stop_price) or (d < 0 and bar.open >= pos.stop_price)):
                hit = (bar.open, "stop_gap", "taker")
            elif pos.stop_price is not None and ((d > 0 and bar.low <= pos.stop_price) or (d < 0 and bar.high >= pos.stop_price)):
                if pos.liquidation_price is not None and (
                        (d > 0 and pos.liquidation_price >= pos.stop_price) or (d < 0 and pos.liquidation_price <= pos.stop_price)):
                    hit = (pos.liquidation_price, "liquidation", "taker")
                else:
                    hit = (pos.stop_price, "stop", "taker")
            elif pos.liquidation_price is not None and (
                    (d > 0 and bar.low <= pos.liquidation_price) or (d < 0 and bar.high >= pos.liquidation_price)):
                hit = (pos.liquidation_price, "liquidation", "taker")
            elif pos.take_profit is not None and ((d > 0 and bar.high > pos.take_profit) or (d < 0 and bar.low < pos.take_profit)):
                hit = (pos.take_profit, "take_profit", "maker")
            if hit is not None:
                # excursion up to the exit level only
                pos.best = max(pos.best, hit[0]) if d > 0 else min(pos.best, hit[0])
                pos.worst = min(pos.worst, hit[0]) if d > 0 else max(pos.worst, hit[0])
                if hit[1] == "liquidation":
                    liquidations += 1
                close(pos, i, hit[0], hit[1], hit[2], bar.close_time)
                pending_exit = None
            else:
                pos.best = max(pos.best, bar.high) if d > 0 else min(pos.best, bar.low)
                pos.worst = min(pos.worst, bar.low) if d > 0 else max(pos.worst, bar.high)
                if pos.trailing_distance is not None and (
                        pos.trailing_activation is None or (pos.best - pos.entry_reference) * d >= pos.trailing_activation):
                    trail = pos.best - d * pos.trailing_distance
                    trail = filters.round_price(trail, up=d < 0)
                    if pos.stop_price is None or (d > 0 and trail > pos.stop_price) or (d < 0 and trail < pos.stop_price):
                        pos.stop_price = trail
        # 4) equity point at the close
        now = bar.close_time
        m = mark(position, bar.close)
        curve.append((now, m))
        bar_returns.append(m / prev_mark - 1 if prev_mark > 0 else 0.0)
        prev_mark = m
        day = utc_day_start(now)
        if day != current_day:
            current_day, day_start_equity = day, (curve[-2][1] if len(curve) > 1 else m)
        peak = max(peak, m)
        points.append(EquityPoint(now, m))
        while points and points[0].at < now - timedelta(days=7):
            points.popleft()
        while closed_for_risk and closed_for_risk[0].closed_at < now - timedelta(days=7):
            closed_for_risk.popleft()
        # 5) decision on the closed bar
        if i < warmup or i + costs.latency_bars >= n:
            continue
        if score_from is not None and bar.open_time < score_from:
            continue
        sig = strategy.signal(PrefixView(candles, i + 1))
        if position is not None:
            if max_hold is not None and pending_exit is None and i - position.entry_index >= max_hold:
                pending_exit = (i + costs.latency_bars, "time_stop")
            if pending_exit is None and ((position.direction > 0 and (sig.exit_long or sig.entry < 0))
                                         or (position.direction < 0 and (sig.exit_short or sig.entry > 0))):
                pending_exit = (i + costs.latency_bars, "signal_exit")
            continue
        if sig.entry == 0 or pending is not None:
            continue
        signals += 1
        if max_entries_per_day is not None and entries_by_day[now.date()] >= max_entries_per_day:
            rejected["entry_cap_per_day"] += 1
            continue
        lo = bisect.bisect_left(issue_times, candles[max(0, i - quality_window)].open_time)
        hi = bisect.bisect_right(issue_times, bar.open_time)
        dq = ("candle_issue_in_window",) if hi > lo else ()
        req = EntryRequest(
            now=now, symbol=symbol, direction=sig.entry, reference_price=bar.close, stop_distance=sig.stop_distance,
            regime=sig.regime, atr=sig.features.get("atr") if sig.features.get("atr") else ind.atr(PrefixView(candles, i + 1), 14),
            spread_fraction=2 * costs.half_spread, feed_health=HealthStatus.HEALTHY, data_quality_reasons=dq,
            strategy_id=strategy.strategy_id, atr_bar=bar.timeframe.delta,
        )
        account = AccountRiskState(m, peak, day_start_equity, tuple(closed_for_risk), tuple(points), 0.0)
        decision = risk.evaluate_entry(req, account)
        if not decision.approved:
            for r in decision.reasons:
                rejected[_reason_key(r)] += 1
            continue
        limit = None
        if costs.entry_order == "limit":
            limit = filters.round_price(bar.close * (1 - sig.entry * costs.limit_offset), up=sig.entry < 0)
        entries_by_day[now.date()] += 1
        act = i + costs.latency_bars
        pending = _PendingEntry(sig.entry, decision.quantity, act, act + costs.limit_ttl_bars - 1, limit,
                                sig.stop_distance, sig.take_profit_distance, sig.trailing_distance, sig.regime,
                                sig.reason, dict(sig.features), decision.decision_id,
                                trailing_activation=sig.trailing_activation)

    if position is not None:
        last = candles[-1]
        close(position, n - 1, last.close, "end_of_data", "taker", last.close_time)
        curve[-1] = (curve[-1][0], equity)
    return EventBacktestResult(
        strategy_id=strategy.strategy_id, symbol=symbol, timeframe=timeframe.value, initial_equity=initial_equity,
        final_equity=equity, trades=tuple(trades), equity_curve=tuple(curve),
        bar_returns=tuple(bar_returns), signals=signals, rejected_entries=dict(rejected), missed_fills=missed,
        partial_fills=partial, liquidations=liquidations, assumptions=tuple(assumptions),
    )
