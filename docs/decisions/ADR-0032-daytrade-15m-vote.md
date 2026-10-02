# ADR-0032: 15m day-trade indicator vote, time stop, entry cap, quality window

**Status:** Accepted (implementation only; no hypothesis registered, nothing run on real data)
**Date:** 2026-10-03
**Deciders:** account owner (15m only, long and short, 12 h hold, entry cap 100), Claude Code session

## Context

ADR-0030 designed day trading on 15m bars; ADR-0031 added the `daytrade` kind. This implements steps 2
and 3 of the ADR-0030 plan.

## Decision

- `DayTradeVote` (`strategies/daytrade.py`), a subclass of `IndicatorVote`. Same 6 voters, calibration
  and combination; long and short; optional funding/OI side votes. Candidate ids
  `daytrade_indicator_vote[_side]_h{16,48}_c0.6_v1`, four of them in `configs/strategies.json`
  (horizon x side data), markets BTC/ETH/SOL/XRP (locks still checked per market and window).
- Constants, fixed from reasoning, not from results: `score_scale` 0.1, `horizon` 16/48 bars, `fit_lookback`
  1000, vol gate 96 vs 960 bars, `bars_per_day` 96, `max_hold_bars` 48, `max_entries_per_day` 100,
  `quality_window_bars` 192.
- Correction to ADR-0030: only `ema_trend`, `roc` (and `vwap_dev`, not in the default panel) have a
  return-sized saturation constant, so only those are scaled. `macd_hist` is already ATR-normalised and
  is not scaled.
- Shared code, default off so swing results do not change: `raw_scores(..., scale=1.0)`;
  `IndicatorVote` fields `score_scale`, `vol_short`, `vol_long`, `bars_per_day`, `max_hold_bars`,
  `max_entries_per_day`, `quality_window_bars`. The swing strategy id is now built from `family`
  (identical string for swing) and its `parameters` list the new knobs only when they differ from the
  defaults, so the registered swing records are byte-identical.
- Event engine reads the optional strategy attributes: a position held `max_hold_bars` is closed at
  the next bar open (`exit_reason="time_stop"`); entries beyond `max_entries_per_day` per UTC day are
  rejected and counted (`entry_cap_per_day`); `quality_window_bars` replaces the warm-up-sized
  data-quality look-back (ADR-0028 finding 2). Costs, funding and every risk-engine protection are
  unchanged and still applied per trade.
- Open interest is a daily series: the OI-confirm vote now takes one close per `bars_per_day` step, so price
  and OI cover the same days (for daily bars this is exactly the old `prefix[-30:]`).

## Not done

- No hypothesis registered, no budget used. Earliest default registration is still 2026-10-29 02:32:24 UTC.
- Speed (measured, correcting ADR-0030's "about 100x slower, needs an incremental calibrator"): a timing run
  on generated candles (timing only, not a result) took ~9 ms per decision bar and 6.6 s for one fold
  (1,200 warm-up bars + 672 scored bars). A 16-fold study of four candidates is therefore on the order
  of 10 minutes plus integrity checks and the TEST, which fits one Actions job. The speed PR is dropped
  unless a real-data timing run on Actions says otherwise.
- Coverage preflight and signal-count diagnostic on unlocked 15m ranges (no returns) come after the speed work.
