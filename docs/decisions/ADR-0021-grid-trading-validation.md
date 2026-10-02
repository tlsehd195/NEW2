# ADR-0021: Grid trading (frequant-style) as a validated candidate

**Status:** Accepted
**Date:** 2026-09-29
**Deciders:** account owner (동동, asked for it), Claude Code session

## Context

동동 asked about frequant.kr, a Korean product whose core offering is grid
trading (그리드 매매). The earlier research note concluded that a grid is an
execution scheme, not a validated edge: it earns the step on each round trip
while the price oscillates inside the ladder, and it carries the losing side
of the inventory when the price trends out of it. It was flagged to run
through NEW2's own pipeline instead of being adopted on the vendor's word.
동동 asked for exactly that (2026-09-29).

No peer-reviewed paper backs grid trading as a crypto return source, so this
is not an S/A-grade candidate (ADR-0005). It is tested because a real product
sells it, with the same bar as everything else: pre-registration, locked
windows, walk-forward, PBO <= 0.2, DSR >= 0.95, TEST excess over buy-and-hold
>= 0.

## Decision

1. **Engine** `backtest/grid_engine.py`, single USDⓈ-M perpetual:
   - limit fills at the level price with the maker fee, only when the bar
     path trades strictly through the level; path = open -> nearer extreme
     -> other extreme -> close, plus the move from the previous close;
   - market orders (resets, stops, fail-closed flattening) pay taker fee,
     half spread and volatility-scaled square-root impact (same model as
     `event_engine`);
   - funding charged at every recorded settlement on the position held; a
     missing settlement while holding flattens the grid until the next reset;
   - liquidation: cross margin, equity <= maintenance rate x notional wipes
     the account. The archive has no bracket table, so the rate is a stated
     input: **1%** (conservative versus Binance's lowest tiers);
   - missing bars flatten the grid until the next reset.
   - Integrity check done while building it: with zero costs on a simulated
     martingale aggregated into proper OHLC bars, 1m, 5m and 15m runs all
     average about 0 (40 paths x 45 days), so the path rule adds no free
     edge. A test keeps 5m and 15m within 1% of each other.
2. **Candidates** `strategies/grid.py` (fixed, not fitted). All re-centre
   every 7 days (epoch-aligned), 8 levels per side, half-width = 1.5 x
   sigma_day x sqrt(7) x centre from the previous 14 days, stop one step
   beyond the filled edge, one side fully filled = 1x equity:
   - `grid_neutral_1p5sigma_v1`: futures-neutral grid (long below the
     centre, short above);
   - `grid_spot_1p5sigma_v1`: the classic spot grid bot (buys half the
     ladder at the start, long-only);
   - `grid_neutral_range_filter_v1`: the neutral grid, deployed only when
     the previous 14 daily closes have efficiency ratio < 0.3 (a recent
     range), else flat that week. This is the "only run it in sideways
     markets" advice sellers give.
3. **Regime decomposition** (reporting only, never used by the grid): each
   7-day period is classified after the fact by the efficiency ratio of its
   own daily closes: range < 0.35, trend >= 0.6 (split up/down by the
   period's return), mixed in between, partial when a fold edge cut it
   under 3 days. The report gives each regime's period count, deployments,
   stops and summed return, for the folds and for TEST, plus a P&L
   breakdown: grid round trips, inventory closed at resets/stops, open
   inventory, maker fees, taker fees, spread+impact, funding.
4. **Study** `validation/grid_study.py` + `scripts/run_grid_validation.py` +
   workflow `grid_validation.yml`: policy family `grid`, 60-day warm-up /
   30-day OOS folds, TEST = last 20%, locked before the TEST runs.
5. **Market and range:** BNBUSDT, 15m bars, 2023-04-22 to 2026-01-01.
   BNB is a large, long-listed coin (no meme coins). BTCUSDT and ETHUSDT
   have too little unlocked history left; BNBUSDT's locks (TEST-17
   2022-08-17..2023-04-06, TEST-18 2026-01-02..2026-09-01) bracket this
   range exactly. Hypothesis id H-0021 (H-0019 is in use on another branch).

## Consequences

- Grid is a new family with its own 3-per-30-days budget. If H-0021 fails,
  the same three grids are not re-run on other coins (same multiple-testing
  trap as the momentum family).
- Not modelled: exchange tick/step rounding of levels (no BNBUSDT entry in
  `configs/markets.json`), mark price (traded price used), real maintenance
  brackets. All are listed in the report's assumptions.
- A pass would still only be BACKTEST evidence; paper trading comes next.
