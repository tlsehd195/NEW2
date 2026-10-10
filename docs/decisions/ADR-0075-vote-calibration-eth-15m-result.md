# ADR-0075: ETH 15m vote calibration result (exploratory)

**Status:** Accepted (measurement record; INCONCLUSIVE as a validation result)
**Date:** 2026-10-10
**Deciders:** account owner (동동), Claude Code session

## Context

동동 chose to repeat the BTC measurements (ADR-0072, ADR-0074) on ETHUSDT 15m
to see whether the BTC result is general. Same tool (`scripts/calibrate_vote.py`),
run on 동동's PC, no code or constant changes.

Range: `--start 2024-12-02` was refused by `assert_not_locked` (TEST-13 ends
2024-12-01 and the warm-up reaches back ~12 days), so only the start was moved
later, to **2024-12-14**, through **2026-08-31** (61,158 bars,
`binance_vision_archive`, no data notes). The range was only shrunk;
`locked_windows.json` is untouched. Label: exploratory measurement against
up/down outcomes, not a backtest, not a preregistered hypothesis, no budget or
TEST data spent.

## Result

Combined vote (Brier vs always-base-rate Brier; up-rate when p>=0.60 (n) /
p<=0.40 (n)):

| Candidate | n | base up | Brier (base) | ECE | p>=0.60 | p<=0.40 |
|---|---|---|---|---|---|---|
| vote_h16 | 3749 | 0.5041 | 0.2536 (0.2500) | 0.0534 | 0.503 (173) | 0.517 (91) |
| vote_side_h16 | 3749 | 0.5041 | 0.2534 (0.2500) | 0.0533 | 0.493 (140) | 0.567 (60) |
| vote_h48 | 1248 | 0.5056 | 0.2608 (0.2500) | 0.0647 | 0.441 (186) | 0.527 (110) |
| vote_side_h48 | 1248 | 0.5056 | 0.2604 (0.2500) | 0.0633 | 0.453 (179) | 0.546 (97) |

Per indicator (Brier; p>=0.60 up-rate (n); p<=0.40 up-rate (n)):
- h16: bollinger_b 0.2538, 0.531 (207), 0.431 (130); donchian_pos 0.2538,
  0.482 (218), 0.446 (157); ema_trend 0.2536, 0.531 (243), 0.438 (153);
  obv_slope 0.2564, 0.459 (244), 0.584 (161); roc 0.2538, 0.507 (219), 0.481
  (131); rsi 0.2538, 0.533 (197), 0.521 (94); funding_crowding 0.2532, 0.488
  (84), 0.591 (22); oi_confirm 0.2551, 0.461 (282), 0.565 (92).
- h48: Brier 0.2603 to 0.2675 for the six price indicators, funding_crowding
  0.2598, oi_confirm 0.2654; p>=0.60 up-rates 0.432 to 0.472, mostly below the
  base rate of 0.506.

## Reading

- All four candidates and every indicator have a Brier score worse than always
  guessing the base rate, as on BTC.
- h48 is inverted: when the vote says 60% or more, ETH went up only 44 to 45%
  of the time, below the base rate. That is a sign the signal is noise or
  mean-reverting here, not evidence of an edge.
- The BTC h16 lead (ema_trend 0.567, roc 0.562 when bullish) does not appear on
  ETH (0.531, 0.507). It did not replicate.

## Decision

- No constant, weight, threshold, indicator set or strategy change; no
  hypothesis registered; no budget spent.
- With ADR-0072 and ADR-0074: on BTC (2023-04-20 to 2024-06-18) and ETH
  (2024-12-14 to 2026-08-31) 15m, neither the combined indicator vote nor any
  single indicator carries measurable information beyond the base rate. The
  60% entry rule is unsupported on both. Two symbols, non-overlapping windows,
  so this is not one lucky or unlucky stretch; it still says nothing about
  other symbols or other kinds of signals.
- Any continued work on this vote needs a new idea first (different inputs,
  not a reweighting of these six), then a new preregistered hypothesis.
