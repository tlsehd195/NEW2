"""Data quality gate: may a NEW signal be generated from this data?

Fail-closed (CLAUDE.md rule 4). Any of the following blocks new entries,
each with its reason recorded:

- the recent candle series has a gap, duplicate, out-of-order bar,
  broken OHLC invariant, negative volume or misaligned bar
  (`data.quality.check_candles`), or its last bar is older than one
  full bar interval (stale),
- a price in the window is non-finite or non-positive, or a single bar
  moved more than `max_bar_move` (spike -- recorded, not smoothed away),
- the live feed health is not HEALTHY (stale websocket, disconnected,
  never connected, recent blocking quality event),
- the spread is missing or wider than `max_spread_fraction`,
- the REST and WebSocket versions of the same bar disagree.

The gate never repairs data and never closes positions; it only says
whether a new signal is allowed and why not.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from cointrader._time import require_aware
from cointrader.data.market_events import BookTicker, DataQualityEvent
from cointrader.data.models import Candle
from cointrader.data.quality import check_candles
from cointrader.live.config import HealthStatus


@dataclass(frozen=True)
class QualityLimits:
    max_spread_fraction: float = 0.002  # 20 bp
    max_bar_move: float = 0.25  # one bar moving >25% is a spike to investigate
    max_source_mismatch: float = 0.001  # REST vs WS close within 10 bp
    require_book: bool = True


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.allowed == bool(self.reasons):
            raise ValueError("allowed must be True exactly when there are no reasons")


def compare_sources(ws: Candle, rest: Candle, *, tolerance: float) -> Optional[DataQualityEvent]:
    """REST/WebSocket disagreement on the same closed bar."""
    if (ws.market, ws.timeframe, ws.open_time) != (rest.market, rest.timeframe, rest.open_time):
        raise ValueError("compare_sources needs the same market/timeframe/open_time")
    worst = max(abs(getattr(ws, f) - getattr(rest, f)) / getattr(rest, f) for f in ("open", "high", "low", "close"))
    if worst > tolerance:
        return DataQualityEvent("rest_ws_mismatch", ws.market, max(ws.received_at, rest.received_at),
                                f"{ws.open_time.isoformat()}: {ws.source} vs {rest.source} differ by {worst:.4%}",
                                "quality_gate")
    return None


def evaluate_data_quality(
    *,
    now: datetime,
    candles: Sequence[Candle],
    feed_health: Optional[HealthStatus],
    book: Optional[BookTicker] = None,
    recent_events: Sequence[DataQualityEvent] = (),
    limits: QualityLimits = QualityLimits(),
) -> GateDecision:
    require_aware("now", now)
    reasons: list[str] = []
    if not candles:
        reasons.append("no_candles")
    else:
        for issue in check_candles(candles):
            reasons.append(f"candle_{issue.kind}@{issue.at.isoformat()}")
        for prev, cur in zip(candles, candles[1:]):
            if prev.close > 0 and abs(cur.close / prev.close - 1) > limits.max_bar_move:
                reasons.append(f"price_spike@{cur.open_time.isoformat()}")
        for c in candles:
            if min(c.open, c.high, c.low, c.close) <= 0:
                reasons.append(f"non_positive_price@{c.open_time.isoformat()}")
        last = candles[-1]
        if now - last.close_time >= last.timeframe.delta:
            reasons.append(f"candles_stale_last_close_{last.close_time.isoformat()}")
        if last.close_time > now:
            reasons.append("last_candle_not_closed")
    if feed_health is not HealthStatus.HEALTHY:
        reasons.append(f"feed_{(feed_health or HealthStatus.UNKNOWN).value.lower()}")
    if book is None:
        if limits.require_book:
            reasons.append("book_missing")
    else:
        spread = book.spread_fraction
        if not math.isfinite(spread) or spread > limits.max_spread_fraction:
            reasons.append(f"spread_too_wide_{spread:.5f}")
    for ev in recent_events:
        if ev.blocks_trading:
            reasons.append(f"quality_event_{ev.kind}")
    # de-duplicate while keeping order
    seen: set[str] = set()
    unique = tuple(r for r in reasons if not (r in seen or seen.add(r)))
    return GateDecision(not unique, unique)
