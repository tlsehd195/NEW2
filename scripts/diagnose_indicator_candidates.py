#!/usr/bin/env python3
"""Measure candidate indicators that do NOT overlap the six default votes (ADR-0070).

For ~18 candidates (see features/candidate_indicators.py, plus cross-asset lead-lag and open-interest change):
 1. overlap: |corr| of the candidate's VOTE-FORM score with each of the six default scores (and with their
    absolute values, since regime features relate to magnitude), and the effective number of independent
    indicators (participation ratio / entropy, as ADR-0046) of six + candidate.
 2. predictive power at 4h (16 bars) and 12h (48 bars) on NON-overlapping samples:
      T1 corr(z, fwd return)           every candidate
      T2 corr(z, |fwd return|)         non-directional candidates (does it forecast the size of the move?)
      T5 corr(vote score, fwd return)  non-directional candidates (the form that would enter the vote)
      T4 momentum edge, top vs bottom tercile of the candidate (is trend-following better in some regime?)
    plus per-trade mean of sign(vote score) * fwd for |z| >= 1 against the 0.12-0.20 % round-trip cost, and
    the sign of T1 in each half of the sample.
 3. multiple-testing: every p-value of T1/T2/T5/T4 goes into ONE family; Holm and Benjamini-Hochberg are
    reported with the family size m. The six default indicators are measured the same way as controls and are
    NOT in the family.

Reads TRAIN+VALIDATION bars only (end must not reach the market's reserved TEST) and no strategy is run.
Prints JSON. Standard library only.

    python3 scripts/diagnose_indicator_candidates.py --symbol BTCUSDT --other ETHUSDT --start 2023-04-20 --end 2024-03-25
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
sys.path.insert(0, str(REPO / "scripts"))

from cointrader.features.candidate_indicators import ALL_FEATURES, DIRECTIONAL, MIN_INDEX, NONDIRECTIONAL, CandleSeries  # noqa: E402
from cointrader.features.indicator_votes import DEFAULT_PANEL, raw_scores  # noqa: E402

COST_LO, COST_HI = 0.12, 0.20  # percent, round trip
GRID = 4  # bars between feature samples; 16 / 48 bar horizons use every 4th / 12th grid point (no overlap)
SCALE = 0.1  # DayTradeVote.score_scale
HORIZONS = (16, 48)
EXTRA_DIR = ("lead_lag", "oi_dir")  # need data outside this symbol's candles
EXTRA_NON = ("oi_chg",)


def mean(x):
    return math.fsum(x) / len(x) if x else None


def sd(x):
    if len(x) < 3:
        return None
    m = mean(x)
    return math.sqrt(math.fsum((a - m) ** 2 for a in x) / (len(x) - 1))


def corr(x, y):
    n = len(x)
    if n < 10:
        return None
    mx, my = mean(x), mean(y)
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx, syy = math.fsum((a - mx) ** 2 for a in x), math.fsum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def tstat_corr(r, n):
    return None if r is None or abs(r) >= 1 else r * math.sqrt(n - 2) / math.sqrt(1 - r * r)


def pval(t):
    return None if t is None else math.erfc(abs(t) / math.sqrt(2))


def welch_t(a, b):
    if len(a) < 10 or len(b) < 10:
        return None
    va, vb = sd(a) ** 2 / len(a), sd(b) ** 2 / len(b)
    return (mean(a) - mean(b)) / math.sqrt(va + vb) if va + vb > 0 else None


def holm_bh(ps):
    """ps: list of (key, p). Returns (#Holm rejections at 0.05, #BH rejections at 0.05)."""
    m = len(ps)
    order = sorted(ps, key=lambda kv: kv[1])
    holm = 0
    for i, (_, p) in enumerate(order):
        if p <= 0.05 / (m - i):
            holm += 1
        else:
            break
    bh = 0
    for i, (_, p) in enumerate(order, start=1):
        if p <= 0.05 * i / m:
            bh = i
    return holm, bh


def correlation_matrix(cols):
    k = len(cols)
    return [[1.0 if a == b else (corr(cols[a], cols[b]) or 0.0) for b in range(k)] for a in range(k)]


def effective(corr_m):
    from diagnose_indicator_overlap import effective_count, eigenvalues
    e = effective_count(eigenvalues(corr_m))
    return {"participation": round(e["participation_ratio"], 3), "entropy": round(e["entropy_exp"], 3)}


def build_rows(candles, other, oi_points, first_index):
    """One row per grid bar: raw features, vote-form scores, the six default scores, forward returns."""
    s = CandleSeries()
    s.extend(candles)
    ox = None
    if other is not None:
        ox = {c.open_time: math.log(c.close) for c in other}
    rows = []
    oi_sorted = sorted(oi_points, key=lambda p: p.as_of)
    for t in range(max(first_index, MIN_INDEX), len(candles) - max(HORIZONS) - 1, GRID):
        f = s.features(t, ALL_FEATURES)
        v = s.vote_scores(t, ALL_FEATURES)
        d = raw_scores(candles[t - 139:t + 1], DEFAULT_PANEL, scale=SCALE)
        if f is None or v is None or d is None:
            continue
        now = candles[t].close_time
        # cross-asset: how far the other market ran ahead of this one over the last 4h (positive = other outperformed)
        if ox is not None:
            a, b = candles[t].open_time, candles[t - 16].open_time
            if a in ox and b in ox:
                gap = (ox[a] - ox[b]) - (s.x[t] - s.x[t - 16])
                f["lead_lag"] = gap / (s._sd(t, 96) * 4.0)
                v["lead_lag"] = math.tanh(f["lead_lag"])
        known = [p for p in oi_sorted if p.as_of <= now]
        if len(known) >= 2 and known[-2].open_interest > 0 and known[-1].open_interest > 0:
            chg = math.log(known[-1].open_interest / known[-2].open_interest)
            dir16 = math.tanh((s.x[t] - s.x[t - 16]) / (s._sd(t, 96) * 4.0))
            f["oi_chg"], v["oi_chg"] = chg, math.tanh(chg / 0.03) * dir16
            f["oi_dir"], v["oi_dir"] = chg * dir16, math.tanh(chg / 0.03) * dir16
        rows.append({"t": t, "f": f, "v": v, "d": d,
                     **{f"fwd{h}": candles[t + h].close / candles[t].close - 1.0 for h in HORIZONS}})
    return rows


def add_z(rows, names, minimum=240):
    """Causal z-score of each raw feature (expanding mean/sd over earlier grid points). Drops the first `minimum` rows."""
    out = []
    acc = {k: [0, 0.0, 0.0] for k in names}  # n, sum, sumsq
    for r in rows:
        z = {}
        for k in names:
            x = r["f"].get(k)
            if x is None:
                continue
            n, sm, sq = acc[k]
            if n >= minimum:
                var = sq / n - (sm / n) ** 2
                if var > 0:
                    z[k] = (x - sm / n) / math.sqrt(var)
            acc[k] = [n + 1, sm + x, sq + x * x]
        r2 = dict(r)
        r2["z"] = z
        out.append(r2)
    return out


def signed_trades(rows, name, h):
    """sign(vote score) * fwd when the causal z of the raw feature is beyond +-1 (fixed convention, no flipping)."""
    tr = []
    for r in rows:
        z = r["z"].get(name)
        v = r["v"].get(name)
        if z is None or v is None or abs(z) < 1 or v == 0:
            continue
        tr.append((1.0 if v > 0 else -1.0) * r[f"fwd{h}"])
    return tr


def measure(rows, h, names, nondir):
    step = h // GRID
    sub = [r for r in rows if (r["t"] // GRID) % step == 0]
    half = len(sub) // 2
    res = {}
    for name in names:
        rr = [r for r in sub if name in r["z"] and name in r["v"]]
        if len(rr) < 100:
            res[name] = {"n": len(rr), "note": "too few samples"}
            continue
        z, f = [r["z"][name] for r in rr], [r[f"fwd{h}"] for r in rr]
        c1 = corr(z, f)
        t1 = tstat_corr(c1, len(rr))
        one = {"n": len(rr), "T1_corr_fwd": c1, "T1_t": t1, "T1_p": pval(t1)}
        h1 = corr(z[:len(rr) // 2], f[:len(rr) // 2])
        h2 = corr(z[len(rr) // 2:], f[len(rr) // 2:])
        one["T1_halves"] = [h1, h2]
        one["T1_same_sign_halves"] = h1 is not None and h2 is not None and h1 * h2 > 0
        tr = signed_trades([r for r in rr], name, h)
        if len(tr) >= 10:
            m = mean(tr)
            one["trade_mean_pct"] = 100 * m
            one["trade_t"] = m / (sd(tr) / math.sqrt(len(tr))) if sd(tr) else None
            one["trade_hit"] = sum(1 for x in tr if x > 0) / len(tr)
            one["trades"] = len(tr)
            mid = len(rr) // 2
            ta = signed_trades(rr[:mid], name, h)
            tb = signed_trades(rr[mid:], name, h)
            one["trade_mean_pct_halves"] = [100 * mean(ta) if ta else None, 100 * mean(tb) if tb else None]
        if name in nondir:
            c2 = corr(z, [abs(x) for x in f])
            t2 = tstat_corr(c2, len(rr))
            one.update({"T2_corr_absfwd": c2, "T2_t": t2, "T2_p": pval(t2)})
            vs = [r["v"][name] for r in rr]
            c5 = corr(vs, f)
            t5 = tstat_corr(c5, len(rr))
            one.update({"T5_corr_vote_fwd": c5, "T5_t": t5, "T5_p": pval(t5)})
            mom = [(1.0 if r["d"]["ema_trend"] > 0 else -1.0) * r[f"fwd{h}"] for r in rr]
            order = sorted(range(len(rr)), key=lambda i: z[i])
            third = len(rr) // 3
            lo, hi = [mom[i] for i in order[:third]], [mom[i] for i in order[-third:]]
            t4 = welch_t(hi, lo)
            one.update({"T4_momentum_edge_top_pct": 100 * mean(hi), "T4_momentum_edge_bottom_pct": 100 * mean(lo),
                        "T4_t": t4, "T4_p": pval(t4)})
        res[name] = one
    ctl = {}
    for k in DEFAULT_PANEL:  # controls: the six default scores through the same T1
        rr = sub
        sc = [r["d"][k] for r in rr]
        c = corr(sc, [r[f"fwd{h}"] for r in rr])
        ctl[k] = {"T1_corr_fwd": c, "T1_t": tstat_corr(c, len(rr))}
    return {"samples": len(sub), "candidates": res, "controls_default_six": ctl}


def overlap(rows, names):
    cols_d = {k: [r["d"][k] for r in rows] for k in DEFAULT_PANEL}
    base = effective(correlation_matrix([cols_d[k] for k in DEFAULT_PANEL]))
    out = {"base_six": base, "candidates": {}}
    for name in names:
        rr = [r for r in rows if name in r["v"]]
        if len(rr) < 200:
            continue
        v = [r["v"][name] for r in rr]
        raw = {k: abs(corr(v, [r["d"][k] for r in rr]) or 0.0) for k in DEFAULT_PANEL}
        ab = {k: abs(corr([abs(x) for x in v], [abs(r["d"][k]) for r in rr]) or 0.0) for k in DEFAULT_PANEL}
        k_raw = max(raw, key=raw.get)
        fv = [r["f"][name] for r in rr]
        fraw = max(abs(corr(fv, [r["d"][k] for r in rr]) or 0.0) for k in DEFAULT_PANEL)
        fabs = max(abs(corr([abs(x) for x in fv], [abs(r["d"][k]) for r in rr]) or 0.0) for k in DEFAULT_PANEL)
        cols = [[r["d"][k] for r in rr] for k in DEFAULT_PANEL] + [v]
        out["candidates"][name] = {"max_abs_corr_vs_six": raw[k_raw], "most_similar": k_raw,
                                   "max_abs_corr_of_magnitudes": max(ab.values()),
                                   "raw_feature_max_abs_corr_vs_six": fraw, "raw_feature_max_abs_corr_of_magnitudes": fabs,
                                   "mean_abs_corr_vs_six": mean(list(raw.values())),
                                   "six_plus_candidate": effective(correlation_matrix(cols))}
    return out


def combine(paths):
    """Pool the p-values of several per-symbol outputs into ONE family and apply Holm / BH to it."""
    pv, rows = [], {}
    for path in paths:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        for h, block in d["horizons"].items():
            for name, r in block["candidates"].items():
                for key in ("T1_p", "T2_p", "T5_p", "T4_p"):
                    if r.get(key) is not None:
                        pv.append((f"{d['symbol']}/{h}/{name}/{key}", r[key]))
                if "T1_corr_fwd" in r:
                    rows.setdefault(name, {})[f"{d['symbol']}/{h}"] = {
                        "T1": r["T1_corr_fwd"], "halves_same_sign": r.get("T1_same_sign_halves"),
                        "trade_mean_pct": r.get("trade_mean_pct"), "T2": r.get("T2_corr_absfwd"), "T5": r.get("T5_corr_vote_fwd"),
                        "T4_top_minus_bottom_pct": None if "T4_momentum_edge_top_pct" not in r else
                        r["T4_momentum_edge_top_pct"] - r["T4_momentum_edge_bottom_pct"]}
    holm, bh = holm_bh(pv)
    out = {"p_values_in_family": len(pv), "raw_below_0.05": sum(1 for _, p in pv if p < 0.05),
           "expected_by_chance": round(0.05 * len(pv), 1), "holm_rejections": holm, "bh_rejections": bh,
           "smallest_p": sorted(pv, key=lambda kv: kv[1])[:12], "per_candidate": rows}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--combine":
        return combine(sys.argv[2:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--other", required=True, help="the other market, for the cross-asset lead-lag feature")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    from cointrader.data.models import Timeframe
    from cointrader.research.market_data import load_candles, load_open_interest
    from cointrader.validation.locked_windows import assert_not_locked, load_locked_windows
    from cointrader.validation.screening import load_reserved, reserved_for

    s, e = (datetime.fromisoformat(x).replace(tzinfo=timezone.utc) for x in (a.start, a.end))
    tf = Timeframe.MINUTE_15
    warm = s - (MIN_INDEX + 60) * tf.delta
    for sym in (a.symbol, a.other):
        held = reserved_for(load_reserved(), sym)
        if held is not None and e > held.start:
            raise SystemExit(f"end {e} reaches the reserved TEST of {sym} ({held.start})")
        assert_not_locked(load_locked_windows(REPO / "configs" / "locked_windows.json"), sym, warm, e)
    candles, notes = load_candles(a.symbol, tf, warm, e)
    other, onotes = load_candles(a.other, tf, warm, e)
    oi, oinotes = load_open_interest(a.symbol, warm, e)
    first = next(i for i, c in enumerate(candles) if c.open_time >= s)
    rows = build_rows(candles, other, oi, first)
    names = list(ALL_FEATURES) + [n for n in EXTRA_DIR + EXTRA_NON if any(n in r["f"] for r in rows)]
    nondir = set(NONDIRECTIONAL) | set(EXTRA_NON)
    rows = add_z(rows, names)[240:]  # the first 240 grid points only seed the z-score
    out = {"label": "MEASUREMENT ONLY (no strategy run, no TEST read, no registration)", "symbol": a.symbol,
           "other": a.other, "start": a.start, "end": a.end, "bars": len(candles), "grid_rows": len(rows),
           "notes": (notes + onotes + oinotes)[:12], "overlap": overlap(rows, names)}
    out["horizons"] = {str(h): measure(rows, h, names, nondir) for h in HORIZONS}
    pv = []
    for h, block in out["horizons"].items():
        for name, r in block["candidates"].items():
            for key in ("T1_p", "T2_p", "T5_p", "T4_p"):
                if r.get(key) is not None:
                    pv.append((f"{a.symbol}/{h}/{name}/{key}", r[key]))
    out["family"] = {"p_values": len(pv), "raw_below_0.05": sum(1 for _, p in pv if p < 0.05),
                     "expected_by_chance": round(0.05 * len(pv), 1),
                     "smallest": sorted(pv, key=lambda kv: kv[1])[:8]}
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
