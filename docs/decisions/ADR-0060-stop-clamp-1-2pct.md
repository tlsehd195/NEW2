# ADR-0060: Stop distance clamped to 1-2% of price

**Status:** Accepted
**Date:** 2026-10-10
**Deciders:** account owner, Claude Code session

## Context

ADR-0059 sized by `risk_per_trade / stop distance` with the stop at `stop_atr x ATR(14)`, so the stop (and thus the leverage) floated with volatility. The owner asked for the stop to stay between 1% and 2% of price.

## Decision

`RiskConfig.stop_min_fraction = 0.01` and `stop_max_fraction = 0.02` (configs/risk.json). The ATR-based stop is kept but clamped to [1%, 2%] of the entry reference price before sizing. `RiskDecision.stop_distance` carries the effective value; paper and the event backtest use it for the real stop, so sizing and the stop always agree. The liquidation >= 3 stop distances check (ADR-0059) is unchanged. Unset (None) means no clamp, so other callers are unaffected. RISK_ENGINE_VERSION 1.1.0.

## Consequences

- With risk 1%, leverage = 1% / stop% is about 0.5x-1x, lower than before; the 20x cap never binds. More size needs a higher `risk_per_trade` (not changed here, owner decision).
- RiskConfig.version() changes: paper must restart, results are not comparable to earlier runs. No hypothesis pre-registration or budget used.
