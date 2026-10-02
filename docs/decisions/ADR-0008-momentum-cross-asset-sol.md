# ADR-0008: 일봉 모멘텀 저상관 후보(H-0007/8)를 세 번째 자산(KRW-SOL)으로 추가 교차검증

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** account owner, Claude Code session

## Context

ADR-0007's decorrelated 3-candidate daily momentum grid (`ts_momentum_2/14/28`,
`momentum_candidate_grid_daily_decorrelated()`) dropped PBO from 0.857 (5 correlated
candidates, H-0006) to 0.371 on KRW-BTC (H-0007). H-0008 replicated that improvement on a
second, independent asset: KRW-ETH, PBO=0.343, and the best DSR seen in the project so far
(0.923, 14-day), still short of the pre-registered 0.95 bar. Both assets are still
INCONCLUSIVE, but the direction (fewer/decorrelated candidates -> lower PBO, without hurting
DSR) now holds on two assets, not one.

A single additional out-of-sample asset is a cheap, high-value next check before concluding
this is a genuine cross-asset effect: if PBO/DSR patterns hold on a third, unrelated major
coin, that is stronger evidence than a BTC/ETH pair (which are highly correlated with each
other) alone. Per the user's 2026-09-28 instruction, only major/large-cap coins are eligible
(meme coins and low-quality altcoins are excluded from the candidate universe going forward).
KRW-SOL (Solana) is a large-cap, long-listed, non-meme coin with a KRW market on Upbit, and
its price history is largely uncorrelated with the BTC/ETH pair's specific historical
episodes tested so far (different listing date, different market microstructure).

## Decision

Register a new hypothesis H-0009: the same pre-registered decorrelated candidate set
(`ts_momentum_2/14/28`, `momentum_daily_decorrelated`) and criteria as H-0007/H-0008, run on
KRW-SOL daily bars, over Upbit's available KRW-SOL history through 2026-09-01. No code
changes are required -- `--market KRW-SOL` is already supported by `run_swing_study.py`, and
locked windows are checked per-market, so this needs no new market string plumbing. The
candidate grid and success criteria are unchanged from H-0007/H-0008 (no post-hoc tuning
based on either asset's results).

## Consequences

- If PBO stays low (~0.2-0.4) and DSR stays elevated on a third, less-correlated major asset,
  that is meaningfully stronger evidence the decorrelation fix is a real, general effect
  rather than a BTC/ETH-specific artifact.
- If PBO or DSR degrades sharply, that argues the effect may be asset-specific (BTC/ETH share
  more structural similarity with each other than with SOL), and future work should treat the
  daily-momentum lead more cautiously rather than assume it generalizes.
- Locks a new TEST window under `market="KRW-SOL"`, independent of the KRW-BTC/KRW-ETH lock
  calendars, so it does not compete with them for scarce locked-window space.
- Still bound by the same pre-registered success criteria (max_pbo<=0.2, min_dsr>=0.95); even
  a good result here does not by itself clear those bars or justify live capital.
