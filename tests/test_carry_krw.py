import pytest

from cointrader.accounting.krw_ledger import ExitCostConfig, KrwTaxConfig
from cointrader.carry.krw import after_tax_view

EXIT = ExitCostConfig(1.0, 0.0005, 1000.0)


def test_small_capital_pays_no_tax_but_exit_costs():
    r = after_tax_view(net_apr_pct=10, capital_krw=10_000_000, krw_per_usdt=1400, tax=KrwTaxConfig(), exit_costs=EXIT)
    assert r["tax_krw"] == 0
    assert r["gain_krw"] == 1_000_000
    assert r["exit_cost_krw"] == round(11_000_000 * 0.0005 + 1400 + 1000)


def test_large_capital_taxed_above_deduction():
    r = after_tax_view(net_apr_pct=10, capital_krw=100_000_000, krw_per_usdt=1400, tax=KrwTaxConfig(), exit_costs=EXIT)
    taxable = 10_000_000 - r["exit_cost_krw"]
    assert r["tax_krw"] == round((taxable - 2_500_000) * 0.22)
    assert r["after_tax_apr_pct"] < 10


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        after_tax_view(net_apr_pct=5, capital_krw=0, krw_per_usdt=1400, tax=KrwTaxConfig(), exit_costs=EXIT)
