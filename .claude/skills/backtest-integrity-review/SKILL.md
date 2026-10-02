---
name: backtest-integrity-review
description: Review crypto backtest, validation and market-data code for lookahead bias, stream-gap and maintenance-window data holes, delisting survivorship, unrealistic fill/cost assumptions, and missing funding/liquidation effects. Use before trusting any backtest or study result, when changing src/cointrader/{data,backtest,strategies,validation}, or when wiring in a new exchange or data source.
---

# Backtest & Data Integrity Review (crypto)

Adapted from tlsehd195/NEW-'s stock-market version. Quant backtests fail
silently: the code runs, prints a Sharpe ratio, and is wrong. Generic code
review does not catch this class of bug. Run this checklist against
`src/cointrader/data`, `backtest`, `strategies`, `validation` and any script
that fetches or replays market data before a result informs a decision.

Read `CLAUDE.md` and `docs/decisions/` first so findings are framed against
this repo's own rules.

## 1. Lookahead bias

- Does any signal read a bar that had not closed at decision time? The
  engine hands strategies a `PrefixView`; flag anything that bypasses it
  (passing the full candle list, reading a global, precomputing an
  indicator over the whole series).
- Is execution at a price the strategy could not have known? A decision
  on bar t must fill at bar t+1's open or later, never at bar t's close.
- Are splits chronological, with nothing (scaler, threshold, parameter
  choice) fit on data that includes the TEST range?
- Is `received_at` ever earlier than a candle's `close_time`? That means
  a still-forming bar was treated as closed.

## 2. Data holes: stream gaps, maintenance, no-trade bars

- **WebSocket drops:** every disconnect must be followed by a REST
  backfill of the missed interval, reported as a `FeedEvent`. Flag any path
  where a gap is filled by carrying the last price forward or silently
  skipped.
- **Exchange maintenance and halts:** Upbit/Bithumb hold scheduled
  maintenance and per-coin deposit/withdrawal/trading suspensions. Bars in
  those windows are missing or stale; a strategy must not "trade" through
  them in a backtest. Check they are detected and excluded, not treated as
  normal flat bars.
- **No-trade intervals:** Upbit emits no candle for an interval with zero
  trades. For illiquid markets that is real; for majors it means missing
  data. Check `data.quality.check_candles` gaps are acted on, not ignored.
- **Pagination / rate-limit truncation:** a REST history fetch that hit a
  rate limit or returned a short page must not be reported as complete.
- **Mixed sources:** candles from different exchanges (e.g. Binance
  history + Upbit live) must not be spliced into one series without saying
  so; the KRW premium makes prices differ.

## 3. Survivorship & universe bias

- Is the market list built from coins listed **today**? Coins delisted or
  flagged (유의종목, 상장폐지) disappear from the current list and flatter
  any multi-coin result. Universe selection must be point-in-time.
- Is a coin's listing date respected (no trading before it existed on
  that exchange)?

## 4. Costs and fills

- Is every position change charged fee + spread + impact
  (`CandleCostModel`, or `simulate_market_fill` on a real book)? Zero-cost
  or fee-only backtests overstate swing results and invalidate scalping
  results outright.
- Are cost-model defaults still the stated placeholders? Say so in the
  report until they are calibrated on recorded order books.
- Is order size checked against the bar's traded value / visible depth,
  with oversized orders refused rather than priced at the last level?
- Exchange minimum order value and tick size respected?

## 5. Futures (funding, liquidation)

Futures backtesting exists now: `backtest/futures_engine.py` (leveraged
long/short with intrabar liquidation checks, ADR-0004) and
`backtest/funding_carry_engine.py` (funding-settlement carry, ADR-0009).
For any code touching either:

- Funding payments charged every funding interval, from a real recorded
  rate (`backtest/funding.py`'s `apply_funding_payment`, or
  `funding_carry_engine.py`'s own settlement loop) -- never estimated
  from the premium index or defaulted to zero on a gap.
- Mark price (not last trade price) used for funding and liquidation
  checks, matching Binance's own convention (ADR-0004).
- Liquidation modeled at the correct maintenance margin wherever leverage
  is applied (a backtest that survives a drawdown the exchange would
  have liquidated is wrong). Note: `funding_carry_engine.py` itself
  treats exposure as an unlevered fraction of equity and does **not**
  model liquidation -- a leveraged variant of any candidate that passes
  there must still go through `risk/leverage.py`/`risk/futures_sizing.py`
  before it means anything.
- A funding record with a missing/non-finite `mark_price` forces the
  position flat for that settlement (fail-closed), never silently
  treated as zero funding due or skipped without counting it.

## 6. Statistical validity

- Was the hypothesis pre-registered (`validation/preregistration.py`)
  before the run, and does the run match it?
- Do any TRAIN/VALIDATION/TEST ranges overlap `configs/locked_windows.json`?
  Was the TEST range locked immediately after use?
- Does DSR deflate against every candidate ever registered, not only the
  ones in this report?
- A single-window result without walk-forward + PBO/DSR is
  **INCONCLUSIVE** (see `validation-status-guard`).

## Output

Report like `code-review`: file, line, which category above, and the
concrete failure scenario (what wrong number or decision it would produce,
on what data). Stay within these categories; no style comments.
