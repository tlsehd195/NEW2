"""15-minute day-trading indicator vote (ADR-0030, ADR-0032).

Same vote as `IndicatorVote` (6 calibrated indicators, equal-weight log-odds, long and short), with the
knobs that depend on bar size set once, from reasoning about 15m bars rather than from any result:

- `score_scale` 0.1: a 15m move is about 1/sqrt(96) ~ 1/10 of a daily one, so the return-sized score
  constants shrink to match; otherwise every score sits near 0 and the Platt prior keeps P near 0.5.
- `horizon` 16 / 48 bars (4 h / 12 h), `fit_lookback` 1000 bars (~10 days).
- vol gate on 96 bars (1 day) against 960 bars (10 days).
- `max_hold_bars` 48 (12 h time stop), `max_entries_per_day` 100 (a runaway ceiling, owner's choice),
  `quality_window_bars` 192 (2 days: a candle hole blocks entries for 2 days, not for the whole warm-up).
- `bars_per_day` 96 so the daily open-interest vote compares price over the same days.
"""

from __future__ import annotations

from dataclasses import dataclass

from cointrader.strategies.indicator_vote import IndicatorVote


@dataclass(frozen=True)
class DayTradeVote(IndicatorVote):
    horizon: int = 16
    fit_lookback: int = 1000
    timeframe: str = "15m"
    family: str = "daytrade"
    version: str = "1"
    score_scale: float = 0.1
    vol_short: int = 96
    vol_long: int = 960
    bars_per_day: int = 96
    max_hold_bars: int = 48
    max_entries_per_day: int = 100
    quality_window_bars: int = 192
