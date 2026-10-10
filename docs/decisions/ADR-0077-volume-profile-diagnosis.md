# ADR-0077: Volume-profile signal diagnosis (VWAP, POC, value area)

**Status:** Accepted (measurement only; nothing registered)
**Date:** 2026-10-10
**Deciders:** account owner, Claude Code session

## Context

The owner asked whether the Instagram-promoted FX site aurum-lux.net publishes a usable strategy. It does not: the EAs are one-line
marketing and the paid curriculum only names tools (VWAP, anchored VWAP, fixed-range volume profile POC/VAH/VAL, Bollinger bands).
Those tools are not among NEW2's six voting indicators, so the textbook versions were screened as signals, no budget spent.

## Decision

`scripts/diagnose_volume_profile.py` (+ `volume_profile_diag.yml`) scores 15m perp bars, positive = follow:
v1 anchored-VWAP deviation (UTC-day anchor) / 96-bar mean range; v2 distance to the previous 96-bar POC; v3 previous-day VAH/VAL breakout.
Same Phase A analysis as ADR-0076. Ranges: BTCUSDT 2023-04-20..2024-03-25, ETHUSDT 2024-12-14..2026-09-01 (unlocked; TEST untouched).
Registers nothing, spends no budget, changes no strategy.

## Result (round trip 16 bp; GitHub Actions run 38068398684)

- Correlations with the next 1h/4h/12h return are 0.00-0.04 (t_eff mostly below 1.7). v1/v2 correlate 0.29-0.42 with the past hour's
  return, so they are mostly "past return" again. v3 is about 0 everywhere.
- Gross edge is 0-10 bp, below the 12-20 bp cost, in nearly every bucket and horizon. Monotonic in score? No (v2 ETH goes negative at the top).
- Two cells clear 16 bp: BTC v2 12h q95 (+35 bp, t 1.03) and ETH v1 4h q95 (+21 bp, t 1.39). Neither replicates on the other symbol,
  and quarters flip sign (ETH v1: +57, +35, -20, +15). With 3 scores x 3 horizons x 4 buckets x 2 symbols read, this is what chance produces.
- Calibration beats the base rate on a few 1h cells only, not at 4h/12h.

## Consequences

- Volume-profile signals join the closed list: no usable edge above cost on 15m BTC/ETH. The site's methods are not worth registering.
- Any future retry needs a new preregistered id and a registration slot after 2026-10-29; do not tune constants to this table.
