# ADR-0007: H-0006의 PBO 상승 원인(상관 높은 후보) 대응: 덜 상관된 모멘텀 후보 조합으로 재검증

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** account owner, Claude Code session

## Context

H-0006 (daily-execution literature momentum, 2017-2022, 81 walk-forward folds)
produced the strongest per-candidate evidence in the project: all 5 lookback
candidates (2/7/14/21/28 days) had consistently positive mean fold returns and
DSR up to 0.84 (still short of the 0.95 pre-registered bar, but far above every
earlier hypothesis). Yet PBO *rose* to 0.857 from H-0005's 0.457 on the same
candidate family with 3x fewer folds -- the opposite of what more data should
do if the signal itself were the problem.

The likely explanation (recorded in TEST-6's note): the 5 candidates are the
same absolute-momentum rule at adjacent lookbacks, so their fold-by-fold
returns are highly correlated. CSCV's PBO answers "does the candidate that
looks best in-sample keep looking best out-of-sample" -- with 5 near-duplicate
candidates, which one edges out the others in-sample is dominated by noise,
so PBO can stay high even when every candidate individually has real
(DSR-confirmed) edge. This is a different question from whether the momentum
family works at all.

## Decision

Re-test the same literature-grounded daily momentum signal with a **smaller,
maximally-spread candidate set** -- the shortest (2-day) and longest (28-day)
horizons already in `momentum_candidate_grid_daily()`, plus the middle (14-day)
one, instead of all 5 adjacent lookbacks. Fewer, less mutually-correlated
candidates should let CSCV's in-sample/out-of-sample comparison be more
informative rather than dominated by near-ties between similar candidates.

This uses the same `TimeSeriesMomentum` instances (`ts_momentum_2`,
`ts_momentum_14`, `ts_momentum_28`) already validated in H-0005/H-0006, added
as a new `momentum_candidate_grid_daily_decorrelated()` grid function -- no
new strategy code, no change to any existing grid, hypothesis, or locked
window. Tested on a NEW non-overlapping date range (the unused
2017-01-01..2021-08-19 portion of H-0006's own TRAIN+VALIDATION history, which
was never locked since only TEST windows get locked), under its own
hypothesis id.

## Consequences

- If PBO drops meaningfully with the smaller set, that supports the
  correlated-candidates explanation and argues for always pre-registering
  momentum-family grids as a few spread-out horizons rather than every
  horizon the papers mention.
- If PBO stays high even with 3 decorrelated candidates, the correlated-
  candidates explanation is wrong or incomplete, and the high PBO likely does
  reflect a real selection-overfitting risk in this signal family after all.
- Either way this does not change H-0005/H-0006's own recorded results or
  locked windows -- it is a new, separately pre-registered test.
