#!/usr/bin/env python3
"""Performance of closed PAPER (or backtest) trades from the outcome journal.

    python3 scripts/show_performance.py [--mode paper] [--data-root var/data] [--days 30]

Every number printed is labelled with its mode. Paper results are
simulated fills on real market data -- not live results.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.analytics.performance import breakdown, trade_metrics  # noqa: E402
from cointrader.journal.records import trade_from_outcome  # noqa: E402
from cointrader.journal.store import LayeredStore  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402


def main() -> int:
    cfg = load_paper()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="paper", choices=("paper", "backtest", "live"))
    ap.add_argument("--data-root", type=Path, default=REPO / cfg["data_root"])
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).date()
    rows = [r for r in LayeredStore(args.data_root).read("outcome", start=since) if r["mode"] == args.mode]
    trades = [trade_from_outcome(r) for r in rows]
    tf = {r["trade_seq"]: r["timeframe"] for r in rows}
    label = {"paper": "PAPER (simulated fills on real market data; not live performance)",
             "backtest": "BACKTEST (simulated; not paper or live performance)", "live": "LIVE"}[args.mode]
    out = {"label": label, "days": args.days, "trades": len(trades)}
    if trades:
        out["overall"] = trade_metrics(trades, days=args.days)
        out["by_strategy_regime_symbol_timeframe"] = breakdown(trades, timeframe_of=lambda t: tf.get(t.trade_id, "?"))
        slips = [(r["expected_entry_slippage"], r["actual_entry_slippage"]) for r in rows
                 if r.get("expected_entry_slippage") is not None and r.get("actual_entry_slippage") is not None]
        if slips:
            out["entry_slippage_expected_vs_actual"] = {
                "expected_mean": sum(e for e, _ in slips) / len(slips),
                "actual_mean": sum(a for _, a in slips) / len(slips), "n": len(slips)}
    else:
        out["note"] = "no closed trades yet; nothing to evaluate"
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
