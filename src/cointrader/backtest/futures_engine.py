"""Bar-by-bar backtest for a single-market, isolated-margin futures
position -- the leveraged, long-or-short sibling of `backtest.engine`.

Same no-look-ahead construction as the spot engine (`PrefixView`,
decide-on-bar-t / execute-at-bar-t+1-open), extended with what leverage
actually changes:

- `target` is notional-to-equity ratio in `[-max_leverage, max_leverage]`
  (negative = short), not `[0, 1]`.
- Every position change re-derives a liquidation price from the isolated
  margin (the whole `equity` backs this one position, matching a
  single-market engine) via `risk.leverage.estimate_liquidation_price`.
- A bar whose low (long) or high (short) crosses the live liquidation
  price force-closes the position at that price for a full loss of the
  posted margin on that fraction, and the position stays flat until the
  strategy opens a fresh one (ADR-0004: no same-bar re-entry).
- Funding is charged/paid once per bar a position is held open, from a
  caller-supplied `funding_rates` series aligned 1:1 with `candles` --
  never estimated (see `backtest.funding`). A bar where a position is
  open but its funding rate is missing/non-finite fails closed (raises),
  the same way an out-of-range strategy exposure does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from cointrader.backtest.costs import CandleCostModel
from cointrader.backtest.engine import PrefixView, _CAP_SAFETY_MARGIN
from cointrader.backtest.funding import apply_funding_payment
from cointrader.data.models import Candle
from cointrader.risk.leverage import MarginTier, PositionSide, estimate_liquidation_price

# Target notional-to-equity ratio; negative = short.
FuturesStrategy = Callable[[Sequence[Candle]], float]


@dataclass(frozen=True)
class FuturesBacktestResult:
    period_returns: tuple[float, ...]
    exposures: tuple[float, ...]  # signed notional/equity actually held after each bar
    trades: int
    total_cost_fraction: float
    liquidity_capped_bars: int
    liquidation_events: int
    total_funding_fraction: float  # net funding paid (positive) or received (negative)

    @property
    def total_return(self) -> float:
        growth = 1.0
        for r in self.period_returns:
            growth *= 1 + r
        return growth - 1


def _side(target: float) -> PositionSide:
    return PositionSide.LONG if target > 0 else PositionSide.SHORT


def run_futures_backtest(
    candles: Sequence[Candle],
    strategy: FuturesStrategy,
    funding_rates: Sequence[Optional[float]],
    *,
    max_leverage: float = 3.0,
    tiers: "list[MarginTier]",
    cost_model: CandleCostModel = CandleCostModel(),
    equity: float = 10_000_000.0,
    warmup: int = 0,
) -> FuturesBacktestResult:
    if len(funding_rates) != len(candles):
        raise ValueError("funding_rates must be the same length as candles (one per bar, fail-closed)")
    if max_leverage <= 0:
        raise ValueError("max_leverage must be > 0")
    if len(candles) < warmup + 3:
        return FuturesBacktestResult((), (), 0, 0.0, 0, 0, 0.0)

    exposure = 0.0  # signed notional/equity currently held
    side: Optional[PositionSide] = None
    liq_price: Optional[float] = None
    entry_price: Optional[float] = None
    position_size = 0.0  # base-asset quantity, unsigned
    liquidated_flat = False  # true after a forced liquidation, until the strategy re-enters

    returns: list[float] = []
    exposures: list[float] = []
    trades = 0
    liquidity_capped_bars = 0
    liquidation_events = 0
    total_cost = 0.0
    total_funding = 0.0

    for t in range(warmup, len(candles) - 2):
        target = strategy(PrefixView(candles, t + 1))
        if not math.isfinite(target) or not -max_leverage <= target <= max_leverage:
            raise ValueError(f"strategy returned exposure {target!r}; must be within [-{max_leverage}, {max_leverage}]")
        entry_bar, exit_bar = candles[t + 1], candles[t + 2]
        cost = 0.0

        if liquidated_flat and target != 0.0:
            liquidated_flat = False  # strategy is opening a fresh position

        if target != exposure:
            desired_delta = target - exposure
            desired_notional = abs(desired_delta) * equity
            bar_value = entry_bar.volume * entry_bar.open
            max_notional = cost_model.max_participation * bar_value if bar_value > 0 else 0.0
            filled_notional = min(desired_notional, max_notional * _CAP_SAFETY_MARGIN)
            if filled_notional < desired_notional:
                liquidity_capped_bars += 1
            if filled_notional > 0:
                filled_delta = filled_notional / equity * (1.0 if desired_delta > 0 else -1.0)
                cost = cost_model.cost_fraction(filled_notional, bar_value) * abs(filled_delta)
                exposure += filled_delta
                trades += 1
                if exposure == 0.0:
                    side, liq_price, entry_price, position_size = None, None, None, 0.0
                else:
                    side = _side(exposure)
                    entry_price = entry_bar.open
                    position_size = abs(exposure) * equity / entry_price
                    liq_price = estimate_liquidation_price(
                        side=side,
                        entry_price=entry_price,
                        position_size=position_size,
                        wallet_balance=equity,
                        tiers=tiers,
                    )

        gross = 0.0
        funding_amount_fraction = 0.0
        liquidated_this_bar = False

        if exposure != 0.0 and side is not None and liq_price is not None and entry_price is not None:
            # The return realized this iteration spans entry_bar's own
            # open-to-close path (exit_bar's open is just where it is
            # marked), so entry_bar's low/high is what could touch the
            # liquidation price during this holding period.
            crossed = (side is PositionSide.LONG and entry_bar.low <= liq_price) or (
                side is PositionSide.SHORT and entry_bar.high >= liq_price
            )
            if crossed:
                gross = -abs(exposure)  # full loss of posted margin on this fraction
                liquidation_events += 1
                liquidated_this_bar = True
                exposure, side, liq_price, entry_price, position_size = 0.0, None, None, None, 0.0
                liquidated_flat = True
            else:
                gross = exposure * (exit_bar.open / entry_bar.open - 1)
                rate = funding_rates[t + 1]
                if rate is None or not math.isfinite(rate):
                    raise ValueError(
                        f"position open at bar {t + 1} but funding_rates[{t + 1}] is missing/invalid (fail-closed)"
                    )
                payment = apply_funding_payment(
                    side=side, position_size=position_size, mark_price=entry_bar.open, funding_rate=rate
                )
                funding_amount_fraction = payment.amount / equity
                gross -= funding_amount_fraction

        returns.append(gross - cost)
        exposures.append(exposure)
        total_cost += cost
        total_funding += funding_amount_fraction
        if liquidated_this_bar:
            pass  # already folded into `gross`; nothing further to accumulate

    return FuturesBacktestResult(
        tuple(returns), tuple(exposures), trades, total_cost, liquidity_capped_bars, liquidation_events, total_funding
    )
