from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cointrader.funding.approval import REQUIRED_CONFIRMATION_TOKEN, FundTransferApproval
from cointrader.funding.bridge import (
    BridgeConfig, PurchaseReceipt, WithdrawalReceipt, execute_transfer, quote_transfer,
)
from cointrader.tax.realized_gains import (
    Fill, FillSide, ShortSaleNotSupported, compute_fifo_realized_gains, summarize_by_year,
)

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
KST = timezone(timedelta(hours=9))


def fill(asset, side, qty, price, fee, at, venue="upbit") -> Fill:
    return Fill(asset, side, qty, price, fee, at, venue)


class TestFifoRealizedGains:
    def test_simple_buy_then_sell(self):
        fills = [
            fill("BTC", FillSide.BUY, 1.0, 100.0, 1.0, T0),
            fill("BTC", FillSide.SELL, 1.0, 150.0, 1.5, T0 + timedelta(days=1)),
        ]
        gains = compute_fifo_realized_gains(fills)
        assert len(gains) == 1
        g = gains[0]
        assert g.cost_basis_krw == pytest.approx(101.0)
        assert g.proceeds_krw == pytest.approx(148.5)
        assert g.gain_krw == pytest.approx(47.5)

    def test_fifo_order_across_two_lots(self):
        fills = [
            fill("BTC", FillSide.BUY, 1.0, 100.0, 0.0, T0),
            fill("BTC", FillSide.BUY, 1.0, 200.0, 0.0, T0 + timedelta(days=1)),
            fill("BTC", FillSide.SELL, 1.5, 300.0, 0.0, T0 + timedelta(days=2)),
        ]
        gains = compute_fifo_realized_gains(fills)
        assert len(gains) == 2
        assert gains[0].quantity == pytest.approx(1.0) and gains[0].cost_basis_krw == pytest.approx(100.0)
        assert gains[1].quantity == pytest.approx(0.5) and gains[1].cost_basis_krw == pytest.approx(100.0)

    def test_processes_in_execution_order_not_input_order(self):
        fills = [
            fill("BTC", FillSide.SELL, 1.0, 150.0, 0.0, T0 + timedelta(days=1)),
            fill("BTC", FillSide.BUY, 1.0, 100.0, 0.0, T0),
        ]
        gains = compute_fifo_realized_gains(fills)
        assert gains[0].gain_krw == pytest.approx(50.0)

    def test_short_sale_rejected(self):
        with pytest.raises(ShortSaleNotSupported):
            compute_fifo_realized_gains([fill("BTC", FillSide.SELL, 1.0, 100.0, 0.0, T0)])

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError):
            Fill("BTC", FillSide.BUY, 1.0, 100.0, 0.0, datetime(2024, 1, 1), "upbit")

    def test_multi_asset_lots_are_independent(self):
        fills = [
            fill("BTC", FillSide.BUY, 1.0, 100.0, 0.0, T0),
            fill("ETH", FillSide.SELL, 1.0, 100.0, 0.0, T0),
        ]
        with pytest.raises(ShortSaleNotSupported):
            compute_fifo_realized_gains(fills)  # ETH sell has no lot even though BTC does


class TestYearlySummary:
    def test_groups_by_kst_year_and_nets_gain_loss(self):
        end_2024 = datetime(2024, 12, 31, 16, 0, tzinfo=timezone.utc)  # 2025-01-01 01:00 KST
        fills = [
            fill("BTC", FillSide.BUY, 1.0, 100.0, 0.0, T0),
            fill("BTC", FillSide.SELL, 1.0, 150.0, 0.0, T0 + timedelta(days=10)),  # gain, 2024
            fill("BTC", FillSide.BUY, 1.0, 100.0, 0.0, T0 + timedelta(days=20)),
            fill("BTC", FillSide.SELL, 1.0, 80.0, 0.0, end_2024),  # loss, but 2025 in KST
        ]
        gains = compute_fifo_realized_gains(fills)
        summary = {s.year: s for s in summarize_by_year(gains)}
        assert summary[2024].net_krw == pytest.approx(50.0)
        assert summary[2025].net_krw == pytest.approx(-20.0)

    def test_empty_input_yields_no_rows(self):
        assert summarize_by_year([]) == []


