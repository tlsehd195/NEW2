# ADR-0061: Risk per trade 2%

**Status:** Accepted
**Date:** 2026-10-10
**Deciders:** account owner, Claude Code session

## Context

ADR-0060 clamps the stop to 1-2% of price, so with risk 1% leverage fell to about 0.5-1x. The owner chose option B (2%) to get more size.

## Decision

`configs/risk.json` risk_per_trade 0.01 -> 0.02. Stop clamp, 20x cap and liquidation >= 3 stop distances unchanged.

## Consequences

- Leverage = 2% / stop% = about 1x (2% stop) to 2x (1% stop); 20x cap never binds.
- A stop-out loses 2% of equity; max_daily_loss 10% allows about 5 consecutive stop-outs.
- RiskConfig.version() changes: paper must restart, not comparable to earlier runs. No pre-registration or budget used.
