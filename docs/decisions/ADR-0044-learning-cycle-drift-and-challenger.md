# ADR-0044: Daily learning cycle: drift check and shadow challenger retraining

**Status:** Accepted (part 1: drift + journal data; part 2: shadow challenger — see "Part 2")
**Date:** 2026-10-08
**Deciders:** account owner (동동, "재학습 기능 만들어"), Claude Code session

## Context

The ML models and drift monitor ported from NEW- (ADR-0022) were code only: nothing called them,
paper-trading records were never read back, and there was no scheduled re-evaluation
(`PROJECT_MASTER_PLAN.md`, pipeline steps 2 and 4). 동동 asked for a retraining feature.

Constraints that shape it:

- A retrained model or setting may not drive trading decisions. Using one needs a new pre-registered
  hypothesis (CLAUDE.md rule 1), and the 30-day registration budget is full until 2026-10-29.
- Paper and live must keep the same code path; 15-minute bars only (project decision 2026-10-02).
- `src/` is standard-library only.

While building this, the paper trader turned out to keep only 600 bars while the 15m votes need
~1,190, so it never left warm-up; that was fixed separately (PR #31, no ADR).

## Decision

1. **A `learning` package that reads the journal and writes only to a new `learning` journal layer**
   (protected, never deleted, `configs/retention.json`). It may not import the trader, execution,
   live, registry or lifecycle code, nor touch a `.strategies` attribute, and may not name a
   `configs/` path (`tests/test_safety_boundaries.py`). So it has no way to change what trades.
2. **Paper records become training data** (`learning/journal_data.py`): closed 15m candles from the
   `normalized` layer and per-bar feature snapshots from the `feature` layer, deduplicated by bar time.
   To make the history complete from day one, the paper trader now also journals its REST
   bootstrap bars (`via: "bootstrap"`, real `source` kept), and each decision record now carries the
   strategy's own signal features (for the vote: `p_long`, per-indicator probabilities).
3. **Daily drift check** (`learning/drift_check.py`): one UTC day's feature snapshots vs the previous
   7 days, mean-shift and bucket-frequency tests from `monitoring/drift.py`, on 9 scale-free
   snapshot fields fixed before any result. Result `NO_DRIFT` / `DRIFT_DETECTED` / `UNKNOWN` goes to
   the `learning` layer; drift sends a WARNING notification. Observation only.
4. **Scheduling** (`learning/cycle.DailyLearningCycle`): the paper process runs the cycle itself,
   synchronously between feed events, once per finished UTC day after a lag, and writes a
   `daily_cycle_done` marker so restarts never repeat a day. Missed days are not backfilled
   automatically; `scripts/run_learning_cycle.py --day` does that by hand. A failing cycle is
   recorded in `audit` and notified, and never stops trading. Chosen over a GitHub Actions schedule
   because the journal lives on the machine that runs paper trading, not in the repo.

## Consequences

- Drift and journal-derived data exist without changing any trading decision, risk limit, config or
  registry entry. Tests: `tests/test_learning_drift.py`, the new safety-boundary test.
- 9 features x 2 tests per day: some alarms will be chance. A flag means "look", not "re-validate now".
- Nothing here is a validation result. A challenger that looks better in shadow still has to go
  through pre-registration -> locked window -> walk-forward -> PBO/DSR -> one TEST.
