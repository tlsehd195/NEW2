from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.analytics.performance import result_summary, return_metrics, trade_metrics
from cointrader.backtest.event_engine import ExecutionCosts, FuturesTerms, run_event_backtest
from cointrader.data.models import Candle, Timeframe
from cointrader.risk.engine import RiskConfig, RiskEngine, SymbolFilters
from cointrader.risk.leverage import MarginTier
from cointrader.strategies.base import Signal, flat

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
FILTERS = {"BTCUSDT": SymbolFilters("BTCUSDT", 0.01, 0.001, 0.001, 1.0)}
NO_FUNDING = FuturesTerms(assume_no_funding=True)
FREE = ExecutionCosts(taker_fee=0.0, maker_fee=0.0, half_spread=0.0, impact_coefficient=0.0)


def risk(**kw):
    kw.setdefault("cooldown", None)
    kw.setdefault("stoploss_guard", None)
    kw.setdefault("drawdown_guard", None)
    return RiskEngine(RiskConfig(**kw), FILTERS)


def bar(i, o, h, l, c, v=1e6):
    return Candle("BTCUSDT", Timeframe.HOUR_1, T0 + timedelta(hours=i), o, h, l, c, v, "test", T0 + timedelta(hours=i + 1))


def flat_bars(n, price=100.0):
    return [bar(i, price, price + 0.5, price - 0.5, price) for i in range(n)]


@dataclass
class Scripted:
    """Enters at decision index `at` (len(history)-1 == at) and exits at `exit_at`."""

    entries: dict = field(default_factory=dict)  # index -> (direction, stop, tp, trail)
    exits: set = field(default_factory=set)
    strategy_id: str = "scripted"
    family: str = "swing"
    version: str = "1"
    timeframe: str = "1h"
    warmup: int = 1
    parameters: dict = field(default_factory=dict)

    def signal(self, history, context=None):
        i = len(history) - 1
        if i in self.entries:
            d, stop, tp, trail = self.entries[i]
            return Signal(d, strength=1.0, reason="scripted", stop_distance=stop, take_profit_distance=tp,
                          trailing_distance=trail, regime="RANGE", features={"atr": 1.0})
        if i in self.exits:
            return flat("scripted_exit", regime="RANGE", exit_long=True, exit_short=True)
        return flat("hold", regime="RANGE")


def run(cs, strat, costs=FREE, **kw):
    return run_event_backtest(cs, strat, kw.pop("risk", risk(risk_per_trade=0.01)), costs=costs,
                              futures=kw.pop("futures", NO_FUNDING), initial_equity=10_000.0, **kw)


def test_entry_executes_at_next_bar_open_never_same_bar():
    cs = flat_bars(10)
    cs[3] = bar(3, 101.0, 101.5, 100.5, 101.0)
    r = run(cs, Scripted(entries={2: (1, 5.0, None, None)}, exits={5}))
    t = r.trades[0]
    assert t.entry_time == cs[3].open_time and t.entry_reference == 101.0
    assert t.exit_time == cs[6].open_time and t.exit_reason == "signal_exit"


def test_strategy_cannot_see_unclosed_bars():
    class Peeker(Scripted):
        def signal(self, history, context=None):
            history[len(history)]  # the next bar -> IndexError
    with pytest.raises(IndexError):
        run(flat_bars(10), Peeker())


def test_cost_decomposition_adds_up():
    cs = flat_bars(12)
    cs[6] = bar(6, 110.0, 110.5, 109.5, 110.0)
    costs = ExecutionCosts(taker_fee=0.0005, half_spread=0.0002, impact_coefficient=0.1)
    r = run(cs, Scripted(entries={2: (1, 5.0, None, None)}, exits={5}), costs)
    t = r.trades[0]
    assert t.gross_pnl == pytest.approx(t.quantity * (110.0 - 100.0))
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.fees - t.spread_cost - t.slippage_cost - t.funding)
    assert t.fees > 0 and t.spread_cost > 0 and t.slippage_cost > 0
    assert t.entry_fill > t.entry_reference and t.exit_fill < t.exit_reference
    assert r.final_equity == pytest.approx(10_000 + t.net_pnl)
    cb = r.cost_breakdown()
    assert cb["net_pnl"] == pytest.approx(cb["gross_pnl"] - cb["fees"] - cb["spread"] - cb["slippage"] - cb["funding"])


def test_stop_fills_at_stop_and_gap_fills_at_open():
    cs = flat_bars(10)
    cs[5] = bar(5, 100.0, 100.2, 94.0, 99.0)
    r = run(cs, Scripted(entries={2: (1, 5.0, None, None)}))
    assert r.trades[0].exit_reason == "stop" and r.trades[0].exit_reference == 95.0
    cs[5] = bar(5, 90.0, 90.5, 89.0, 90.0)
    r = run(cs, Scripted(entries={2: (1, 5.0, None, None)}))
    assert r.trades[0].exit_reason == "stop_gap" and r.trades[0].exit_reference == 90.0


