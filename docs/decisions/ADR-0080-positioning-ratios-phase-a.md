# ADR-0080: Phase A diagnostic: crowd-positioning long/short ratios (N1)

**Status:** Proposed (rules fixed before the run; result section added after)
**Date:** 2026-10-11
**Deciders:** account owner (동동), Claude Code session

## Context

동동 chose "N1 only" from strategy research v2 (`strategy_research_v2.md`, candidate N1). N2 (limit-order entry) waits for this result.
Budget: the 30-day registration budget (3) is full until 2026-10-29 11:32 KST, so nothing is registered and no final exam is run.

The Binance `metrics` archive carries participant-positioning ratios that the repo never read (`METRICS_COLUMNS` only reads open
interest). They are a different information source from price, funding, open-interest level (ADR-0070 `oi_dir`/`oi_chg`) and taker
volume (ADR-0064). The taker column is excluded on purpose. Evidence for the idea is practitioner-grade (crowd-extreme contrarian use), not
academic, so this is a measurement only.

## Decision (fixed before the run, no adjusting afterwards)

- Ratios: `count_toptrader_long_short_ratio` (top traders, accounts), `sum_toptrader_long_short_ratio` (top traders, position size),
  `count_long_short_ratio` (all accounts). Score = causal z of log ratio against the previous 96 bars (24 h) or 960 bars (10 d) of 15m
  decisions (current value excluded). A reading stamped T is usable from T + 5 min; older than 30 min counts as missing.
- Both conventions come from one number: follow (long when the ratio is high) and contrarian (its mirror). Horizons 4/16/48 bars.
  Signal buckets q80/q90/q95 of |z| (existing `analyse_score`), cost 12/16/20 bp round trip.
- Segments (lock-free spans before the reserved windows, scoring starts 11 days after the segment start): BTCUSDT 2021-05-02..2022-08-17,
  BTCUSDT 2023-04-07..2024-03-25, ETHUSDT 2024-12-02..2026-04-28. Lock and reserved checks are enforced by the script.
- Multiple testing: one family of 3 segments x 3 ratios x 2 lookbacks x 3 horizons = 54 correlation tests (two-sided, effective-sample t),
  Holm adjusted.
- **PASS** for a cell = Holm-adjusted p < 0.05 in at least one segment AND best-bucket gross move >= 12 bp in the same direction in at
  least 2 of the 3 segments. A passing cell is sent to screening under a NEW id (never registration, never a changed constant); no
  PASS = N1 is dropped.
- Code: `BinanceVisionPositioning` (header mismatch or empty/non-positive values are counted, never filled), `features/positioning.py`,
  `scripts/diagnose_positioning.py`, workflow `positioning_diag.yml`. `DEFAULT_PANEL` and every strategy are unchanged.

## Consequences

Result: to be filled in after the run.
