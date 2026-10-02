from __future__ import annotations

import io
import math
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.data.binance_vision import (
    BinanceVisionFundingRateHistory,
    BinanceVisionFuturesCandles,
    BinanceVisionSpotCandles,
    join_mark_price_from_candles,
)
from cointrader.data.models import Timeframe
from cointrader.data.binance_funding import FundingRateRecord

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _zip_csv(name: str, lines: list[str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, "\n".join(lines) + "\n")
    return buf.getvalue()


def _funding_zip(symbol: str, month: str, rows: list[tuple[datetime, float]]) -> bytes:
    lines = ["calc_time,funding_interval_hours,last_funding_rate"]
    for t, rate in rows:
        lines.append(f"{int(t.timestamp() * 1000)},8,{rate}")
    return _zip_csv(f"{symbol}-fundingRate-{month}.csv", lines)


def _kline_zip(symbol: str, interval: str, period: str, rows: list[tuple[datetime, float, float, float, float, float]]) -> bytes:
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"]
    for open_time, o, h, l, c, v in rows:
        close_time_ms = int((open_time + Timeframe.HOUR_1.delta).timestamp() * 1000) - 1
        lines.append(f"{int(open_time.timestamp() * 1000)},{o},{h},{l},{c},{v},{close_time_ms},0,0,0,0,0")
    return _zip_csv(f"{symbol}-{interval}-{period}.csv", lines)


class TestFundingArchive:
    def test_fetch_from_two_monthly_archives(self):
        jan_rows = [(T0 + timedelta(hours=8 * i), 0.0001 * i) for i in range(3)]
        feb_start = datetime(2024, 2, 1, tzinfo=timezone.utc)
        feb_rows = [(feb_start + timedelta(hours=8 * i), 0.0002 * i) for i in range(3)]
        archives = {
            "https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip":
                _funding_zip("BTCUSDT", "2024-01", jan_rows),
            "https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-02.zip":
                _funding_zip("BTCUSDT", "2024-02", feb_rows),
        }
        client = BinanceVisionFundingRateHistory(transport=lambda url: archives.get(url))
        now = datetime(2024, 3, 15, tzinfo=timezone.utc)
        records = client.fetch("BTCUSDT", T0, datetime(2024, 3, 1, tzinfo=timezone.utc), now=now)
        assert [r.funding_time for r in records] == sorted(t for t, _ in jan_rows + feb_rows)
        assert all(math.isnan(r.mark_price) for r in records)
        assert all(r.source == "binance_vision_archive" for r in records)
        assert client.last_gaps == ()

    def test_missing_archive_file_becomes_a_reported_gap_not_silence(self):
        client = BinanceVisionFundingRateHistory(transport=lambda url: None)
        now = datetime(2024, 3, 1, tzinfo=timezone.utc)
        records = client.fetch("BTCUSDT", T0, datetime(2024, 2, 1, tzinfo=timezone.utc), now=now)
        assert records == []
        assert len(client.last_gaps) == 1
        assert "monthly funding archive missing" in client.last_gaps[0].detail

    def test_trailing_partial_month_falls_back_to_daily_archives(self):
        now = datetime(2024, 1, 10, 12, tzinfo=timezone.utc)
        day1 = datetime(2024, 1, 8, tzinfo=timezone.utc)
        day2 = datetime(2024, 1, 9, tzinfo=timezone.utc)
        archives = {
            f"https://data.binance.vision/data/futures/um/daily/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01-08.zip":
                _funding_zip("BTCUSDT", "2024-01-08", [(day1 + timedelta(hours=8), 0.0001)]),
            f"https://data.binance.vision/data/futures/um/daily/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01-09.zip":
                _funding_zip("BTCUSDT", "2024-01-09", [(day2 + timedelta(hours=8), 0.0002)]),
        }
        client = BinanceVisionFundingRateHistory(transport=lambda url: archives.get(url))
        records = client.fetch("BTCUSDT", day1, datetime(2024, 1, 10, tzinfo=timezone.utc), now=now)
        assert [round(r.funding_rate, 4) for r in records] == [0.0001, 0.0002]

    def test_excludes_records_outside_requested_range(self):
        rows = [(T0 + timedelta(hours=8 * i), 0.0001 * i) for i in range(6)]
        archives = {
            "https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip":
                _funding_zip("BTCUSDT", "2024-01", rows),
        }
        client = BinanceVisionFundingRateHistory(transport=lambda url: archives.get(url))
        now = datetime(2024, 3, 1, tzinfo=timezone.utc)
        start = T0 + timedelta(hours=16)
        end = T0 + timedelta(hours=32)
        records = client.fetch("BTCUSDT", start, end, now=now)
        assert all(start <= r.funding_time < end for r in records)
        assert len(records) == 2


class TestKlineArchive:
    def test_fetch_from_monthly_archive(self):
        rows = [(T0 + timedelta(hours=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0) for i in range(5)]
        archives = {
            "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip":
                _kline_zip("BTCUSDT", "1h", "2024-01", rows),
        }
        received_at = datetime(2024, 3, 1, tzinfo=timezone.utc)
        client = BinanceVisionFuturesCandles(transport=lambda url: archives.get(url), now=lambda: received_at)
        candles = client.fetch("BTCUSDT", Timeframe.HOUR_1, T0, T0 + timedelta(hours=5))
        assert len(candles) == 5
        assert [c.open_time for c in candles] == [t for t, *_ in rows]
        assert all(c.source == "binance_vision_archive" for c in candles)

    def test_unsupported_timeframe_rejected(self):
        client = BinanceVisionFuturesCandles(transport=lambda url: None)
        with pytest.raises(ValueError):
            client.fetch("BTCUSDT", "not-a-timeframe", T0, T0 + timedelta(hours=5))


class TestMixedEpochUnits:
    def test_millisecond_and_microsecond_open_times_both_parse_correctly(self):
        # Regression for the real H-0012 crash ("year 56971 is out of
        # range"): a spot klines archive file used microsecond epoch
        # timestamps instead of the millisecond ones every file seen so
        # far under ADR-0012 used, with no schema marker to distinguish
        # them.
        ms_row = int(T0.timestamp() * 1000)
        us_row = int(T0.timestamp() * 1_000_000)
        lines = [
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore",
            f"{ms_row},100,101,99,100.5,10,{ms_row + 3599999},0,0,0,0,0",
        ]
        body = _zip_csv("BTCUSDT-1h-2024-01.csv", lines)
        received_at = datetime(2024, 3, 1, tzinfo=timezone.utc)
        client = BinanceVisionFuturesCandles(
            transport=lambda url: body if "2024-01" in url else None, now=lambda: received_at,
        )
        candles = client.fetch("BTCUSDT", Timeframe.HOUR_1, T0, T0 + timedelta(hours=1))
        assert candles[0].open_time == T0

        lines_us = [
            "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore",
            f"{us_row},100,101,99,100.5,10,{us_row + 3599999000},0,0,0,0,0",
        ]
        body_us = _zip_csv("BTCUSDT-1h-2024-01.csv", lines_us)
        client_us = BinanceVisionFuturesCandles(
            transport=lambda url: body_us if "2024-01" in url else None, now=lambda: received_at,
        )
        candles_us = client_us.fetch("BTCUSDT", Timeframe.HOUR_1, T0, T0 + timedelta(hours=1))
        assert candles_us[0].open_time == T0


class TestSpotKlineArchive:
    def test_fetch_from_monthly_archive_uses_spot_base_url_and_tags_source(self):
        rows = [(T0 + timedelta(hours=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0) for i in range(5)]
        archives = {
            "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip":
                _kline_zip("BTCUSDT", "1h", "2024-01", rows),
        }
        received_at = datetime(2024, 3, 1, tzinfo=timezone.utc)
        client = BinanceVisionSpotCandles(transport=lambda url: archives.get(url), now=lambda: received_at)
        candles = client.fetch("BTCUSDT", Timeframe.HOUR_1, T0, T0 + timedelta(hours=5))
        assert len(candles) == 5
        assert [c.open_time for c in candles] == [t for t, *_ in rows]
        # distinctly tagged so a spot candle can never be silently mixed
        # into a futures-only calculation (ADR-0013).
        assert all(c.source == "binance_vision_archive_spot" for c in candles)

    def test_spot_archive_does_not_answer_to_futures_url(self):
        # A spot request must never accidentally hit the futures base URL
        # (or vice versa) -- the two are different markets/prices.
        futures_only = {
            "https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip":
                _kline_zip("BTCUSDT", "1h", "2024-01", [(T0, 100.0, 101.0, 99.0, 100.5, 10.0)]),
        }
        client = BinanceVisionSpotCandles(transport=lambda url: futures_only.get(url), now=lambda: datetime(2024, 3, 1, tzinfo=timezone.utc))
        candles = client.fetch("BTCUSDT", Timeframe.HOUR_1, T0, T0 + timedelta(hours=1))
        assert candles == []
        assert len(client.last_gaps) == 1


class TestJoinMarkPrice:
    def test_fills_mark_price_from_closest_prior_candle_close(self):
        from tests.helpers import make_candles

        candles = make_candles(5, start=T0, timeframe=Timeframe.HOUR_1, market="BTCUSDT")
        records = [
            FundingRateRecord(symbol="BTCUSDT", funding_time=T0 + timedelta(hours=3), funding_rate=0.0001, mark_price=float("nan")),
        ]
        joined = join_mark_price_from_candles(records, candles)
        assert joined[0].mark_price == candles[2].close  # 3rd candle (index 2) closes at T0+3h
        assert joined[0].source.endswith("+kline_close")

    def test_no_eligible_candle_keeps_nan_not_invented(self):
        records = [
            FundingRateRecord(symbol="BTCUSDT", funding_time=T0 - timedelta(hours=1), funding_rate=0.0001, mark_price=float("nan")),
        ]
        from tests.helpers import make_candles
        candles = make_candles(3, start=T0, timeframe=Timeframe.HOUR_1, market="BTCUSDT")
        joined = join_mark_price_from_candles(records, candles)
        assert math.isnan(joined[0].mark_price)
