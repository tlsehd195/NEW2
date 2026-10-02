"""Research / AI dataset builder with leakage, survivorship and selection checks.

One row per (symbol, closed bar): the features a strategy could have seen
when that bar closed, plus forward-looking TARGETS measured only on bars
that open at or after the feature bar's close:

    future_return_<h>   close-to-close log return over the horizon
    mfe_<h> / mae_<h>   max favourable / adverse excursion (long view)
    success_<h>         future_return_<h> > round-trip cost threshold

Checks performed (each failure is recorded in `DatasetReport.issues`,
and rows that fail are EXCLUDED, never silently kept):

* leakage: features are recomputed from candles closed at or before the
  row time only; a row whose stored feature `as_of` is later than the
  bar close is rejected; targets never use the feature bar itself.
* locked windows: a row whose feature bar or any target bar touches a
  locked TEST window for that market is excluded (those windows may
  never be used again, for anything -- CLAUDE.md rule 1).
* survivorship: the universe is taken as given per timestamp; a symbol
  whose data ends early is reported (`symbols_ending_early`), not
  dropped from earlier dates -- dropping it would bias toward survivors.
* selection: excluded-row counts are reported by reason, so a filter
  that removed most rows is visible.

Nothing in this module writes to `configs/`, a strategy registry, or any
live setting: a dataset is research input, and accumulated data never
changes a running strategy by itself (ADR-0015; AST test enforces).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional, Sequence

from cointrader.data.models import Candle
from cointrader.features.snapshot import SNAPSHOT_WARMUP, candle_features
from cointrader.validation.locked_windows import LockedWindow, overlaps

DATASET_VERSION = "1.0.0"
DEFAULT_HORIZONS = {"1m": timedelta(minutes=1), "5m": timedelta(minutes=5), "15m": timedelta(minutes=15),
                    "1h": timedelta(hours=1)}


@dataclass
class DatasetReport:
    rows: list[dict]
    excluded: Counter = field(default_factory=Counter)
    issues: list[str] = field(default_factory=list)
    symbols_ending_early: list[str] = field(default_factory=list)
    dataset_version: str = DATASET_VERSION

    def summary(self) -> dict:
        total = len(self.rows) + sum(self.excluded.values())
        return {"dataset_version": self.dataset_version, "rows": len(self.rows), "excluded": dict(self.excluded),
                "excluded_fraction": (sum(self.excluded.values()) / total) if total else None,
                "symbols_ending_early": self.symbols_ending_early, "issues": self.issues[:20]}


def _targets(bars: Sequence[Candle], i: int, horizon: timedelta, cost: float) -> Optional[dict]:
    """Targets for the feature bar `bars[i]`, using bars i+1..k where
    bars[k].close_time <= bars[i].close_time + horizon."""
    base = bars[i]
    end = base.close_time + horizon
    fwd = []
    for b in bars[i + 1:]:
        if b.close_time > end:
            break
        fwd.append(b)
    if not fwd or fwd[-1].close_time != end:
        return None  # horizon not fully observed (gap or end of data) -> no target, never a partial one
    ret = math.log(fwd[-1].close / base.close)
    mfe = max(b.high for b in fwd) / base.close - 1
    mae = 1 - min(b.low for b in fwd) / base.close
    return {"future_return": ret, "mfe": mfe, "mae": max(mae, 0.0), "success": ret > cost,
            "target_end": end}


def build_dataset(
    candles_by_symbol: dict[str, Sequence[Candle]],
    *,
    locked: Sequence[LockedWindow],
    horizons: Optional[dict[str, timedelta]] = None,
    round_trip_cost: float = 0.0012,
    feature_fn: Callable[[Sequence[Candle]], dict] = candle_features,
    warmup: int = SNAPSHOT_WARMUP,
    stride: int = 1,
) -> DatasetReport:
    horizons = horizons or DEFAULT_HORIZONS
    report = DatasetReport(rows=[])
    last_times = {s: c[-1].close_time for s, c in candles_by_symbol.items() if c}
    if last_times:
        latest = max(last_times.values())
        report.symbols_ending_early = sorted(s for s, t in last_times.items() if t < latest)
    for symbol, bars in sorted(candles_by_symbol.items()):
        bars = list(bars)
        if any(b.open_time <= a.open_time for a, b in zip(bars, bars[1:])):
            report.issues.append(f"{symbol}: candles not strictly increasing; symbol skipped")
            report.excluded["unsorted_symbol"] += len(bars)
            continue
        tf = bars[0].timeframe.delta if bars else None
        usable = {k: h for k, h in horizons.items() if tf and h >= tf and h % tf == timedelta(0)}
        for k in sorted(set(horizons) - set(usable)):
            report.issues.append(f"{symbol}: horizon {k} not a multiple of the bar interval; no target")
        for i in range(warmup - 1, len(bars), stride):
            bar = bars[i]
            max_end = bar.close_time + max(usable.values(), default=timedelta(0))
            if overlaps(locked, symbol, bar.open_time, max_end):
                report.excluded["locked_window"] += 1
                continue
            history = bars[: i + 1]
            feats = feature_fn(history)
            as_of = feats.get("as_of")
            if as_of is not None and datetime.fromisoformat(str(as_of)) > bar.close_time:
                report.excluded["feature_after_row_time"] += 1
                report.issues.append(f"{symbol} {bar.open_time.isoformat()}: feature as_of after bar close (leak)")
                continue
            row = {"symbol": symbol, "timeframe": bar.timeframe.value, "as_of": bar.close_time.isoformat(),
                   "source": bar.source, "features": feats}
            complete = True
            for key, h in usable.items():
                t = _targets(bars, i, h, round_trip_cost)
                if t is None:
                    complete = False
                    break
                if t["target_end"] <= bar.close_time:  # defensive: a target must be strictly in the future
                    raise AssertionError("target window does not lie after the feature bar")
                row[f"future_return_{key}"] = t["future_return"]
                row[f"mfe_{key}"] = t["mfe"]
                row[f"mae_{key}"] = t["mae"]
                row[f"success_{key}"] = t["success"]
            if not complete:
                report.excluded["incomplete_target_window"] += 1
                continue
            report.rows.append(row)
    return report
