"""Assembles the KRW report from the outcome journal, a flows file and the rate CSV.
Shared by `scripts/show_krw_pnl.py` and the paper runner so both print the same numbers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cointrader.accounting.krw_ledger import KrwPnlReport, build_report, event_from_dict, trade_events_from_outcomes
from cointrader.accounting.krw_rates import load_series
from cointrader.journal.store import LayeredStore
from cointrader.settings import load_krw_accounting


def build_krw_report(*, mode: str, flows_path: Path, rates_path: Path, data_root: Path, days: int = 365,
                     as_of: Optional[datetime] = None):
    """-> (report, tax_config). Raises ValueError with the reason when rates or flows are missing."""
    tax, exit_costs, staleness = load_krw_accounting()
    rates = load_series(rates_path, staleness)
    if not flows_path.exists():
        raise ValueError(f"no flows file at {flows_path}: record the KRW -> USDT purchase first")
    flows = [event_from_dict(json.loads(line)) for line in flows_path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    rows = [r for r in LayeredStore(data_root).read("outcome", start=since) if r["mode"] == mode]
    events = flows + trade_events_from_outcomes(rows, rates)
    when = as_of or max([e.at for e in events] + [t for t, _ in rates.points[-1:]])
    try:
        mark, src = rates.at(when), rates.source
    except ValueError as exc:
        mark, src = None, f"unavailable: {exc}"
    return build_report(events, as_of=when, mark_rate=mark, mark_rate_source=src, tax=tax, exit_costs=exit_costs), tax


def one_line(rep: KrwPnlReport) -> str:
    def w(x):
        return "알 수 없음" if x is None else f"{x:+,.0f}원"
    return (f"[{rep.mode}] 원화 기준: 실현 {w(rep.realized_net_krw)}, 환차손익(미실현) {w(rep.fx_unrealized)}, "
            f"세후 회수 기준 {w(rep.net_if_cashed_out_krw)} (세금은 추정치)")
