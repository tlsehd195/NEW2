# ADR-0034: Probability calibration metrics; disposition of the six open-source candidates

**Status:** Accepted (observation tool only; no threshold or trade changes)
**Date:** 2026-10-03
**Deciders:** account owner (동동), Claude Code session

## Context

동동 asked for six open-source projects to be examined (jarrodwatts/jev-trader,
aowang-ai/jev-trade, buberlo/jev-trader, OpenByteInc/QuantDinger,
ccxt/ccxt, freqtrade/freqtrade), was shown a graded list of six borrowable
ideas, and answered "전부" (all). Read-only review found no hardcoded keys,
obfuscation, telemetry or withdraw code. "Jev" is TypeSafe AI's paid API
model; calling it would be a paid, non-reproducible external dependency that
cannot be replayed in validation, so none of the jev repos' model calls are
taken. freqtrade is GPL-3.0, so only ideas are used, never code.

Checking the six ideas against the repository before writing code showed
that most were already present.

## Decision

| # | Idea | Disposition |
|---|------|-------------|
| 1 | freqtrade lookahead/recursive analysis | Already done: `validation/lookahead.py` (ADR-0014). No change. |
| 2 | Hard risk veto + kill switch layer | Already done: `risk/engine.py`, `risk/protections.py`, `live/kill_switch.py` (protected, human-only). No change; no safety file touched. |
| 3 | Brier / log loss / ECE / reliability curve | **New:** `validation/calibration.py` (stdlib, pure) plus tests. |
| 4 | Dry-run by default, same code for live | Already done: one `Broker` path, `PaperBroker` vs gated `LiveBroker` (ADR-0015). No change. |
| 5 | Sortino/Calmar style loss functions | Already done: `analytics/performance.py` reports sortino, calmar, drawdown. We do not hyperopt, because every searched parameter is a hidden trial PBO/DSR cannot deflate. |
| 6 | ccxt exchange adapter | Not built. `execution/binance_client.py` already covers the one exchange we use; ccxt is a large external dependency that `src/` (stdlib only) cannot import. If a second exchange is ever wanted, the adapter lives outside `src/` behind the existing `Broker` interface and gets its own ADR. |

`validation/calibration.py` measures whether a forecast probability (the
indicator vote's combined long probability, ADR-0023, or a future model)
comes true at the stated rate. `calibration_report` returns Brier, the Brier
of always guessing the base rate, log loss, ECE and the bins. It returns no
numbers, with a reason, when there are fewer than 100 samples, outcomes are
one class, or any input is non-finite or outside [0, 1]. `beats_base_rate`
is True only if Brier is strictly below the base-rate Brier.

## Consequences

- Calibration is a descriptive diagnostic. It does not enter the
  validation order, locks, PBO/DSR or any entry threshold, and it registers
  no hypothesis and uses no budget or TEST data.
- A forecast that fails `beats_base_rate` should not be trusted to set
  the 60% entry threshold. Wiring it into a study is a separate change that
  needs its own approval.
- Not taken from QuantDinger: SaaS stack (billing, Postgres/Redis/Kafka,
  server-side code execution) and its `curl | bash` installer.
