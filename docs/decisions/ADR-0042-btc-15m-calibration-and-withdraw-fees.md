# ADR-0042: BTC 15m vote calibration on real data

**Status:** Accepted (measurement recorded; no constant changed)
**Date:** 2026-10-03
**Deciders:** account owner (동동), Claude Code session

## Context

ADR-0035 built `scripts/calibrate_vote.py` but did not run it on real data
because the sandbox could not reach the Binance archive. 동동 enabled network
access for data servers and asked for the run; data.binance.vision now
answers 200.

## Decision

1. Ran, unchanged:
   `python3 scripts/calibrate_vote.py --kind daytrade --symbol BTCUSDT --start 2023-04-20 --end 2024-06-18`
   (non-locked range; `assert_not_locked` passed; 41,958 15m candles incl.
   warmup, no data notes, default stride = horizon so labels do not overlap).

   | candidate | samples | base up | Brier | Brier (base rate) | ECE | p>=0.60: n / up-rate | p<=0.40: n / up-rate |
   |---|---|---|---|---|---|---|---|
   | h16 | 2548 | 52.3% | 0.2510 | 0.2495 | 0.039 | 212 / 51.4% | 109 / 41.3% |
   | h16 + funding/OI | 2548 | 52.3% | 0.2511 | 0.2495 | 0.031 | 164 / 50.0% | 82 / 41.5% |
   | h48 | 848 | 51.3% | 0.2601 | 0.2498 | 0.087 | 147 / 49.7% | 59 / 54.2% |
   | h48 + funding/OI | 848 | 51.3% | 0.2586 | 0.2498 | 0.087 | 143 / 49.0% | 54 / 50.0% |

   Dropped pairs: h16 one tie each, h48 one no-verdict each, no candle gaps.

2. Reading (exploratory, not a validation result):
   - No candidate beats always predicting the base rate (Brier worse in all
     four). The P(long) carries no measurable up/down information here.
   - The long entry zone (p >= 0.60) went up 49-51% of the time, i.e. not
     the ~60% the number claims; at or below the base rate.
   - Only h16's short zone (p <= 0.40) leans the right way (down ~59%,
     n = 82-109, roughly 2.3 standard errors from the base rate). With 8
     zones looked at and no correction, this is not evidence of an edge.
   - h48 is worse: higher ECE, and its short zone went up more often than not.
3. Nothing is changed because of this: thresholds, weights, strategies,
   hypotheses, budget and TEST windows are untouched. Any change suggested by
   it (e.g. long-side off, different confidence) needs a new preregistered id
   and 동동's approval.
4. The withdraw fees 동동 sent in the same message were already set on main
   by another thread (ADR-0036 update, PR #26); this ADR does not change them.

## Consequences

- The calibration result is a warning for pre-registration: on BTC 2023-04 to
  2024-06 the 60% long signal is not a 60% signal. Raw JSON is in
  `reports/` locally (git-ignored); the table above is the record.
