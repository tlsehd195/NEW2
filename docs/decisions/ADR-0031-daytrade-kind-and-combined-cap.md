# ADR-0031: `daytrade` kind, its policy, and a combined registration cap

**Status:** Accepted
**Date:** 2026-10-03
**Deciders:** account owner (tapped option A "신설+합산 상한" on the decision card, relayed by the coordinator
session; the card also carried the proposed defaults 12 h max hold, 2.5 ATR stop, 2 entries per day; the owner then replaced the entry cap, see below), Claude Code session

## Context

ADR-0030 proposed day trading on 15m bars as its own kind. Its open question: a new kind gets its own
per-kind registration bucket (3 per 30 days, counted by candidate-id prefix), which is extra trial
capacity unless a combined cap exists.

## Decision

1. `daytrade` is a known family (`strategies/registry.FAMILIES`) with `DAYTRADE_POLICY`
   (`validation/policies.py`): fold_train 14 d, fold_test 7 d, `num_groups=8`, `min_folds=16`,
   same success criteria as swing/scalp. Candidate ids for it must start with `daytrade_`.
2. `Budget` gets `max_all_kinds` (default 3, same 30-day window). A registration is refused when
   the number of hypotheses of **all kinds** registered in the window is already >= `max_all_kinds`,
   in addition to the existing per-kind limit. This only adds a refusal; no existing limit is raised
   or weakened.
3. The documented budget-raise path (ADR-0027) lifts both limits together: `scripts/run_validation.py`
   passes `max_per_window` as both `max_per_window` and `max_all_kinds`, and still needs `--budget-adr`.
4. The old test that scalp stays registrable while swing is full now says so only when the combined cap
   is not the binding one (`Budget(3, max_all_kinds=99)`); a new test covers the combined cap and the
   window expiry.

Defaults approved with the same choice (to be written into the pre-registration, not into code yet):
max hold 12 h (48 bars), 2.5 ATR stop, at most **100** entries per symbol per day. The owner asked for no
entry limit (or else 100) after the card; 100 is a runaway-safety ceiling, effectively unlimited. Risk-engine
protections and per-trade costs are unchanged (details in ADR-0030).

## When registration becomes possible (computed from `research/preregistration.jsonl`, rule above)

- Per-kind `daytrade_` bucket: empty, so not binding.
- Combined cap (3 per 30 days over all kinds): H-0016, H-0017, H-0018, H-0021 (all 2026-09-29) and
  H-0022 (2026-10-02) are the rows still counting late October; the count first drops to 2 (H-0021, H-0022)
  once H-0018 (2026-09-29 02:32:23 UTC) is more than 30 days old.
  **Earliest default registration: 2026-10-29 02:32:24 UTC = 11:32 KST.** (For reference, swing alone
  would have been possible from 2026-10-29 00:13 UTC.)
- An earlier registration needs an ADR-backed raise (both limits rise together).

## Not done

No hypothesis registered, no budget used or raised, no threshold changed. The 15m strategy, the
time-based data-quality window and the speed work are separate PRs (ADR-0030 plan steps 2–4).
