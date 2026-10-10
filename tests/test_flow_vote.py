from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.backtest.engine import PrefixView
from cointrader.data.models import Candle, Timeframe
from cointrader.strategies.flow_vote import FlowVote
from cointrader.strategies.registry import StrategyRegistry

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
STEP = timedelta(minutes=15)


def bars(n, *, buy_share=lambda i: 0.5, taker=True, seed=1):
    rng, out, price = random.Random(seed), [], 100.0
    for i in range(n):
        o, price = price, price * math.exp(rng.gauss(0, 0.002))
        vol = 1000.0
        tb = vol * buy_share(i) if taker else None
        out.append(Candle("BTCUSDT", Timeframe.MINUTE_15, T0 + i * STEP, o, max(o, price) * 1.001, min(o, price) * 0.999,
                          price, vol, "test", T0 + (i + 1) * STEP, taker_buy_volume=tb))
    return out


def noisy(i):
    return 0.5 + 0.1 * math.sin(i / 7.0) + random.Random(i).uniform(-0.03, 0.03)


def test_identity_and_gates_in_id():
    s = FlowVote()
    assert s.strategy_id == "daytrade_flow_vote_w16_z1_v1" and s.family == "daytrade" and s.timeframe == "15m"
    assert FlowVote(flow_window=48, vpin_gate=True, funding_gate=True).strategy_id == "daytrade_flow_vote_vpin_fund_w48_z1_v1"
    assert s.warmup == 960 + 16 + 1000 + 16 + 1
    assert s.parameters["z_in"] == 1.0 and "vpin_window" not in s.parameters


def test_candle_taker_volume_must_fit_in_volume():
    with pytest.raises(ValueError):
        Candle("BTCUSDT", Timeframe.MINUTE_15, T0, 1, 1, 1, 1, 10.0, "t", T0, taker_buy_volume=11.0)


def test_missing_taker_volume_is_flat():
    s = FlowVote()
    sig = s.signal(bars(s.warmup + 5, taker=False))
    assert sig.entry == 0 and sig.reason == "warmup_or_flow_unavailable"


def test_flow_surge_goes_long_and_flow_collapse_goes_short():
    s = FlowVote()
    n = s.warmup + 40
    up = bars(n, buy_share=lambda i: 0.8 if i >= n - 8 else noisy(i))
    sig = s.signal(up)
    assert sig.entry == 1 and sig.stop_distance > 0 and 0.0 <= sig.features["p_long"] <= 1.0
    dn = bars(n, buy_share=lambda i: 0.2 if i >= n - 8 else noisy(i))
    assert FlowVote().signal(dn).entry == -1
    assert FlowVote(allow_short=False).signal(dn).entry == 0


def test_signal_ignores_future_bars():
    s = FlowVote()
    candles = bars(s.warmup + 60, buy_share=noisy)
    k = s.warmup + 20
    a = FlowVote().signal(candles[:k])
    b = FlowVote().signal(PrefixView(candles, k))
    assert (a.entry, a.reason, a.features["flow_z"]) == (b.entry, b.reason, b.features["flow_z"])


def test_funding_gate_needs_funding_data():
    s = FlowVote(funding_gate=True)
    n = s.warmup + 40
    sig = s.signal(bars(n, buy_share=lambda i: 0.8 if i >= n - 8 else noisy(i)))
    assert sig.entry == 0 and sig.reason == "funding_unavailable"


def test_registry_builds_the_registered_candidates():
    reg = StrategyRegistry.load()
    ids = [i for i in reg.ids() if "flow_vote" in i]
    assert len(ids) == 4
    for i in ids:
        assert reg.build(i, market="BTCUSDT", family="daytrade").strategy_id == i
