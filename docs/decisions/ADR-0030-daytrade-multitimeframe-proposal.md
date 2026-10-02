# ADR-0030: Day-trading (단타) target: multi-timeframe indicator vote, design proposal

**Status:** Proposed (design only: nothing built, no hypothesis registered, no budget used)
**Date:** 2026-10-03
**Deciders:** account owner, Claude Code session

## Context

The owner changed the second target from scalping to **day trading (단타)**: hold hours to ~1–2 days,
aiming at roughly 3%+ per trade, instead of seconds-to-minutes scalping. Swing (1d) stays as is.

Why this is a better fit than scalping for this codebase:

- Candles carry no order-book data, so true scalping (the microstructure candidate was already excluded, see PROJECT_STATUS)
  cannot be validated honestly. Hours-to-days holds can.
- Cost drag: round trip is about 0.05% x 2 (taker) + 0.01% x 2 (half spread) + volatility-scaled impact,
  roughly 0.12–0.20% of notional (`ExecutionCosts` defaults). Against a 3% target that is ~5%; against a
  scalper's 0.2–0.3% target it would be most of the edge.
- ADR-0028 showed 1d trades rarely (30-day folds, a 346-bar warm-up eats the data). Intraday bars give
  16+ folds from months, not years, of data.

## Proposal

### 1. Family and timeframes

- New strategy family `daytrade` (`candidates[0]` starts with `daytrade_`, so it has its own
  registration budget, separate from `swing_`).
- **Direction** from closed **1d** and **1h** bars; **entry timing** from **15m** bars (default) or
  **5m** bars (option, see below). 1m/3m are not used.
- Exits: 2.5 ATR(1h) stop, vote reversal on 1h, hard time stop at 48 h.

#### 5m vs 15m for entry timing (the owner asked whether 5m works: yes, as an option)

| | 15m | 5m |
|---|---|---|
| bars per year per symbol | ~35k | ~105k (3x) |
| backtest speed | ~100x slower than 1d | ~300x slower than 1d; needs the caching work first |
| latency model (1 bar) | 15 min of slippage on market entry | 5 min, tighter fills |
| entry signals | fewer, smoother | more, noisier; more false timing triggers |
| missing-bar exposure | 1 hole blocks a shorter span | 3x more holes to catch; time-based quality window mandatory |
| cost per trade | same (0.12–0.20% round trip) | same; but more entries means more total cost if the 1h/1d gate does not cap trades |

Cost per trade does not depend on the timing bar; total cost does, through trade count. So the rule
is the same for both: **the 1h/1d layers decide whether a trade may exist at all, the timing bar only
chooses when inside that window**, and a per-day entry cap (proposal: 2 per symbol per day) bounds
frequency. Expected: 5m gives somewhat earlier, better-priced entries but not a different edge,
because direction still comes from 1h/1d. It also multiplies candidate count, so 15m and 5m are
**separate pre-registered candidate sets, never tuned against each other on the same TEST**. Proposal:
validate 15m first, then 5m as its own hypothesis. This is a trade-frequency and speed argument,
not a measured result.

### 2. Multi-timeframe vote

Reuse `IndicatorVote` per timeframe, unchanged in calibration (Platt, L2=5, realized pairs only):

| layer | bars | horizon | role |
|-------|------|---------|------|
| regime | 1d | 5 d | side filter: only trade with the sign of P_1d (needs P_1d >= 0.55 on that side) |
| direction | 1h | 12–24 bars | main vote, same 6 voters, enter at P >= 0.60 and >= 60% agreement |
| timing | 15m (or 5m) | 8 bars (24 bars at 5m) | entry trigger: P_15m on the same side, or pull-back (bollinger_b / rsi vote flips to the side) |

Combination stays an equal-weight log-odds mean across layers (every learned weight is a hidden trial,
ADR-0023). Funding (contrarian) and OI-confirm votes attach to the 1h layer only (8h funding, daily OI
cannot time a 15m entry).

**No look-ahead:** a higher-timeframe value at time t may use only bars whose `close_time <= t`. This
needs a new `MultiTimeframeView` (aligned closed-bar slices) and a test in `validation/integrity.py`
that shifts the 1d/1h series by one bar and requires the decisions to change only at bar closes.

### 3. Data

- Binance USD-M archive klines 15m, 1h, 1d (monthly files, daily fallback from ADR-0029), funding (8h),
  OI (daily metrics, from 2020-09 for BTC). Same `source` provenance.
- Size: 15m is ~35k bars/year/symbol; 3 years of BTC = ~105k bars (file sizes not measured).
- The event engine loops bar by bar and refits calibrators, so 15m will be ~100x slower than 1d.
  Needed before any run: incremental calibrator state and a per-fold cache (no change to results,
  only speed), and a smoke run on Actions to measure wall time.
- `Timeframe` already has 15m/1h; `data_quality` window must be expressed in time (e.g. 7 days) rather
  than in warm-up bars, otherwise one missing 15m bar blocks entries for days (ADR-0028 finding 2).

### 4. Costs and honesty limits

- Fees 0.05% taker (maker 0.02% only if a limit-entry model is validated, not assumed), half spread
  0.01%, volatility-scaled impact, funding every 8h on the real series, latency 1 bar (15m).
- Replace with the account's real `commissionRate` before any result is trusted (existing rule).
- Intraday cost model is *less* certain than daily: spreads widen in volatility; stop fills gap through.
  Report must carry the existing `BACKTEST` label and a stress run at 2x costs.
- 3% per trade is an outcome to measure, not a parameter to fit: no threshold is tuned to hit it.

### 5. Fit with fold and lock rules

- New `DAYTRADE_POLICY`: fold_train 14 d, fold_test 7 d, `num_groups=8`, `min_folds=16`. That needs
  ~(14 + 16 x 7) = 126 days of decision range plus warm-up (about 12 days for 1d+1h layers), so an
  unlocked range of ~8–9 months per market per split is enough (versus ~2.8 years for 1d).
- Split stays 60/20/20; `assert_not_locked` still checks train, validation, test **and warm-up**
  against locks per market, so SOLUSDT TEST-22 (2024-06-21 → 2025-01-29) and BTC/ETH/XRP locks stay
  closed to this family too. Candidate unlocked ranges, **to be measured by the coverage preflight,
  not assumed**: BTC 2023-04-06 → 2024-06-18, ETH after 2024-12-01, XRP 2023-04-06 → 2024-01-24.
- Ordering unchanged: pre-registration → lock check → walk-forward → PBO/DSR → one TEST → lock.
  Intraday multiplies the candidate grid fast, so start with at most 4 candidates
  (horizon x side-data), the same shape as H-0022.
- Trial count for DSR is global (`log.total_registered_candidates()`), so day-trading candidates deflate
  against swing ones too.

## What this ADR does not do

No code, no registration, no budget raise, no threshold change. The earliest default-rule swing
registration is still ~2026-10-29 09:14 KST; `daytrade_` is a separate family and would have its own
3-per-30-days budget, but even so registration needs the owner's explicit go.

## Open questions for the owner

1. Direction of work: build the day-trade harness first (multi-timeframe view, time-based quality
   window, speed work), or finish the swing re-validation first?
2. Hold limit 48 h and stop 2.5 ATR(1h) are proposals; acceptable as pre-registered defaults?
3. Long-only first, or both sides (the swing candidates allowed shorts)?
