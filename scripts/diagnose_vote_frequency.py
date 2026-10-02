#!/usr/bin/env python3
"""How often would the indicator-vote candidates enter, and what blocks them?

Diagnostic only: registers nothing, locks nothing, reads NO returns or PnL --
it counts signal states bar by bar, so it cannot become a hidden parameter
search on outcomes. The range is checked against `configs/locked_windows.json`
and refused on any overlap (the SOLUSDT TEST-22 window is therefore never
loaded).

    python3 scripts/diagnose_vote_frequency.py --symbol SOLUSDT --start 2022-01-15 \
        --end 2024-06-21 --out reports/vote_frequency.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.backtest.engine import PrefixView  # noqa: E402
from cointrader.features import indicators as ind  # noqa: E402
from cointrader.features.side_indicators import volatility_ratio  # noqa: E402
from cointrader.strategies.indicator_vote import IndicatorVote  # noqa: E402

GRID_ENTER = (0.55, 0.58, 0.60, 0.65)
GRID_AGREE = (0.5, 0.6, 0.7)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _q(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)
    at = lambda f: round(v[min(len(v) - 1, int(f * (len(v) - 1) + 0.5))], 4)  # noqa: E731
    return {"n": len(v), "min": round(v[0], 4), "p05": at(0.05), "p25": at(0.25), "p50": at(0.5), "p75": at(0.75),
            "p95": at(0.95), "max": round(v[-1], 4)}


def diagnose(candles: list, strat: IndicatorVote, first_index: int) -> dict:
    reasons: dict[str, int] = {}
    rows = []  # (time, p_long, agree_long_frac, agree_short_frac, gated)
    per_ind: dict[str, list[float]] = {}
    for i in range(first_index, len(candles)):
        view = PrefixView(candles, i + 1)
        v = strat.verdict(view)
        if v is None:
            reasons["no_verdict(warmup_or_indicator_or_side_data_missing)"] = \
                reasons.get("no_verdict(warmup_or_indicator_or_side_data_missing)", 0) + 1
            continue
        atr = ind.atr(list(view[-(strat.atr_period * 4 + 1):]), strat.atr_period)
        ratio = volatility_ratio(view, short=strat.vol_short, long=strat.vol_long)
        if atr is None or atr <= 0 or ratio is None:
            reasons["atr_or_vol_unavailable"] = reasons.get("atr_or_vol_unavailable", 0) + 1
            continue
        total = max(1, len(v.per_indicator))
        gated = not (strat.vol_gate_lo <= ratio <= strat.vol_gate_hi)
        rows.append((candles[i].close_time, v.p_long, v.agree_long / total, v.agree_short / total, gated))
        for k, p in v.per_indicator.items():
            per_ind.setdefault(k, []).append(p)
        p_long_ok = v.p_long >= strat.enter_confidence
        p_short_ok = v.p_long <= 1 - strat.enter_confidence
        a_long_ok = v.agree_long / total >= strat.min_agree
        a_short_ok = v.agree_short / total >= strat.min_agree
        if (p_long_ok and a_long_ok) or (strat.allow_short and p_short_ok and a_short_ok):
            key = "vote_passes_but_vol_gate_blocks" if gated else "entry_signal"
        elif (p_long_ok or p_short_ok):
            key = "p_passes_but_agreement_too_low"
        elif a_long_ok or a_short_ok:
            key = "agreement_ok_but_p_below_enter_confidence"
        else:
            key = "neither_p_nor_agreement"
        reasons[key] = reasons.get(key, 0) + 1

    grid = {}
    for enter in GRID_ENTER:
        for agree in GRID_AGREE:
            def ok(r, e=enter, a=agree):
                _, p, al, as_, gated = r
                return not gated and ((p >= e and al >= a) or (strat.allow_short and p <= 1 - e and as_ >= a))
            hits = [ok(r) for r in rows]
            starts = sum(1 for j, h in enumerate(hits) if h and (j == 0 or not hits[j - 1]))
            grid[f"enter{enter:g}_agree{agree:g}"] = {"entry_bars": sum(hits), "entry_runs": starts}

    by_half: dict[str, int] = {}
    for r in rows:
        if r[1] >= strat.enter_confidence or r[1] <= 1 - strat.enter_confidence:
            key = f"{r[0].year}H{1 if r[0].month <= 6 else 2}"
            by_half[key] = by_half.get(key, 0) + 1
    return {
        "strategy_id": strat.strategy_id, "bars_evaluated": len(rows), "reason_counts": reasons,
        "p_long": _q([r[1] for r in rows]),
        "share_p_at_or_beyond_enter": round(sum(1 for r in rows if r[1] >= strat.enter_confidence
                                                or r[1] <= 1 - strat.enter_confidence) / max(1, len(rows)), 4),
        "bars_with_p_beyond_enter_by_half_year": by_half,
        "agree_long_fraction": _q([r[2] for r in rows]), "agree_short_fraction": _q([r[3] for r in rows]),
        "vol_gate_blocked_share": round(sum(1 for r in rows if r[4]) / max(1, len(rows)), 4),
        "per_indicator_p": {k: _q(v) for k, v in per_ind.items()},
        "threshold_grid_signal_counts_only": grid,
    }


def engine_view(candles: list, strat: IndicatorVote, windows: list, risk, futures) -> dict:
    """What the event engine actually did per walk-forward fold: counts and position size only -- returns and
    PnL are deliberately not read out."""
    from cointrader.backtest.event_engine import run_event_backtest  # noqa: E402
    tf_delta = candles[1].open_time - candles[0].open_time
    folds, rejected = [], {}
    for w in windows:
        warm_from = min(w.train_start, w.test_start - (strat.warmup + 1) * tf_delta)
        seg = [c for c in candles if warm_from <= c.open_time < w.test_end]
        r = run_event_backtest(seg, strat, risk, futures=futures, score_from=w.test_start)
        for k, n in r.rejected_entries.items():
            rejected[k] = rejected.get(k, 0) + n
        notional = [t.quantity * t.entry_fill / t.equity_at_entry for t in r.trades if t.equity_at_entry]
        folds.append({"test_start": w.test_start.date().isoformat(), "engine_signals": r.signals,
                      "trades": len(r.trades), "rejected_entries": dict(r.rejected_entries),
                      "entry_notional_over_equity": [round(x, 4) for x in notional]})
    all_notional = [x for f in folds for x in f["entry_notional_over_equity"]]
    return {"strategy_id": strat.strategy_id, "folds": len(folds),
            "folds_with_engine_signal": sum(1 for f in folds if f["engine_signals"]),
            "folds_with_trade": sum(1 for f in folds if f["trades"]), "total_trades": sum(f["trades"] for f in folds),
            "rejected_entries_total": rejected, "entry_notional_over_equity": _q(all_notional), "per_fold": folds}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True, help="first decision day (inclusive)")
    ap.add_argument("--end", required=True, help="end of range (exclusive); must not touch a locked window")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    from cointrader.data.binance_funding import FundingRateRecord  # noqa: E402
    from cointrader.data.models import Timeframe  # noqa: E402
    from cointrader.research.market_data import load_candles, load_funding, load_open_interest  # noqa: E402
    from cointrader.validation.locked_windows import assert_not_locked, load_locked_windows  # noqa: E402

    start, end, tf = _utc(args.start), _utc(args.end), Timeframe("1d")
    probe = IndicatorVote()
    warm_start = start - (probe.warmup + 1) * tf.delta
    assert_not_locked(load_locked_windows(REPO / "configs" / "locked_windows.json"), args.symbol, warm_start, end)

    candles, notes = load_candles(args.symbol, tf, warm_start, end)
    funding_map, n1 = load_funding(args.symbol, candles[0].open_time, end)
    oi, n2 = load_open_interest(args.symbol, candles[0].open_time, end)
    funding = [FundingRateRecord(args.symbol, t, r, float("nan"), "binance_vision_archive") for t, r in funding_map.items()]
    first = next(i for i, c in enumerate(candles) if c.open_time >= start)
    out = {"label": "DIAGNOSTIC (signal counts only; no returns, no PnL; not a validation result)",
           "symbol": args.symbol, "decision_range": [start.isoformat(), end.isoformat()],
           "source": "binance_vision_archive", "candles": len(candles),
           "data_notes": [n for n in notes if not n.startswith("open interest")][:20] + [
               f"funding archive gaps: {len(n1)}", f"open-interest archive gaps: {len(n2)} (days)",
               f"side data: {len(funding)} funding records, {len(oi)} open-interest days"],
           "candidates": []}
    from cointrader.research.market_data import load_futures_terms  # noqa: E402
    from cointrader.risk.engine import RiskEngine  # noqa: E402
    from cointrader.settings import load_markets, load_risk  # noqa: E402
    from cointrader.validation.policies import POLICIES  # noqa: E402
    from cointrader.validation.walk_forward import generate_walk_forward_windows  # noqa: E402
    pol = POLICIES["swing"]
    windows = generate_walk_forward_windows(start, end, train=pol.fold_train, test=pol.fold_test, step=pol.fold_test)
    futures, _ = load_futures_terms(args.symbol, start, end)
    filters, _, _ = load_markets()
    risk = RiskEngine(load_risk(), filters)
    out["engine_per_fold"] = []
    for h in (5, 10):
        for side in (False, True):
            s = IndicatorVote(horizon=h, use_side_data=side)
            if side:
                s = s.attach_side_data(funding=funding, open_interest=oi)
            out["candidates"].append(diagnose(candles, s, first))
            out["engine_per_fold"].append(engine_view(candles, s, windows, risk, futures))
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
