from datetime import datetime, timedelta, timezone

import pytest

from cointrader.carry.executor import CarryCosts, PaperCarryExecutor
from cointrader.carry.runner import CarryParams, CarryRunner, solve_shrink_coins
from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle, Timeframe
from cointrader.risk.leverage import MarginTier

T0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
TIERS = [MarginTier(0, None, 0.005, 0)]
NOFEE = CarryCosts(0.0, 0.0, 0.0)


def candles(prices, market="X", highs=None):
    out = []
    for i, c in enumerate(prices):
        h = highs[i] if highs else c
        out.append(Candle(market, Timeframe.HOUR_1, T0 + timedelta(hours=i), c, h, min(c, h), c, 1.0, "t", T0))
    return out


def fundings(n_hours, rate, step=8):
    return [FundingRateRecord("X", T0 + timedelta(hours=h), rate, float("nan")) for h in range(0, n_hours, step)]


def run(prices, rate, params=CarryParams(), costs=NOFEE, highs=None, funding=None):
    ex = PaperCarryExecutor(10_000.0, costs)
    r = CarryRunner(ex, params, TIERS)
    f = funding if funding is not None else fundings(len(prices), rate)
    return ex, r, r.run(candles(prices), candles(prices, highs=highs), f)


def test_flat_price_equity_is_funding_minus_fees():
    n = 24 * 30
    ex, r, s = run([100.0] * n, 0.0001, costs=CarryCosts())
    # one entry fee only (no exit): 0.15% of notional; notional = 10000/(1+0.0015+0.5)
    notional = 10_000 / (1 + 0.001 + 0.0005 + 0.5)
    assert ex.fees_paid == pytest.approx(notional * 0.0015)
    assert ex.funding_received == pytest.approx(notional * 0.0001 * 90, rel=0.02)
    assert r.liquidations == 0 and r.rebalances == 0
    assert s["capital_end"] == pytest.approx(10_000 - ex.fees_paid + ex.funding_received, abs=0.01)


def test_hedge_makes_price_moves_cancel_with_topup():
    prices = [100.0] * 50 + [100 + i for i in range(60)] + [160.0] * 50
    ex, r, s = run(prices, 0.0)
    assert r.rebalances >= 1 and r.liquidations == 0
    assert s["capital_end"] == pytest.approx(10_000, abs=0.01)   # no fees, no funding, equal coins
    assert ex.q_spot == pytest.approx(ex.q_perp)


def test_shrink_solution_hits_target_ratio():
    x = solve_shrink_coins(q=10, wallet=2.0, target_ratio=0.5, spot=100, perp=100,
                           spot_fee=0.001, perp_fee=0.0005, slippage=0.0)
    ex = PaperCarryExecutor(1.0)
    ex.q_spot = ex.q_perp = 10
    ex.perp_entry = 100
    ex.margin = 2.0
    ex.shrink_and_top_up(x, 100, 100)
    assert ex.wallet(100) / (ex.q_perp * 100) == pytest.approx(0.5)


def test_gap_through_maintenance_liquidates_and_loses_margin():
    prices = [100.0] * 120
    highs = [100.0] * 60 + [190.0] + [100.0] * 59      # intrabar spike, closes back
    ex, r, s = run(prices, 0.0, highs=highs)
    assert r.liquidations == 1
    assert s["capital_end"] < 10_000 * 0.8              # lost ~ the 1/3 of capital posted as margin
    assert s["min_liq_buffer_at_high_pct"] < 0


def test_leverage_changes_buffer_not_hedge():
    prices = [100.0] * 60 + [110.0] * 60
    _, _, lo = run(prices, 0.0, CarryParams(leverage=2))
    _, _, hi = run(prices, 0.0, CarryParams(leverage=10))
    assert hi["min_liq_buffer_close_pct"] < lo["min_liq_buffer_close_pct"]


def test_funding_filter_leaves_on_negative_and_costs_fees():
    n = 24 * 40
    neg = fundings(n, 0.0001)[:60] + fundings(n, -0.0003)[60:]
    pos_then_neg = [FundingRateRecord("X", r.funding_time, 0.0001 if i < 60 else -0.0003, float("nan"))
                    for i, r in enumerate(fundings(n, 0))]
    p = CarryParams(funding_filter=True, exit_apr=0.0, reenter_apr=0.05)
    ex, r, s = run([100.0] * n, 0, p, CarryCosts(), funding=pos_then_neg)
    assert r.entries == 1 and s["time_in_position_pct"] < 100
    always = run([100.0] * n, 0, CarryParams(), CarryCosts(), funding=pos_then_neg)[2]
    assert s["net_return_pct"] > always["net_return_pct"]


def test_params_validate():
    with pytest.raises(ValueError):
        CarryParams(leverage=0.5)
    with pytest.raises(ValueError):
        CarryCosts(spot_fee=0.5)
