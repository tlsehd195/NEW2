from datetime import datetime, timedelta, timezone

import pytest

from cointrader.accounting.krw_ledger import (
    ExitCostConfig, FuturesTradeClose, InsufficientUsdt, KrwFee, KrwRateSeries, KrwTaxConfig, MixedModes,
    UsdtPurchase, UsdtSale, UsdtTransfer, build_report, event_from_dict, events_from_fund_transfer,
    trade_events_from_outcomes,
)

T0 = datetime(2027, 1, 5, tzinfo=timezone.utc)


def at(h: float) -> datetime:
    return T0 + timedelta(hours=h)


def trade(h, gross, fees=0.0, funding=0.0, rate=1400.0, mode="paper", spread=0.0, slip=0.0):
    return FuturesTradeClose(at=at(h), mode=mode, gross_pnl_usdt=gross, fees_usdt=fees, spread_usdt=spread,
                             slippage_usdt=slip, funding_usdt=funding, rate_krw=rate, rate_source="test")


def buy(h=0, usdt=1000.0, rate=1400.0, fee_rate=0.0005, mode="paper"):
    g = usdt * rate
    return UsdtPurchase(at=at(h), mode=mode, krw_gross=g, usdt=usdt, fee_krw=g * fee_rate)


def test_round_trip_breaks_down_every_cost_and_fx():
    events = [
        buy(0, 1000, 1400),  # 1,400,000 + 700 fee
        UsdtTransfer(at=at(1), mode="paper", network_fee_usdt=1.0, rate_krw=1400, rate_source="t", direction="to_overseas"),
        trade(2, gross=50, fees=2, funding=1, rate=1410),  # net +47
        UsdtTransfer(at=at(3), mode="paper", network_fee_usdt=1.0, rate_krw=1420, rate_source="t", direction="to_domestic"),
        UsdtSale(at=at(4), mode="paper", usdt=1045, krw_gross=1045 * 1420, fee_krw=1045 * 1420 * 0.0005),
        KrwFee(at=at(5), mode="paper", krw=1000, label="bank"),
    ]
    rep = build_report(events, as_of=at(6), mark_rate=1420, mark_rate_source="t")
    c = rep.components
    assert c["trading_gross"] == pytest.approx(50 * 1410)
    assert c["trading_fees"] == pytest.approx(-2 * 1410)
    assert c["funding"] == pytest.approx(-1 * 1410)
    assert c["network_fees"] == pytest.approx(-1400 - 1420)
    assert c["krw_fees"] == -1000
    assert c["fx_realized"] > 0  # USDT bought at 1400, left at 1420
    assert rep.usdt_held == pytest.approx(0.0)
    # Everything adds up to the actual cash result.
    cash = rep.krw_received - rep.krw_paid
    assert rep.realized_net_krw == pytest.approx(cash)
    assert rep.identity_error == pytest.approx(0.0, abs=1e-6)


def test_fx_loss_shows_even_with_zero_trading():
    rep = build_report([buy(0, 1000, 1400, fee_rate=0)], as_of=at(1), mark_rate=1350, mark_rate_source="t")
    assert rep.fx_unrealized == pytest.approx(-50_000)
    assert rep.net_before_tax_krw == pytest.approx(-50_000)


def test_losing_trade_realizes_fx_on_the_usdt_it_consumes():
    events = [buy(0, 1000, 1400, fee_rate=0), trade(1, gross=-100, rate=1500)]
    rep = build_report(events, as_of=at(2), mark_rate=1500, mark_rate_source="t")
    assert rep.components["trading_gross"] == pytest.approx(-150_000)
    assert rep.components["fx_realized"] == pytest.approx(100 * (1500 - 1400))
    assert rep.identity_error == pytest.approx(0.0, abs=1e-6)


def test_tax_estimate_uses_deduction_rate_and_effective_year():
    tax = KrwTaxConfig()
    assert tax.estimate(2026, 10_000_000) == 0.0
    assert tax.estimate(2027, 2_000_000) == 0.0
    assert tax.estimate(2027, 12_500_000) == pytest.approx(10_000_000 * 0.22)
    events = [buy(0, 10_000, 1400, fee_rate=0), trade(1, gross=5_000, rate=1400)]  # 7,000,000 KRW profit
    rep = build_report(events, as_of=at(2), mark_rate=1400, mark_rate_source="t",
                       exit_costs=ExitCostConfig(1.0, 0.0005, 1000.0))
    assert rep.years[0].year == 2027 and rep.years[0].tax_in_force
    assert rep.estimated_tax_krw == pytest.approx((7_000_000 - 2_500_000) * 0.22)
    assert not rep.tax_verified
    assert rep.net_if_cashed_out_krw == pytest.approx(
        7_000_000 - rep.exit_cost_krw - rep.estimated_tax_krw)


def test_exit_estimate_refuses_when_not_configured():
    rep = build_report([buy()], as_of=at(1), mark_rate=1400, mark_rate_source="t")
    assert rep.exit_cost_krw is None and "not configured" in rep.exit_cost_reason
    assert rep.net_if_cashed_out_krw is None


