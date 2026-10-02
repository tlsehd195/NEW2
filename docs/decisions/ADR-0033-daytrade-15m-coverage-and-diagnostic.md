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

## Next

Run the diagnostic on BTCUSDT 2023-04-20 → 2024-06-18 (non-locked) to see whether the 15m vote produces
signals and trades at all and what blocks them, before any registration. If the scale or gate constants
need changing, that is decided and recorded here first, on signal counts only.
