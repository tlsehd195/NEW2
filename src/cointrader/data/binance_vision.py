"""Binance USDⓈ-M futures AND spot historical data via the public
`data.binance.vision` static-file archive -- NOT the live REST API (see
`binance_funding.py` / `binance_futures.py` for that). Spot support
(`BinanceVisionSpotCandles`) was added under ADR-0013 to measure a real
spot-vs-futures basis for hedged carry, alongside the futures-only fetchers
built under ADR-0012.

Why this exists (ADR-0012): `fapi.binance.com` returns HTTP 451 (region
block) from GitHub Actions, confirmed 2026-09-28. `data.binance.vision` is a
plain static-file CDN (not an exchange API) and answered HTTP 200 to the
same runner in the same run. This module is **research/backtest-only**: it
can only ever serve months/days Binance has already published as an
archive, so it must never be used for anything needing the current price or
funding rate, and never for live trading.

Archive CSV schemas below were confirmed empirically against real files
fetched from a GitHub Actions job (not assumed from documentation) -- see
the connectivity-check run referenced in ADR-0012:

    fundingRate: calc_time,funding_interval_hours,last_funding_rate
        (epoch calc_time; no mark-price column -- unlike the live REST
        endpoint, which does include one)
    klines: open_time,open,high,low,close,volume,close_time,quote_volume,
        count,taker_buy_volume,taker_buy_quote_volume,ignore
        (identical field order to the live REST kline array)

Epoch columns (`calc_time`, `open_time`) are milliseconds in files
confirmed under ADR-0012/ADR-0013, but a real H-0012 run hit a spot
klines file using microseconds instead, with no schema-version marker to
tell the two apart -- Binance changed this at some point in its archive
history. `_epoch_to_datetime` detects the unit by magnitude rather than
assuming milliseconds everywhere (see its docstring).

Every `FundingRateRecord` this module returns carries
`source="binance_vision_archive"` and `mark_price=NaN` -- the archive has
no mark-price column, so it is never guessed here (CLAUDE.md rule 4/5).
`join_mark_price_from_candles` fills it explicitly and transparently (from
kline close price, a documented approximation of the exchange's real mark
index) before handing records to `funding_carry_engine`, which otherwise
treats every NaN mark_price as a fail-closed gap.
"""

from __future__ import annotations

import csv
import io
import math
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

from cointrader._time import require_aware
from cointrader.data.binance_funding import FundingRateRecord
from cointrader.data.models import Candle, Timeframe

BASE_URL = "https://data.binance.vision/data/futures/um"
SPOT_BASE_URL = "https://data.binance.vision/data/spot"
SOURCE = "binance_vision_archive"

FUNDING_COLUMNS = ("calc_time", "funding_interval_hours", "last_funding_rate")
# Daily "metrics" files (5-minute rows). Column names are from the archive's
# published schema and have NOT been verified from this sandbox (the proxy
# blocks data.binance.vision); `_parse_csv_rows` fails closed on a mismatch.
METRICS_COLUMNS = ("create_time", "symbol", "sum_open_interest", "sum_open_interest_value")
KLINE_COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)

_INTERVALS = {
    Timeframe.MINUTE_1: "1m",
    Timeframe.MINUTE_3: "3m",
    Timeframe.MINUTE_5: "5m",
    Timeframe.MINUTE_15: "15m",
    Timeframe.HOUR_1: "1h",
    Timeframe.HOUR_4: "4h",
    Timeframe.DAY_1: "1d",
}

# None means "the archive does not have this file" (HTTP 404) -- a real,
# expected outcome (e.g. the exchange skipped publishing a day), not an
# error. Any other failure propagates instead of being swallowed.
Transport = Callable[[str], Optional[bytes]]


def _urllib_transport(url: str) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/zip"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


@dataclass(frozen=True)
class ArchiveGap:
    """A requested archive file was missing (404). Callers must surface
    this, never silently treat a gap as "no data existed" (CLAUDE.md rule 4)."""

    at: datetime
    detail: str