def test_fail_closed_cases():
    with pytest.raises(MixedModes):
        build_report([buy(mode="paper"), trade(1, 10, mode="live")], as_of=at(2))
    with pytest.raises(InsufficientUsdt):
        build_report([buy(0, 10), trade(1, gross=-50)], as_of=at(2))
    with pytest.raises(ValueError):
        build_report([buy(5)], as_of=at(1))  # event after as_of
    with pytest.raises(ValueError):
        trade(0, 1, rate=0)


def test_rate_series_refuses_stale_or_missing():
    s = KrwRateSeries(((at(0), 1400.0), (at(1), 1410.0)), "upbit", timedelta(minutes=30))
    assert s.at(at(1.25)) == 1410.0
    with pytest.raises(ValueError):
        s.at(at(2))  # stale
    with pytest.raises(ValueError):
        s.at(at(-1))  # before first point


def test_outcome_rows_convert_and_check_net():
    s = KrwRateSeries(((at(0), 1400.0),), "upbit")
    row = {"exit_time": at(0.5).isoformat(), "mode": "paper", "gross_pnl": 10.0, "fees": 1.0, "spread_cost": 0.5,
           "slippage_cost": 0.5, "funding": -0.2, "net_pnl": 8.2, "trade_id": "paper:x:BTC:1"}
    (ev,) = trade_events_from_outcomes([row], s)
    assert ev.net_usdt == pytest.approx(8.2) and ev.rate_krw == 1400.0
    with pytest.raises(ValueError):
        trade_events_from_outcomes([{**row, "net_pnl": 9.0}], s)


def test_event_from_dict_and_fund_transfer_record():
    ev = event_from_dict({"type": "krw_fee", "at": at(0).isoformat(), "mode": "live", "krw": 1000, "label": "bank"})
    assert isinstance(ev, KrwFee)
    with pytest.raises(ValueError):
        event_from_dict({"type": "nope", "at": at(0).isoformat()})

    from cointrader.funding.bridge import (FundTransferQuote, FundTransferRecord, PurchaseReceipt,
                                           WithdrawalReceipt)
    q = FundTransferQuote("q", 1_400_700, "binance", 1000, 1400, at(0), at(0.1))
    rec = FundTransferRecord(q, PurchaseReceipt(1000, 1400, 700, "p"), WithdrawalReceipt(999, 1, "w"), "me", at(0))
    evs = events_from_fund_transfer(rec)
    rep = build_report(evs, as_of=at(1), mark_rate=1400, mark_rate_source="t")
    assert rep.usdt_held == pytest.approx(999)
    assert rep.components["network_fees"] == pytest.approx(-1400)
    assert rep.components["domestic_fees"] == pytest.approx(-700)


def test_config_file_loads():
    from cointrader.settings import load_krw_accounting
    tax, exits, stale = load_krw_accounting()
    assert tax.rate == 0.22 and tax.basic_deduction_krw == 2_500_000 and not tax.verified
    assert stale == timedelta(hours=1)


def test_tax_filing_package_matches_report(tmp_path):
    import csv

    from cointrader.accounting.tax_export import write_filing_package
    events = [buy(0, 10_000, 1400, fee_rate=0.0005), trade(1, gross=5_000, fees=10, rate=1400)]
    rep = build_report(events, as_of=at(2), mark_rate=1400, mark_rate_source="t")
    paths = write_filing_package(rep, 2027, tmp_path)
    assert [p.name for p in paths] == ["summary_2027.csv", "details_2027.csv", "checklist_2027.md"]
    rows = list(csv.reader(paths[0].open(encoding="utf-8-sig")))
    net = next(r for r in rows if r[1] == "2027년 실현 순손익")
    assert float(net[2]) == pytest.approx(rep.realized_net_krw, abs=1)
    tax_row = next(r for r in rows if r[1].startswith("추정세액"))
    assert float(tax_row[2]) == pytest.approx(rep.estimated_tax_krw, abs=1) and "세무사" in tax_row[3]
    detail = list(csv.reader(paths[1].open(encoding="utf-8-sig")))[1:]
    assert sum(float(r[3]) for r in detail) == pytest.approx(rep.realized_net_krw, abs=1)
    assert "세무 조언 아님" in paths[2].read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        write_filing_package(rep, 2030, tmp_path)


def test_tax_filing_package_before_effective_year_says_zero(tmp_path):
    import csv

    from cointrader.accounting.tax_export import write_filing_package
    ev = [UsdtPurchase(at=datetime(2026, 5, 1, tzinfo=timezone.utc), mode="paper", krw_gross=14_000_000,
                       usdt=10_000, fee_krw=0),
          FuturesTradeClose(at=datetime(2026, 5, 2, tzinfo=timezone.utc), mode="paper", gross_pnl_usdt=5000,
                            fees_usdt=0, spread_usdt=0, slippage_usdt=0, funding_usdt=0, rate_krw=1400,
                            rate_source="t")]
    rep26 = build_report(ev, as_of=datetime(2026, 5, 3, tzinfo=timezone.utc))
    p = write_filing_package(rep26, 2026, tmp_path)
    rows = list(csv.reader(p[0].open(encoding="utf-8-sig")))
    assert float(next(r for r in rows if r[1].startswith("추정세액"))[2]) == 0.0
    assert "시행 전" in p[2].read_text(encoding="utf-8")
