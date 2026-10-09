from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.backtest.event_engine import ExecutionCosts, FuturesTerms, run_event_backtest
from cointrader.data.models import Candle, Timeframe
from cointrader.features.indicator_votes import DEFAULT_PANEL, raw_scores
from cointrader.risk.engine import RiskConfig, RiskEngine, SymbolFilters
from cointrader.strategies.base import Signal
from cointrader.strategies.daytrade import DayTradeVote
from cointrader.strategies.indicator_vote import IndicatorVote

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
FILTERS = {"BTCUSDT": SymbolFilters("BTCUSDT", 0.01, 0.001, 0.001, 1.0)}
FREE = ExecutionCosts(taker_fee=0.0, maker_fee=0.0, half_spread=0.0, impact_coefficient=0.0)


def bars(n, step=timedelta(minutes=15), timeframe=Timeframe.MINUTE_15, drift=0.0001):
    out, price = [], 100.0
    for i in range(n):
        o, price = price, price * (1 + drift)
        out.append(Candle("BTCUSDT", timeframe, T0 + i * step, o, max(o, price) + 0.01, min(o, price) - 0.01, price,
                          1e6, "test", T0 + (i + 1) * step))
    return out


def test_swing_vote_is_unchanged_by_the_new_knobs():
    s = IndicatorVote()
    assert s.strategy_id == "swing_indicator_vote_h5_c0.6_v2"
    assert set(s.parameters) == {"horizon", "fit_lookback", "enter_confidence", "exit_confidence", "min_agree",
                                 "stop_atr", "atr_period", "allow_short", "use_side_data", "vol_gate_hi", "vol_gate_lo"}
    assert (s.max_hold_bars, s.max_entries_per_day, s.quality_window_bars) == (None, None, None)


def test_daytrade_vote_identity_and_parameters():
    s = DayTradeVote()
    assert s.family == "daytrade" and s.timeframe == "15m"
    assert s.strategy_id == "daytrade_indicator_vote_h16_c0.6_v1"
    assert DayTradeVote(horizon=48, use_side_data=True).strategy_id == "daytrade_indicator_vote_side_h48_c0.6_v1"
    assert s.warmup == 140 + 1000 + 16 + 1
    p = s.parameters
    assert (p["score_scale"], p["max_hold_bars"], p["max_entries_per_day"], p["quality_window_bars"]) == (0.1, 48, 100, 192)
    assert p["vol_short"] == 96 and p["vol_long"] == 960 and p["allow_short"] is True


def test_trail_atr_is_off_by_default_and_named_in_the_id_when_set():
    assert DayTradeVote().trail_atr is None and "trail_atr" not in DayTradeVote().parameters
    s = DayTradeVote(trail_atr=2.5)
    assert s.strategy_id == "daytrade_indicator_vote_h16_c0.6_t2.5_v1"
    assert s.parameters["trail_atr"] == 2.5


def test_score_scale_defaults_to_daily_behaviour_and_unsaturates_small_moves():
    h = bars(200)
    base = raw_scores(h, DEFAULT_PANEL)
    assert raw_scores(h, DEFAULT_PANEL, scale=1.0) == base
    scaled = raw_scores(h, DEFAULT_PANEL, scale=0.1)
    # a steady 15m drift is a tiny move in daily units: unscaled the return-sized scores sit near 0
    assert abs(base["ema_trend"]) < 0.2 < abs(scaled["ema_trend"])
    assert abs(base["roc"]) < abs(scaled["roc"])
    for k in ("rsi", "donchian_pos", "bollinger_b", "obv_slope"):
        assert scaled[k] == base[k]  # dimensionless scores do not depend on the scale
    with pytest.raises(ValueError):
        raw_scores(h, DEFAULT_PANEL, scale=0.0)


def test_donchian_pos_is_positive_near_the_top_of_the_range():
    assert raw_scores(bars(200), DEFAULT_PANEL)["donchian_pos"] > 0.5
    assert raw_scores(bars(200, drift=-0.0001), DEFAULT_PANEL)["donchian_pos"] < -0.5


def test_knob_validation():
    for kw in ({"score_scale": 0.0}, {"vol_short": 1}, {"vol_short": 50, "vol_long": 40}, {"bars_per_day": 0},
               {"max_hold_bars": 0}, {"max_entries_per_day": 0}, {"quality_window_bars": 0}, {"trail_atr": 0.0}):
        with pytest.raises(ValueError):
            DayTradeVote(**kw)


@dataclass
class AlwaysLong:
    max_hold_bars: int | None = None
    max_entries_per_day: int | None = None
    quality_window_bars: int | None = None
    strategy_id: str = "always_long"
    family: str = "daytrade"
    version: str = "1"
    timeframe: str = "15m"
    warmup: int = 1
    parameters: dict | None = None

    def signal(self, history, context=None):
        return Signal(1, strength=1.0, reason="always", stop_distance=5.0, regime="RANGE", features={"atr": 1.0})


def flat15(n):
    return [Candle("BTCUSDT", Timeframe.MINUTE_15, T0 + i * timedelta(minutes=15), 100.0, 100.5, 99.5, 100.0, 1e6,
                   "test", T0 + (i + 1) * timedelta(minutes=15)) for i in range(n)]


def run(strategy, n=60):
    risk = RiskEngine(RiskConfig(risk_per_trade=0.01, cooldown=None, stoploss_guard=None, drawdown_guard=None), FILTERS)
    return run_event_backtest(flat15(n), strategy, risk, costs=FREE, futures=FuturesTerms(assume_no_funding=True),
                              initial_equity=10_000.0)


def test_time_stop_closes_positions_after_max_hold_bars():
    r = run(AlwaysLong(max_hold_bars=4))
    assert r.trades and r.trades[0].exit_reason == "time_stop"
    held = (r.trades[0].exit_time - r.trades[0].entry_time) / timedelta(minutes=15)
    assert 4 <= held <= 6  # decided at 4 bars held, executed after the latency bar
    assert all(t.exit_reason != "time_stop" for t in run(AlwaysLong()).trades)  # off by default


def test_daily_entry_cap_is_counted_per_utc_day_and_reported():
    r = run(AlwaysLong(max_hold_bars=2, max_entries_per_day=3), n=90)  # 90 x 15m = 22.5 h, one UTC day
    assert len(r.trades) <= 3
    assert r.rejected_entries.get("entry_cap_per_day", 0) > 0
    uncapped = run(AlwaysLong(max_hold_bars=2), n=90)
    assert len(uncapped.trades) > 3


def test_quality_window_bars_overrides_warmup_default():
    cs = flat15(60)
    del cs[20]  # one candle hole
    risk = RiskEngine(RiskConfig(risk_per_trade=0.01, cooldown=None, stoploss_guard=None, drawdown_guard=None), FILTERS)

    def blocked(window):
        r = run_event_backtest(cs, AlwaysLong(quality_window_bars=window, max_hold_bars=3), risk, costs=FREE,
                               futures=FuturesTerms(assume_no_funding=True))
        return r.rejected_entries.get("data_quality:candle_issue_in_window", 0), len(r.trades)

    (b10, t10), (b50, t50) = blocked(10), blocked(50)
    assert 0 < b10 < b50  # the hole blocks entries only for as long as the configured window
    assert t10 > t50
