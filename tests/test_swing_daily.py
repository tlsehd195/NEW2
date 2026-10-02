"""H-0015 daily low-turnover swing candidates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from cointrader.data.models import Candle, Timeframe
from cointrader.strategies.registry import StrategyRegistry
from cointrader.strategies.swing import DonchianTrend, SmaTrendFilter, TrendPullback, swing_candidate_grid_v2
from cointrader.validation.integrity import check_signal_strategy
from tests.helpers import make_candles

T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)


def daily(closes):
    return [Candle("ETHUSDT", Timeframe.DAY_1, T0 + timedelta(days=i), c, c * 1.01, c * 0.99, c, 1e6, "test", T0)
            for i, c in enumerate(closes)]


def test_integrity_passes_for_daily_candidates():
    cs = make_candles(700, market="ETHUSDT", seed=5, timeframe=Timeframe.DAY_1)
    for s in swing_candidate_grid_v2():
        rep = check_signal_strategy(cs, s, samples=15)
        assert rep.passed, (s.strategy_id, rep.findings[:3])


def test_candidates_are_registered_long_only_daily():
    reg = StrategyRegistry.load()
    for s in swing_candidate_grid_v2():
        assert reg.build(s.strategy_id, market="ETHUSDT", family="swing").strategy_id == s.strategy_id
        assert s.timeframe == "1d"


def test_sma_trend_enters_above_rising_average_and_exits_below():
    up = daily([100 + i for i in range(250)])
    s = SmaTrendFilter()
    assert s.signal(up).entry == 1
    down = daily([100 + i for i in range(250)] + [350 - 5 * i for i in range(40)])
    sig = s.signal(down)
    assert sig.entry == 0 and sig.exit_long


def test_donchian_breaks_out_and_never_shorts():
    s = DonchianTrend()
    flat_then_up = daily([100.0] * 250 + [110.0])
    assert s.signal(flat_then_up).entry == 1
    flat_then_down = daily([100.0] * 250 + [80.0])
    sig = s.signal(flat_then_down)
    assert sig.entry == 0 and sig.exit_long


def test_pullback_buys_dip_only_in_uptrend():
    s = TrendPullback()
    rising = [100 + 0.5 * i for i in range(250)]
    dip = daily(rising + [rising[-1] * 0.93])
    sig = s.signal(dip)
    assert sig.entry == 1 and sig.take_profit_distance > 0
    falling = daily([300 - 0.5 * i for i in range(250)] + [170.0])
    assert s.signal(falling).entry == 0
