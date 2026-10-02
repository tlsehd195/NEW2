# ADR-0030: Day-trading (단타) target: 15-minute indicator vote, long and short (design proposal)

(The file name keeps the slug from the first draft, which was multi-timeframe. The final scope below is
single-timeframe, 15m only.)

**Status:** Proposed (design only: nothing built, no hypothesis registered, no budget used)
**Date:** 2026-10-03
**Deciders:** account owner, Claude Code session

## Context and owner decisions (2026-10-02/03)

- Second target changed from scalping to **day trading (단타)**, roughly 3%+ per trade.
- Daily-bar swing work is dropped ("A"): existing `swing_*` candidates, hypotheses and locks stay as
  the historical record; no new daily-only candidates.
- **Long and short**, not long-only.
- **15-minute bars only.** One timeframe: no 1h/1d direction layer, no 5m entry layer, no
  multi-timeframe view. (An earlier draft proposed 1d/1h direction with 15m or 5m timing; withdrawn.)

Why day trading on 15m fits this codebase better than scalping:

- Candles carry no order-book data, so true scalping cannot be validated honestly (the microstructure
  candidate was already excluded, see PROJECT_STATUS).
- Cost per trade is about 0.05% x 2 (taker) + 0.01% x 2 (half spread) + volatility-scaled impact,
  roughly 0.12–0.20% round trip (`ExecutionCosts` defaults). Against a 3% target that is ~5%; against a
  scalper's 0.2–0.3% target it would be most of the edge.
- ADR-0028 showed 1d trades rarely (30-day folds, a 346-bar warm-up eats the data). 15m gives 16+
  folds from months of data.

## Proposal

### 1. One timeframe, the existing vote

`IndicatorVote` (6 voters, Platt calibration, equal-weight log-odds, entry at P >= 0.60 and >= 60%
agreement, `allow_short`) already works on any bar size, because its windows are counted in bars.
Everything is computed on closed 15m bars only, so there is **no multi-timeframe look-ahead problem**
and no new view class. What has to change, as a new strategy id (`daytrade_indicator_vote_*`), not an
edit of the swing one:

| item | swing (1d) today | 15m day trade (proposal) | why |
|---|---|---|---|
| score squash scales (`ema_trend` 0.03, `roc` 0.05, `macd` 0.25 ATR) | daily-sized moves | divide by ~sqrt(96) ≈ 10 (about 0.003 / 0.005) | a 15m move is ~1/10 of a daily one; unscaled, scores stay near 0 and the Platt L2 prior (toward p = 0.5) keeps every P near 0.5, i.e. no trades |
| indicator lookbacks (EMA 20, ROC 14, RSI 14, Donchian 20, BB 20) | bars | **unchanged in bars** (20 bars = 5 h) | standard definitions; scaling them is an extra hidden parameter |
| `horizon` (calibration outcome look-ahead) | 5 / 10 bars | **16 / 48 bars (4 h / 12 h)** | matches "minutes to hours" holds |
| `fit_lookback` | 200 bars | 1,000 bars (~10 days) | 200 bars would be 2 days of realized pairs; must stay >= 10 x horizon |
| vol gate (`volatility_ratio`) | 10 d vs 60 d | 96 bars (1 d) vs 960 bars (10 d), same [0.5, 2.0] band | 60 d of 15m bars is 5,760 bars of warm-up for a gate that only blocks entries |
| exits | P < 0.52, 2.5 ATR stop | same, plus **time stop 48 bars (12 h)** | day trade, not a position |
| warm-up | 351 bars | ~1,200 bars (~12.5 days) | `MIN_BARS` + `fit_lookback` + `horizon` |
| data-quality window | `quality_window = warmup` bars | **time-based** (e.g. last 2 days) | ADR-0028 finding 2: one hole blocked entries for the entire warm-up; at 15m a bar-count window would block ~12 days per hole |

The squash-scale row is the one real correction needed; it must be tested (score distributions on
non-locked data, signal counts only, as `scripts/diagnose_vote_frequency.py` does) before any
registration. The scales are fixed constants from this reasoning, not fitted to outcomes.

Candidate set for the first hypothesis, at most 4: horizon {16, 48} x side-data {off, on (funding
contrarian + OI confirm)}. Direction is not a grid axis: every candidate may go long or short.

### 2. Long and short

- The short side is modelled, not mirrored: funding is applied with its sign from the real 8 h series
  (longs pay positive funding, shorts receive it); `FuturesTerms` already takes it.
- Same fees, spread and impact on both sides; the 2x-cost stress run covers both.
- Risk engine unchanged for both sides (0.5% equity risk per trade, 2.5 ATR stop, leverage cap).
  Nothing under `configs/live/` or the safety files changes.
- More freedom means more trials: DSR deflates against every registered candidate (global trial count).

### 3. Data

- Binance USD-M archive klines 15m (monthly files, daily fallback from ADR-0029), funding (8 h), OI
  (daily metrics, BTC from 2020-09). Same `source` provenance. ~35k bars/year/symbol; file sizes not
  measured.
- The event engine loops bar by bar and refits calibrators, so 15m is ~100x slower than 1d. Needed
  before a real run: incremental calibrator state and a per-fold cache (no change to results), and a
  smoke run on Actions to measure wall time.

### 4. Costs and honesty limits

- Fees 0.05% taker (maker 0.02% only if a limit-entry model is validated, not assumed), half spread
  0.01%, volatility-scaled impact, funding every 8 h, latency 1 bar (15 min).
