#!/usr/bin/env python3
"""Phase A diagnostics for crowd-positioning long/short ratios (ADR-0080, candidate N1). MEASUREMENT ONLY.

Three ratios from the Binance `metrics` archive (top-trader account ratio, top-trader position ratio, all-account ratio;
the taker-volume column is deliberately excluded, ADR-0064 covered it). Each is log-transformed and turned into a causal
z-score against the previous 96 bars (24 h) and 960 bars (10 d) of 15m decisions. Both conventions are read from the same
numbers: "follow" (long when the ratio is high) and "contrarian" (its mirror). Horizons 4/16/48 bars. Registers nothing,
spends no budget, changes no constant. Refuses locked or reserved ranges.

    python3 scripts/diagnose_positioning.py --symbol BTCUSDT --start 2021-05-01 --end 2022-08-17 --out reports/pos_btc_a.json
    python3 scripts/diagnose_positioning.py --combine reports/pos_btc_a.json reports/pos_btc_b.json reports/pos_eth.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from cointrader.data import binance_vision as bv  # noqa: E402
from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.features.positioning import causal_zscore, sample_at_decisions  # noqa: E402
from cointrader.research.market_data import load_candles  # noqa: E402
from cointrader.validation.locked_windows import assert_not_locked, load_locked_windows  # noqa: E402
from cointrader.validation.screening import load_reserved, reserved_for  # noqa: E402
from diagnose_signal_candidates import HORIZONS, PAST_BARS, analyse_score, cached_transport  # noqa: E402

RATIOS = {"top_count": "top-trader accounts", "top_sum": "top-trader positions", "all_count": "all accounts"}
LOOKBACKS = (96, 960)
WARMUP = timedelta(days=11)  # 960 bars + margin: scoring starts this long after --start
COSTS = (12, 16, 20)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def p_two_sided(t):
    return None if t is None else math.erfc(abs(t) / math.sqrt(2))


def holm(pvals: dict) -> dict:
    """Holm step-down adjusted p-values for {key: p}."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m, run, out = len(items), 0.0, {}
    for k, (key, p) in enumerate(items):
        run = max(run, min(1.0, (m - k) * p))
        out[key] = run
    return out


def measure(a) -> dict:
    start, end = _utc(a.start), _utc(a.end)
    assert_not_locked(load_locked_windows(REPO / "configs" / "locked_windows.json"), a.symbol, start - timedelta(days=1), end)
    held = reserved_for(load_reserved(), a.symbol)
    if held and end > held.start and start < held.end:
        raise SystemExit(f"range reaches the reserved TEST {held.start}")
    get = cached_transport(a.cache)
    urls = [f"{bv.BASE_URL}/daily/metrics/{a.symbol}/{a.symbol}-metrics-{d:%Y-%m-%d}.zip" for d in bv._days(start, end)]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(get, urls))
    fetcher = bv.BinanceVisionPositioning(transport=get)
    pts = fetcher.fetch(a.symbol, start, end)
    candles, notes = load_candles(a.symbol, Timeframe.MINUTE_15, start, end)
    times = [p.as_of for p in pts]
    decisions = [c.open_time for c in candles]
    rows = [{"i": i, "t": c.open_time,
             "past": c.open / candles[i - PAST_BARS].open - 1.0 if i >= PAST_BARS else 0.0} for i, c in enumerate(candles)]
    for name in RATIOS:
        x = sample_at_decisions(times, [math.log(getattr(p, name)) for p in pts], decisions)
        for lb in LOOKBACKS:
            z = causal_zscore(x, lb)
            for r, v in zip(rows, z):
                r[f"{name}_z{lb}"] = v
    first_ok = start + WARMUP
    rows = [r for r in rows if r["t"] >= first_ok and r["i"] >= PAST_BARS]
    days = max((end - first_ok).days, 1)
    out = {"label": "PHASE A DIAGNOSTIC (exploratory read; registers nothing; do not tune constants to it)",
           "symbol": a.symbol, "range": [start.isoformat(), end.isoformat()], "scored_from": first_ok.isoformat(),
           "candles": len(candles), "metrics_rows": len(pts), "metrics_gaps": len(fetcher.last_gaps),
           "metrics_gap_examples": [g.detail[-110:] for g in fetcher.last_gaps[:3]], "skipped_rows": fetcher.skipped_rows,
           "candle_notes": notes[:5], "cells": {}}
    for name in RATIOS:
        for lb in LOOKBACKS:
            key = f"{name}_z{lb}"
            valid = [r for r in rows if r[key] is not None]
            cell = {"coverage": round(len(valid) / len(rows), 4) if rows else 0, "horizons": {}}
            for h in HORIZONS:
                cell["horizons"][f"h{h}"] = analyse_score(candles, rows, key, h, days)
            out["cells"][key] = cell
    return out


