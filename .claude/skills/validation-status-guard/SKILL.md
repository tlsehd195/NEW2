---
name: validation-status-guard
description: Check that any status update, ADR, PR description or report about a strategy's validation state uses precise labels (INCONCLUSIVE / backtest / paper trading / live) and names its data source before it is written. Use before writing anything that says a strategy or data pipeline is "validated", "works", "profitable" or "ready", including README, docs/decisions ADRs, PR bodies and Discord reports.
---

# Validation Status Guard

Adopted from tlsehd195/NEW-, whose history includes the failure this
guards against: describing a backtest or paper result as if it proved
something about real trading. Run this before writing the claim, not after.

## Labels (use exactly these, never blur them)

| Label | Meaning |
| --- | --- |
| **INCONCLUSIVE** | Any result without walk-forward + PBO/DSR + one held-out TEST, or one that failed the pre-registered criteria. Default when unsure. |
| **백테스트 (backtest)** | Simulated on historical candles/books. Name the market, timeframe, date range, data source, cost-model settings, and the evolution status reached (BACKTESTED / VALIDATED / OOS_TESTED). |
| **페이퍼 트레이딩 (paper)** | Live market data, simulated orders. Name the period and whether fills used real order-book snapshots. |
| **실거래 (live)** | Real orders on the exchange. Only after `live/safety_gate.py` passed with a human `LiveActivationApproval`. |

## Before writing any status or validation claim

1. **Trace the data source.** Is the number from real exchange data (which
   exchange, which fetch, which range), from synthetic test fixtures, or
   from a mix? Check the code path or report file; "it ran and printed a
   number" is not evidence of which.
2. **Match the claim to the evidence.**
   - "검증 완료" / "validated" only for a strategy that reached OOS_TESTED
     on real data with the pre-registered criteria met, and even then say
     "백테스트 기준", not "수익이 난다".
   - Synthetic-fixture test runs prove code correctness only. Never report
     them as evidence about any market.
   - If real data was not fetched (network block, missing key, rate limit),
     say exactly that: what was blocked, where, what was tried. This repo's
     own bootstrap is an example: the cloud sandbox gets HTTP 403 from
     api.upbit.com, so no real-data study had run as of 2026-09-28.
3. **Don't carry old findings forward unchecked.** If an earlier note said
   data was blocked or a check passed, say whether this session re-checked
   it, and show the check.
4. **Readiness claims go through the gate.** "Ready for paper" or "ready
   for live" must cite the candidate's evolution status and, for live, the
   safety-gate result and human approval. A green backtest alone is never
   grounds for "ready for live".

## Output

If a draft violates any of the above, quote the sentence, say what
evidence is missing or mislabeled, and propose corrected wording. Don't
rewrite silently; the data lineage needs confirmation from the user or the
code, not a guess.
