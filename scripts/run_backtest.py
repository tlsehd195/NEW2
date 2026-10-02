#!/usr/bin/env python3
"""Event-driven BACKTEST of one registered strategy on Binance USD-M
history (data.binance.vision archive; works from GitHub Actions).

    python3 scripts/run_backtest.py --strategy swing_trend_ema_atr_20_50_v1 \
        --symbol BTCUSDT --start 2022-01-01 --end 2023-01-01 [--out reports/bt.json]

A backtest is exploration, not validation: it prints no pass/fail and
never changes a strategy's status. The range may not touch a locked TEST
window (CLAUDE.md rule 1). Output is labelled BACKTEST.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.analytics.performance import breakdown, result_summary  # noqa: E402
from cointrader.backtest.event_engine import ExecutionCosts, run_event_backtest  # noqa: E402
from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.research.market_data import load_candles, load_futures_terms  # noqa: E402
from cointrader.risk.engine import RiskEngine  # noqa: E402
from cointrader.settings import load_markets, load_risk  # noqa: E402
from cointrader.strategies.registry import StrategyRegistry  # noqa: E402
from cointrader.validation.locked_windows import assert_not_locked, load_locked_windows  # noqa: E402


def _utc(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--timeframe", help="defaults to the strategy's registered timeframe")
    ap.add_argument("--entry-order", default="market", choices=("market", "limit"))
    ap.add_argument("--no-funding", action="store_true", help="explicitly assume zero funding (reported)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    registry = StrategyRegistry.load()
    spec = registry.get(args.strategy)
    strategy = registry.build(args.strategy, market=args.symbol)
    tf = Timeframe(args.timeframe or spec.timeframes[0])
    start, end = _utc(args.start), _utc(args.end)
    assert_not_locked(load_locked_windows(), args.symbol, start, end)
    filters, _, verified = load_markets()
    candles, notes = load_candles(args.symbol, tf, start, end)
    if not candles:
        print(json.dumps({"error": "no candles returned; nothing to test", "data_notes": notes}, indent=2))
        return 2
    if args.no_funding:
        from cointrader.backtest.event_engine import FuturesTerms
        futures, fnotes = FuturesTerms(assume_no_funding=True), ["funding assumed zero (--no-funding)"]
    else:
        futures, fnotes = load_futures_terms(args.symbol, start, end)
    result = run_event_backtest(candles, strategy, RiskEngine(load_risk(), filters),
                                costs=ExecutionCosts(entry_order=args.entry_order), futures=futures)
    summary = result_summary(result, 365 * 86400 / tf.delta.total_seconds())
    out = {"summary": summary, "breakdown": breakdown(result.trades, timeframe_of=lambda t: tf.value),
           "data_notes": notes + fnotes, "market_rules_verified": verified, "candles": len(candles),
           "range": [args.start, args.end]}
    text = json.dumps(out, ensure_ascii=False, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
