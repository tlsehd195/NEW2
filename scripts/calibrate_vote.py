#!/usr/bin/env python3
"""Calibration of the indicator-vote P(long) on a non-locked range (ADR-0035).

MEASUREMENT ONLY: changes no threshold, registers no hypothesis, spends no
budget, touches no locked window. It reads the direction (up/down) of price
`horizon` bars after each decision, so treat what it prints as an exploratory
read of outcomes: do not tune constants to it. Any change it suggests needs a
new preregistered hypothesis id.

    python3 scripts/calibrate_vote.py --kind daytrade --symbol BTCUSDT \
        --start 2023-04-20 --end 2024-06-18 --out reports/vote_calibration.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.strategies.daytrade import DayTradeVote  # noqa: E402
from cointrader.strategies.indicator_vote import IndicatorVote  # noqa: E402
from cointrader.validation.vote_calibration import collect_forecasts, summarize  # noqa: E402

KINDS = {"swing": (IndicatorVote, "1d", (5, 10)), "daytrade": (DayTradeVote, "15m", (16, 48))}


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def calibrate(candles: list, strat, first_index: int, *, stride=None) -> dict:
    def p_long(view):
        v = strat.verdict(view)
        return None if v is None else v.p_long

    f = collect_forecasts(candles, p_long, strat.horizon, first_index, stride=stride)
    out = summarize(f, horizon=strat.horizon, stride=stride or strat.horizon, enter_confidence=strat.enter_confidence)
    out["strategy_id"] = strat.strategy_id
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", choices=sorted(KINDS), default="daytrade")
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True, help="exclusive; must not touch a locked window")
    ap.add_argument("--stride", type=int, help="bars between decisions (default: horizon, non-overlapping labels)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    from cointrader.data.binance_funding import FundingRateRecord  # noqa: E402
    from cointrader.data.models import Timeframe  # noqa: E402
    from cointrader.research.market_data import load_candles, load_funding, load_open_interest  # noqa: E402
    from cointrader.validation.locked_windows import assert_not_locked, load_locked_windows  # noqa: E402

    cls, tf_value, horizons = KINDS[args.kind]
    start, end, tf = _utc(args.start), _utc(args.end), Timeframe(tf_value)
    probe = cls()
    warm_start = start - (probe.warmup + 1) * tf.delta
    assert_not_locked(load_locked_windows(REPO / "configs" / "locked_windows.json"), args.symbol, warm_start, end)
    candles, notes = load_candles(args.symbol, tf, warm_start, end)
    funding_map, _ = load_funding(args.symbol, candles[0].open_time, end)
    oi, _ = load_open_interest(args.symbol, candles[0].open_time, end)
    funding = [FundingRateRecord(args.symbol, t, r, float("nan"), "binance_vision_archive") for t, r in funding_map.items()]
    first = next(i for i, c in enumerate(candles) if c.open_time >= start)
    out = {"label": "CALIBRATION MEASUREMENT (exploratory read of up/down outcomes; not a validation result; "
                    "do not tune constants to it)",
           "kind": args.kind, "timeframe": tf_value, "symbol": args.symbol,
           "decision_range": [start.isoformat(), end.isoformat()], "source": "binance_vision_archive",
           "candles": len(candles), "data_notes": notes[:20], "candidates": []}
    for h in horizons:
        for side in (False, True):
            s = cls(horizon=h, use_side_data=side)
            if side:
                s = s.attach_side_data(funding=funding, open_interest=oi)
            out["candidates"].append(calibrate(candles, s, first, stride=args.stride))
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
