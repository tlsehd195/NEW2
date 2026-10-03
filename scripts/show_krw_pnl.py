#!/usr/bin/env python3
"""KRW net PnL after every leg of KRW -> USDT -> Binance -> KRW (ADR-0036).

    python3 scripts/show_krw_pnl.py --mode paper \
        --flows var/paper/krw_flows.jsonl --rates var/data/krw_usdt.csv \
        [--rate-source upbit_KRW-USDT] [--data-root var/data] [--days 365] [--as-of 2026-10-03T00:00:00+00:00] [--json]

--flows   JSONL of money movements (one per line, `type` = usdt_purchase |
          usdt_sale | usdt_transfer | krw_fee). Paper: write the simulated
          initial purchase there with mode "paper". Live: the bridge's
          receipts and the exchanges' own statements.
--rates   CSV `time,rate` (ISO UTC time, KRW per USDT), e.g. Upbit KRW-USDT
          closes. Every closed trade is valued at the rate at its exit.

Closed trades come from the outcome journal (same rows as
show_performance.py), so paper and live run the same code. Read-only:
this never moves money or places orders.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.accounting.krw_ledger import (  # noqa: E402
    KrwRateSeries, build_report, event_from_dict, trade_events_from_outcomes,
)
from cointrader.journal.store import LayeredStore  # noqa: E402
from cointrader.settings import load_krw_accounting, load_paper  # noqa: E402

LABELS = {
    "trading_gross": "선물 매매손익(총)", "trading_fees": "선물 거래 수수료", "spread_slippage": "스프레드·슬리피지",
    "funding": "펀딩비", "domestic_fees": "국내 거래소 수수료", "network_fees": "출금·네트워크 수수료",
    "krw_fees": "원화 수수료", "fx_realized": "환차손익(실현)",
}


def load_rates(path: Path, source: str, staleness: timedelta) -> KrwRateSeries:
    with path.open(encoding="utf-8") as f:
        pts = tuple((datetime.fromisoformat(r["time"]), float(r["rate"])) for r in csv.DictReader(f))
    return KrwRateSeries(pts, source, staleness)


def won(x) -> str:
    return "알 수 없음" if x is None else f"{(x or 0.0):+,.0f}원"


def main() -> int:
    paper = load_paper()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="paper", choices=("paper", "backtest", "live"))
    ap.add_argument("--flows", type=Path, required=True)
    ap.add_argument("--rates", type=Path, required=True)
    ap.add_argument("--rate-source", default="upbit_KRW-USDT")
    ap.add_argument("--data-root", type=Path, default=REPO / paper["data_root"])
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--as-of", type=datetime.fromisoformat, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    tax, exit_costs, staleness = load_krw_accounting()
    rates = load_rates(args.rates, args.rate_source, staleness)
    flows = [event_from_dict(json.loads(line)) for line in args.flows.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).date()
    rows = [r for r in LayeredStore(args.data_root).read("outcome", start=since) if r["mode"] == args.mode]
    events = flows + trade_events_from_outcomes(rows, rates)
    as_of = args.as_of or max([e.at for e in events] + [t for t, _ in rates.points[-1:]])
    try:
        mark, mark_src = rates.at(as_of), rates.source
    except ValueError as exc:
        mark, mark_src = None, f"unavailable: {exc}"
    rep = build_report(events, as_of=as_of, mark_rate=mark, mark_rate_source=mark_src, tax=tax, exit_costs=exit_costs)

    if args.json:
        print(json.dumps({**rep.__dict__, "as_of": rep.as_of.isoformat(), "years": [y.__dict__ for y in rep.years],
                          "estimated_tax_krw": rep.estimated_tax_krw, "net_before_tax_krw": rep.net_before_tax_krw,
                          "net_if_cashed_out_krw": rep.net_if_cashed_out_krw}, ensure_ascii=False, indent=2))
        return 0
    tag = {"paper": "모의(PAPER, 실거래 아님)", "backtest": "백테스트(모의)", "live": "실거래(LIVE)"}[rep.mode]
    print(f"[{tag}] 기준 {rep.as_of.isoformat()}  청산 거래 {rep.trade_count}건")
    print(f"넣은 원화 {rep.krw_paid:,.0f}원 / 돌려받은 원화 {rep.krw_received:,.0f}원 / 보유 USDT {rep.usdt_held:,.4f}")
    for k, v in rep.components.items():
        print(f"  {LABELS[k]:<14} {won(v)}")
    print(f"  {'환차손익(미실현)':<14} {won(rep.fx_unrealized)}  (환율 {rep.mark_rate} {rep.mark_rate_source})")
    print(f"실현 순손익 {won(rep.realized_net_krw)} / 세전 합계 {won(rep.net_before_tax_krw)}")
    print(f"지금 원화로 회수 시 비용 {won(None if rep.exit_cost_krw is None else -rep.exit_cost_krw)} ({rep.exit_cost_reason})")
    flag = "" if rep.tax_verified else " [추정치, 세무사 확인 필요]"
    for y in rep.years:
        state = "과세" if y.tax_in_force else "시행 전"
        print(f"  {y.year}년 실현 {won(y.realized_net_krw)} → 세금 추정 {won(-y.estimated_tax_krw)} ({state}){flag}")
    print(f"세후 순수익(회수 기준) {won(rep.net_if_cashed_out_krw)}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
