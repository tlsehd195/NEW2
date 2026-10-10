#!/usr/bin/env python3
"""Phase A diagnostics for volume-profile signals (ADR-0077). MEASUREMENT ONLY.

Ideas taken from the public curriculum of an Instagram-promoted FX mentoring site (VWAP, anchored VWAP, fixed-range volume profile
POC/VAH/VAL). The site publishes no rules, so these are the textbook versions, scored on 15m perp bars:

  v1  anchored-VWAP deviation: (last close - UTC-day anchored VWAP) / prior 96-bar mean true range, squashed to (-1, 1)
  v2  POC distance: (last close - POC of the previous 96 bars) / same range unit, squashed
  v3  value-area position: previous UTC day's VAH/VAL; +1 above VAH, -1 below VAL, otherwise 0 (breakout read)

Sign convention: positive score = "follow" (v1/v2: price above the anchor -> up; v3: breakout -> same side). A NEGATIVE signed mean
in the output therefore means the fade worked. Registers nothing, spends no budget; refuses locked/reserved TEST ranges. Reuses the
Phase A analysis (ADR-0076): correlations with an effective-sample t, quantile buckets, net-of-cost, quarter stability, Platt calibration.

    python3 scripts/diagnose_volume_profile.py --symbol BTCUSDT --start 2023-04-20 --end 2024-03-25 --out reports/vp_btc.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import diagnose_signal_candidates as d  # noqa: E402
from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.research.market_data import load_candles  # noqa: E402
from cointrader.validation.locked_windows import assert_not_locked, load_locked_windows  # noqa: E402
from cointrader.validation.screening import load_reserved, reserved_for  # noqa: E402

LOOKBACK = 96  # bars of 15m = 24 h
BINS = 48
MIN_DAY_BARS = 8


def _squash(x: float, scale: float = 1.5) -> float:
    return math.tanh(x / scale)


def _tp(c) -> float:
    return (c.high + c.low + c.close) / 3.0


def profile(bars):
    """Volume histogram over typical price; returns (poc, vah, val) with a 70 % value area grown around the POC."""
    lo = min(c.low for c in bars)
    hi = max(c.high for c in bars)
    if hi <= lo:
        return None
    w = (hi - lo) / BINS
    vol = [0.0] * BINS
    for c in bars:
        vol[min(BINS - 1, int((_tp(c) - lo) / w))] += c.volume
    total = math.fsum(vol)
    if total <= 0:
        return None
    k = max(range(BINS), key=vol.__getitem__)
    a = b = k
    acc = vol[k]
    while acc < 0.7 * total and (a > 0 or b < BINS - 1):
        up = vol[b + 1] if b < BINS - 1 else -1.0
        dn = vol[a - 1] if a > 0 else -1.0
        if up >= dn:
            b += 1
            acc += up
        else:
            a -= 1
            acc += dn
    return lo + (k + 0.5) * w, lo + (b + 1) * w, lo + a * w


def build_rows(candles):
    step = Timeframe.MINUTE_15.delta
    rows = []
    day_cache = {}
    for i in range(LOOKBACK, len(candles)):
        c = candles[i]
        win = candles[i - LOOKBACK:i]
        if c.open_time - candles[i - LOOKBACK].open_time != step * LOOKBACK:
            continue  # candle hole inside the lookback: fail closed
        last = win[-1]
        rng = math.fsum(x.high - x.low for x in win) / LOOKBACK
        row = {"i": i, "t": c.open_time, "v1": None, "v2": None, "v3": None,
               "past": c.open / candles[i - d.PAST_BARS].open - 1.0}
        if rng > 0:
            day = last.open_time.replace(hour=0, minute=0, second=0, microsecond=0)
            today = [x for x in win if x.open_time >= day]
            if len(today) >= MIN_DAY_BARS and today[0].open_time == day:
                vv = math.fsum(x.volume for x in today)
                if vv > 0:
                    row["v1"] = _squash((last.close - math.fsum(_tp(x) * x.volume for x in today) / vv) / rng)
            p = profile(win)
            if p:
                row["v2"] = _squash((last.close - p[0]) / rng)
        # v3: previous UTC day's value area (needs a complete previous day)
        pday = last.open_time.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        if pday not in day_cache:
            j0 = next((j for j in range(max(0, i - 2 * LOOKBACK), i) if candles[j].open_time >= pday), None)
            bars = [x for x in candles[j0:i] if pday <= x.open_time < pday + timedelta(days=1)] if j0 is not None else []
            day_cache[pday] = profile(bars) if len(bars) == 96 else None
        p = day_cache[pday]
        if p:
            row["v3"] = 1.0 if last.close > p[1] else (-1.0 if last.close < p[2] else 0.0)
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True, help="exclusive")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    start = datetime.fromisoformat(a.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(a.end).replace(tzinfo=timezone.utc)
    warm = start - timedelta(days=3)
    assert_not_locked(load_locked_windows(REPO / "configs" / "locked_windows.json"), a.symbol, warm, end)
    held = reserved_for(load_reserved(), a.symbol)
    if held and end > held.start:
        raise SystemExit(f"end {end} reaches the reserved TEST {held.start}")
    candles, notes = load_candles(a.symbol, Timeframe.MINUTE_15, warm, end)
    first = next(i for i, c in enumerate(candles) if c.open_time >= start)
    rows = [r for r in build_rows(candles) if r["i"] >= first]
    days = (end - start).days
    out = {"label": "PHASE A DIAGNOSTIC (exploratory; not a validation result; registers nothing; do not tune constants to it)",
           "symbol": a.symbol, "range": [start.isoformat(), end.isoformat()], "candles": len(candles),
           "candle_notes": notes[:10], "cost_bps_roundtrip": list(d.COST_BPS), "scores": {}}
    for key in ("v1", "v2", "v3"):
        valid = [r for r in rows if r[key] is not None]
        out["scores"][key] = {
            "coverage": round(len(valid) / len(rows), 4) if rows else 0,
            "share_nonzero": round(sum(1 for r in valid if r[key] != 0) / len(valid), 4) if valid else 0,
            "horizons": {f"h{h}": d.analyse_score(candles, rows, key, h, days) for h in d.HORIZONS}}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
