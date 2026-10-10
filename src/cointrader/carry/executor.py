"""Two-leg carry executor: the interface the runner talks to, and its paper implementation (ADR-0067).

`CarryExecutor` is the only thing `runner.CarryRunner` calls to change positions, so a future live executor
(Binance spot + USD-M perp) can replace `PaperCarryExecutor` without touching the decision code. There is
no live implementation, on purpose. All money is USDT. The perp leg is a short on isolated margin.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CarryCosts:
    spot_fee: float = 0.001      # taker fraction of notional
    perp_fee: float = 0.0005
    slippage: float = 0.0        # per leg, per fill, fraction of price (0.30% round trip = fees only)

    def __post_init__(self) -> None:
        for n in ("spot_fee", "perp_fee", "slippage"):
            if not 0 <= getattr(self, n) < 0.05:
                raise ValueError(f"{n} must be in [0, 0.05)")


class CarryExecutor(Protocol):
    def open_pair(self, capital: float, margin_frac: float, spot: float, perp: float) -> None: ...
    def close_pair(self, spot: float, perp: float) -> None: ...
    def shrink_and_top_up(self, coins: float, spot: float, perp: float) -> None: ...
    def credit_funding(self, rate: float, mark: float) -> None: ...
    def liquidate_perp(self) -> None: ...
    def sell_spot(self, spot: float) -> None: ...


class PaperCarryExecutor:
    def __init__(self, cash: float, costs: CarryCosts = CarryCosts()) -> None:
        if cash <= 0:
            raise ValueError("cash must be positive")
        self.costs = costs
        self.cash = cash
        self.q_spot = 0.0
        self.q_perp = 0.0          # coins short
        self.perp_entry = 0.0
        self.margin = 0.0          # isolated wallet, excludes unrealised pnl
        self.fees_paid = 0.0
        self.funding_received = 0.0
        self.trades = 0

    # -- valuation
    def unrealised(self, perp: float) -> float:
        return self.q_perp * (self.perp_entry - perp)

    def wallet(self, perp: float) -> float:
        """Isolated margin balance as the exchange sees it (margin + unrealised)."""
        return self.margin + self.unrealised(perp)

    def equity(self, spot: float, perp: float) -> float:
        return self.cash + self.q_spot * spot + self.margin + self.unrealised(perp)

    @property
    def in_position(self) -> bool:
        return self.q_perp > 0 or self.q_spot > 0

    # -- fills
    def _fee(self, notional: float, rate: float) -> float:
        fee = notional * rate
        self.fees_paid += fee
        return fee

    def open_pair(self, capital: float, margin_frac: float, spot: float, perp: float) -> None:
        if self.in_position:
            raise RuntimeError("already in position")
        c = self.costs
        capital = min(capital, self.cash)
        sbuy, psell = spot * (1 + c.slippage), perp * (1 - c.slippage)
        # capital = spot cost + fees + margin; spot notional N, perp notional N (equal coins)
        n = capital / (1 + c.spot_fee + c.perp_fee + margin_frac)
        q = n / sbuy
        self.cash -= capital
        self.q_spot = q
        self._fee(n, c.spot_fee)
        self.q_perp, self.perp_entry = q, psell
        self._fee(q * psell, c.perp_fee)
        self.margin = margin_frac * q * psell
        # the unspent rounding (slippage on the perp fill) stays in cash
        self.cash += capital - (n * (1 + c.spot_fee) + q * psell * c.perp_fee + self.margin)
        self.trades += 2

    def close_pair(self, spot: float, perp: float) -> None:
        c = self.costs
        proceeds = self.q_spot * spot * (1 - c.slippage)
        self._fee(proceeds, c.spot_fee)
        buy = perp * (1 + c.slippage)
        realised = self.q_perp * (self.perp_entry - buy)
        self._fee(self.q_perp * buy, c.perp_fee)
        self.cash += (proceeds * (1 - c.spot_fee) + self.margin + realised
                      - self.q_perp * buy * c.perp_fee)
        self.q_spot = self.q_perp = self.perp_entry = self.margin = 0.0
        self.trades += 2

    def shrink_and_top_up(self, coins: float, spot: float, perp: float) -> None:
        """Sell `coins` of spot and buy back the same coins of perp, moving the spot proceeds (and the
        realised perp pnl) into margin. Keeps the legs equal in coins while raising the margin ratio."""
        c = self.costs
        coins = min(coins, self.q_perp, self.q_spot)
        proceeds = coins * spot * (1 - c.slippage)
        fee_s = self._fee(proceeds, c.spot_fee)
        buy = perp * (1 + c.slippage)
        realised = coins * (self.perp_entry - buy)
        fee_p = self._fee(coins * buy, c.perp_fee)
        self.q_spot -= coins
        self.q_perp -= coins
        self.margin += proceeds - fee_s + realised - fee_p
        self.trades += 2

    def credit_funding(self, rate: float, mark: float) -> None:
        """rate > 0: shorts receive. Paid into / out of the isolated margin balance."""
        amt = self.q_perp * mark * rate
        self.margin += amt
        self.funding_received += amt

    def liquidate_perp(self) -> None:
        """Exchange liquidation: the isolated margin is gone and the perp leg is closed. Spot stays."""
        self.margin = 0.0
        self.q_perp = self.perp_entry = 0.0

    def sell_spot(self, spot: float) -> None:
        proceeds = self.q_spot * spot * (1 - self.costs.slippage)
        self.cash += proceeds - self._fee(proceeds, self.costs.spot_fee)
        self.q_spot = 0.0
        self.trades += 1
