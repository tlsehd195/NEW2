"""Shadow challenger: retrain the ML models once a day and score them
against the running (champion) strategy on the same bars. Never trades.

For UTC day D and each champion horizon h (bars):

* train   ridge and bagged-tree models (`ml/`) on journal samples whose
          target bar closed at or before D 00:00 (the newest `TRAIN_BARS`),
          so nothing from day D leaks into the fit;
* predict every bar that closed during D, and compare with the realized
          h-bar forward log return (the cycle runs after D + h bars);
* champion the decisions the paper trader actually journaled for the same
          bars: its entry signal and its `p_long` (ADR-0044 part 1).

Both sides get the same metrics: information coefficient (Pearson of
score vs realized return), direction hit rate over all bars, and for the
bars where it would have entered, the count, hit rate and mean return net
of `ROUND_TRIP_COST`. These are a few days of shadow numbers, not a
validation result: one day of 96 bars is noise, and a challenger that looks
better still needs pre-registration -> locked window -> walk-forward ->
PBO/DSR -> one TEST before it may trade (CLAUDE.md rule 1).

Every constant below was fixed before any result was seen.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional, Sequence

from cointrader.data.models import Candle, Timeframe
from cointrader.journal.store import LayeredStore
from cointrader.learning.journal_data import load_candles, missing_bars
from cointrader.ml.features import FEATURE_IDS, FEATURE_WINDOW, compute_feature_vector
from cointrader.ml.linear_model import RidgeModel, select_ridge_via_expanding_window_cv
from cointrader.ml.samples import MLSample
from cointrader.ml.tree_model import BaggedTreeModel

TRAIN_BARS = 2000        # ~21 days of 15m bars
MIN_TRAIN = 300          # fewer usable samples -> UNKNOWN, no fit
CONFIDENCE_MIN = 0.6     # same as MLStrategy
ROUND_TRIP_COST = 0.002  # perps round trip ~0.12-0.20%; the upper end, also the entry edge
FAMILIES = ("ridge", "forest")
STATUS = "SHADOW_ONLY_UNVALIDATED"
PROMOTION = "not usable for trading; needs pre-registration -> walk-forward -> PBO/DSR -> one TEST (CLAUDE.md rule 1)"


def _samples(candles: Sequence[Candle], horizon: int, step: timedelta) -> tuple[list[MLSample], int]:
    """(sample per usable bar, bars skipped because a hole made the horizon not `horizon` bars long)."""
    out, gaps = [], 0
    for i in range(FEATURE_WINDOW - 1, len(candles)):
        j = i + horizon
        target = None
        if j < len(candles):
            if candles[j].open_time - candles[i].open_time != horizon * step:
                gaps += 1
                continue
            a, b = candles[i].close, candles[j].close
            target = math.log(b / a) if a > 0 and b > 0 else None
        if candles[i].open_time - candles[i + 1 - FEATURE_WINDOW].open_time != (FEATURE_WINDOW - 1) * step:
            gaps += 1
            continue
        feats = compute_feature_vector(candles[i + 1 - FEATURE_WINDOW: i + 1])
        if feats is None or target is None or not math.isfinite(target):
            continue
        out.append(MLSample(candles[i].close_time, candles[j].close_time, feats, target))
    return out, gaps


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = math.fsum(xs) / n, math.fsum(ys) / n
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = math.fsum((x - mx) ** 2 for x in xs)
    syy = math.fsum((y - my) ** 2 for y in ys)
    return None if sxx <= 0 or syy <= 0 else sxy / math.sqrt(sxx * syy)


def score(rows: list[tuple[Optional[float], int, float]]) -> dict:
    """rows: (score or None, entry -1/0/+1, realized forward log return)."""
    scored = [(s, r) for s, _, r in rows if s is not None]
    nonzero = [(s, r) for s, r in scored if s != 0 and r != 0]
    entries = [(e, r) for _, e, r in rows if e != 0]
    nets = [e * r - ROUND_TRIP_COST for e, r in entries]
    ic = _pearson([s for s, _ in scored], [r for _, r in scored])
    return {
        "n_bars": len(rows),
        "ic": None if ic is None else round(ic, 4),
        "direction_hit_rate": round(sum(1 for s, r in nonzero if s * r > 0) / len(nonzero), 4) if nonzero else None,
        "n_entries": len(entries),
        "entry_hit_rate": round(sum(1 for e, r in entries if e * r > 0) / len(entries), 4) if entries else None,
        "mean_net_return": round(math.fsum(nets) / len(nets), 6) if nets else None,
        "sum_net_return": round(math.fsum(nets), 6),
    }


def _fit(family: str, samples: list[MLSample]):
    model = (RidgeModel(FEATURE_IDS, select_ridge_via_expanding_window_cv(samples, FEATURE_IDS))
             if family == "ridge" else BaggedTreeModel(FEATURE_IDS))
    model.fit(samples)
    return model


def _champion_rows(store: LayeredStore, symbol: str, timeframe: str, strategy_id: str, day_start: datetime,
                   day_end: datetime) -> dict[str, tuple[Optional[float], int]]:
    """bar close time (iso) -> (p_long - 0.5 or None, journaled entry) for the champion's decisions."""
    step = Timeframe(timeframe).delta
    out = {}
    for r in store.read("decision", start=day_start.date(), end=(day_end + timedelta(days=1)).date()):
        if r.get("symbol") != symbol or r.get("strategy_id") != strategy_id or r.get("timeframe") != timeframe:
            continue
        closed = datetime.fromisoformat(r["bar_open_time"]) + step
        if not day_start < closed <= day_end:
            continue
        sig = r.get("signal") or {}
        p = (sig.get("features") or {}).get("p_long")
        out.setdefault(closed.isoformat(), (p - 0.5 if isinstance(p, (int, float)) else None, int(sig.get("entry", 0))))
    return out