def _add_month(dt: datetime) -> datetime:
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1)
    return dt.replace(month=dt.month + 1)


def _month_starts(start: datetime, end: datetime):
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cursor < end:
        yield cursor
        cursor = _add_month(cursor)


def _days(start: datetime, end: datetime):
    cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while cursor < end:
        yield cursor
        cursor += timedelta(days=1)


def _looks_like_epoch_millis(token: str) -> bool:
    try:
        int(token)
        return True
    except ValueError:
        return False


def _parse_csv_rows(zip_bytes: bytes, expected_columns: tuple[str, ...]) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected exactly one CSV in archive, found {names}")
        raw = zf.read(names[0]).decode("utf-8")
    lines = raw.splitlines()
    if not lines:
        return []
    # Some older archives ship without a header row; detect by whether the
    # first field of the first line parses as an epoch (a header's first
    # field is a column name and never will).
    has_header = not _looks_like_epoch_millis(lines[0].split(",")[0])
    if has_header:
        reader = csv.DictReader(lines)
        fieldnames = reader.fieldnames or []
        if set(expected_columns) - set(fieldnames):
            raise ValueError(f"unexpected archive columns {fieldnames}, expected {expected_columns}")
        return list(reader)
    return [dict(zip(expected_columns, row)) for row in csv.reader(lines)]


# Binance switched some archive files (confirmed on spot klines; unclear
# which others) from millisecond to microsecond epoch timestamps at some
# point without a schema-version marker -- discovered when a real H-0012
# run crashed with "year 56971 is out of range" (a millisecond-assumed
# divide against a microsecond value). A millisecond epoch for any date
# in this project's realistic range (2015-2100) is below 1e13; a
# microsecond epoch for the same range is above 1e15. 1e14 is comfortably
# between the two, so every archive timestamp column is passed through
# this instead of a hardcoded "/ 1000" (CLAUDE.md rule 4: never guess a
# unit and get a silently wrong timestamp instead of a clear failure).
_MICROSECOND_THRESHOLD = 10**14


def _epoch_to_datetime(raw: str) -> datetime:
    value = int(raw)
    if value >= _MICROSECOND_THRESHOLD:
        return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _funding_row_to_record(symbol: str, row: dict) -> FundingRateRecord:
    return FundingRateRecord(
        symbol=symbol,
        funding_time=_epoch_to_datetime(row["calc_time"]),
        funding_rate=float(row["last_funding_rate"]),
        mark_price=float("nan"),
        source=SOURCE,
    )


