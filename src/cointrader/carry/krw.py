"""KRW after-tax view of a carry result (ADR-0068). Pure arithmetic on an annualised USDT-side return.

Not a ledger: one year of the same return is assumed, the KRW/USDT rate is held fixed at `krw_per_usdt`
(FX gain/loss is NOT modelled and can exceed the whole carry), and the position is taken out once at the
end of the year through the Upbit exit path (USDT sell fee, one Binance withdrawal, one bank withdrawal).
Tax uses `KrwTaxConfig.estimate` (22% above the 2.5M KRW deduction, year-by-year, unverified).
"""
from __future__ import annotations

from cointrader.accounting.krw_ledger import ExitCostConfig, KrwTaxConfig


def after_tax_view(*, net_apr_pct: float, capital_krw: float, krw_per_usdt: float,
                   tax: KrwTaxConfig, exit_costs: ExitCostConfig, year: int = 2027) -> dict:
    if capital_krw <= 0 or krw_per_usdt <= 0:
        raise ValueError("capital_krw and krw_per_usdt must be positive")
    gain = capital_krw * net_apr_pct / 100
    end_value = capital_krw + gain
    exit_krw = (end_value * (exit_costs.domestic_sell_fee_rate or 0.0)
                + (exit_costs.overseas_withdraw_fee_usdt or 0.0) * krw_per_usdt
                + (exit_costs.krw_withdraw_fee_krw or 0.0))
    taxable = gain - exit_costs_deductible(exit_krw)
    tax_krw = tax.estimate(year, taxable)
    after = gain - exit_krw - tax_krw
    return {"capital_krw": capital_krw, "gain_krw": round(gain), "exit_cost_krw": round(exit_krw),
            "tax_krw": round(tax_krw), "after_tax_gain_krw": round(after),
            "after_tax_apr_pct": round(100 * after / capital_krw, 2),
            "tax_verified": tax.verified}


def exit_costs_deductible(exit_krw: float) -> float:
    """Exit costs reduce the taxable gain (necessary expenses); whether they really do is part of the unverified tax view."""
    return exit_krw
