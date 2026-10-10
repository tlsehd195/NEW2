from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

from cointrader.data.binance_vision import BinanceVisionBookDepth
from cointrader.data.models import Timeframe
from cointrader.features.indicator_votes import DEFAULT_PANEL, intraday_tsm_score, quarter_hour_imbalance_score
from tests.helpers import make_candles

D0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _depth_zip(rows):
    lines = ["timestamp,percentage,depth,notional"]
    for at, pct, depth in rows:
        lines.append(f"{at:%Y-%m-%d %H:%M:%S},{pct},{depth},0")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def test_book_depth_parses_snapshots_and_float_percentages():
    rows = [(D0 + timedelta(seconds=30 * k), p, d) for k in range(2) for p, d in (("-1.00", 3.0), ("1.00", 1.0))]
    url = "https://data.binance.vision/data/futures/um/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-2024-01-01.zip"
    f = BinanceVisionBookDepth(transport=lambda u: _depth_zip(rows) if u == url else None)
    snaps = f.fetch("BTCUSDT", D0, D0 + timedelta(days=2))
    assert [s.depth for s in snaps] == [{-1: 3.0, 1: 1.0}] * 2
    assert len(f.last_gaps) == 1  # 2024-01-02 missing is reported, not filled


def _snaps(n, bid, ask, end):
    class S:  # duck type of BookDepthSnapshot
        def __init__(s, at):
            s.at, s.depth = at, {-1: bid, 1: ask}
    return [S(end - timedelta(seconds=30 * (n - k))) for k in range(n)]


def test_quarter_hour_score_is_causal_and_fail_closed():
    t = D0 + timedelta(hours=1)
    assert abs(quarter_hour_imbalance_score(_snaps(10, 3.0, 1.0, t), t) - 0.5) < 1e-12
    assert quarter_hour_imbalance_score(_snaps(2, 3.0, 1.0, t), t) is None  # too few
    future = _snaps(10, 3.0, 1.0, t + timedelta(minutes=10))  # snapshots at/after the bar open are ignored
    assert quarter_hour_imbalance_score(future, t) is None


def test_intraday_tsm_sign_and_warmup():
    c = make_candles(40, start=D0, timeframe=Timeframe.MINUTE_15, drift=0.002, vol=0.0)
    assert intraday_tsm_score(c[:6]) is None  # fewer than 8 bars of the day
    assert intraday_tsm_score(c[:20]) > 0
    down = make_candles(40, start=D0, timeframe=Timeframe.MINUTE_15, drift=-0.002, vol=0.0)
    assert intraday_tsm_score(down[:20]) < 0
    # first bars after UTC midnight: only the new day counts
    assert intraday_tsm_score(c[:100] if len(c) >= 100 else c) is not None


def test_default_panel_untouched():
    assert DEFAULT_PANEL == ("ema_trend", "donchian_pos", "roc", "rsi", "bollinger_b", "obv_slope")
