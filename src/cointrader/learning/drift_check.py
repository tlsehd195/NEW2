"""Daily feature-drift check on what the paper/live trader actually saw.

Compares one UTC day's journaled feature snapshots with the previous
`BASELINE_DAYS` days, per symbol, using `monitoring.drift` (mean shift and
bucket-frequency shift). Observation only: the result is written to the
`learning` layer and a WARNING is sent when drift is detected; nothing
stops trading or changes a model. UNKNOWN (too few rows) is reported as
UNKNOWN, never as "no drift".

The feature list is fixed here, before any result was seen: the
scale-free fields of the standard snapshot (`features.snapshot`), so a
price level change alone is not "drift". With 9 features x 2 tests some
alarms are expected by chance; a flag is a prompt to look, not proof.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from cointrader.journal.store import LayeredStore
from cointrader.learning.journal_data import load_feature_rows
from cointrader.monitoring.drift import DriftStatus, any_drift, feature_drift

BASELINE_DAYS = 7
DRIFT_FEATURES = ("rsi_14", "roc_10", "bb_pct_b", "bb_zscore", "atr_14_fraction", "realized_vol_20",
                  "volume_z_20", "vwap_deviation_20", "price_vs_ema_50")


def _numeric(rows: list[dict]) -> list[dict]:
    return [{k: r[k] for k in DRIFT_FEATURES if isinstance(r.get(k), (int, float))} for r in rows]


def daily_drift_record(store: LayeredStore, symbol: str, timeframe: str, day_start: datetime) -> dict:
    """The `learning`-layer record for the UTC day starting at `day_start`."""
    day_end = day_start + timedelta(days=1)
    baseline = _numeric(load_feature_rows(store, symbol, timeframe, start=day_start - timedelta(days=BASELINE_DAYS),
                                          until=day_start))
    current = _numeric(load_feature_rows(store, symbol, timeframe, start=day_start, until=day_end))
    results = feature_drift(baseline, current, DRIFT_FEATURES)
    if any_drift(results):
        status = DriftStatus.DRIFT_DETECTED
    elif all(r.status is DriftStatus.NO_DRIFT for r in results):
        status = DriftStatus.NO_DRIFT
    else:
        status = DriftStatus.UNKNOWN
    return {
        "event": "feature_drift", "symbol": symbol, "timeframe": timeframe, "day": day_start.date().isoformat(),
        "status": status.value, "baseline_days": BASELINE_DAYS, "n_baseline": len(baseline), "n_current": len(current),
        "drifted": sorted({r.metric for r in results if r.status is DriftStatus.DRIFT_DETECTED}),
        "results": [{**asdict(r), "status": r.status.value} for r in results],
        "use": "observation only; never changes a model or trading",
    }