def test_stop_wins_when_stop_and_take_profit_share_a_bar():
    cs = flat_bars(10)
    cs[5] = bar(5, 100.0, 120.0, 90.0, 100.0)
    r = run(cs, Scripted(entries={2: (1, 5.0, 10.0, None)}))
    assert r.trades[0].exit_reason == "stop"


def test_take_profit_is_maker_and_needs_trade_through():
    cs = flat_bars(10)
    cs[5] = bar(5, 100.0, 110.0, 99.8, 105.0)  # touches exactly 110: no fill
    cs[6] = bar(6, 105.0, 110.5, 104.0, 108.0)
    costs = ExecutionCosts(taker_fee=0.001, maker_fee=0.0002, half_spread=0.0, impact_coefficient=0.0)
    r = run(cs, Scripted(entries={2: (1, 5.0, 10.0, None)}), costs)
    t = r.trades[0]
    assert t.exit_reason == "take_profit" and t.exit_time == cs[6].close_time and t.exit_liquidity == "maker"
    assert t.fees == pytest.approx(t.quantity * 100 * 0.001 + t.quantity * 110 * 0.0002)


def test_trailing_stop_ratchets():
    cs = flat_bars(12)
    cs[4] = bar(4, 100, 110, 99.9, 109)
    cs[5] = bar(5, 109, 109.5, 104.0, 105)
    r = run(cs, Scripted(entries={2: (1, 20.0, None, 5.0)}))
    t = r.trades[0]
    assert t.exit_reason == "stop" and t.exit_reference == pytest.approx(105.0)
    assert t.mfe == pytest.approx(0.10)


def test_partial_fill_when_order_exceeds_participation():
    cs = [bar(i, 100, 100.5, 99.5, 100, v=10.0) for i in range(10)]  # 1000 USDT traded per bar
    r = run(cs, Scripted(entries={2: (1, 1.0, None, None)}, exits={5}), risk=risk(risk_per_trade=0.01, max_leverage=5))
    assert r.partial_fills == 1
    assert r.trades[0].quantity == pytest.approx(0.5)  # 5% of 1000 USDT / 100
    assert r.trades[0].filled_fraction < 0.01


def test_limit_entry_missed_and_filled():
    cs = flat_bars(10)
    costs = ExecutionCosts(entry_order="limit", limit_offset=0.01, limit_ttl_bars=2, taker_fee=0, maker_fee=0.0002,
                           half_spread=0, impact_coefficient=0)
    r = run(cs, Scripted(entries={2: (1, 5.0, None, None)}), costs)
    assert r.missed_fills == 1 and not r.trades
    cs[4] = bar(4, 100, 100.2, 98.5, 99.5)  # trades through 99.0
    r = run(cs, Scripted(entries={2: (1, 5.0, None, None)}, exits={6}), costs)
    assert r.trades[0].entry_liquidity == "maker" and r.trades[0].entry_reference == 99.0


def test_funding_is_charged_and_missing_rate_fails_closed():
    cs = flat_bars(30)
    settle = T0 + timedelta(hours=8)
    terms = FuturesTerms(funding={T0: 0.0, settle: 0.001, T0 + timedelta(hours=16): 0.001, T0 + timedelta(hours=24): 0.0})
    r = run(cs, Scripted(entries={2: (1, 5.0, None, None)}, exits={10}), futures=terms)
    t = r.trades[0]
    assert t.funding == pytest.approx(t.quantity * 100.0 * 0.001)
    short = run(cs, Scripted(entries={2: (-1, 5.0, None, None)}, exits={10}), futures=terms).trades[0]
    assert short.funding == pytest.approx(-short.quantity * 100.0 * 0.001)
    with pytest.raises(ValueError, match="funding rate missing"):
        run(cs, Scripted(entries={2: (1, 5.0, None, None)}, exits={10}), futures=FuturesTerms(funding={T0: 0.0}))
    with pytest.raises(ValueError, match="funding series missing"):
        run(cs, Scripted(entries={2: (1, 5.0, None, None)}), futures=FuturesTerms())


