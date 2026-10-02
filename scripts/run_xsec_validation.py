#!/usr/bin/env python3
"""Pre-registered walk-forward validation of cross-sectional (coin-versus-
coin) portfolio candidates, then the one-time TEST, then the TEST range
is locked for the basket and for every coin in it (ADR-0017).

    python3 scripts/run_xsec_validation.py --hypothesis-id H-0017 --universe USDTM-2020 \
        --start 2020-02-01 --end 2023-04-06 --statement "..." --rationale "..." \
        --registered-by 동동 --out reports/xsec.json

Order enforced (CLAUDE.md rule 1): every coin's range checked against the
locks -> hypothesis checks and pre-registration (universe inside the
statement) -> walk-forward -> PBO/DSR -> TEST once -> LOCK -> automatic
lifecycle steps (stop at OOS_TESTED). Commit research/*.jsonl and
configs/locked_windows.json afterwards.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.research.hypotheses import register_checked  # noqa: E402
from cointrader.research.lifecycle import CandidateLedger  # noqa: E402
from cointrader.research.market_data import load_candles, load_funding  # noqa: E402
from cointrader.settings import load_risk  # noqa: E402
from cointrader.strategies.cross_sectional import XSEC_FACTORIES  # noqa: E402
from cointrader.validation.locked_windows import load_locked_windows  # noqa: E402
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog  # noqa: E402
from cointrader.validation.signal_study import criteria_from  # noqa: E402
from cointrader.validation.xsec_study import (  # noqa: E402
    XSEC_POLICY,
    assert_basket_not_locked,
    lock_range,
    run_xsec_study,
    universe_statement,
)


def _utc(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def _safe(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def main(loader=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hypothesis-id", required=True)
    ap.add_argument("--universe", required=True, help="key in configs/xsec_universes.json")
    ap.add_argument("--strategies", nargs="+", required=True, help="registered xsec candidate ids")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--statement", required=True)
    ap.add_argument("--rationale", required=True)
    ap.add_argument("--registered-by", required=True)
    ap.add_argument("--universes", type=Path, default=REPO / "configs" / "xsec_universes.json")
    ap.add_argument("--log", type=Path, default=REPO / "research" / "preregistration.jsonl")
    ap.add_argument("--locked-path", type=Path, default=REPO / "configs" / "locked_windows.json")
    ap.add_argument("--ledger", type=Path, default=REPO / "research" / "candidate_status.jsonl")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    universe = tuple(json.loads(args.universes.read_text(encoding="utf-8"))[args.universe]["symbols"])
    basket = f"XS:{args.universe}"
    ids = list(args.strategies)
    candidates = [XSEC_FACTORIES[i] for i in ids]
    tf = Timeframe(args.timeframe)
    start, end = _utc(args.start), _utc(args.end)
    warmup_start = start - (max(c.warmup for c in candidates) + 1) * tf.delta
    locked = load_locked_windows(args.locked_path)
    # Before anything is registered or loaded: no coin's range may touch a locked window.
    assert_basket_not_locked(locked, basket, universe, warmup_start, end)

    now = datetime.now(timezone.utc)
    hypothesis = Hypothesis(
        hypothesis_id=args.hypothesis_id, statement=universe_statement(args.statement, universe), market=basket,
        timeframe=args.timeframe, data_start=start, data_end=end, candidates=tuple(ids),
        success_criteria=dict(XSEC_POLICY.success_criteria), registered_by=args.registered_by, registered_at=now,
    )
    log = PreregistrationLog(args.log)
    register_checked(log, args.log, hypothesis, locked, rationale=args.rationale)

    load_c, load_f = loader or (load_candles, load_funding)
    candles, funding, notes = {}, {}, []
    for sym in universe:
        bars, n1 = load_c(sym, tf, warmup_start, end)
        rates, n2 = load_f(sym, warmup_start, end)
        candles[sym], funding[sym] = bars, rates
        notes += [f"{sym}: {len(bars)} bars" + (f", first {bars[0].open_time.date()}" if bars else "")]
        notes += [f"{sym}: {n}" for n in (n1 + n2)[:20]]
    note = f"{args.hypothesis_id} xsec {basket} {args.timeframe}; TEST used once by run_xsec_validation.py at {now.isoformat()}"
    windows: list = []

    def lock_before_test(test_start, test_end):
        windows.extend(lock_range(args.hypothesis_id, basket, universe, test_start, test_end, tuple(ids), note,
                                  args.locked_path))

    report = run_xsec_study(hypothesis, log, candles, candidates, locked, risk=load_risk(), funding=funding,
                            before_test=lock_before_test)
    ledger = CandidateLedger(args.ledger)
    criteria = criteria_from(hypothesis, XSEC_POLICY)
    transitions = {}
    for c in report.candidates:
        written = ledger.advance(c.strategy_id, report.evidence_for(c.strategy_id), criteria, now,
                                 hypothesis_id=args.hypothesis_id)
        transitions[c.strategy_id] = [f"{t.from_status.value}->{t.to_status.value}: {t.reason}" for t in written]
    out = {
        "label": report.label, "hypothesis_id": report.hypothesis_id, "basket": report.basket,
        "universe": list(report.universe), "timeframe": report.timeframe, "fold_count": report.fold_count,
        "pbo": _safe(report.pbo), "trials_deflated_against": report.trials_deflated_against,
        "train_start": report.train_start.isoformat(), "validation_end": report.validation_end.isoformat(),
        "test_window_locked": {"name": windows[0].name, "start": windows[0].start.isoformat(),
                               "end": windows[0].end.isoformat(), "coins_locked": len(windows) - 1},
        "test_coins_with_data": list(report.test_coins_with_data),
        "candidates": [{k: _safe(v) for k, v in dataclasses.asdict(c).items() if k != "test_summary"}
                       | {"test_excess_return": c.test_excess_return, "test_summary": c.test_summary}
                       for c in report.candidates],
        "lifecycle_transitions": transitions, "data_notes": notes,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2, default=_safe)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
