# ADR-0010: H-0009(28일 모멘텀, KRW-SOL)의 사전등록 기준 통과를 네 번째 자산(KRW-XRP)에서 재현 확인

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** account owner, Claude Code session

## Context

H-0009 (KRW-SOL) is the first hypothesis in the project where a candidate
(`ts_momentum_28`) cleared all three pre-registered success criteria (max_pbo<=0.2,
min_dsr>=0.95, min_test_excess_return>=0.0): PBO=0.0, DSR=0.983, TEST excess return +33.96pp.
This is still a single asset and a single TEST window, so before treating it as a real,
generalizable edge rather than one clean draw, it needs an out-of-sample check on a further,
independent asset -- the same logic that motivated ADR-0008 (BTC -> ETH -> SOL).

`compute_pbo` (`validation/pbo_dsr.py`) requires at least 2 candidates (`len(names) < 2` is
rejected), so a single-candidate "just test ts_momentum_28 alone" run is not mechanically
possible with the existing pipeline, and picking just the winning candidate post-hoc would
also be a form of look-ahead bias (choosing the "best" candidate only after seeing which one
won on SOL). The correct check is the same, unmodified 3-candidate grid
(`momentum_candidate_grid_daily_decorrelated()`) used in H-0007/H-0008/H-0009, run on a new
asset, watching whether `ts_momentum_28` (or any candidate) again clears the bar --
not redefining the candidate set around one prior winner.

KRW-XRP (Ripple) is chosen: a large-cap, long-listed, non-meme coin on Upbit (per the user's
2026-09-28 asset-universe instruction), with price dynamics historically distinct from
BTC/ETH/SOL (different narrative drivers, different volatility regime at times), making it a
meaningfully independent robustness check rather than another highly-correlated large-cap.

## Decision

Register H-0010: the unchanged decorrelated 3-candidate grid and success criteria from
H-0007/H-0008/H-0009, run on **KRW-XRP** daily bars, over Upbit's available KRW-XRP history
through 2026-09-01. No code changes needed (`--market KRW-XRP` is already supported; locked
windows are checked per-market).

## Consequences

- If `ts_momentum_28` (or another candidate) clears the pre-registered bar again on an
  independent asset, that is much stronger evidence of a real, general edge than a single
  SOL result -- worth then considering what remains before even paper trading (see
  `backtest-integrity-review` skill: costs, liquidity caps, look-ahead checks specific to
  this candidate/asset).
- If it does not replicate, H-0009's result is more likely a single lucky draw (SOL's TEST
  window, 2025-07~2026-09, may have had idiosyncratic dynamics that happened to favor a
  28-day momentum signal) than a general daily-momentum edge, and future work should treat
  H-0009 as informative but not yet load-bearing.
- Locks a new TEST window under `market="KRW-XRP"`, independent of existing lock calendars.
- Whatever the outcome, this alone still does not authorize paper or live trading --
  `live/safety_gate.py` and human approval remain required for any step beyond backtest.
