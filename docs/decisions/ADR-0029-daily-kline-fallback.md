# ADR-0029: Kline loader falls back to daily archive files

**Status:** Accepted
**Date:** 2026-10-03
**Deciders:** account owner (chose "1번" for the new-market re-validation), Claude Code session

## Context

ADR-0028 left "re-validate on a new market" with one gap: BTCUSDT 1d shows ~14 walk-forward folds
(< `min_folds` 16) in its unlocked range. The hypothesis was that the Binance archive has no
*monthly* kline file for BTC's first, partial months, but does have *daily* files, so a daily
fallback could extend BTC back to 2019-09-08 (17 folds).

## Decision

`_KlineArchiveCandles.fetch` (futures and spot) now tries the daily files of a month whose monthly
file is missing. Same archive, same `source`; every day still missing is reported as an
`ArchiveGap` (a month with no daily file at all is one gap, "no daily files either"). Nothing is
filled in, so fail-closed and provenance are unchanged.

## Verification on real data (Actions run 37033506125, BTCUSDT 1d, 2019-09-08 -> 2022-08-17)

The hypothesis was **false**. The archive has no BTC daily files before **2019-12-31** (monthly
2019-09..11 and daily 2019-12-01..17 are all absent). Candles now start 2019-12-31 (960 bars, 0
holes) instead of 2020-01-01: one extra bar, no extra fold. BTC therefore does **not** reach 17
folds with this route; the fold count stays ~14.

## Consequences

- The fallback is kept (small, tested, strictly more complete), but it does not unblock a BTC re-validation.
- Remaining route to >= 16 folds on BTC: shorter warm-up (new candidates with `fit_lookback=100`,
  warm-up ~252; unlocked range 2020-09-09 -> 2022-08-17). That is a new hypothesis under the
  normal budget rule (earliest default registration ~2026-10-29 09:14 KST, or an ADR-backed raise).
- Nothing was registered, no budget raised, no threshold changed.
