# ADR-0078: bookTicker top-of-book imbalance (S1) on BTC: no signal

**Status:** Accepted (measurement record; INCONCLUSIVE as a validation result)
**Date:** 2026-10-10
**Deciders:** account owner (동동), Claude Code session

## Context

ADR-0076 measured S1 (quarter-hour order imbalance) with `bookDepth` (resting size within 1 %/3 % of mid) and found nothing, but named
the limit: the paper's quantity is closer to top-of-book imbalance, and `bookTicker` costs about 45 GB for BTC. 동동 approved that
download in the thread ("45 그거 승인할게"). Same label as ADR-0076: an exploratory read of up/down outcomes, not a validation result;
no hypothesis registered, no budget spent, no TEST data.

## Method

`scripts/extract_book_ticker_marks.py` downloads one daily archive at a time (about 140 MB), keeps the last update in each 30 s slot of
the 5 minutes before every quarter-hour mark, and deletes the file. The mark's score is the mean (`s1_top`) or the last slot
(`s1_top_last`) of (bid_qty - ask_qty)/(bid_qty + ask_qty). `scripts/diagnose_signal_candidates.py --marks` then runs the ADR-0076
analysis unchanged (decision at the bar open, horizons 1h/4h/12h, locked windows refused).

Coverage: `bookTicker` exists for BTCUSDT from 2023-05-16 (2023-04-20..2023-05-15 are 26 archive 404s, reported as missing, not filled)
to the window end 2024-03-24, 314 days; 91.95 % of decision bars have a score. ETH was not run: its unlocked window (2024-12-14..2026-08)
is outside the days this download was approved for and was not sized.

## Result

BTCUSDT, 30,000 decisions; "q80" = the 20 % largest |score|, gross mean in bp following the score's sign; cost to beat 12-20 bp.

| Score | h | corr (t_eff) | q80 gross bp (t_eff) | by quarter | net at 16 bp | beats base-rate Brier |
|---|---|---|---|---|---|---|
| mean | 1h | 0.003 (0.3) | -0.2 (-0.2) | -0.3/-0.5/1.2/-1.4 | -16.2 | no |
| mean | 4h | -0.001 (-0.0) | -1.0 (-0.2) | -1.5/-0.1/-0.2/-2.9 | -17.0 | no |
| mean | 12h | -0.007 (-0.2) | -3.2 (-0.2) | 0.5/-4.9/0.7/-9.3 | -19.2 | no |
| last | 1h | 0.008 (0.7) | 0.5 (0.4) | 1.2/-1.4/2.2/-0.5 | -15.5 | no |
| last | 4h | 0.012 (0.5) | 0.7 (0.1) | 1.5/-1.2/3.8/-2.3 | -15.4 | no |
| last | 12h | 0.002 (0.1) | 1.1 (0.1) | -3.6/-1.6/6.3/2.1 | -14.9 | no |

Hit rates are 49-51 %, the base rate (the long skew seen with depth imbalance is gone). Correlation with the past hour's return is
0.006-0.010 and partial correlation is the same as the raw one. Raw JSON:
`/mnt/project-files/research/phaseA-signal-candidates-2026-10-10/btc_top_of_book.json`.

## Reading

Top-of-book imbalance before the quarter-hour mark carries no information about the next 1-12 hours on BTC in this window: correlations
are about 0, moves after a signal are within +-3 bp against 12-20 bp of cost, signs flip between quarters, and no variant beats the
base-rate Brier. The depth-based result of ADR-0076 was not an artefact of using a coarser proxy.

Limits: one symbol and one 10-month regime; the 5-minute pre-mark window and 30 s slots are my choice (the paper's exact window and
"opening return" were not reproduced); the paper (arXiv 2607.09426) was not read in full. Its effect may exist in a 2025-26 market, but
the unlocked data for that is ETH only and was not downloaded.

## Decision

- S1 is closed on BTC with both depth and top-of-book data. No hypothesis, budget, final exam, constant or strategy change.
- An ETH top-of-book pass (about 600 days, roughly 85 GB) is not started; it needs 동동's decision and is not recommended given two nulls.

## Consequences

- The research report's top candidate has no support in two independent data forms; S2 stays a parked BTC-only lead (ADR-0076).
