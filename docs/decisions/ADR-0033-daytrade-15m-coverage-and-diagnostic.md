# ADR-0033: 15m unlocked-range coverage and the day-trade signal-count diagnostic

**Status:** Accepted (measurement and tooling only; nothing registered, no returns read)
**Date:** 2026-10-03
**Deciders:** account owner (15m day trading, long and short), Claude Code session

## Coverage preflight on real 15m data (Actions `data_coverage.yml`, runs 37046452349 / 37046456676 / 37046461055)

Source `binance_vision_archive`. Every range below came back with **0 candle holes, 0 funding gaps and 0
open-interest gaps**:

| market | range checked | 15m bars | funding / OI |
|---|---|---|---|
| BTCUSDT | 2023-04-07 → 2024-06-18 | 42,048 | 1,314 / 438 |
| ETHUSDT | 2024-12-02 → 2026-08-31 | 61,152 | 1,911 / 637 |
| XRPUSDT | 2023-04-07 → 2024-01-24 | 28,032 | 876 / 292 |

## Unlocked ranges (from `configs/locked_windows.json`, USDT markets)

`assert_not_locked` blocks any overlap of train, validation, test **and the ~12.5-day warm-up** with a lock
on the same market, whichever candidate. Unlocked:

- BTCUSDT: 2023-04-06 → 2024-06-18 16:00 (locked before and after).
- ETHUSDT: from 2024-12-01 on (locked 2022-08-17 → 2024-12-01).
- XRPUSDT: 2023-04-06 → 2024-01-24, and from 2024-12-07 on (the late range is not coverage-checked yet).
- SOLUSDT: before 2024-06-21 and from 2025-03-01 on (not coverage-checked for 15m yet; the H-0022 TEST
  stays locked).

## Fold counts (`DAYTRADE_POLICY`, 60/20/20 split, data start moved past the lock by the warm-up)

| market | decision range | folds (need >= 16) | TEST length |
|---|---|---|---|
| BTCUSDT | 2023-04-20 → 2024-06-18 | 46 | 85 days |
| XRPUSDT (early) | 2023-04-20 → 2024-01-24 | 29 | 55 days |
| ETHUSDT | 2024-12-14 → 2026-08-31 | 69 | 125 days |
| XRPUSDT (late) | 2024-12-20 → 2026-08-31 | 68 | 123 days |

Unlike daily bars (ADR-0029: ~14 folds), 15m clears 16 folds on every market with room to spare.

## Tooling

`scripts/diagnose_vote_frequency.py` gets `--kind swing|daytrade` (default swing, behaviour unchanged).
`daytrade` uses `DayTradeVote`, `DAYTRADE_POLICY` folds, 15m bars and horizons 16/48. It is still a
signal-count diagnostic: it counts per-bar states and, per walk-forward fold, engine signals, trades,
rejected entries, entry size over equity. It never reads returns or PnL, refuses any range touching a
lock (the warm-up included), registers and locks nothing. The workflow `vote_frequency_diag.yml` takes
a `kind` input.

## Result: BTCUSDT 15m, 2023-04-20 → 2024-06-18 (non-locked), Actions run 37047340738 (job ~81 min)

Signal counts only: no returns, no PnL. 41,958 bars, funding and OI complete. Per walk-forward fold the
engine view uses 58 folds (the whole range, not the 80% train+validation of a study).

| candidate | entry-signal bars of ~40.8k | distinct entry runs at c0.6 / agree 0.6 | folds with a trade (of 58) | trades | typical trades per fold (median / max) |
|---|---|---|---|---|---|
| h16 | 4,387 (10.8%) | 943 | 54 | 520 | 8 / 23 |
| h16 + side | 3,102 (7.6%) | 588 | 45 | 313 | 5 / 20 |
| h48 | 7,992 (19.6%) | 851 | 56 | 528 | 9 / 24 |
| h48 + side | 7,440 (18.2%) | 597 | 50 | 442 | 7 / 23 |

What it shows:

1. **The vote works at 15m.** Calibrated P spreads over about 0.38–0.67 (5th–95th percentile), so the
   `score_scale` correction did what it was meant to (ADR-0032); signals are frequent, not rare.
2. **No data-quality rejections at all** (0), so the time-based 192-bar window removes the ADR-0028 problem.
3. **The binding limits are the risk-engine protections, not the vote or the 100/day ceiling.**
   Rejected entries: stop-loss guard 284–997 and cooldown 173–337 per candidate. Trades are about 1 per
   day (313–528 over 58 folds x 7 days), far below the 100/day ceiling.
4. **Position size:** entry notional / equity has median 0.63–0.70, 95th percentile 1.4–1.6, maximum 2.0
   (the leverage cap), because 2.5 x ATR(15m) stops are tight.
5. The vol gate blocks 12.6% of bars (as designed; it only blocks entries).

Arithmetic caveat, not a result: round-trip cost is about 0.12–0.20% of notional; at ~0.7x equity notional
and ~1.3 trades per day that is roughly 0.1% of equity per day (a few tens of percent per year) before any
edge. The implied stop is about 0.5% / 0.68 = 0.7% of price, so a "3% per trade" win would be ~4 R; with a
12 h hold that is a stretch for a 15m vote. Whether the vote has an edge over its cost is exactly what the
validation study measures; this diagnostic does not and must not be read as evidence either way.

No constants were changed because of this run (counts look healthy).

## Next

Optionally repeat on ETHUSDT (2024-12-14 → 2026-08-31) and XRPUSDT. Then the pre-registration, which is
blocked until 2026-10-29 02:32:24 UTC by the combined cap and needs the owner's go.
