#!/usr/bin/env python3
"""KRW net PnL after every leg of KRW -> USDT -> Binance -> KRW (ADR-0036).

    python3 scripts/show_krw_pnl.py --mode paper [--flows ...] [--rates ...] [--data-root var/data] [--days 365] [--as-of 2026-10-03T00:00:00+00:00] [--json] [--export-year 2027 --out-dir var/tax]

--flows   (paper default var/paper/krw_flows.jsonl, written by the paper runner; required otherwise) JSONL of money movements (one per line, `type` = usdt_purchase |
          usdt_sale | usdt_transfer | krw_fee). Paper: write the simulated
          initial purchase there with mode "paper". Live: the bridge's
          receipts and the exchanges' own statements.
--rates   (default var/data/krw_usdt.csv, written by scripts/collect_krw_rates.py
          and the paper runner) CSV `time,rate`: Upbit KRW-USDT 15m closes.
          Every closed trade is valued at the rate at its exit.

--export-year  also writes that year's tax-filing package (summary, details,
          checklist) into --out-dir; see accounting/tax_export.py.

Closed trades come from the outcome journal (same rows as
show_performance.py), so paper and live run the same code. Read-only:
this never moves money or places orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.accounting.krw_report import build_krw_report  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402

LABELS = {
    "trading_gross": "선물 매매손익(총)", "trading_fees": "선물 거래 수수료", "spread_slippage": "스프레드·슬리피지",
    "funding": "펀딩비", "domestic_fees": "국내 거래소 수수료", "network_fees": "출금·네트워크 수수료",
    "krw_fees": "원화 수수료", "fx_realized": "환차손익(실현)",
}


def won(x) -> str:
    return "알 수 없음" if x is None else f"{(x or 0.0):+,.0f}원"


def main() -> int:
    paper = load_paper()
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="paper", choices=("paper", "backtest", "live"))
    ap.add_argument("--flows", type=Path, default=None)
    ap.add_argument("--rates", type=Path, default=REPO / paper["data_root"] / "krw_usdt.csv")
    ap.add_argument("--data-root", type=Path, default=REPO / paper["data_root"])
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--as-of", type=datetime.fromisoformat, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--export-year", type=int, default=None)
    ap.add_argument("--out-dir", type=Path, default=REPO / "var" / "tax")
    args = ap.parse_args()

    if args.flows is None:
        if args.mode != "paper":
            ap.error("--flows is required for live/backtest: record real purchases and transfers, never the paper file")
        args.flows = REPO / paper["state_dir"] / "krw_flows.jsonl"
    rep, tax = build_krw_report(mode=args.mode, flows_path=args.flows, rates_path=args.rates,
                                data_root=args.data_root, days=args.days, as_of=args.as_of)

    if args.export_year is not None:
        from cointrader.accounting.tax_export import write_filing_package
        for path in write_filing_package(rep, args.export_year, args.out_dir / rep.mode, tax):
            print(f"wrote {path}", file=sys.stderr)
    if args.json:
        print(json.dumps({**rep.__dict__, "as_of": rep.as_of.isoformat(), "years": [y.__dict__ for y in rep.years], "lines": len(rep.lines),
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
