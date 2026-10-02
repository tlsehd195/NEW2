"""KRW -> USDT -> overseas-exchange fund bridge.

One human-initiated transfer at a time ("press the button", ADR-0003) --
never a scheduler, never retried automatically. The flow is always:

    1. `quote_transfer`   -- ask the domestic exchange what this KRW
                             amount buys right now, no order placed.
    2. a human reviews the quote and runs
       `scripts/run_fund_transfer.py`, which asks for the confirmation
       phrase interactively (same discipline as
       `scripts/grant_live_approval.py`) and produces a
       `FundTransferApproval` bound to that exact `quote_id`.
    3. `execute_transfer` checks the approval matches this quote, is not
       expired, and is within the configured per-transfer limit, then
       buys USDT and withdraws it.

**No real exchange client is wired in here.** `DomesticExchangeClient`
and `WithdrawalClient` are Protocols; a real implementation needs the
user's own API keys and, on the withdrawal side, an address the
domestic exchange has already verified under the Travel Rule -- Upbit in
particular only allows withdrawal to addresses/VASPs it has cleared, so
whether a direct Upbit -> Binance withdrawal is even possible depends on
account-specific verification this session cannot check. Confirm that
with Upbit's own docs/support before relying on this for a real
transfer (ADR-0002, ADR-0003).

This file is protected by `.claude/hooks/protect-safety-files.sh`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Protocol

from cointrader._time import require_aware
from cointrader.funding.approval import FundTransferApproval


@dataclass(frozen=True)
class BridgeConfig:
    # None = not configured, which is a fail-closed condition (same
    # convention as risk/sizing.py and live/config.py): a transfer limit
    # must be set explicitly before any transfer can execute.
    max_krw_per_transfer: Optional[float] = None
    quote_validity: timedelta = timedelta(minutes=5)


class DomesticExchangeClient(Protocol):
    def quote_buy_usdt(self, krw_amount: float) -> tuple[float, float]:
        """Returns (estimated_usdt, price_krw_per_usdt), no order placed."""
        ...

    def execute_buy_usdt(self, krw_amount: float, quote_id: str) -> "PurchaseReceipt": ...


class WithdrawalClient(Protocol):
    def withdraw_usdt(self, usdt_amount: float, destination_label: str, quote_id: str) -> "WithdrawalReceipt": ...


@dataclass(frozen=True)
class PurchaseReceipt:
    filled_usdt: float
    price_krw_per_usdt: float
    fee_krw: float
    external_id: str


@dataclass(frozen=True)
class WithdrawalReceipt:
    sent_usdt: float
    network_fee_usdt: float
    external_id: str


@dataclass(frozen=True)
class FundTransferQuote:
    quote_id: str
    krw_amount: float
    destination_label: str
    estimated_usdt: float
    price_krw_per_usdt: float
    quoted_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_aware("FundTransferQuote.quoted_at", self.quoted_at)
        require_aware("FundTransferQuote.expires_at", self.expires_at)
        if self.krw_amount <= 0:
            raise ValueError("krw_amount must be positive")
        if not self.destination_label:
            raise ValueError("destination_label must not be empty")

    def is_expired(self, as_of: datetime) -> bool:
        require_aware("as_of", as_of)
        return as_of >= self.expires_at


@dataclass(frozen=True)
class FundTransferRecord:
    quote: FundTransferQuote
    purchase: PurchaseReceipt
    withdrawal: WithdrawalReceipt
    approved_by: str
    executed_at: datetime


def quote_transfer(
    krw_amount: float, destination_label: str, domestic_client: DomesticExchangeClient,
    *, now: datetime, config: BridgeConfig = BridgeConfig(),
) -> FundTransferQuote:
    require_aware("now", now)
    if krw_amount <= 0:
        raise ValueError("krw_amount must be positive")
    if not destination_label:
        raise ValueError("destination_label must not be empty")
    estimated_usdt, price = domestic_client.quote_buy_usdt(krw_amount)
    return FundTransferQuote(
        quote_id=uuid.uuid4().hex, krw_amount=krw_amount, destination_label=destination_label,
        estimated_usdt=estimated_usdt, price_krw_per_usdt=price,
        quoted_at=now, expires_at=now + config.quote_validity,
    )


def execute_transfer(
    quote: FundTransferQuote, approval: FundTransferApproval,
    domestic_client: DomesticExchangeClient, withdrawal_client: WithdrawalClient,
    *, now: datetime, config: BridgeConfig,
) -> FundTransferRecord:
    """Human-only: requires a `FundTransferApproval` bound to this exact
    `quote.quote_id`. Fail-closed on an unconfigured or exceeded limit, an
    expired quote, or a mismatched/invalid approval -- never guesses or
    proceeds partially."""
    require_aware("now", now)
    if not isinstance(approval, FundTransferApproval) or not approval.is_valid():
        raise ValueError("execute_transfer requires a valid FundTransferApproval")
    if approval.quote_id != quote.quote_id:
        raise ValueError("approval does not match this quote (possible replay against a different quote)")
    if quote.is_expired(now):
        raise ValueError(f"quote {quote.quote_id} expired at {quote.expires_at.isoformat()}")
    if config.max_krw_per_transfer is None:
        raise ValueError("BridgeConfig.max_krw_per_transfer is not configured; refusing to transfer")
    if quote.krw_amount > config.max_krw_per_transfer:
        raise ValueError(
            f"krw_amount {quote.krw_amount} exceeds configured max_krw_per_transfer "
            f"{config.max_krw_per_transfer}"
        )

    purchase = domestic_client.execute_buy_usdt(quote.krw_amount, quote.quote_id)
    withdrawal = withdrawal_client.withdraw_usdt(purchase.filled_usdt, quote.destination_label, quote.quote_id)
    return FundTransferRecord(
        quote=quote, purchase=purchase, withdrawal=withdrawal,
        approved_by=approval.approved_by, executed_at=now,
    )