def _kline_row_to_candle(
    symbol: str, timeframe: Timeframe, row: dict, received_at: datetime, *, source: str = SOURCE,
) -> Candle:
    return Candle(
        market=symbol,
        timeframe=timeframe,
        open_time=_epoch_to_datetime(row["open_time"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        source=source,
        received_at=received_at,
        taker_buy_volume=float(row["taker_buy_volume"]) if row.get("taker_buy_volume") not in (None, "") else None,
    )


class BinanceVisionFundingRateHistory:
    """Historical funding settlements from the static archive. `fetch`
    uses monthly files for any month that has already fully elapsed, and
    falls back to daily files for the trailing partial month (the
    exchange only publishes a month's archive after it ends)."""

    def __init__(self, *, transport: Transport = _urllib_transport) -> None:
        self._transport = transport
        self.last_gaps: tuple[ArchiveGap, ...] = ()

    def fetch(
        self, symbol: str, start: datetime, end: datetime, *, now: Optional[datetime] = None,
    ) -> list[FundingRateRecord]:
        require_aware("start", start)
        require_aware("end", end)
        now = now or datetime.now(timezone.utc)
        if end <= start:
            self.last_gaps = ()
            return []
        records: dict[datetime, FundingRateRecord] = {}
        gaps: list[ArchiveGap] = []
        for month_start in _month_starts(start, end):
            month_end = _add_month(month_start)
            if month_end <= now:
                url = f"{BASE_URL}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month_start:%Y-%m}.zip"
                body = self._transport(url)
                if body is None:
                    gaps.append(ArchiveGap(month_start, f"monthly funding archive missing: {url}"))
                    continue
                for row in _parse_csv_rows(body, FUNDING_COLUMNS):
                    rec = _funding_row_to_record(symbol, row)
                    if start <= rec.funding_time < end:
                        records[rec.funding_time] = rec
            else:
                for day in _days(max(month_start, start), min(month_end, end)):
                    url = f"{BASE_URL}/daily/fundingRate/{symbol}/{symbol}-fundingRate-{day:%Y-%m-%d}.zip"
                    body = self._transport(url)
                    if body is None:
                        gaps.append(ArchiveGap(day, f"daily funding archive missing: {url}"))
                        continue
                    for row in _parse_csv_rows(body, FUNDING_COLUMNS):
                        rec = _funding_row_to_record(symbol, row)
                        if start <= rec.funding_time < end:
                            records[rec.funding_time] = rec
        self.last_gaps = tuple(gaps)
        return sorted(records.values(), key=lambda r: r.funding_time)


@dataclass(frozen=True)
class OpenInterestPoint:
    """Last 5-minute open-interest reading of one UTC day. `as_of` is that
    row's own timestamp: the value is usable only from `as_of` onward."""

    symbol: str
    as_of: datetime
    open_interest: float  # base-asset contracts
    source: str = SOURCE


def _metrics_time(raw: str) -> datetime:
    return datetime.strptime(raw.strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


class BinanceVisionOpenInterestHistory:
    """One `OpenInterestPoint` per UTC day from the daily metrics archive
    (last row of the day). A missing day is an `ArchiveGap`, never filled."""

    def __init__(self, *, transport: Transport = _urllib_transport) -> None:
        self._transport = transport
        self.last_gaps: tuple[ArchiveGap, ...] = ()

    def fetch(self, symbol: str, start: datetime, end: datetime) -> list[OpenInterestPoint]:
        require_aware("start", start)
        require_aware("end", end)
        points: list[OpenInterestPoint] = []
        gaps: list[ArchiveGap] = []
        for day in _days(start, end):
            url = f"{BASE_URL}/daily/metrics/{symbol}/{symbol}-metrics-{day:%Y-%m-%d}.zip"
            body = self._transport(url)
            if body is None:
                gaps.append(ArchiveGap(day, f"daily metrics archive missing: {url}"))
                continue
            rows = _parse_csv_rows(body, METRICS_COLUMNS)
            if not rows:
                gaps.append(ArchiveGap(day, f"daily metrics archive empty: {url}"))
                continue
            last = max(rows, key=lambda r: _metrics_time(r["create_time"]))
            points.append(OpenInterestPoint(symbol, _metrics_time(last["create_time"]), float(last["sum_open_interest"])))
        self.last_gaps = tuple(gaps)
        return points


class _KlineArchiveCandles:
    """Shared monthly-then-daily-fallback kline fetch, parameterized by
    archive base URL -- futures (`.../data/futures/um`) and spot
    (`.../data/spot`) publish klines in the identical layout and CSV
    schema, only the base path differs. Subclasses fix `_base_url` and
    `source` so callers can never confuse which market a `Candle` came
    from (CLAUDE.md rule 5)."""

    _base_url: str = BASE_URL
    _source: str = SOURCE

    def __init__(
        self,
        *,
        transport: Transport = _urllib_transport,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._transport = transport
        self._now = now
        self.last_gaps: tuple[ArchiveGap, ...] = ()

    def _fetch_days(self, symbol, timeframe, interval, first_day, stop, start, end, received_at, by_time) -> list[ArchiveGap]:
        gaps: list[ArchiveGap] = []
        for day in _days(first_day, stop):
            url = f"{self._base_url}/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{day:%Y-%m-%d}.zip"
            body = self._transport(url)
            if body is None:
                gaps.append(ArchiveGap(day, f"daily klines archive missing: {url}"))
                continue
            for row in _parse_csv_rows(body, KLINE_COLUMNS):
                c = _kline_row_to_candle(symbol, timeframe, row, received_at, source=self._source)
                if start <= c.open_time < end and c.close_time <= received_at:
                    by_time[c.open_time] = c
        return gaps

    def fetch(self, symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> list[Candle]:
        require_aware("start", start)
        require_aware("end", end)
        if timeframe not in _INTERVALS:
            raise ValueError(f"unsupported timeframe for Binance vision klines: {timeframe!r}")
        received_at = self._now()
        if end <= start:
            self.last_gaps = ()
            return []
        interval = _INTERVALS[timeframe]
        by_time: dict[datetime, Candle] = {}
        gaps: list[ArchiveGap] = []
        for month_start in _month_starts(start, end):
            month_end = _add_month(month_start)
            if month_end <= received_at:
                url = f"{self._base_url}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{month_start:%Y-%m}.zip"
                body = self._transport(url)
                if body is not None:
                    for row in _parse_csv_rows(body, KLINE_COLUMNS):
                        c = _kline_row_to_candle(symbol, timeframe, row, received_at, source=self._source)
                        if start <= c.open_time < end and c.close_time <= received_at:
                            by_time[c.open_time] = c
                    continue
                # Monthly file absent (e.g. a symbol's first, partial month): fall back to its daily files.
                # Same archive, same `source`; every day still missing is reported as a gap.
                first_day, stop = max(month_start, start), min(month_end, end)
                month_gaps = self._fetch_days(symbol, timeframe, interval, first_day, stop, start, end,
                                              received_at, by_time)
                if len(month_gaps) == len(list(_days(first_day, stop))):
                    gaps.append(ArchiveGap(month_start, f"monthly klines archive missing (no daily files either): {url}"))
                else:
                    gaps.extend(month_gaps)
            else:
                gaps.extend(self._fetch_days(symbol, timeframe, interval, max(month_start, start),
                                             min(month_end, end), start, end, received_at, by_time))
        self.last_gaps = tuple(gaps)
        return [c for _, c in sorted(by_time.items())]


class BinanceVisionFuturesCandles(_KlineArchiveCandles):
    """Historical USDⓈ-M futures klines from the static archive."""

    _base_url = BASE_URL
    _source = SOURCE


class BinanceVisionSpotCandles(_KlineArchiveCandles):
    """Historical spot klines from the static archive -- same layout and
    CSV schema as futures (confirmed empirically, see ADR-0013), only the
    base path differs. Needed to measure the real spot-vs-futures basis
    for a hedged carry trade (ADR-0013): the futures-only archive this
    module already served (`BinanceVisionFuturesCandles`) cannot compute
    a basis on its own. `source` is distinctly tagged
    `binance_vision_archive_spot` so a joined series can never silently
    mix a spot candle into a futures-only calculation or vice versa."""

    _base_url = SPOT_BASE_URL
    _source = f"{SOURCE}_spot"


def join_mark_price_from_candles(
    records: Sequence[FundingRateRecord], candles: Sequence[Candle],
) -> list[FundingRateRecord]:
    """Fills `mark_price` on archive records (NaN by construction) from
    the closest candle close at-or-before each settlement -- an explicit,
    documented approximation of the exchange's real mark-price index, not
    a silent guess. Records with no eligible candle keep `mark_price=NaN`
    (still a fail-closed gap downstream); NaN inputs are never overwritten
    with a non-NaN value that was itself invented."""
    sorted_candles = sorted(candles, key=lambda c: c.open_time)
    out = []
    idx = 0
    last_close: Optional[float] = None
    for record in sorted(records, key=lambda r: r.funding_time):
        while idx < len(sorted_candles) and sorted_candles[idx].close_time <= record.funding_time:
            last_close = sorted_candles[idx].close
            idx += 1
        if last_close is None or not math.isfinite(last_close):
            out.append(record)
            continue
        out.append(FundingRateRecord(
            symbol=record.symbol,
            funding_time=record.funding_time,
            funding_rate=record.funding_rate,
            mark_price=last_close,
            source=f"{record.source}+kline_close",
        ))
    return out
