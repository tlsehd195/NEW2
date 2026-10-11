from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone

from cointrader.data.binance_vision import BinanceVisionPositioning
from cointrader.features.positioning import causal_zscore, sample_at_decisions

D0 = datetime(2024, 1, 15, tzinfo=timezone.utc)


def _zip(rows, header="create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
                      "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.csv", header + "\n" + "\n".join(rows) + "\n")
    return buf.getvalue()


def test_positioning_parses_skips_bad_rows_and_reports_gaps():
    rows = ["2024-01-15 00:05:00,BTCUSDT,1,2,1.5,1.2,2.0,0.9", "2024-01-15 00:10:00,BTCUSDT,1,2,,1.2,2.0,0.9",
            "2024-01-15 00:15:00,BTCUSDT,1,2,0,1.2,2.0,0.9"]
    url = "https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2024-01-15.zip"
    f = BinanceVisionPositioning(transport=lambda u: _zip(rows) if u == url else None)
    pts = f.fetch("BTCUSDT", D0, D0 + timedelta(days=2))
    assert [(p.top_count, p.top_sum, p.all_count) for p in pts] == [(1.5, 1.2, 2.0)]
    assert f.skipped_rows == 2 and len(f.last_gaps) == 1


def test_positioning_old_header_is_a_gap_not_a_crash():
    f = BinanceVisionPositioning(transport=lambda u: _zip(["2021-05-01 00:05:00,BTCUSDT,1,2"], "create_time,symbol,sum_open_interest,sum_open_interest_value"))
    assert f.fetch("BTCUSDT", D0, D0 + timedelta(days=1)) == [] and "schema" in f.last_gaps[0].detail


def test_sample_is_causal_and_stale_is_missing():
    times = [D0 + timedelta(minutes=5 * k) for k in range(4)]
    got = sample_at_decisions(times, [10.0, 11.0, 12.0, 13.0], [D0 + timedelta(minutes=10), D0 + timedelta(hours=3)])
    assert got == [11.0, None]  # at 00:10 the 00:05 row is the newest usable one; 3h later everything is stale


def test_zscore_excludes_current_value_and_needs_history():
    x = [1.0, 2.0] * 10 + [5.0]
    z = causal_zscore(x, 10)
    assert z[0] is None and z[5] is None
    assert z[-1] is not None and z[-1] > 3
    # changing the current value must not change the window: z is monotone in x[i] with the same history
    assert causal_zscore(x[:-1] + [6.0], 10)[-1] > z[-1]