- Replace with the account's real `commissionRate` before any result is trusted (existing rule).
- Intraday costs are *less* certain than daily (spreads widen in volatility, stops gap). Reports keep the
  `BACKTEST` label plus a 2x-cost stress run.
- "3% per trade" is an outcome to measure, not a parameter to fit; no threshold is tuned to hit it.
- Entry cap per symbol per day: the proposal was 2; **the owner chose no limit, or else 100**, so it is
  **100 per symbol per day**, a runaway-safety ceiling rather than a strategy parameter (effectively
  unlimited). Trade frequency is therefore bounded by the signal itself and by the protections that
  already exist, not by this cap: the risk engine's daily loss limit (3%), max drawdown (15%), the
  stop-loss guard (3 losses in 24 h pause 6 h) and drawdown guard (6% in 48 h pause 12 h) all still
  apply (`configs/risk.json`, same engine as paper/live). The cap is a fixed pre-registered value;
  changing it later means a new hypothesis id, like any other parameter.
- Frequent entries must show up as cost: the event engine charges taker fee, half spread, volatility-scaled
  impact and funding **per trade** (`Trade` records gross PnL, fees, spread, slippage, funding), so cost drag
  from many entries lands in net returns, DSR and the 2x-cost stress run; the validation report must print
  trades per day and total cost as a share of gross PnL, so an overtrading result cannot hide.
- Trial-count rules: the cap value is not a grid axis (one fixed value), so it adds no trials; more
  trades do not change DSR's trial count (that counts registered candidates), but they make fold
  returns less noisy, which is why the cost-share report is mandatory.

### 5. Fit with fold and lock rules

- New `DAYTRADE_POLICY`: fold_train 14 d, fold_test 7 d, `num_groups=8`, `min_folds=16`. Needs
  ~(14 + 16 x 7) = 126 days of decision range plus ~12.5 days warm-up, so an unlocked range of about
  8–9 months per market per 60/20/20 split is enough (1d needed ~2.8 years).
- `assert_not_locked` still checks train, validation, test **and warm-up** per market, so SOLUSDT
  TEST-22 (2024-06-21 → 2025-01-29) and the BTC/ETH/XRP locks stay closed to this family too.
  Candidate unlocked ranges, **to be measured by the coverage preflight, not assumed**: BTC
  2023-04-06 → 2024-06-18, ETH after 2024-12-01, XRP 2023-04-06 → 2024-01-24.
- Order unchanged: pre-registration → lock check → walk-forward → PBO/DSR → one TEST → lock.

## Hypothesis kind and registration budget: what the code does today

Read from the code (not changed):

- `ValidationPolicy` is looked up by the strategy's `family` field; only `swing` (fold 60 d / 30 d) and
  `scalp` (fold 3 d / 4 d) exist (`validation/policies.py`, `strategies/registry.py: FAMILIES`).
  `run_signal_study` refuses a candidate whose `family` differs from the policy.
- The registration **budget is counted differently**: by the candidate-id prefix,
  `candidates[0].split("_")[0]` (`research/hypotheses.py: _family`), 3 per 30 days. A candidate id
  starting with `daytrade_` is its own budget bucket whatever its `family` field says, and one
  starting with `swing_` shares swing's (ADR-0027 raised it for H-0022 only).
- Dead-candidate patterns (`ts_momentum_*`, `funding_carry_*`, `basis_carry_*`) do not touch an
  indicator-vote day-trading set.

| option | change | consequence |
|---|---|---|
| 1. reuse `scalp` + `SCALP_POLICY` | none | folds of 3 d / 4 d and "scalp" semantics (seconds-to-minutes) misdescribe hours-long holds |
| 2. new `daytrade` kind (recommended) | add `DAYTRADE_POLICY`, add `"daytrade"` to `POLICIES` and `FAMILIES`; ids start with `daytrade_` | honest naming, own budget bucket (3 / 30 d), nothing loosened for swing/scalp; a small rules-code change |
| 3. reuse `swing` | none | folds of 60 d / 30 d are far too coarse for 15m bars and share swing's already-used budget |

Recommendation: option 2. A new bucket is also a fresh 3-per-30-days budget, i.e. extra trial
capacity; if that is not wanted, add a **cross-family** cap (e.g. 4 per 30 days over all kinds) in the
same change. Either is a rule change: no rule is changed, nothing registered and no budget raised
without the owner's explicit approval.

## Loader fallback (ADR-0029)

Written for a daily-bar problem and did not solve it. It does not matter for 15m (monthly archives
exist), is harmless, tested and merged, so it stays, but is off the critical path.

## Plan (each step a small PR with its own ADR from the script; nothing below registers or runs TEST)

1. Day-trade rules: `DAYTRADE_POLICY` + `daytrade` family (needs approval, see above).
2. `daytrade_indicator_vote` strategy: scaled scores, bar-based horizons, time stop, entry cap, tests.
3. Time-based data-quality window in the event engine (default behaviour unchanged for 1d).
4. Speed: incremental calibrator + per-fold cache; Actions smoke run to time it.
5. Coverage preflight and signal-count diagnostic on unlocked 15m ranges (no returns).
6. Only then: pre-registration, with the owner's go.

## Open questions for the owner

1. Approve option 2 (new `daytrade` kind), with or without a cross-family cap?
2. Hold limit 12 h, stop 2.5 ATR and entry cap (now 100/day, owner's choice) are the pre-registered defaults.
