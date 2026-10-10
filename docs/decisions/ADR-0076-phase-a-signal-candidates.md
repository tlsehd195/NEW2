# ADR-0076: Phase A diagnostics: quarter-hour order imbalance (S1) and intraday TSM (S2)

**Status:** Accepted (measurement record; INCONCLUSIVE as a validation result)
**Date:** 2026-10-10
**Deciders:** account owner (동동), Claude Code session

## Context

동동 told the session to follow the strategy research report (`전략리서치_보고서.md`) and do Phase A (R1-R4) only: diagnose two
new information sources before spending any registration budget. The 30-day budget (3) is full until 2026-10-29 11:32 KST, so
nothing is registered and no final exam is run.

- **S1** (Kim & Hansen 2026, arXiv 2607.09426, not read in full by this session): order-book imbalance at the start of a quarter hour.
- **S2** (Shen et al.): return since the UTC-day open predicts later returns.

Existing results this does not repeat: taker flow and funding direction no edge (ADR-0064), vol regime and time of day below cost
(ADR-0065), 5m allowed but nothing beat cost (ADR-0066), per-indicator calibration no information (ADR-0072/74/75). The report's
statement that 5m is unused is outdated (ADR-0066).

## Data availability (checked, not assumed)

`data.binance.vision` answered from this cloud session (the earlier "blocked" note no longer holds).

| Archive (futures um) | Availability | Used |
|---|---|---|
| `bookDepth` (~30 s snapshots, resting size within +-1..5 % of mid) | BTC/ETH from 2023-01, daily, ~0.3 MB/day | yes |
| `bookTicker` (top of book) | only from about 2023-05-10 to 2024-03-30, ~140 MB per BTC day (about 45 GB for the window) | no |
| `metrics`, `aggTrades` | present | not needed |

So S1 is measured with **+-1 % and +-3 % depth imbalance**, not top-of-book imbalance. The paper's quantity is the order imbalance in
its own definition; depth within 1 % is a coarser proxy. This is the main limit of this result.

## What was built (diagnostic only)

- `features/indicator_votes.py`: `quarter_hour_imbalance_score` (mean (bid-ask)/(bid+ask) over snapshots in the 5 minutes before the
  bar opens, causal) and `intraday_tsm_score` (squashed return from the UTC-day open, needs 8 closed bars of the day). **`DEFAULT_PANEL`
  and every strategy are unchanged** (test pins the panel).
- `data/binance_vision.py`: `BinanceVisionBookDepth` (gaps reported, never filled; the archive writes percentages as `-5.00` in later files).
- `scripts/diagnose_signal_candidates.py`: refuses locked or reserved ranges; reports coverage, signal frequency, move size against
  round-trip cost 12/16/20 bp, correlation with an effective-sample t-stat (labels overlap), stability by quarter, dependence on the past
  1h return, and walk-forward Platt calibration against the always-base-rate Brier. Workflow `signal_candidates_diag.yml` (manual).

Decision at the open of each 15m bar, entry at that open, horizons 4/16/48 bars (1h/4h/12h). Like `calibrate_vote.py` it reads the
up/down outcome after a decision, so it is an **exploratory read**, not a validation result. Raw JSON:
`/mnt/project-files/research/phaseA-signal-candidates-2026-10-10/`.

Windows (everything else is locked): **BTCUSDT 2023-04-20..2024-03-25** (32,736 bars, 978,722 snapshots, 0 archive gaps) and
**ETHUSDT 2024-12-14..2026-09-01** (60,192 bars, 1,754,608 snapshots, 0 gaps). The two windows do not overlap in time.

## Result

Gross mean move after a signal, following the score's sign, in bp (cost to beat: 12-20 bp). "q80" = the 20 % largest |score|.

