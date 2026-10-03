"""LiveBroker: the `Broker` that would send real orders to Binance.

It is a scaffold that CANNOT send an order unless the existing live
safety gate (`live.safety_gate.evaluate_safety_gate`, a protected,
unmodified file) passes at that moment. The gate requires, all at once:
environment == "live", `live_trading_enabled`, a valid human
`LiveActivationApproval`, a strategy status of APPROVED/DEPLOYED, every
risk limit configured, kill switch OFF, exchange and data feed HEALTHY,
and account and position state known.

The caller supplies `gate_context`, a function that builds a fresh
`SafetyGateContext` for every order. This module never constructs an
approval, never releases the kill switch, never promotes a strategy, and
never retries a refused order. How a running process obtains the human
approval object is intentionally NOT implemented here -- it is a
safety-boundary decision for the account owner (ADR-0015).

Default everywhere in this repository is paper; nothing instantiates a
LiveBroker unless a human configures live mode.
"""

from __future__ import annotations

from typing import Callable, Optional

from cointrader.execution.binance_client import BinanceApiError, BinanceFuturesClient
from cointrader.execution.models import AccountSnapshot, OrderIntent, OrderState, OrderStatus, PositionSnapshot
from cointrader.live.config import HealthStatus
from cointrader.live.safety_gate import SafetyGateContext, SafetyGateResult, evaluate_safety_gate


class LiveOrderRefused(RuntimeError):
    pass


class LiveBroker:
    mode = "live"

    def __init__(self, client: BinanceFuturesClient, gate_context: Callable[[], SafetyGateContext],
                 margin_policy=None) -> None:
        self._client = client
        self._margin_policy = margin_policy  # risk.margin_policy.MarginPolicy | None (ADR-0037)
        self._gate_context = gate_context
        self.last_gate: Optional[SafetyGateResult] = None

    def preflight(self, intent: OrderIntent) -> Optional[str]:
        result = evaluate_safety_gate(self._gate_context())
        self.last_gate = result
        if not result.passed:
            return "safety gate failed: " + ", ".join(result.failed_conditions)
        if self._margin_policy is not None and not intent.reduce_only:
            # Exits are never blocked by a settings mismatch; only new exposure is.
            from cointrader.risk.margin_policy import margin_settings_mismatches
            try:
                bad = margin_settings_mismatches(self._client.symbol_config(intent.symbol), [intent.symbol],
                                                 self._margin_policy)
            except BinanceApiError as exc:
                return f"margin settings unreadable: {exc}"
            if bad:
                return "margin settings differ from policy: " + "; ".join(bad)
        return None

    def submit(self, intent: OrderIntent) -> OrderStatus:
        refusal = self.preflight(intent)  # evaluated again right before sending, never cached
        if refusal:
            raise LiveOrderRefused(refusal)
        try:
            return self._client._new_order(intent)
        except BinanceApiError as exc:
            return OrderStatus(intent.client_order_id, OrderState.REJECTED, detail=str(exc))

    def cancel(self, symbol: str, client_order_id: str) -> OrderStatus:
        # Cancelling reduces risk; still requires the exchange to be reachable, not the full gate.
        return self._client._cancel_order(symbol, client_order_id)

    def query(self, symbol: str, client_order_id: str) -> OrderStatus:
        return self._client.query_order(symbol, client_order_id)

    def open_orders(self, symbol: Optional[str] = None) -> list[OrderStatus]:
        return self._client.open_orders(symbol)

    def positions(self) -> list[PositionSnapshot]:
        return self._client.positions()

    def account(self) -> AccountSnapshot:
        return self._client.account()

    def health(self) -> HealthStatus:
        try:
            self._client.ping()
            return HealthStatus.HEALTHY
        except (ConnectionError, BinanceApiError):
            return HealthStatus.UNAVAILABLE