def test_funding_stamps_a_few_ms_off_the_grid_still_count_but_far_ones_do_not():
    # The archive stamps some settlements at e.g. 08:00:00.003 rather than 08:00:00.
    cs = flat_bars(30)
    ms = timedelta(milliseconds=3)
    jittered = FuturesTerms(funding={T0: 0.0, T0 + timedelta(hours=8) + ms: 0.001,
                                     T0 + timedelta(hours=16) + ms: 0.001, T0 + timedelta(hours=24): 0.0})
    t = run(cs, Scripted(entries={2: (1, 5.0, None, None)}, exits={10}), futures=jittered).trades[0]
    assert t.funding == pytest.approx(t.quantity * 100.0 * 0.001)
    far = FuturesTerms(funding={T0: 0.0, T0 + timedelta(hours=8, minutes=5): 0.001})
    with pytest.raises(ValueError, match="funding rate missing"):
        run(cs, Scripted(entries={2: (1, 5.0, None, None)}, exits={10}), futures=far)
    twice = FuturesTerms(funding={T0: 0.0, T0 + timedelta(hours=8): 0.001, T0 + timedelta(hours=8) + ms: 0.002})
    with pytest.raises(ValueError, match="two funding records"):
        run(cs, Scripted(entries={2: (1, 5.0, None, None)}, exits={10}), futures=twice)


def test_liquidation_loses_posted_margin():
    cs = flat_bars(10)
    cs[5] = bar(5, 100.0, 100.2, 60.0, 70.0)
    tiers = (MarginTier(0, None, 0.005, 0.0),)
    terms = FuturesTerms(margin_leverage=5.0, tiers=tiers, assume_no_funding=True)
    r = run(cs, Scripted(entries={2: (1, 50.0, None, None)}), futures=terms,
            risk=risk(risk_per_trade=0.05, max_leverage=5))
    t = r.trades[0]
    assert t.exit_reason == "liquidation" and r.liquidations == 1
    assert t.gross_pnl == pytest.approx(-t.quantity * t.entry_reference / 5.0)


def test_risk_protections_apply_inside_backtest():
    cs = flat_bars(20)
    cs[5] = bar(5, 100.0, 100.2, 94.0, 99.0)
    strat = Scripted(entries={2: (1, 5.0, None, None), 5: (1, 5.0, None, None)})
    r = run(cs, strat, risk=RiskEngine(RiskConfig(stoploss_guard=None, drawdown_guard=None), FILTERS))
    assert len(r.trades) == 1 and r.rejected_entries.get("protection:cooldown") == 1


def test_data_quality_gap_blocks_entries():
    cs = flat_bars(12)
    cs = cs[:4] + cs[5:]  # gap
    r = run(cs, Scripted(entries={5: (1, 5.0, None, None)}, warmup=1), quality_window=3)
    assert r.rejected_entries.get("data_quality:candle_issue_in_window") == 1


def test_metrics_and_summary():
    m = return_metrics([0.01, -0.02, 0.03], 365)
    assert m["total_return"] == pytest.approx(1.01 * 0.98 * 1.03 - 1)
    assert m["max_drawdown"] == pytest.approx(0.02)
    cs = flat_bars(12)
    cs[6] = bar(6, 110.0, 110.5, 109.5, 110.0)
    r = run(cs, Scripted(entries={2: (1, 5.0, None, None)}, exits={5}), ExecutionCosts())
    s = result_summary(r, 24 * 365)
    assert s["trade_count"] == 1 and s["label"].startswith("BACKTEST")
    assert set(s["cost_breakdown"]) == {"gross_pnl", "fees", "spread", "slippage", "funding", "net_pnl"}
    assert trade_metrics([])["trade_count"] == 0


def test_volatility_scaled_impact_uses_only_past_bars_and_is_small_for_quiet_markets():
    # Same order, same participation: impact follows the pre-fill volatility, not a fixed 10% * sqrt(participation).
    quiet = flat_bars(30)
    costs = ExecutionCosts(taker_fee=0.0, half_spread=0.0, impact_coefficient=1.0, impact_vol_floor=0.0005)
    r = run(quiet, Scripted(entries={25: (1, 5.0, None, None)}, exits={27}), costs)
    t = r.trades[0]
    slip_frac = (t.entry_fill - t.entry_reference) / t.entry_reference
    assert 0 < slip_frac <= 0.0005  # floor * sqrt(participation <= 1)
    fixed = ExecutionCosts(taker_fee=0.0, half_spread=0.0, impact_coefficient=0.1, impact_model="fixed")
    t2 = run(quiet, Scripted(entries={25: (1, 5.0, None, None)}, exits={27}), fixed).trades[0]
    assert (t2.entry_fill - t2.entry_reference) / t2.entry_reference > 10 * slip_frac
    # a volatility spike AFTER the fill bar cannot change the entry cost (no look-ahead)
    spiky = flat_bars(30)
    spiky[28] = bar(28, 100.0, 130.0, 70.0, 100.0)
    t3 = run(spiky, Scripted(entries={25: (1, 5.0, None, None)}, exits={27}), costs).trades[0]
    assert t3.entry_fill == pytest.approx(t.entry_fill)
