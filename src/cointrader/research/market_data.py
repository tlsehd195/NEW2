"""Historical Binance USD-M data for research scripts.

Uses the static archive (data.binance.vision), which is reachable from
GitHub Actions (the live API returns HTTP 451 there, ADR-0012). Missing
archive files are reported, never filled in."""

from __future__ import annotations

from datetime import datetime

from cointrader.backtest.event_engine import FuturesTerms
from cointrader.data.binance_vision import (
    BinanceVisionFundingRateHistory, BinanceVisionFuturesCandles, BinanceVisionOpenInterestHistory,
)
from cointrader.data.models import Candle, Timeframe
from cointrader.data.quality import check_candles


def load_candles(symbol: str, timeframe: Timeframe, start: datetime, end: datetime) -> tuple[list[Candle], list[str]]:
    fetcher = BinanceVisionFuturesCandles()
    candles = fetcher.fetch(symbol, timeframe, start, end)
    notes = [f"archive gap: {g}" for g in getattr(fetcher, "last_gaps", ())]
    notes += [f"quality: {i.kind}@{i.at.isoformat()}" for i in check_candles(candles)][:50]
    return candles, notes


def load_futures_terms(symbol: str, start: datetime, end: datetime, *, leverage: float = 3.0) -> tuple[FuturesTerms, list[str]]:
    hist = BinanceVisionFundingRateHistory()
    records = hist.fetch(symbol, start, end)
    notes = [f"funding archive gap: {g}" for g in hist.last_gaps]
    return FuturesTerms(margin_leverage=leverage, funding={r.funding_time: r.funding_rate for r in records}), notes


def load_open_interest(symbol: str, start: datetime, end: datetime) -> tuple[list, list[str]]:
    """Daily open-interest points; archive gaps are reported, never filled."""
    hist = BinanceVisionOpenInterestHistory()
    points = hist.fetch(symbol, start, end)
    return points, [f"open interest archive gap: {g}" for g in hist.last_gaps]


def load_funding(symbol: str, start: datetime, end: datetime) -> tuple[dict, list[str]]:
    """Settlement time -> rate; archive gaps are reported, never filled."""
    hist = BinanceVisionFundingRateHistory()
    records = hist.fetch(symbol, start, end)
    return {r.funding_time: r.funding_rate for r in records}, [f"funding archive gap: {g}" for g in hist.last_gaps]
