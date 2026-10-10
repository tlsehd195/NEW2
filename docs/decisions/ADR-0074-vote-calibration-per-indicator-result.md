# ADR-0074: BTC 15m per-indicator calibration result (exploratory)

**Status:** Accepted (measurement record; INCONCLUSIVE as a validation result)
**Date:** 2026-10-10
**Deciders:** account owner (동동), Claude Code session

## Context

ADR-0073 added per-indicator calibration to `scripts/calibrate_vote.py`. It was
run on 동동's PC on BTCUSDT 15m, 2023-04-20 to 2024-06-18 (unlocked,
`binance_vision_archive`), same range and decision bars as ADR-0072. Samples
per candidate: h16 2548, h48 848. Always-base-rate Brier: h16 0.24948, h48
0.24983. Label: exploratory measurement against up/down outcomes, not a
backtest, not a preregistered hypothesis, spends no budget or TEST data.

## Result

Brier / ECE / up-rate when the indicator's p>=0.60 (n) / when p<=0.40 (n).

vote_h16 (base up-rate 0.523):
- bollinger_b 0.2508 / 0.038 / 0.515 (303) / 0.441 (186)
- donchian_pos 0.2517 / 0.036 / 0.517 (259) / 0.457 (151)
- ema_trend 0.2514 / 0.036 / 0.567 (321) / 0.450 (198)
- obv_slope 0.2548 / 0.060 / 0.512 (254) / 0.500 (152)
- roc 0.2513 / 0.033 / 0.562 (274) / 0.445 (164)
- rsi 0.2521 / 0.043 / 0.522 (205) / 0.409 (110)

vote_side_h16 adds funding_crowding 0.2534 / 0.042 / 0.436 (101) / 0.727 (11)
and oi_confirm 0.2545 / 0.050 / 0.500 (202) / 0.490 (104); the other six match
the rows above within 0.001.

vote_h48 (base 0.513): Brier 0.2599 to 0.2623, ECE 0.085 to 0.109; ema_trend
0.513 (156) / 0.507 (73), roc 0.470 (151) / 0.538 (65), rsi 0.511 (143) /
0.550 (60).

vote_side_h48 adds funding_crowding 0.2579 / 0.086 / 0.533 (120) / 0.385 (39)
and oi_confirm 0.2593 / 0.089 / 0.538 (160) / 0.452 (93).

## Reading

- Every indicator, in every candidate, has a Brier score worse than always
  guessing the base rate. No single indicator is informative on this range.
- At h16, ema_trend (0.567, n 321) and roc (0.562, n 274) went up more often
  than the base rate when bullish, and ~0.45 when bearish, so the direction
  is right. The effect is small and the Brier score is still worse than the
  baseline.
- The same signal disappears at h48 (ema_trend 0.513 / 0.507, roc 0.470 /
  0.538, roc even inverts). It does not hold across horizons.
- funding_crowding's 0.727 (n 11) is too small to read.
- Six to eight indicators across four candidates were compared against the
  same outcomes, so one of them looking decent is expected by chance.

## Decision

- No constant, weight, threshold, indicator set or strategy change; no
  hypothesis registered; no budget spent.
- Do not drop, reweight or add indicators on this table. Any such change is a
  new hypothesis: preregistered id, locked-window check, walk-forward, PBO/DSR
  with the number of looks counted, and a single TEST.
- Together with ADR-0072: on BTC 15m 2023-04-20 to 2024-06-18 neither the
  combined vote nor any single indicator carries measurable information
  beyond the base rate. This is one symbol and one window; it says the
  60% entry rule is unsupported here, not that no 15m edge exists elsewhere.
