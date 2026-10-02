# ADR-0022: ML models, leak-free dataset, MLStrategy and drift monitor adopted from NEW-

**Status:** Accepted (code only; the strategy is an unvalidated CANDIDATE)
**Date:** 2026-10-02
**Deciders:** account owner (동동), Claude Code session

> Numbering note: `scripts/adr_number.py` needs `origin/main`, and the GitHub
> repo was empty when this was written, so 0022 (next after 0021 in this
> checkout) was picked by hand. Run `python3 scripts/adr_number.py check`
> once a remote main exists; renumber if it reports a clash.

## Context

동동 asked for a coin scalping/swing program that also learns, and to reuse
what is worth reusing from the stock program `tlsehd195/NEW-`. A candidate
list was shown first; then 동동 delegated the choice ("너가 생각해서 가져와")
and asked that the work be documented the way NEW- does (ADRs for every
design decision plus `docs/PROJECT_STATUS.md`). NEW2 already had the
paper/live execution split (`execution/`, `paper/`), persistence and
reconciliation, so those were not needed.

## Decision

1. **Adopt, adapted** (all `src/`, standard library only):
   - `ml/linear_model.py` (from NEW- `ml/linear_model.py`): ridge, features
     standardized with train mean/std, intercept unpenalized, penalty chosen by
     chronological expanding-window CV over a fixed grid. A CV training sample
     is used only if its `target_time` is at or before the fold's test start.
   - `ml/tree_model.py` (from NEW- `ml/tree_model.py`): 25 depth-2 bagged
     regression trees, fixed hyperparameters (searching them would add hidden
     trials PBO/DSR cannot deflate), local seeded RNG, no wall-clock input.
   - `ml/features.py`, `ml/dataset.py`, `ml/samples.py`: six non-overlapping
     features (momentum RSI, mean-reversion Bollinger z, trend close/EMA50,
     volume z, close vs VWAP, ATR%), forward log-return target. **No
     imputation**: an unavailable or non-finite feature drops the sample.
     `MLSample` refuses `target_time <= as_of_time` and non-finite values.
   - `ml/ml_strategy.py`: `MLStrategy` implements `SignalStrategy`.
   - `monitoring/drift.py` (from NEW- `monitoring/drift.py`): mean, variance
     and bucket-frequency shift tests, plus `feature_drift`. **Observation
     only**; `UNKNOWN` with a reason when samples are too few, never "fine".
2. **Confidence is not a probability.** Ridge: |prediction - mean| over two
   training-target standard deviations, capped at 1. Forest: share of trees
   agreeing in sign with the mean. It is carried in `Signal.strength` and as
   `confidence_pct`. It must not be reported as a win rate or accuracy.
3. **Refit is a pure function of the last `warmup` bars.** The model is refit
   once per `refit_every`-bar bucket of the bar's timestamp and trains on
   samples whose target bar closed at or before that bucket's start. So the
   existing look-ahead / warm-up / determinism checks
   (`validation.integrity`) apply and pass; an earlier design anchored refits
   to history length and failed the warm-up check.
   `warmup = feature window + horizon + train_bars + refit_every`.
4. **Not registered.** `MLStrategy` is not in `strategies/registry.py` and
   has no hypothesis id. It needs a pre-registration (new id), locked-window
   check, walk-forward, PBO/DSR and one TEST before any claim (CLAUDE.md rule
   1). Defaults (horizon 12, 500 training bars, `edge_min` 0.2%) are
   placeholders to be fixed in that pre-registration, not tuned values.
5. **Not adopted:**
   - NEW- paper session persistence, reconciliation, performance report:
     NEW2 already has equivalents (`paper/engine.py` state save/restore,
     `execution/reconciliation.py`, `analytics/performance.py`).
   - White/Hansen reality check (`reality_check_spa.py`): PBO/DSR already
     gate candidates; revisit if many ML variants accumulate.
   - `ai_gateway`, SEC/13F/Toss/FRED/S&P 500/Alpha101: stock-specific or
     too slow for scalping.
   - NEW- live safety files: NEW2's own kill switch, approval and gate stay
     as they are (CLAUDE.md rule 2/3).
6. **Documentation practice** (동동's request): every design decision gets an
   ADR, and `docs/PROJECT_STATUS.md` is updated in the same change. Added to
   `CLAUDE.md` operating rules.

## Consequences

- `tests/test_ml_models.py` and `tests/test_drift.py` cover models, the
  no-leak dataset, determinism, call-order independence, future-bar
  independence and `check_signal_strategy` for both families (full suite:
  464 passed).
- Nothing here changes any live or paper behaviour; no config or registry
  file was touched.
- Open: no real-data result exists for `MLStrategy`; it is INCONCLUSIVE until
  validated. Wiring drift results into the paper runner and journaling
  predictions for paper-to-live comparison are follow-ups.