def challenger_records(store: LayeredStore, symbol: str, timeframe: str, day_start: datetime,
                       champions: dict[str, int]) -> list[dict]:
    """One `challenger_eval` record per champion strategy (strategy_id -> horizon in bars)."""
    step = Timeframe(timeframe).delta
    day_end = day_start + timedelta(days=1)
    longest = max(champions.values(), default=0)
    candles = load_candles(store, symbol, timeframe, until=day_end + longest * step,
                           start=day_start - (TRAIN_BARS + FEATURE_WINDOW + longest + 96) * step)
    base = {"event": "challenger_eval", "symbol": symbol, "timeframe": timeframe, "day": day_start.date().isoformat(),
            "status": STATUS, "promotion": PROMOTION, "round_trip_cost": ROUND_TRIP_COST,
            "candles": len(candles), "missing_bars": missing_bars(candles)}
    out = []
    for sid, h in sorted(champions.items()):
        samples, gaps = _samples(candles, h, step)
        train = [s for s in samples if s.target_time <= day_start][-TRAIN_BARS:]
        evals = [s for s in samples if day_start < s.as_of_time <= day_end]
        rec = {**base, "horizon": h, "gap_skipped_bars": gaps, "n_train": len(train), "n_eval": len(evals)}
        if len(train) < MIN_TRAIN or not evals:
            out.append({**rec, "result": "UNKNOWN", "reason": "insufficient_history"})
            continue
        realized = {s.as_of_time.isoformat(): s.target for s in evals}
        champ = _champion_rows(store, symbol, timeframe, sid, day_start, day_end)
        champ_rows = [(champ[k][0], champ[k][1], r) for k, r in realized.items() if k in champ]
        challengers = []
        for family in FAMILIES:
            try:
                model = _fit(family, train)
            except ValueError as exc:
                challengers.append({"family": family, "result": "UNKNOWN", "reason": f"fit_failed: {exc}"})
                continue
            rows = []
            for s in evals:
                pred, conf = model.predict_with_confidence(s.features)
                entry = (1 if pred >= ROUND_TRIP_COST else -1 if pred <= -ROUND_TRIP_COST else 0) \
                    if conf >= CONFIDENCE_MIN else 0
                rows.append((pred, entry, s.target))
            challengers.append({"family": family, "result": "SCORED", **score(rows)})
        out.append({**rec, "result": "SCORED", "train_from": train[0].as_of_time.isoformat(),
                    "train_to": train[-1].as_of_time.isoformat(),
                    "champion": {"strategy_id": sid, "n_journaled": len(champ_rows), **score(champ_rows)},
                    "challengers": challengers})
    return out