def combine(paths) -> int:
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    segs = [f"{r['symbol']}:{r['range'][0][:10]}" for r in reports]
    pv = {}
    for s, r in zip(segs, reports):
        for key, cell in r["cells"].items():
            for h, res in cell["horizons"].items():
                p = p_two_sided(res.get("corr_t_eff"))
                if p is not None:
                    pv[(s, key, h)] = p
    adj = holm(pv)
    print(f"family size (corr tests, Holm): {len(pv)} of {len(segs)}x{len(RATIOS)}x{len(LOOKBACKS)}x{len(HORIZONS)} cells")
    print("segments:", ", ".join(f"{s} (rows {r['metrics_rows']}, gaps {r['metrics_gaps']}, scored from {r['scored_from'][:10]})" for s, r in zip(segs, reports)))
    print(f"Holm-significant corr (adj p<0.05): {sorted(k for k, v in adj.items() if v < 0.05) or 'none'}")
    print("\nBest-bucket gross move in bp (follow sign; contrarian = negative), by cell; q = best of q80/q90/q95 by |mean|")
    print("score | h | " + " | ".join(segs) + " | all-seg gross>=12bp same sign?")
    passing = []
    for key in next(iter(reports))["cells"]:
        for h in (f"h{x}" for x in HORIZONS):
            cols, means = [], []
            for s, r in zip(segs, reports):
                res = r["cells"][key]["horizons"][h]
                best = None
                for b in (res.get("signal_buckets") or {}).values():
                    if best is None or abs(b["mean_signed_bps"]) > abs(best["mean_signed_bps"]):
                        best = b
                m = best["mean_signed_bps"] if best else None
                means.append(m)
                c = res.get("corr_pearson")
                cols.append(f"{m:+.1f}bp (corr {c:+.3f}, t {res.get('corr_t_eff')}, adj p {adj.get((s, key, h), float('nan')):.2f})" if m is not None and c is not None else "n/a")
            ok = all(m is not None for m in means) and (all(m >= 12 for m in means) or all(m <= -12 for m in means))
            two = sum(1 for m in means if m is not None and m >= 12) >= 2 or sum(1 for m in means if m is not None and m <= -12) >= 2
            sig = any(adj.get((s, key, h), 1.0) < 0.05 for s in segs)
            if two and sig:  # pre-fixed rule (ADR-0080): the best-bucket column alone is selection-biased on noise
                passing.append((key, h, means))
            print(f"{key} | {h} | " + " | ".join(cols) + f" | {'ALL' if ok else ('2of3' if two else '-')}")
    print(f"\nPASS = Holm-significant corr in >=1 segment AND best-bucket gross >=12bp same direction in >=2 of {len(segs)} segments: {passing or 'none'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol")
    ap.add_argument("--start")
    ap.add_argument("--end", help="exclusive")
    ap.add_argument("--cache", type=Path, default=Path("/tmp/metrics_cache"))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--combine", nargs="+")
    a = ap.parse_args()
    if a.combine:
        return combine(a.combine)
    if not (a.symbol and a.start and a.end):
        ap.error("--symbol, --start and --end are required")
    out = measure(a)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
