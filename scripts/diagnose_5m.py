#!/usr/bin/env python3
"""Measure what 5m bars offer against the round-trip cost before screening anything (ADR-0066).

Reads TRAIN+VALIDATION bars only (end must not reach the reserved TEST). No strategy, no registration, no final exam.
Sections: S size of moves vs cost and vs the 1-2% stop clamp; V indicator vote (unchanged constants) per-trade gross edge;
F taker-flow z; A spike fade; B vol regime; C time of day. Horizons are in 5m bars (12 = 1h, 48 = 4h, 144 = 12h).
Prints JSON. Round trip cost 0.12-0.20 % on perps.
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

import diagnose_edge_candidates as E  # noqa: E402
import diagnose_flow_signal as F  # noqa: E402

STEP = 300


def prepare(candles):
    n = len(candles)
    ok = [True] * n
    ret = [0.0] * n
    for i in range(1, n):
        if (candles[i].open_time - candles[i - 1].open_time).total_seconds() != STEP:
            ok[i] = False
        ret[i] = candles[i].close / candles[i - 1].close - 1
    return ret, ok


def move_size(candles, ok):
    out = {}
    n = len(candles)
    for h in (12, 48, 144):
        absf, rng = [], []
        for i in range(0, n - h, h):
            if not E.contiguous(ok, i, i + h):
                continue
            absf.append(abs(candles[i + h].close / candles[i].close - 1))
            hi = max(c.high for c in candles[i:i + h + 1])
            lo = min(c.low for c in candles[i:i + h + 1])
            rng.append((hi - lo) / candles[i].close)
        s = sorted(absf)
        r = sorted(rng)
        out[f"h{h}"] = {"n": len(s), "mean_abs_move_pct": round(100 * E.mean(s), 3), "median_abs_move_pct": round(100 * s[len(s) // 2], 3),
                        "share_abs_move_ge_0.20pct": round(sum(1 for x in s if x >= 0.002) / len(s), 3),
                        "share_abs_move_ge_1pct": round(sum(1 for x in s if x >= 0.01) / len(s), 3),
                        "median_high_low_range_pct": round(100 * r[len(r) // 2], 3),
                        "share_range_lt_1pct": round(sum(1 for x in r if x < 0.01) / len(r), 3)}
    br = [abs(c.close / c.open - 1) for c in candles]
    out["mean_abs_bar_move_pct"] = round(100 * E.mean(br), 4)
    out["mean_bar_range_pct"] = round(100 * E.mean([(c.high - c.low) / c.close for c in candles]), 4)
    return out


SCALED = dict(timeframe="5m", score_scale=0.058, vol_short=288, vol_long=2880, bars_per_day=288, quality_window_bars=576,
              fit_lookback=3000)


def spike_fade(candles, ret, ok, k=3.0, lookback=288):
    out = {}
    n = len(candles)
    for hold in (12, 48):
        res = []
        i = lookback + 1
        while i < n - hold:
            sg = E.sd(ret[i - lookback:i])
            if sg and abs(ret[i]) >= k * sg and E.contiguous(ok, i - lookback, i + hold):
                fwd = candles[i + hold].close / candles[i].close - 1
                res.append(-fwd if ret[i] > 0 else fwd)
                i += hold
            i += 1
        out[f"hold{hold}"] = E.stats(res)
    return out


def vote(candles, ok, horizons):
    from cointrader.backtest.engine import PrefixView
    from cointrader.strategies.daytrade import DayTradeVote
    out = {}
    n = len(candles)
    for name, extra in (("scaled", SCALED), ("bars_unchanged", {"timeframe": "5m"})):
      for h in horizons:
        strat = DayTradeVote(horizon=h, max_hold_bars=3 * 48, **extra)
        first = strat.warmup + 2
        tr, tr1, tr2, ups, ps = [], [], [], [], []
        half = (first + n) // 2
        for i in range(first, n - h, h):
            if not E.contiguous(ok, i, i + h):
                continue
            v = strat.verdict(PrefixView(candles, i + 1))
            if v is None:
                continue
            fwd = candles[i + h].close / candles[i].close - 1
            ps.append(v.p_long)
            ups.append(1 if fwd > 0 else 0)
            if v.p_long >= 0.6:
                x = fwd
            elif v.p_long <= 0.4:
                x = -fwd
            else:
                continue
            tr.append(x)
            (tr1 if i < half else tr2).append(x)
        base = sum(ups) / len(ups) if ups else None
        brier = E.mean([(p - y) ** 2 for p, y in zip(ps, ups)]) if ps else None
        brier0 = E.mean([(base - y) ** 2 for y in ups]) if ups else None
        out[f"{name}_h{h}"] = {"decisions": len(ps), "base_rate_up": base, "brier": brier, "brier_base_rate": brier0,
                        "enter_ge60_or_le40": E.stats(tr), "half1": E.stats(tr1), "half2": E.stats(tr2)}
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
    candles, notes = load_candles(a.symbol, Timeframe.MINUTE_5, s, e)
    fmap, fn = load_funding(a.symbol, s, e)
    ret, ok = prepare(candles)
    res = {"symbol": a.symbol, "start": a.start, "end": a.end, "bars": len(candles), "gaps": ok.count(False),
           "notes": notes[:10] + fn[:5], "S_move_size": move_size(candles, ok)}
    res["V_vote"] = vote(candles, ok, (12, 48, 144))
    if all(c.taker_buy_volume is not None for c in candles):
        res["F_taker_flow"] = [F.analyse(candles, w, 2880, sorted(fmap.items())) for w in (12, 48)]
    res["A_spike_fade"] = spike_fade(candles, ret, ok)
    res["B_vol_regime"] = E.vol_regime(candles, ret, ok, rv_win=288, rank_win=2880, h=48)
    res["C_time_of_day"] = E.time_of_day(candles, ok, hold=12)
    text = json.dumps(res, ensure_ascii=False, indent=2, default=str)
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
