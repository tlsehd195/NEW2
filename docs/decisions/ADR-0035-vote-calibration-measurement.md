# ADR-0035: Calibration measurement for the 15m indicator vote

**Status:** Accepted (measurement tool only; not run on real data yet)
**Date:** 2026-10-03
**Deciders:** account owner (동동), Claude Code session

## Context

ADR-0034 added calibration metrics. 동동 answered "예" to wiring them into
the 15-minute vote validation. The vote enters at combined P(long) >= 0.60
(ADR-0023); that number is only meaningful if "60%" is followed by an
up-move about 60% of the time. ADR-0033's diagnostic deliberately reads no
returns, so this needs a separate tool.

## Decision

- `validation/vote_calibration.py` + `scripts/calibrate_vote.py`
  (`--kind daytrade|swing`): for decision bars on a non-locked range,
  compare `verdict().p_long` (computed from a `PrefixView`, closed bars only)
  with whether the close `horizon` bars later is above the decision close.
  The vote's own Platt fit only used outcomes visible at the decision bar,
  so each pair is out of sample for that fit.
- Output: Brier vs always guessing the base rate, log loss, ECE, reliability
  bins, and the two regions the entry rule acts on (up-rate when
  p >= enter, and when p <= 1 - enter).
- Fail-closed: a pair is dropped and counted when a candle hole lies
  between decision and outcome bar, the outcome is an exact tie, or there is
  no verdict; fewer than 100 samples or one outcome class gives no numbers
  and a reason. `assert_not_locked` runs first, as in the diagnostic.
- Labels `horizon` bars apart overlap, so the default stride equals the
  horizon (non-overlapping labels); a smaller stride reports an effective
  sample size.
- Strictly measurement: no threshold, strategy, trade or registry change, no
  hypothesis registered, no budget spent, no TEST data used.

## Consequences

- The tool reads up/down outcomes, so its output is an exploratory read, not
  a validation result, and constants must not be tuned to it. A change it
  suggests (for example a different entry confidence) needs a new
  preregistered hypothesis id.
- It has been tested only on generated candles (plumbing). It has **not**
  been run on BTC/ETH/XRP archive data: that is the next step and, because it
  reads outcomes on a real window, is left for 동동 to approve.
- `scripts/diagnose_vote_frequency.py` and the strategy code are untouched,
  so this does not collide with the indicator-ensemble work.
