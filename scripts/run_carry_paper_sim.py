#!/usr/bin/env python3
"""Paper simulation of spot-long / perp-short funding carry on real Binance history (ADR-0067).

Read-only measurement: no hypothesis registration, no final exam. Refuses any window that overlaps
configs/locked_windows.json or configs/reserved_windows.json. Prints JSON for several leverages, with and
without the funding filter. Liquidation tiers are the ASSUMED ones in configs/margin_policy.json.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.carry.executor import CarryCosts, PaperCarryExecutor  # noqa: E402
from cointrader.carry.runner import CarryParams, CarryRunner  # noqa: E402
from cointrader.data.binance_vision import (  # noqa: E402
    BinanceVisionFundingRateHistory, BinanceVisionFuturesCandles, BinanceVisionSpotCandles)
from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.data.upbit_rest import UpbitRestCandles  # noqa: E402
from cointrader.carry.krw import after_tax_view  # noqa: E402
from cointrader.risk.leverage import MarginTier  # noqa: E402
from cointrader.settings import load_krw_accounting, load_margin_policy  # noqa: E402


def utc(day):
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def overlaps_locked(symbol, start, end):
    bad = []
    for fn in ("locked_windows.json", "reserved_windows.json"):
        p = REPO / "configs" / fn
        if not p.exists():
            continue
        for w in json.loads(p.read_text(encoding="utf-8")):
            if w["market"] in (symbol, "*") and utc(w["start"][:10]) < end and start < utc(w["end"][:10]):
                bad.append(f"{fn}:{w.get('name', 'reserved')}")
    return bad


def fx_summary(start, end):
    """KRW per USDT over the window from Upbit daily candles: start/end rate, change, worst dip and spike vs start.
    Unavailable (with the reason) when the market has no data within 14 days of the start."""
    c = [x for x in UpbitRestCandles().fetch("KRW-USDT", Timeframe.DAY_1, start, end)]
    if not c:
        from datetime import timedelta
        allc = UpbitRestCandles().fetch("KRW-USDT", Timeframe.DAY_1, utc("2019-01-01"), datetime.now(timezone.utc) - timedelta(days=1))
        return {"available": False, "reason": "no KRW-USDT candles in window",
                "first_candle_ever": str(allc[0].open_time.date()) if allc else None, "candles_ever": len(allc)}
    if (c[0].open_time - start).days > 14:
        return {"available": False, "reason": f"KRW-USDT data starts {c[0].open_time.date()}"}
    r0, r1 = c[0].open, c[-1].close
    return {"available": True, "first_day": str(c[0].open_time.date()), "start_rate": r0, "end_rate": r1,
            "change": r1 / r0 - 1, "worst_dip_pct": round(100 * (min(x.low for x in c) / r0 - 1), 2),
            "worst_spike_pct": round(100 * (max(x.high for x in c) / r0 - 1), 2), "days": len(c)}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--leverages", default="1.5,2,3,5")
    p.add_argument("--spot-fee", type=float, default=0.001)
    p.add_argument("--perp-fee", type=float, default=0.0005)
    p.add_argument("--slippage", type=float, default=0.0)
    p.add_argument("--mmr-scale", type=float, default=1.0, help="stress: multiply the assumed maintenance rates/amounts")
    p.add_argument("--krw-per-usdt", type=float, default=1400.0, help="held fixed; FX moves are not modelled")
    p.add_argument("--fx", action="store_true", help="add KRW/USDT change over the window (Upbit KRW-USDT daily candles)")
    p.add_argument("--krw-capitals", default="10000000,30000000,100000000")
    p.add_argument("--capital-cost-apr", type=float, default=0.04)
    a = p.parse_args(argv)
    start, end = utc(a.start), utc(a.end)
    bad = overlaps_locked(a.symbol, start, end)
    if bad:
        print(json.dumps({"refused": "window overlaps locked/reserved", "windows": bad}))
        return 2
    _, tiers, verified = load_margin_policy()
    tax, exit_costs, _ = load_krw_accounting()
    tiers = {k: [MarginTier(t.notional_floor, t.notional_cap, t.maintenance_margin_rate * a.mmr_scale,
                            t.maintenance_amount * a.mmr_scale) for t in v] for k, v in tiers.items()}
    capitals = [float(x) for x in a.krw_capitals.split(",")]
    funding = BinanceVisionFundingRateHistory().fetch(a.symbol, start, end)
    perp = BinanceVisionFuturesCandles().fetch(a.symbol, Timeframe.HOUR_1, start, end)
    spot = BinanceVisionSpotCandles().fetch(a.symbol, Timeframe.HOUR_1, start, end)
    costs = CarryCosts(a.spot_fee, a.perp_fee, a.slippage)
    fx = fx_summary(start, end) if a.fx else None
    rows = []
    for filt in (False, True):
        for lev in (float(x) for x in a.leverages.split(",")):
            params = CarryParams(leverage=lev, funding_filter=filt, capital_cost_apr=a.capital_cost_apr)
            runner = CarryRunner(PaperCarryExecutor(a.capital, costs), params, tiers[a.symbol])
            row = runner.run(spot, perp, funding)
            row["krw_after_tax"] = [after_tax_view(net_apr_pct=row["net_apr_pct"], capital_krw=c,
                                                   krw_per_usdt=a.krw_per_usdt, tax=tax, exit_costs=exit_costs)
                                    for c in capitals]
            if fx and fx.get("available"):
                row["krw_apr_with_fx_pct"] = round(100 * ((1 + row["net_return_pct"] / 100) * (1 + fx["change"]) - 1)
                                                   * 365 / row["days"], 2)
            rows.append(row)
    print("FX", json.dumps(fx))
    print(json.dumps({
        "symbol": a.symbol, "costs": asdict(costs), "mmr_scale": a.mmr_scale, "krw_per_usdt_fixed": a.krw_per_usdt, "fx": fx,
        "margin_tiers_verified": verified, "bars": {"spot": len(spot), "perp": len(perp)}, "funding_records": len(funding),
        "caveats": "hourly bars; funding uses the settlement's mark price; no FX moves, no borrow interest; capital cost is an assumption; "
                   "liquidation uses assumed tiers (scaled by mmr_scale) and the bar high; tax is the unverified 2027 estimate"}))
    for row in rows:
        print(json.dumps(row))
    for r in rows:
        print("ROW", r["leverage"], "filter" if r["funding_filter"] else "always", "usdt_apr", r["net_apr_pct"],
              "krw_apr_with_fx", r.get("krw_apr_with_fx_pct"), "liq", r["liquidations"], "fx", json.dumps(fx))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
