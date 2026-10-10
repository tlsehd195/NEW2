#!/usr/bin/env python3
"""Measure four non-directional / different-edge candidates on 15m bars before screening any of them (ADR-0065).

A. vol_spike_fade   : after a bar whose |return| >= 3 sigma (sigma = std of the last 96 bar returns), fade it; hold 4 or 16 bars.
B. vol_regime       : does realised-vol rank predict later |return| (known), and does a momentum/reversion edge differ by vol tercile?
C. time_of_day      : mean forward 1h return by UTC hour, rule picked on the first half, scored on the second half.
D. funding_window   : mean return around the 00/08/16 UTC funding settlements, and the plain hedged-carry yield vs. round-trip cost.

Reads TRAIN+VALIDATION bars only (end must not reach the reserved TEST). Every number is printed per half so a stable effect can be told
from a lucky one. No strategy, no registration. Prints JSON; costs: round trip 0.12-0.20 % on perps.
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

COST_LO, COST_HI = 0.12, 0.20  # percent, round trip


def mean(x):
    return math.fsum(x) / len(x) if x else None


def sd(x):
    if len(x) < 3:
        return None
    m = mean(x)
    return math.sqrt(math.fsum((a - m) ** 2 for a in x) / (len(x) - 1))


def stats(trades):
    """trades: per-trade returns in fractions (already signed). Returns n, mean %, t, hit, net of cost range."""
    n = len(trades)
    if n < 3:
        return {"n": n}
    m, s = mean(trades), sd(trades)
    return {"n": n, "mean_pct": round(100 * m, 4), "t": round(m / (s / math.sqrt(n)), 2) if s else None,
            "hit": round(sum(1 for x in trades if x > 0) / n, 3),
            "net_pct_cost_lo": round(100 * m - COST_LO, 4), "net_pct_cost_hi": round(100 * m - COST_HI, 4)}


def corr(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = mean(x), mean(y)
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx, syy = math.fsum((a - mx) ** 2 for a in x), math.fsum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def prepare(candles):
    """Close list, 1-bar log-free returns, and gap flags (a gap bar = missing time, rows around it are skipped)."""
    n = len(candles)
    ok = [True] * n
    for i in range(1, n):
        if (candles[i].open_time - candles[i - 1].open_time).total_seconds() != 900:
            ok[i] = False
    ret = [0.0] * n
    for i in range(1, n):
        ret[i] = candles[i].close / candles[i - 1].close - 1
    return ret, ok


def contiguous(ok, a, b):
    """True if bars a..b (inclusive) have no gap."""
    return all(ok[a + 1:b + 1])


def vol_spike_fade(candles, ret, ok, k=3.0, lookback=96):
    out = {}
    n = len(candles)
    half = n // 2
    for hold in (4, 16):
        res = {"fade_all": [], "fade_h1": [], "fade_h2": [], "cont_all": []}
        i = lookback + 1
        while i < n - hold:
            win = ret[i - lookback:i]
            s = sd(win)
            if s and abs(ret[i]) >= k * s and contiguous(ok, i - lookback, i + hold):
                fwd = candles[i + hold].close / candles[i].close - 1
                fade = -fwd if ret[i] > 0 else fwd
                res["fade_all"].append(fade)
                res["fade_h1" if i < half else "fade_h2"].append(fade)
                res["cont_all"].append(-fade)
                i += hold  # no overlapping trades
            i += 1
        out[f"hold{hold}"] = {k_: stats(v) for k_, v in res.items()}
    return out


def vol_regime(candles, ret, ok, rv_win=96, rank_win=960, h=16):
    n = len(candles)
    rows = []
    for t in range(rank_win + rv_win, n - h, h):
        if not contiguous(ok, t - rank_win - rv_win, t + h):
            continue
        rvs = []
        for j in range(t - rank_win + 1, t + 1, 8):  # rank over a thinned history, cheap
            rvs.append(sd(ret[j - rv_win + 1:j + 1]))
        cur = sd(ret[t - rv_win + 1:t + 1])
        rank = sum(1 for r in rvs if r < cur) / len(rvs)
        fwd = candles[t + h].close / candles[t].close - 1
        past = candles[t].close / candles[t - h].close - 1
        rows.append((rank, fwd, past, candles[t].open_time))
    out = {"samples": len(rows), "h": h,
           "corr_rvrank_absfwd": corr([r[0] for r in rows], [abs(r[1]) for r in rows])}
    for name, lo, hi in (("low", 0, 1 / 3), ("mid", 1 / 3, 2 / 3), ("high", 2 / 3, 1.01)):
        sel = [r for r in rows if lo <= r[0] < hi]
        out[name] = {"n": len(sel), "mean_abs_fwd_pct": round(100 * mean([abs(r[1]) for r in sel]), 3) if sel else None,
                     "momentum_edge": stats([r[1] if r[2] > 0 else -r[1] for r in sel]),
                     "reversion_edge": stats([-r[1] if r[2] > 0 else r[1] for r in sel])}
    return out


def time_of_day(candles, ok, hold=4):
    n = len(candles)
    half = n // 2
    by = {0: {h: [] for h in range(24)}, 1: {h: [] for h in range(24)}}
    for i in range(0, n - hold, hold):
        if not contiguous(ok, i, i + hold):
            continue
        if candles[i].open_time.minute != 0:
            continue
        fwd = candles[i + hold].close / candles[i].close - 1
        by[0 if i < half else 1][candles[i].open_time.hour].append(fwd)
    m1 = [mean(by[0][h]) for h in range(24)]
    m2 = [mean(by[1][h]) for h in range(24)]
    pairs = [(a, b) for a, b in zip(m1, m2) if a is not None and b is not None]
    out = {"corr_half1_half2_hourly_mean": corr([p[0] for p in pairs], [p[1] for p in pairs]),
           "mean_pct_half1": [round(100 * x, 3) if x is not None else None for x in m1],
           "mean_pct_half2": [round(100 * x, 3) if x is not None else None for x in m2]}
    # Rule fixed on half 1: long the 3 best hours, short the 3 worst hours; scored on half 2 only.
    order = sorted(range(24), key=lambda h: m1[h] if m1[h] is not None else 0)
    shorts, longs = order[:3], order[-3:]
    tr = [x for h in longs for x in by[1][h]] + [-x for h in shorts for x in by[1][h]]
    out["rule"] = {"long_hours": longs, "short_hours": shorts, "half2": stats(tr)}
    # Not direction: which hours move most (known to be stable; useful for a volatility gate).
    absr = {h: [] for h in range(24)}
    for i in range(0, n - hold, hold):
        if contiguous(ok, i, i + hold) and candles[i].open_time.minute == 0:
            absr[candles[i].open_time.hour].append(abs(candles[i + hold].close / candles[i].close - 1))
    out["mean_abs_1h_move_pct_by_hour"] = [round(100 * mean(absr[h]), 3) if absr[h] else None for h in range(24)]
    return out


def funding_window(candles, ok, funding):
    n = len(candles)
    half = n // 2
    idx = {c.open_time: i for i, c in enumerate(candles)}
    res = {"pre_8bars": {0: [], 1: []}, "post_8bars": {0: [], 1: []}}
    for i, c in enumerate(candles):
        if c.open_time.minute == 0 and c.open_time.hour in (0, 8, 16) and 8 <= i < n - 8 and contiguous(ok, i - 8, i + 8):
            h = 0 if i < half else 1
            res["pre_8bars"][h].append(candles[i].close / candles[i - 8].close - 1)
            res["post_8bars"][h].append(candles[i + 8].close / candles[i].close - 1)
    out = {k: {"half1": stats(v[0]), "half2": stats(v[1])} for k, v in res.items()}
    rates = [r for _, r in sorted(funding.items())]
    if rates:
        per_year = 3 * 365
        out["carry"] = {"settlements": len(rates), "mean_rate_pct_per_8h": round(100 * mean(rates), 5),
                        "share_positive": round(sum(1 for r in rates if r > 0) / len(rates), 3),
                        "gross_yield_pct_per_year_if_always_short_perp_collecting": round(100 * mean(rates) * per_year, 2),
                        "sd_rate_pct": round(100 * sd(rates), 5),
                        "note": "hedged spot+perp needs two legs: round trip ~2x cost; holds days, not hours. Compare ADR-0013."}
    return out


def main():
    from cointrader.data.models import Timeframe
    from cointrader.research.market_data import load_candles, load_funding
    from cointrader.validation.screening import load_reserved, reserved_for
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
    fmap, fn = load_funding(a.symbol, s, e)
    ret, ok = prepare(candles)
    res = {"symbol": a.symbol, "start": a.start, "end": a.end, "bars": len(candles), "notes": notes[:10] + fn[:5],
           "A_vol_spike_fade": vol_spike_fade(candles, ret, ok),
           "B_vol_regime": vol_regime(candles, ret, ok),
           "C_time_of_day": time_of_day(candles, ok),
           "D_funding_window": funding_window(candles, ok, fmap)}
    text = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
