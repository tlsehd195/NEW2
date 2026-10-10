# ADR-0072: BTC 15m vote calibration result (exploratory)

**Status:** Accepted (measurement record; INCONCLUSIVE as a validation result)
**Date:** 2026-10-10
**Deciders:** account owner (동동), Claude Code session

## Context

ADR-0035 added `scripts/calibrate_vote.py`. 동동 approved running it on BTCUSDT
15m over the unlocked range 2023-04-20 to 2024-06-18. The session's network
policy blocked `data.binance.vision`, so it was run on 동동's own PC.

Label: this is an **exploratory measurement of forecast probabilities against
up/down outcomes** on archive candles (`binance_vision_archive`, 41,958 bars,
no data notes). It is not a backtest, not a preregistered hypothesis, not a
walk-forward result, and spends no budget or TEST data.

## Result

Decisions every `horizon` bars (non-overlapping labels); ties and missing
verdicts dropped (shown as tie/no_verdict).

| Candidate | n | dropped | Brier | Brier of base rate | ECE | up-rate p>=0.60 (n) | up-rate p<=0.40 (n) |
|---|---|---|---|---|---|---|---|
| vote_h16 | 2548 | 1/0 | 0.25096 | 0.24948 | 0.0387 | 0.514 (212) | 0.413 (109) |
| vote_side_h16 | 2548 | 1/0 | 0.25112 | 0.24948 | 0.0305 | 0.500 (164) | 0.415 (82) |
| vote_h48 | 848 | 0/1 | 0.26006 | 0.24983 | 0.0873 | 0.497 (147) | 0.542 (59) |
| vote_side_h48 | 848 | 0/1 | 0.25862 | 0.24983 | 0.0872 | 0.490 (143) | 0.500 (54) |

Base up-rate: 0.523 (h16), 0.513 (h48).

## Reading

- All four candidates have a Brier score **worse than always guessing the
  base rate** (`beats_base_rate` false). On this range the vote's probability
  carries no measurable information.
- When the vote says 60% or more, price went up 49 to 51% of the time, the
  same as the base rate. The 60% entry threshold is not backed by this data.
- The h16 p<=0.40 region went up only 41% of the time (n 109 and 82), a weak
  hint toward shorts, but it does not reproduce at h48 (54% and 50%) and the
  samples are small. It is a lead, not evidence.
- One symbol and one 14-month window; consistent with ADR-0033, which found
  signals are frequent but says nothing about edge.

## Decision

- No constant, threshold or strategy change; no hypothesis registered.
- Do not tune the vote to this table. Any change (for example a short-only
  variant of h16) needs a new preregistered id, and the 30-day budget is
  still governed by the existing limits.
- The raw JSON stays on 동동's PC (`reports/` is gitignored); the table above
  is the record.

## Consequences

- Before spending registration budget on the 15m vote, the first question is
  whether any indicator adds information beyond the base rate. A per-indicator
  calibration pass (same tool, one indicator at a time) is the cheapest next
  measurement and needs 동동's approval.
