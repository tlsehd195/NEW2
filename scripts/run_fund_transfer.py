#!/usr/bin/env python3
"""Human-only: quotes, confirms and executes one KRW -> USDT -> overseas
fund transfer.

    python3 scripts/run_fund_transfer.py --krw-amount 1000000 \
        --destination binance_futures_wallet --max-krw-per-transfer 5000000

Must be run interactively, in a terminal, by the account owner. It never
accepts the confirmation phrase as an argument, so no script or agent can
supply it, and it never retries or schedules itself -- one run is one
transfer.

**No real exchange client is wired in.** `--dry-run` (the default) uses
an in-memory stub so you can see the flow end to end; a real transfer
needs your own `DomesticExchangeClient`/`WithdrawalClient` implementation
plugged in here, with your own API keys (never hardcode them) -- and,
per ADR-0002/ADR-0003, you should confirm with Upbit that the destination
address is Travel-Rule-verified before relying on this at all.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.funding.approval import REQUIRED_CONFIRMATION_TOKEN, FundTransferApproval  # noqa: E402
from cointrader.funding.bridge import (  # noqa: E402
    BridgeConfig, PurchaseReceipt, WithdrawalReceipt, execute_transfer, quote_transfer,
)


class _DryRunDomesticClient:
    """Stub only -- prints what it would do, moves no real money."""

    def quote_buy_usdt(self, krw_amount: float) -> tuple[float, float]:
        price = 1400.0  # placeholder KRW/USDT
        return krw_amount / price * 0.9995, price  # 0.05% fee, illustrative only

    def execute_buy_usdt(self, krw_amount: float, quote_id: str) -> PurchaseReceipt:
        usdt, price = self.quote_buy_usdt(krw_amount)
        print(f"[dry-run] would buy {usdt:.2f} USDT at ~{price} KRW/USDT (quote {quote_id})")
        return PurchaseReceipt(usdt, price, krw_amount * 0.0005, f"dryrun-{quote_id}")


class _DryRunWithdrawalClient:
    def withdraw_usdt(self, usdt_amount: float, destination_label: str, quote_id: str) -> WithdrawalReceipt:
        print(f"[dry-run] would withdraw {usdt_amount:.2f} USDT to {destination_label!r} (quote {quote_id})")
        return WithdrawalReceipt(usdt_amount, 1.0, f"dryrun-{quote_id}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--krw-amount", type=float, required=True)
    p.add_argument("--destination", required=True, help="a label for your own reference, e.g. binance_futures_wallet")
    p.add_argument("--max-krw-per-transfer", type=float, required=True)
    p.add_argument("--dry-run", action="store_true", default=True)
    args = p.parse_args()

    if not sys.stdin.isatty():
        print("refusing: must be run interactively by a human", file=sys.stderr)
        return 2

    domestic = _DryRunDomesticClient()
    withdrawal = _DryRunWithdrawalClient()
    now = datetime.now(timezone.utc)
    config = BridgeConfig(max_krw_per_transfer=args.max_krw_per_transfer)

    quote = quote_transfer(args.krw_amount, args.destination, domestic, now=now, config=config)
    print(f"Quote {quote.quote_id}: {quote.krw_amount:,.0f} KRW -> ~{quote.estimated_usdt:.2f} USDT "
          f"(@ {quote.price_krw_per_usdt} KRW/USDT), expires {quote.expires_at.isoformat()}")

    print(f'Type exactly: {REQUIRED_CONFIRMATION_TOKEN}')
    token = input("> ")
    operator = input("Your name: ").strip()
    approval = FundTransferApproval(quote.quote_id, operator, datetime.now(timezone.utc), token)

    record = execute_transfer(quote, approval, domestic, withdrawal, now=datetime.now(timezone.utc), config=config)
    print(f"Done: {record.purchase.filled_usdt:.2f} USDT bought, "
          f"{record.withdrawal.sent_usdt:.2f} USDT sent to {quote.destination_label}, "
          f"approved by {record.approved_by}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
