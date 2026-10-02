from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.binance_vision import BinanceVisionOpenInterestHistory, OpenInterestPoint
from cointrader.features.side_indicators import (
    funding_crowding_score, known_funding, known_open_interest, oi_confirmation_score, volatility_ratio,
)
from cointrader.strategies.indicator_vote import IndicatorVote
from tests.helpers import T0, make_candles
from cointrader.data.models import Timeframe


def _zip(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("m.csv", csv_text)
    return buf.getvalue()


def test_oi_loader_takes_last_row_of_each_day_and_reports_gaps():
    day = datetime(2023, 1, 1, tzinfo=timezone.utc)
    body = _zip("create_time,symbol,sum_open_interest,sum_open_interest_value\n"
                "2023-01-01 00:05:00,BTCUSDT,100.0,1\n2023-01-01 23:55:00,BTCUSDT,120.5,2\n")

    def transport(url):
        return body if "2023-01-01" in url else None

    h = BinanceVisionOpenInterestHistory(transport=transport)
    pts = h.fetch("BTCUSDT", day, day + timedelta(days=2))
    assert [p.open_interest for p in pts] == [120.5]
    assert pts[0].as_of == datetime(2023, 1, 1, 23, 55, tzinfo=timezone.utc)
    assert len(h.last_gaps) == 1


def test_oi_loader_fails_closed_on_unexpected_columns():
    h = BinanceVisionOpenInterestHistory(transport=lambda url: _zip("a,b\n1,2\n"))
    with pytest.raises(ValueError):
        h.fetch("BTCUSDT", T0, T0 + timedelta(days=1))


def _fund(n, rate, start=T0):
    return [FundingRateRecord("BTCUSDT", start + timedelta(hours=8 * i), rate, float("nan"), "t") for i in range(n)]


def test_funding_score_is_contrarian_and_needs_history():
    assert funding_crowding_score(_fund(3, 0.001)) is None
    assert funding_crowding_score(_fund(9, 0.001)) < -0.9
    assert funding_crowding_score(_fund(9, -0.001)) > 0.9
    assert abs(funding_crowding_score(_fund(9, 0.0))) < 1e-12


def _oi(values, start=T0):
    return [OpenInterestPoint("BTCUSDT", start + timedelta(days=i, hours=23, minutes=55), v) for i, v in enumerate(values)]


def test_oi_score_confirms_only_when_oi_rises():
    closes_up = [100 + 2 * i for i in range(10)]
    assert oi_confirmation_score(_oi([100 + 5 * i for i in range(10)]), closes_up) > 0
    assert oi_confirmation_score(_oi([200 - 5 * i for i in range(10)]), closes_up) == 0.0
    assert oi_confirmation_score(_oi([100] * 3), closes_up) is None


def test_known_filters_are_as_of():
    f = _fund(6, 0.0001)
    assert len(known_funding(f, T0 + timedelta(hours=16))) == 3
    o = _oi([1, 2, 3])
    assert len(known_open_interest(o, T0 + timedelta(days=1, hours=23, minutes=55))) == 2


def test_volatility_ratio_detects_turbulence():
    calm = make_candles(120, seed=1, vol=0.005)
    wild = calm[:110] + make_candles(10, seed=2, vol=0.06, start=calm[109].open_time + calm[0].timeframe.delta)
    assert volatility_ratio(calm[:50]) is None
    assert volatility_ratio(wild) > 2.0


def _strategy(**kw):
    return IndicatorVote(horizon=5, fit_lookback=60, **kw)


def test_side_candidate_is_flat_without_side_data_and_id_differs():
    s = _strategy(use_side_data=True)
    assert "_side" in s.strategy_id and "_side" not in _strategy().strategy_id
    candles = make_candles(s.warmup + 5, seed=3, timeframe=Timeframe.DAY_1)
    sig = s.signal(candles)
    assert sig.entry == 0 and sig.reason == "warmup_or_indicator_unavailable"


def test_side_data_is_as_of_future_records_do_not_change_verdict():
    s = _strategy(use_side_data=True)
    candles = make_candles(s.warmup + 5, seed=3, timeframe=Timeframe.DAY_1, vol=0.01)
    t = len(candles)
    fund = _fund(3 * t + 60, 0.0002, start=candles[0].open_time - timedelta(days=30))
    oi = _oi([1000 + i for i in range(t + 40)], start=candles[0].open_time - timedelta(days=30))
    base = s.attach_side_data(funding=fund, open_interest=oi)
    v1 = base.verdict(candles)
    assert v1 is not None and "funding_crowding" in v1.per_indicator and "oi_confirm" in v1.per_indicator
    cut = candles[-1].close_time
    future_changed = [r for r in fund if r.funding_time <= cut] + _fund(10, 0.05, start=cut + timedelta(hours=1))
    v2 = s.attach_side_data(funding=future_changed, open_interest=oi).verdict(candles)
    assert v2.p_long == v1.p_long


def test_vol_gate_blocks_entries_but_not_exits():
    s = _strategy(vol_gate_hi=1.01)  # gate shuts almost always
    candles = make_candles(s.warmup + 5, seed=4, timeframe=Timeframe.DAY_1, vol=0.01)
    sig = s.signal(candles)
    assert sig.entry == 0
    with pytest.raises(ValueError):
        IndicatorVote(vol_gate_hi=0.9)


def test_strategy_can_actually_enter_on_a_trending_series():
    """Regression: an ATR window one bar short made every signal 'atr_or_vol_unavailable'."""
    s = IndicatorVote(horizon=5, fit_lookback=100, enter_confidence=0.55, exit_confidence=0.51, min_agree=0.5)
    candles = make_candles(s.warmup + 120, seed=21, timeframe=Timeframe.DAY_1, drift=0.004, vol=0.012)
    reasons = {s.signal(candles[:end]).reason for end in range(s.warmup, len(candles), 4)}
    assert "atr_or_vol_unavailable" not in reasons and "warmup_or_indicator_unavailable" not in reasons
    assert reasons & {"vote_long", "vote_short"}


def test_entry_signals_carry_a_defined_regime_so_the_risk_engine_accepts_them():
    """Regression: Signal.regime defaulted to UNDEFINED and the risk engine rejected every entry."""
    s = IndicatorVote(horizon=5, fit_lookback=100, enter_confidence=0.55, exit_confidence=0.51, min_agree=0.5)
    candles = make_candles(s.warmup + 120, seed=21, timeframe=Timeframe.DAY_1, drift=0.004, vol=0.012)
    entries = [sg for sg in (s.signal(candles[:e]) for e in range(s.warmup, len(candles), 4)) if sg.entry != 0]
    assert entries and all(sg.regime != "UNDEFINED" for sg in entries)