class TestFundBridge:
    class _Domestic:
        def __init__(self, price=1400.0):
            self.price = price
            self.executed = []

        def quote_buy_usdt(self, krw_amount):
            return krw_amount / self.price, self.price

        def execute_buy_usdt(self, krw_amount, quote_id):
            self.executed.append((krw_amount, quote_id))
            return PurchaseReceipt(krw_amount / self.price, self.price, krw_amount * 0.0005, "ext-buy")

    class _Withdrawal:
        def __init__(self):
            self.sent = []

        def withdraw_usdt(self, usdt_amount, destination_label, quote_id):
            self.sent.append((usdt_amount, destination_label, quote_id))
            return WithdrawalReceipt(usdt_amount, 1.0, "ext-withdraw")

    def _approval(self, quote_id, **kw):
        base = dict(quote_id=quote_id, approved_by="동동", approved_at=T0,
                    confirmation_token=REQUIRED_CONFIRMATION_TOKEN)
        base.update(kw)
        return FundTransferApproval(**base)

    def test_end_to_end_transfer(self):
        domestic, withdrawal = self._Domestic(), self._Withdrawal()
        quote = quote_transfer(1_000_000, "binance", domestic, now=T0, config=BridgeConfig(max_krw_per_transfer=2_000_000))
        approval = self._approval(quote.quote_id)
        record = execute_transfer(quote, approval, domestic, withdrawal, now=T0 + timedelta(seconds=1),
                                  config=BridgeConfig(max_krw_per_transfer=2_000_000))
        assert record.purchase.filled_usdt == pytest.approx(1_000_000 / 1400.0)
        assert withdrawal.sent[0][1] == "binance"
        assert domestic.executed[0][1] == quote.quote_id

    def test_refuses_without_configured_limit(self):
        domestic, withdrawal = self._Domestic(), self._Withdrawal()
        quote = quote_transfer(1_000_000, "binance", domestic, now=T0)
        with pytest.raises(ValueError, match="not configured"):
            execute_transfer(quote, self._approval(quote.quote_id), domestic, withdrawal,
                             now=T0, config=BridgeConfig())

    def test_refuses_over_limit(self):
        domestic, withdrawal = self._Domestic(), self._Withdrawal()
        quote = quote_transfer(2_000_000, "binance", domestic, now=T0, config=BridgeConfig(max_krw_per_transfer=1_000_000))
        with pytest.raises(ValueError, match="exceeds"):
            execute_transfer(quote, self._approval(quote.quote_id), domestic, withdrawal,
                             now=T0, config=BridgeConfig(max_krw_per_transfer=1_000_000))

    def test_refuses_expired_quote(self):
        domestic, withdrawal = self._Domestic(), self._Withdrawal()
        cfg = BridgeConfig(max_krw_per_transfer=2_000_000, quote_validity=timedelta(minutes=5))
        quote = quote_transfer(1_000_000, "binance", domestic, now=T0, config=cfg)
        with pytest.raises(ValueError, match="expired"):
            execute_transfer(quote, self._approval(quote.quote_id), domestic, withdrawal,
                             now=T0 + timedelta(minutes=10), config=cfg)

    def test_refuses_approval_for_a_different_quote(self):
        domestic, withdrawal = self._Domestic(), self._Withdrawal()
        cfg = BridgeConfig(max_krw_per_transfer=2_000_000)
        quote_a = quote_transfer(1_000_000, "binance", domestic, now=T0, config=cfg)
        quote_b = quote_transfer(1_000_000, "binance", domestic, now=T0, config=cfg)
        with pytest.raises(ValueError, match="does not match"):
            execute_transfer(quote_b, self._approval(quote_a.quote_id), domestic, withdrawal, now=T0, config=cfg)

    @pytest.mark.parametrize("kw", [
        dict(approved_by="AI"), dict(approved_by=""), dict(confirmation_token="no"),
        dict(quote_id=""),
    ])
    def test_invalid_approvals_rejected(self, kw):
        base = dict(quote_id="q1")
        base.update(kw)
        with pytest.raises(ValueError):
            self._approval(**base)
