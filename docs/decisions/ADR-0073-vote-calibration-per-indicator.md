# ADR-0073: Per-indicator calibration of the vote

**Status:** Accepted (measurement tool only; result recorded in a later ADR)
**Date:** 2026-10-10
**Deciders:** account owner (동동), Claude Code session

## Context

ADR-0072 found that the combined vote P(long) carries no measurable
information on BTC 15m (Brier worse than always guessing the base rate). The
cheapest next question is whether any single indicator does, since an
equal-weight log-odds mix of six near-useless inputs and one useful one is
still dominated by noise. 동동 approved this measurement ("예").

## Decision

- `scripts/calibrate_vote.py` now also reports, for every indicator in the
  panel, the same summary (n, Brier vs base-rate Brier, log loss, ECE, up-rate
  at p>=enter and p<=1-enter) from that indicator's own calibrated P(long)
  against the same decision bars and outcomes as the combined vote.
  Verdicts are computed once per bar and reused, so cost is unchanged.
- Still measurement only: no constant, threshold, weight, strategy or
  registry change; no hypothesis; no budget; no TEST data. Output is an
  exploratory read and is labelled so.

## Consequences

- Six more series are read against the same outcomes, which widens the
  chance of a lucky-looking indicator by multiple comparisons. A single
  indicator that looks good is a lead, not evidence: using or reweighting it
  needs a new preregistered id and the normal validation order.
- Dropping or reweighting indicators on the strength of this table alone is
  not allowed.
