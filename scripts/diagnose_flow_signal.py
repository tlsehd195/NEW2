#!/usr/bin/env python3
"""Measure (not assume) what taker-flow imbalance, VPIN-proxy and funding crowding say about later returns (ADR-0064).

Reads TRAIN+VALIDATION bars only (end must be <= the market's reserved TEST start). Forward returns are sampled
every W bars so samples do not overlap. Prints JSON: correlation, hit rate and mean forward return by z bucket,
per-quarter stability, and how much of the flow is just the past return.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.research.market_data import load_candles, load_funding  # noqa: E402
from cointrader.validation.screening import load_reserved, reserved_for  # noqa: E402


def corr(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = math.fsum(x) / n, math.fsum(y) / n
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx, syy = math.fsum((a - mx) ** 2 for a in x), math.fsum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def analyse(candles, w, zw, funding):
    n = len(candles)
    pre = [0.0]
    for c in candles:
        pre.append(pre[-1] + c.taker_buy_volume)
    cv = [0.0]
    for c in candles:
        cv.append(cv[-1] + c.volume)
    flow = [None] * n
    for i in range(w - 1, n):
        v = cv[i + 1] - cv[i + 1 - w]
        flow[i] = (2 * (pre[i + 1] - pre[i + 1 - w]) - v) / v
    rows = []
    for t in range(zw + w, n - w, w):
        win = flow[t + 1 - zw:t + 1]
        m = math.fsum(win) / zw
        sd = math.sqrt(math.fsum((f - m) ** 2 for f in win) / (zw - 1))
        z = (flow[t] - m) / sd
        fwd = candles[t + w].close / candles[t].close - 1
        past = candles[t].close / candles[t - w].close - 1
        pv = math.fsum(abs(2 * candles[k].taker_buy_volume / candles[k].volume - 1) for k in range(t - 47, t + 1)) / 48
        fr = [r for ft, r in funding if ft <= candles[t].close_time][-9:]
        rows.append({"t": candles[t].open_time, "z": z, "fwd": fwd, "past": past, "vpin": pv,
                     "fund": math.fsum(fr) / len(fr) if len(fr) == 9 else None})
    z, f = [r["z"] for r in rows], [r["fwd"] for r in rows]
    out = {"w": w, "samples": len(rows), "corr_z_fwd": corr(z, f), "corr_z_past": corr(z, [r["past"] for r in rows]),
           "corr_past_fwd": corr([r["past"] for r in rows], f)}
    out["t_stat_corr"] = out["corr_z_fwd"] * math.sqrt(len(rows) - 2) / math.sqrt(1 - out["corr_z_fwd"] ** 2)
    buckets = {}
    for name, lo, hi in (("z<=-1", -9, -1), ("-1<z<0", -1, 0), ("0<=z<1", 0, 1), ("z>=1", 1, 9)):
        sel = [r["fwd"] for r in rows if lo < r["z"] <= hi or (name == "0<=z<1" and 0 <= r["z"] < 1)]
        if name == "z>=1":
            sel = [r["fwd"] for r in rows if r["z"] >= 1]
        if name == "z<=-1":
            sel = [r["fwd"] for r in rows if r["z"] <= -1]
        if name == "-1<z<0":
            sel = [r["fwd"] for r in rows if -1 < r["z"] < 0]
        mean = math.fsum(sel) / len(sel) if sel else None
        sd = math.sqrt(math.fsum((x - mean) ** 2 for x in sel) / (len(sel) - 1)) if len(sel) > 2 else None
        buckets[name] = {"n": len(sel), "mean_fwd_pct": None if mean is None else 100 * mean,
                         "se_pct": None if sd is None else 100 * sd / math.sqrt(len(sel)),
                         "share_up": (sum(1 for x in sel if x > 0) / len(sel)) if sel else None}
    out["by_z_bucket"] = buckets
    # Strategy-style edge: long at z>=1, short at z<=-1, per trade, before costs (round trip 0.12-0.20%).
    tr = [r["fwd"] if r["z"] >= 1 else -r["fwd"] for r in rows if abs(r["z"]) >= 1]
    out["signed_edge_pct_per_trade"] = 100 * math.fsum(tr) / len(tr) if tr else None
    out["signed_hit_rate"] = sum(1 for x in tr if x > 0) / len(tr) if tr else None
    out["signed_trades"] = len(tr)
    q = {}
    for r in rows:
        k = f"{r['t'].year}Q{(r['t'].month - 1) // 3 + 1}"
        if abs(r["z"]) >= 1:
            q.setdefault(k, []).append(r["fwd"] if r["z"] >= 1 else -r["fwd"])
    out["per_quarter_signed_edge_pct"] = {k: [round(100 * math.fsum(v) / len(v), 3), len(v)] for k, v in sorted(q.items())}
    vp = sorted(r["vpin"] for r in rows)
    hi = vp[int(0.9 * len(vp))]
    out["abs_fwd_pct_vpin_top10"] = 100 * math.fsum(abs(r["fwd"]) for r in rows if r["vpin"] >= hi) / max(1, sum(1 for r in rows if r["vpin"] >= hi))
    out["abs_fwd_pct_vpin_rest"] = 100 * math.fsum(abs(r["fwd"]) for r in rows if r["vpin"] < hi) / max(1, sum(1 for r in rows if r["vpin"] < hi))
    fr = [r for r in rows if r["fund"] is not None]
    out["corr_funding_fwd"] = corr([r["fund"] for r in fr], [r["fwd"] for r in fr])
    out["funding_samples"] = len(fr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    s, e = (datetime.fromisoformat(x).replace(tzinfo=timezone.utc) for x in (a.start, a.end))
    held = reserved_for(load_reserved(), a.symbol)
    if held is not None and e > held.start:
        raise SystemExit(f"end {e} reaches the reserved TEST {held.start}")
    candles, notes = load_candles(a.symbol, Timeframe.MINUTE_15, s, e)
    if any(c.taker_buy_volume is None for c in candles):
        raise SystemExit("taker_buy_volume missing in the archive")
    fmap, fn = load_funding(a.symbol, s, e)
    funding = sorted(fmap.items())
    res = {"symbol": a.symbol, "start": a.start, "end": a.end, "bars": len(candles), "notes": notes[:10] + fn[:5],
           "windows": [analyse(candles, w, 960, funding) for w in (16, 48)]}
    text = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
