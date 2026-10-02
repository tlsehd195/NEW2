"""Multi-coin portfolio backtest for `CrossSectionalStrategy` candidates
(ADR-0017), on USDⓈ-M perpetual bars with full costs and funding.

Timing
    Weights are decided at the close of a rebalance bar from bars that
    had closed by then, and executed at the NEXT bar's open. A coin is
    ranked only if it has `warmup + 1` consecutive bars ending on the
    decision bar and funding records for every settlement it would be
    held through until the next rebalance (a coin whose costs cannot be
    measured is not traded -- fail-closed). The funding check looks at
    which archive records exist, never at their values.

Costs (same model as `backtest.event_engine`)
    fill = open * (1 +/- (half_spread + impact)), impact =
    impact_coefficient * sigma_bar * sqrt(order value / bar traded value)
    with sigma from the previous `impact_vol_window` bars; taker fee on
    the filled notional. Orders above `max_participation` of the bar's
    traded value are partially filled (counted). Funding: every
    settlement in `[open_t, open_t+1)` charges qty * open_t * rate to a
    long and credits it to a short (the settlement price is approximated
    by that bar's open; stated in `assumptions`).

Risk
    Target gross exposure is capped at `RiskConfig.max_leverage`. If
    equity falls `RiskConfig.max_drawdown` below its peak, every position
    is closed at the next open and the run stays flat (halt). The
    single-symbol rules (per-trade stop sizing, ATR and spread kills,
    stoploss/drawdown guards) do not apply to a weekly portfolio and are
    listed as not applied.

Missing data
    A held coin with no bar on a later day is closed at its last close
    (counted as `forced_closes`; it can only be an optimistic price for a
    long, which is stated). A held coin whose funding record is missing
    (a residual left by a participation-capped exit on a dying market, a
    settled contract, an archive hole) is closed at that bar's open with
    the full uncapped impact (`funding_gap_closes`).
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Mapping, Optional, Sequence

from cointrader.backtest.event_engine import FUNDING_TIME_TOLERANCE, ExecutionCosts
from cointrader.data.models import Candle
from cointrader.risk.engine import RiskConfig
from cointrader.strategies.cross_sectional import CoinHistory, CrossSectionalStrategy

ENGINE_VERSION = "1.0.0"
FUNDING_INTERVAL = timedelta(hours=8)
STALE_BARS = 5


@dataclass
class XsecBacktestResult:
    strategy_id: str
    initial_equity: float
    final_equity: float
    equity_curve: list[tuple[datetime, float]]
    rebalances: int = 0
    turnover: float = 0.0  # traded notional / average equity
    fees: float = 0.0
    spread: float = 0.0
    slippage: float = 0.0
    funding: float = 0.0  # paid (+) / received (-)
    gross_pnl: float = 0.0
    partial_fills: int = 0
    forced_closes: int = 0
    halted_at: Optional[datetime] = None
    flat_rebalances: int = 0  # fewer than min_assets eligible coins
    ineligible_no_funding: int = 0
    funding_gap_closes: int = 0  # held coin closed because its funding record was missing
    ineligible_stale: int = 0  # no trading (zero volume or a frozen price) inside the ranking window
    avg_positions: float = 0.0
    assumptions: tuple[str, ...] = ()

    @property
    def total_return(self) -> float:
        return self.final_equity / self.initial_equity - 1

    def summary(self) -> dict:
        eq = [e for _, e in self.equity_curve]
        rets = [b / a - 1 for a, b in zip(eq, eq[1:]) if a > 0]
        peak, mdd = -math.inf, 0.0
        for e in eq:
            peak = max(peak, e)
            mdd = max(mdd, 1 - e / peak) if peak > 0 else mdd
        sharpe = None
        if len(rets) >= 2:
            m = math.fsum(rets) / len(rets)
            sd = math.sqrt(math.fsum((r - m) ** 2 for r in rets) / (len(rets) - 1))
            sharpe = m / sd * math.sqrt(365) if sd > 0 else None
        return {
            "total_return": self.total_return, "max_drawdown": mdd, "sharpe_annualized_daily": sharpe,
            "rebalances": self.rebalances, "flat_rebalances": self.flat_rebalances, "turnover": self.turnover,
            "avg_positions": self.avg_positions, "halted_at": self.halted_at.isoformat() if self.halted_at else None,
            "forced_closes": self.forced_closes, "partial_fills": self.partial_fills,
            "ineligible_no_funding": self.ineligible_no_funding, "ineligible_stale": self.ineligible_stale,
            "funding_gap_closes": self.funding_gap_closes,
            "cost_breakdown": {"gross_pnl": self.gross_pnl, "fees": self.fees, "spread": self.spread,
                               "slippage": self.slippage, "funding": self.funding,
                               "net_pnl": self.final_equity - self.initial_equity},
            "assumptions": list(self.assumptions),
        }


class FundingSeries:
    """One coin's recorded settlements, at whatever interval the exchange
    used at the time (Binance moved many contracts from 8h to 4h or 1h
    funding; every recorded settlement is charged, none is assumed)."""

    def __init__(self, funding: Optional[Mapping[datetime, float]]) -> None:
        items = sorted((funding or {}).items())
        self.times = [t for t, _ in items]
        self.rates = [r for _, r in items]

    def charges(self, start: datetime, end: datetime) -> list[float]:
        """Rates of the settlements in [start, end)."""
        return self.rates[bisect.bisect_left(self.times, start):bisect.bisect_left(self.times, end)]

    def covers(self, start: datetime, end: datetime) -> bool:
        """True if no gap between consecutive records around [start, end)
        exceeds the longest interval (8h) plus the stamp tolerance -- i.e.
        no settlement inside the range can be missing from the record."""
        if end <= start:
            return True
        limit = FUNDING_INTERVAL + FUNDING_TIME_TOLERANCE
        lo = bisect.bisect_right(self.times, start) - 1  # last record at or before start
        if lo < 0 or start - self.times[lo] > limit:
            return False
        hi = bisect.bisect_left(self.times, end)
        seen = self.times[lo:hi + 1]
        if hi >= len(self.times):  # nothing recorded at or after end: the last one must reach it
            if end - self.times[-1] > limit:
                return False
        return all(b - a <= limit for a, b in zip(seen, seen[1:]))


def _decide(strategy, bars, by_time, slots, today, t, delta, end, assume_no_funding, res) -> dict[str, float]:
    """Target weights at t's close from bars closed by then. A coin needs
    `warmup + 1` consecutive bars that all traded (volume > 0, the price
    moved at least once in the last `STALE_BARS`), and funding records for
    every settlement until the next rebalance executes. A halted or frozen
    market cannot be traded, so it is not ranked (fail-closed)."""
    nxt = t + delta
    until = min(nxt + strategy.rebalance_bars * delta, end)
    history = {}
    for s, b in today.items():
        i = by_time[s][t]
        lo = i - strategy.warmup
        if lo < 0 or b.open_time - bars[s][lo].open_time != strategy.warmup * delta:
            continue  # not enough consecutive history
        if not assume_no_funding and not slots[s].covers(nxt, until):
            res.ineligible_no_funding += 1
            continue
        window = bars[s][lo:i + 1]
        recent = [c.close for c in window[-STALE_BARS:]]
        if any(c.volume <= 0 for c in window) or max(recent) == min(recent):
            res.ineligible_stale += 1
            continue
        history[s] = CoinHistory(closes=tuple(c.close for c in window),
                                 dollar_volumes=tuple(c.close * c.volume for c in window))
    weights = strategy.target_weights(history)
    if not weights:
        res.flat_rebalances += 1
    return weights


def run_xsec_backtest(
    candles: Mapping[str, Sequence[Candle]],
    strategy: CrossSectionalStrategy,
    risk: RiskConfig,
    *,
    score_from: datetime,
    end: datetime,
    funding: Optional[Mapping[str, Mapping[datetime, float]]] = None,
    assume_no_funding: bool = False,
    costs: ExecutionCosts = ExecutionCosts(),
    initial_equity: float = 10_000.0,
) -> XsecBacktestResult:
    """`candles[symbol]` are closed bars of one timeframe sorted by time;
    bars before `score_from` only warm the ranking up. The run starts
    flat at `score_from`'s open and closes everything at the last bar's
    close before `end`."""
    if funding is None and not assume_no_funding:
        raise ValueError("funding records are required (or pass assume_no_funding=True, reported)")
    if not candles:
        raise ValueError("no candles")
    tfs = {c.timeframe for bars in candles.values() for c in bars}
    if len(tfs) != 1:
        raise ValueError("every coin must use the same timeframe")
    delta = next(iter(tfs)).delta
    bars = {s: [c for c in b if c.open_time < end] for s, b in candles.items()}
    by_time = {s: {c.open_time: i for i, c in enumerate(b)} for s, b in bars.items()}
    calendar = sorted({c.open_time for b in bars.values() for c in b})
    slots = {s: FundingSeries((funding or {}).get(s)) for s in bars}

    # sigma of log returns over the previous window, known before each bar opens
    sigma: dict[str, list[float]] = {}
    for s, b in bars.items():
        lr = [0.0] + [math.log(b[i].close / b[i - 1].close) for i in range(1, len(b))]
        out = []
        for i in range(len(b)):
            w = lr[max(1, i - costs.impact_vol_window):i]
            if len(w) >= 2:
                m = sum(w) / len(w)
                sd = math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1))
            else:
                sd = 0.0
            out.append(max(sd, costs.impact_vol_floor))
        sigma[s] = out

    res = XsecBacktestResult(strategy.strategy_id, initial_equity, initial_equity, [])
    assumptions = [
        f"xsec engine {ENGINE_VERSION}; decisions at bar close, fills at next open",
        "funding settlement price approximated by the bar's open",
        "fractional quantities; exchange min-notional/step filters not applied",
        "per-trade stop sizing, ATR/spread kills and stoploss/drawdown guards not applied (portfolio rules: "
        "gross <= max_leverage, max_drawdown halt)",
        "a held coin with a missing later bar is closed at its last close",
        "a held coin whose funding record is missing is closed at that bar's open, uncapped "
        "(10% impact assumed if the bar has no volume)",
    ]
    if assume_no_funding:
        assumptions.append("ASSUMPTION: no funding charged (assume_no_funding=True)")

    cash = initial_equity
    qty: dict[str, float] = {}
    last_close: dict[str, float] = {}
    pending: Optional[dict[str, float]] = None
    peak = initial_equity
    halted = False
    traded_notional = 0.0
    positions_count = []
    first_scored = bisect.bisect_left(calendar, score_from)
    if first_scored >= len(calendar):
        raise ValueError("no bars at or after score_from")

    def fill(sym: str, dq: float, bar: Candle, i: int) -> float:
        """Trade dq at the bar's open; returns the filled quantity."""
        nonlocal cash, traded_notional
        ref = bar.open
        value = abs(dq) * ref
        bv = bar.volume * (bar.open + bar.close) / 2
        cap = costs.max_participation * bv
        if value > cap:
            res.partial_fills += 1
            dq = math.copysign(cap / ref, dq) if cap > 0 else 0.0
            value = abs(dq) * ref
        if dq == 0:
            return 0.0
        impact = costs.impact_coefficient * sigma[sym][i] * math.sqrt(value / bv) if bv > 0 else 0.0
        price = ref * (1 + math.copysign(1, dq) * (costs.half_spread + impact))
        fee = abs(dq) * price * costs.taker_fee
        cash -= dq * price + fee
        res.fees += fee
        res.spread += value * costs.half_spread
        res.slippage += value * impact
        traded_notional += value
        qty[sym] = qty.get(sym, 0.0) + dq
        if abs(qty[sym]) < 1e-12:
            del qty[sym]
        return dq

    def force_close(sym: str, bar: Candle, i: int) -> None:
        nonlocal cash, traded_notional
        q = qty.pop(sym)
        value = abs(q) * bar.open
        bv = bar.volume * (bar.open + bar.close) / 2
        impact = costs.impact_coefficient * sigma[sym][i] * math.sqrt(value / bv) if bv > 0 else 0.1
        price = bar.open * (1 - math.copysign(1, q) * (costs.half_spread + impact))
        fee = abs(q) * price * costs.taker_fee
        cash += q * price - fee
        res.fees += fee
        res.spread += value * costs.half_spread
        res.slippage += value * impact
        traded_notional += value
        res.forced_closes += 1

    def equity_at(prices: Mapping[str, float]) -> float:
        return cash + sum(q * prices[s] for s, q in qty.items())

    # the bar before score_from only decides the first weights (executed at score_from's open)
    for k in range(max(first_scored - 1, 0), len(calendar)):
        t = calendar[k]
        today = {s: bars[s][by_time[s][t]] for s in bars if t in by_time[s]}
        if k < first_scored:
            last_close.update({s: b.close for s, b in today.items()})
            pending = _decide(strategy, bars, by_time, slots, today, t, delta, end, assume_no_funding, res)
            continue
        # a held coin without a bar today: close at its last close
        for s in [s for s in qty if s not in today]:
            q = qty.pop(s)
            fee = abs(q) * last_close[s] * costs.taker_fee
            cash += q * last_close[s] - fee
            res.fees += fee
            res.forced_closes += 1
        opens = {s: b.open for s, b in today.items()}
        # halt check on the open, then execute what was decided at the last close
        eq_open = equity_at(opens)
        if not halted and eq_open < peak * (1 - risk.max_drawdown):
            halted = True
            res.halted_at = t
            pending = {}
        if pending is not None:
            gross = sum(abs(w) for w in pending.values())
            scale = min(1.0, risk.max_leverage / gross) if gross > 0 else 1.0
            for s in sorted(set(qty) | set(pending)):
                if s not in today:
                    continue
                target = pending.get(s, 0.0) * scale * eq_open / today[s].open
                dq = target - qty.get(s, 0.0)
                if abs(dq) * today[s].open > 1e-9 * max(eq_open, 1.0):
                    fill(s, dq, today[s], by_time[s][t])
            pending = None
            res.rebalances += 1
        # funding for settlements inside this bar. A held coin whose funding
        # record is missing (contract settled or delisted, or an archive hole)
        # cannot be measured, so it is closed at this open, before any of the
        # bar's settlements, with the full uncapped impact of the exit.
        for s, q in ([] if assume_no_funding else list(qty.items())):
            if not slots[s].covers(t, t + delta):
                force_close(s, today[s], by_time[s][t])
                res.funding_gap_closes += 1
                continue
            for rate in slots[s].charges(t, t + delta):
                pay = q * today[s].open * rate
                cash -= pay
                res.funding += pay
        last_close.update({s: b.close for s, b in today.items()})
        eq = equity_at(last_close)
        peak = max(peak, eq)
        res.equity_curve.append((t, eq))
        positions_count.append(len(qty))
        # decide at the close of a rebalance bar (not on the last bar: nothing left to execute on)
        is_rebalance = ((t + delta - score_from) // delta) % strategy.rebalance_bars == 0
        if is_rebalance and not halted and k + 1 < len(calendar):
            pending = _decide(strategy, bars, by_time, slots, today, t, delta, end, assume_no_funding, res)

    # close everything at the last close
    for s in list(qty):
        q = qty.pop(s)
        fee = abs(q) * last_close[s] * (costs.taker_fee + costs.half_spread)
        cash += q * last_close[s] - fee
        res.fees += abs(q) * last_close[s] * costs.taker_fee
        res.spread += abs(q) * last_close[s] * costs.half_spread
    res.final_equity = cash
    res.gross_pnl = cash - initial_equity + res.fees + res.spread + res.slippage + res.funding
    if res.equity_curve:
        res.equity_curve[-1] = (res.equity_curve[-1][0], cash)
    avg_eq = sum(e for _, e in res.equity_curve) / len(res.equity_curve) if res.equity_curve else initial_equity
    res.turnover = traded_notional / avg_eq if avg_eq > 0 else 0.0
    res.avg_positions = sum(positions_count) / len(positions_count) if positions_count else 0.0
    res.assumptions = tuple(assumptions)
    return res