| Score | Window | h | corr (t_eff) | q80 gross bp (t_eff) | quarters | net at 16 bp | beats base-rate Brier |
|---|---|---|---|---|---|---|---|
| S1 1% | BTC | 4 | 0.001 (0.1) | 0.6 (0.4) | -0.5/-0.5/1.4/1.3 | -15.4 | yes (0.24893 vs 0.24998) |
| S1 1% | BTC | 16 | -0.006 (-0.3) | -1.2 (-0.2) | mixed | -17.2 | yes (marginal) |
| S1 1% | BTC | 48 | -0.038 (-1.0) | -6.9 (-0.5) | mixed | -22.9 | no |
| S1 1% | ETH | 4 | 0.010 (1.3) | 1.7 (1.2) | -5.3/2.2/2.0/3.9 | -14.3 | no |
| S1 1% | ETH | 16 | 0.018 (1.1) | 5.0 (0.9) | -9.7/5.1/7.3/7.1 | -11.0 | no |
| S1 1% | ETH | 48 | 0.035 (1.2) | 15.9 (1.0) | -18/7/27/20 | -0.1 | no |
| S1 3% | BTC | 16 | 0.007 (0.3) | 5.2 (1.0) | 2/-8/7/21 | -10.8 | yes |
| S2 | BTC | 16 | 0.053 (2.3) | 14.2 (2.2) | 3/0/21/22 | -1.8 | no |
| S2 | BTC | 48 | 0.080 (2.0) | 35.0 (2.1) | 15/-15/49/55 | **+19.0** | no |
| S2 | ETH | 16 | 0.003 (0.2) | -0.6 (-0.1) | -15/10/2/8 | -16.6 | yes (marginal) |
| S2 | ETH | 48 | -0.008 (-0.3) | 1.9 (0.1) | -29/0/25/29 | -14.1 | no |

(S1 3% and the q90/q95 rows are in the JSON; none changes the reading.)

## Reading

- **S1 (depth imbalance at the quarter hour): no usable signal.** At 1h and 4h the move after a signal is 0-5 bp, 3 to 10 times below
  the cost, correlations are about 0 on both symbols, and sign flips between quarters. Most of its 54-61 % "hit rate" is a long skew
  (the median score is positive and both windows rose), not information. It is also negatively tied to the past hour's return
  (-0.44 BTC, -0.14 ETH), but partial correlation given the past return is about 0. Calibration does not beat the base rate on ETH; on BTC the Brier gain is
  0.0001, far below anything worth trading.
- **S2 (intraday TSM): a lead on BTC that did not replicate on ETH.** BTC 12h q80 shows +35 bp gross (+19 bp net at 16 bp cost) with
  t_eff about 2, but one quarter is negative, the gain sits in the last two quarters (a strong rally), the extreme q95 bucket is
  about zero (non-monotonic), and calibration is worse than the base rate. On ETH (a different era) the same score is about 0 at every horizon.
  With 3 scores x 3 horizons x 4 buckets x 2 symbols read, a t of 2 on one cell is what chance produces.
- Neither candidate clears the project's own bar for spending registration budget. This matches the earlier results: price- and
  flow-derived signals on 15m do not beat 12-20 bp.

## Limits (what this does not say)

- Not top-of-book. The paper's effect may need the bookTicker queue and trade timing at the quarter-hour mark; depth within 1 % moves slowly and
  may miss it. A bookTicker pass is possible only for BTC 2023-05..2024-03, about 140 MB per day; a few sampled days would be too small to conclude.
- BTC window is 11 months of one regime; ETH window is a different one. Both rose, so long-skewed rules look better than they are.
- Exploratory read of outcomes: do not tune constants to this table. Any rule needs a new preregistered id.

## Decision

- No hypothesis registered, no budget spent, no final exam run, no TEST data touched. `DEFAULT_PANEL`, thresholds and strategies unchanged.
- S1 on depth data: drop. S2: park as a lead; revisit only with a window that is unlocked at the time and a registration slot after 2026-10-29,
  with the BTC-only, rally-driven pattern named as the risk.
- S3 (regime switch) was not screened here: Phase A named S1 and S2, and S1 data was obtainable, so the fallback was not needed.

## Consequences

- The research report's top candidate (S1) is closed on the data that is available without a large download. The remaining S1 variant
  (bookTicker top of book) costs tens of GB and needs 동동's decision.
- The cloud session can now reach `data.binance.vision`, so future diagnostics need not go through 동동's PC.
