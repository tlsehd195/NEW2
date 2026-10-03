# ADR-0039: ETH and XRP 15m signal-count diagnostic

**Status:** Accepted (measurement only; nothing registered, no budget used, no constant changed, no returns read)
**Date:** 2026-10-03
**Deciders:** account owner ("예" to running it), Claude Code session

## Context

ADR-0033 ran the 15m day-trade signal-count diagnostic on BTCUSDT only. The owner approved running the same
`scripts/diagnose_vote_frequency.py --kind daytrade` on the other unlocked ranges. Label: **DIAGNOSTIC**
(signal counts and position size only; not a validation result). Source `binance_vision_archive`; the ranges
are unlocked per `configs/locked_windows.json`.

## Runs (Actions `vote_frequency_diag.yml`)

| market | decision range | run | candles | funding / OI gaps |
|---|---|---|---|---|
| XRPUSDT (early) | 2023-04-20 → 2024-01-24 | 37086907934 | 27,942 | 0 / 0 |
| ETHUSDT | 2024-12-14 → 2026-08-31 | 37086905892 | (log cut) | (log cut) |

The workflow prints the whole JSON report; the job-log reader returns only the tail, so for ETH the head of
the report (candle count, the first candidate's engine summary) was cut off. The ETH rows below are the three
candidates whose summary survived. The first ETH candidate (`daytrade_indicator_vote_h16_c0.6_v1`) is **not
read**; no value is invented for it. A re-run would need the workflow to print a compact summary.

## Results

The engine view slices the whole range into non-overlapping 7-day test folds (87 for ETH, 37 for XRP).

| market | candidate | folds with a trade | trades | rejected (cooldown / stop-loss guard / drawdown guard) | notional / equity, median (max) |
|---|---|---|---|---|---|
| XRP | h16 | 34 / 37 | 438 | 161 / 428 / 0 | 0.50 (1.69) |
| XRP | h16 + side | 30 / 37 | 276 | 125 / 278 / 0 | 0.47 (1.82) |
| XRP | h48 | 37 / 37 | 492 | 273 / 836 / 20 | 0.49 (1.38) |
| XRP | h48 + side | 35 / 37 | 367 | 225 / 657 / 20 | 0.48 (1.38) |
| ETH | h16 + side | 48 / 87 | 283 | 192 / 257 / 0 | 0.43 (1.74) |
| ETH | h48 | 77 / 87 | 800 | 536 / 1,760 / 0 | 0.45 (2.00) |
| ETH | h48 + side | 72 / 87 | 654 | 470 / 1,439 / 0 | 0.44 (2.00) |

XRP entry-signal bars: 12.7% (h16), 8.7% (h16 + side), 27.1% (h48), 25.4% (h48 + side); vol gate blocks
15.3% of bars; data-quality rejections 0.

## Reading

- Same picture as BTC: the vote produces signals on both markets, and what limits trades is the risk
  engine's loss-streak guard and cooldown, not the vote. The 100-per-day entry cap is never reached.
- ETH h48 trades about 800 times in 626 days, about 1.3 per day, so the cost arithmetic of ADR-0033
  (about 0.1% of equity per day before any edge) applies to it as well.
- Side-data candidates trade less than their plain twins on both markets.
- None of this says whether the signals have an edge. Returns were not read; that is the job of the
  pre-registered validation, which cannot start before the registration date in ADR-0031.

## Consequences

- No constant, threshold or candidate changed. Registration still needs the owner's explicit go.
- XRP late range (from 2024-12-07) and SOL remain without a 15m coverage check or diagnostic.
