# ADR-0006: 1시간봉 실행비용 문제로 일봉 실행 주기에서 문헌기반 모멘텀 재검증

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** account owner, Claude Code session

## Context

Four hypotheses have now run on real KRW-BTC 1h candles, all INCONCLUSIVE:

- H-0001 (trend baselines), H-0002 (literature momentum), H-0003 (adaptive
  ensemble), H-0004 (adaptive ensemble + hysteresis) -- see
  `configs/locked_windows.json` TEST-1..4 for the exact numbers.

A pattern recurs across all four: candidates that trade more often lose
more. In H-0002, TEST losses went from -97.8% (1-day lookback) down to
-19.1% (4-week lookback) monotonically with lookback length. H-0003's
un-hystereses ensemble (flips position almost every bar) lost ~99%; adding
a hysteresis band in H-0004 cut that to single-digit/20%-ish losses. This
is consistent with execution cost drag dominating at 1-hour granularity,
not with the underlying signals being worthless.

The momentum papers this project is grounded in (Liu & Tsyvinski 2021 RFS;
Liu, Tsyvinski & Wu 2022 JF -- see ADR-0005) test **weekly rebalancing**.
`momentum_candidate_grid()` (`src/cointrader/strategies/baselines.py`)
reproduces their horizons (1 day to 4 weeks) but decides and can trade
**every hour**, which is a far higher turnover than the papers ever tested
or than their reported returns assume. That mismatch, not a flaw in the
signal itself, is the more parsimonious explanation for H-0002's failure.

## Decision

Before proposing more strategy variants at 1h, revalidate the *same*
literature-grounded time-series momentum signal at **daily** bars, which
is much closer to the papers' native weekly-ish rebalance cadence and cuts
worst-case turnover by roughly 24x. Concretely:

- Add a `--timeframe` option to `scripts/run_swing_study.py` (default
  `1h`, unchanged), threaded through to the candle fetch and the
  chronological split's `align_to`.
- Add `momentum_candidate_grid_daily()` with lookbacks in **daily** bars
  (2, 7, 14, 21, 28) -- the same horizons as `momentum_candidate_grid()`,
  re-expressed in the new bar unit, not new horizons (2 days stands in
  for the RFS paper's literal 1-day horizon, since `TimeSeriesMomentum`
  requires `lookback >= 2`).
- Register a new hypothesis (its own id, its own TEST window, checked
  against every existing locked window) rather than reusing H-0002's
  registration or its locked TEST-2 window.

This does not change CLAUDE.md's validation order or any safety file. It
is an execution-frequency change to how an already-literature-grounded
signal is tested, not a new free-tuned strategy.

## Consequences

- If the daily-bar run also fails, execution cost is not the explanation
  and the signal itself (or the KRW-BTC market / this project's cost
  model) should be questioned instead of trying yet more timeframes.
- Every other candidate family already validated (trend baselines,
  adaptive ensemble) stays 1h-only for now; this ADR only motivates
  re-testing the literature-momentum family at a lower frequency closer to
  what was actually published.
- Daily bars mean far fewer bars per walk-forward fold; `fold_train_days`/
  `fold_test_days` must be chosen large enough in *days* (not hours) for
  each candidate's lookback, per the gotcha already logged from H-0004
  (a lookback close to or exceeding the fold length silently produces flat
  candidates instead of erroring).
