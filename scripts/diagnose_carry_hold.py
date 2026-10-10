#!/usr/bin/env python3
"""Measure always-on spot-long / perp-short carry (hold, no flipping) net of cost (read-only diagnostic).

No strategy, no registration, no final exam. H-0012 flipped on a funding-sign grid and lost mostly to cost/basis;
this holds ONE position for the whole window and splits the result into funding income, basis PnL and entry/exit cost.
Uses only windows that are not in configs/locked_windows.json or configs/reserved_windows.json (caller's job; checked here).
Funding is credited per settlement on the entry notional (small drift ignored, flagged in output).
Prints JSON.
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

from cointrader.data.binance_vision import (  # noqa: E402
    BinanceVisionFundingRateHistory, BinanceVisionFuturesCandles, BinanceVisionSpotCandles)
from cointrader.data.models import Timeframe  # noqa: E402


def utc(day):
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def overlaps_locked(symbol, start, end):
    bad = []
    for fn in ("locked_windows.json", "reserved_windows.json"):
        p = REPO / "configs" / fn
        if not p.exists():
            continue
        for w in json.loads(p.read_text(encoding="utf-8")):
            if w["market"] == symbol and utc(w["start"][:10]) < end and start < utc(w["end"][:10]):
                bad.append(f"{fn}:{w.get('name', 'reserved')}")
    return bad


def carry_hold(spot, fut, funding, spot_fee, fut_fee, margin_frac):
    """spot, fut: candles sorted by open_time. funding: records. Returns dict of pct-of-notional results."""
    s = {c.open_time: c.close for c in spot}
    f = {c.open_time: c.close for c in fut}
    times = sorted(set(s) & set(f))
    if len(times) < 100:
        return {"error": "too few joined bars", "bars": len(times)}
    t0, t1 = times[0], times[-1]
    s0, f0 = s[t0], f[t0]
    fund = [r for r in funding if t0 <= r.funding_time <= t1]
    days = (t1 - t0).total_seconds() / 86400
    funding_total = sum(r.funding_rate for r in fund)
    # basis pnl on 1 coin long spot, 1 coin short perp, normalised by entry spot price
    basis = [((s[t] - s0) - (f[t] - f0)) / s0 for t in times]
    basis_end = basis[-1]
    cost = 2 * (spot_fee + fut_fee)  # entry + exit, both legs
    cum, peak, mdd = 0.0, 0.0, 0.0
    fi = 0
    fund_sorted = sorted(fund, key=lambda r: r.funding_time)
    for t, b in zip(times, basis):
        while fi < len(fund_sorted) and fund_sorted[fi].funding_time <= t:
            cum += fund_sorted[fi].funding_rate
            fi += 1
        v = cum + b
        peak = max(peak, v)
        mdd = max(mdd, peak - v)
    gross = funding_total + basis_end
    net = gross - cost
    cap = 1.0 + margin_frac
    neg = sum(1 for r in fund if r.funding_rate < 0)
    # rolling 30d funding sums
    per = 3 * 30
    roll = [sum(r.funding_rate for r in fund_sorted[i:i + per]) for i in range(0, max(0, len(fund_sorted) - per), per)]
    return {
        "start": t0.isoformat(), "end": t1.isoformat(), "days": round(days, 1), "settlements": len(fund),
        "negative_settlements_share": round(neg / len(fund), 3) if fund else None,
        "funding_total_pct": round(100 * funding_total, 3),
        "funding_apr_pct": round(100 * funding_total * 365 / days, 2),
        "basis_end_pct": round(100 * basis_end, 3),
        "basis_hourly_sd_pct": round(100 * math.sqrt(sum((b - sum(basis) / len(basis)) ** 2 for b in basis) / len(basis)), 3),
        "cost_round_trip_pct": round(100 * cost, 3),
        "gross_pct": round(100 * gross, 3), "net_pct": round(100 * net, 3),
        "net_apr_on_notional_pct": round(100 * net * 365 / days, 2),
        "net_apr_on_capital_pct": round(100 * net * 365 / days / cap, 2),
        "capital_per_notional": cap,
        "max_drawdown_pct_of_notional": round(100 * mdd, 3),
        "worst_30d_funding_pct": round(100 * min(roll), 3) if roll else None,
        "best_30d_funding_pct": round(100 * max(roll), 3) if roll else None,
        "caveats": "funding on entry notional; hourly closes; no borrow/interest on spot capital; no liquidation model",
    }


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--spot-fee", type=float, default=0.001)
    p.add_argument("--fut-fee", type=float, default=0.0005)
    p.add_argument("--fut-margin", type=float, default=0.5, help="perp margin as share of notional (0.5 = 2x)")
    a = p.parse_args(argv)
    start, end = utc(a.start), utc(a.end)
    bad = overlaps_locked(a.symbol, start, end)
    if bad:
        print(json.dumps({"refused": "window overlaps locked/reserved", "windows": bad}))
        return 2
    fc = BinanceVisionFundingRateHistory()
    funding = fc.fetch(a.symbol, start, end)
    fu = BinanceVisionFuturesCandles().fetch(a.symbol, Timeframe.HOUR_1, start, end)
    sp = BinanceVisionSpotCandles().fetch(a.symbol, Timeframe.HOUR_1, start, end)
    out = {"symbol": a.symbol, "fees": {"spot": a.spot_fee, "perp": a.fut_fee}, "funding_gaps": len(fc.last_gaps),
           "result": carry_hold(sp, fu, funding, a.spot_fee, a.fut_fee, a.fut_margin)}
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
