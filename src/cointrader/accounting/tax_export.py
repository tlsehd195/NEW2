"""One-shot tax-filing package for a calendar year (KST), from a KRW ledger report.

Writes three files per year into `out_dir` so the whole year's overseas
trading result can be handed to an accountant (or used for self-filing)
in one go:

    summary_<year>.csv    every component in KRW, net, deduction, estimated tax
    details_<year>.csv    every booked amount (time, kind, component, KRW, ref)
    checklist_<year>.md   what still has to be confirmed or filed, and by when

This is bookkeeping, not tax advice (ADR-0003, ADR-0036, ADR-0038): the
tax numbers are the same *estimate* as the report's, flagged unverified
until `KrwTaxConfig.verified` is set, and the checklist lists the open
questions instead of answering them. CSVs are UTF-8 with BOM so Excel
shows Korean correctly. Nothing here talks to an exchange or moves money.
"""

from __future__ import annotations

import csv
from pathlib import Path

from cointrader.accounting.krw_ledger import COMPONENTS, KST, KrwPnlReport, KrwTaxConfig

COMPONENT_LABELS = {
    "trading_gross": "선물 매매손익(총)", "trading_fees": "선물 거래 수수료", "spread_slippage": "스프레드·슬리피지",
    "funding": "펀딩비", "domestic_fees": "국내 거래소 수수료", "network_fees": "출금·네트워크 수수료",
    "krw_fees": "원화 수수료", "fx_realized": "환차손익(실현)",
}


def _checklist(year: int, tax: KrwTaxConfig, in_force: bool) -> str:
    status = (f"{year}년은 과세 시행 연도({tax.effective_from_year}년~)입니다." if in_force
              else f"{year}년은 시행 전({tax.effective_from_year}년 이후 양도분부터 과세)이라 세액은 0으로 계산했습니다. "
                   "참고용 장부입니다.")
    return f"""# {year}년 세금 신고 준비 체크리스트 (추정 자료, 세무 조언 아님)

{status}

## 이 자료가 하는 것
- `summary_{year}.csv`: 항목별 원화 손익, 기본공제({tax.basic_deduction_krw:,.0f}원), 세율({tax.rate:.0%}) 적용 추정세액
- `details_{year}.csv`: 위 숫자를 만든 모든 건(일시, 종류, 항목, 원화, 거래 번호)

## 아직 확정되지 않은 것 (세무사 확인 필요)
1. 해외 거래소(바이낸스) **선물 손익**이 가상자산 소득(기타소득)인지, 다른 소득으로 보는지 확인한 자료가 없습니다.
2. 취득가 계산법: 이 장부는 USDT를 원화 **이동평균** 원가로 계산합니다. 해외 거래소분은 선입선출이라는 자료도 있어 확인이 필요합니다.
3. 2026-12-31 이전부터 가진 USDT가 있으면 의제취득가액(그날 시가와 실제 취득가 중 큰 값)이 적용되며, 이 자료는 반영하지 않았습니다(세액이 높게 나오는 쪽).
4. USDT 매도 자체가 양도에 해당하는지, 환차익을 별도로 보는지는 위 1~2와 함께 확인하세요.
5. 세율·공제·시행연도는 국회에서 계속 논의 중이라 바뀔 수 있습니다(`configs/krw_accounting.json`).

## 기한 (현행 기준, 직접 확인하세요)
- 소득세 신고: 다음 해 5월 1일~31일
- 해외금융계좌 신고: 어느 달 말이든 해외 거래소 잔고가 5억원을 넘으면 다음 해 6월 (이 자료는 잔고를 계산하지 않습니다)

## 준비물
- 업비트 거래·입출금 내역, 바이낸스 거래·입출금·펀딩비 내역(원본 CSV)
- 이 폴더의 summary/details 파일
"""


def write_filing_package(report: KrwPnlReport, year: int, out_dir: Path, tax: KrwTaxConfig = KrwTaxConfig()) -> list[Path]:
    """Writes the three files for `year` and returns their paths. A year
    with no booked amounts raises instead of writing an empty package."""
    rows = [ln for ln in report.lines if ln.at.astimezone(KST).year == year]
    if not rows:
        raise ValueError(f"no ledger lines in {year} (KST); nothing to export")
    out_dir.mkdir(parents=True, exist_ok=True)
    by_comp = {c: sum(ln.krw for ln in rows if ln.component == c) for c in COMPONENTS}
    net = sum(by_comp.values())
    in_force = year >= tax.effective_from_year
    taxable = max(0.0, net - tax.basic_deduction_krw) if in_force else 0.0
    estimate = tax.estimate(year, net)
    flag = "검증됨" if tax.verified else "추정치(세무사 확인 필요)"

    summary = out_dir / f"summary_{year}.csv"
    with summary.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["구분", "항목", "금액(원)", "비고"])
        w.writerow(["모드", report.mode, "", "paper는 모의, live만 실제"])
        for c in COMPONENTS:
            w.writerow(["손익", COMPONENT_LABELS[c], f"{by_comp[c]:.0f}", ""])
        w.writerow(["합계", f"{year}년 실현 순손익", f"{net:.0f}", "KST 연도 기준"])
        w.writerow(["세금", "기본공제", f"{tax.basic_deduction_krw:.0f}" if in_force else "0", ""])
        w.writerow(["세금", "과세표준(추정)", f"{taxable:.0f}", "의제취득가액 미반영"])
        w.writerow(["세금", f"추정세액({tax.rate:.0%})", f"{estimate:.0f}", flag])

    details = out_dir / f"details_{year}.csv"
    with details.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["일시(KST)", "이벤트", "항목", "금액(원)", "거래 번호"])
        for ln in sorted(rows, key=lambda x: x.at):
            w.writerow([ln.at.astimezone(KST).isoformat(timespec="seconds"), ln.event,
                        COMPONENT_LABELS[ln.component], f"{ln.krw:.2f}", ln.ref])

    checklist = out_dir / f"checklist_{year}.md"
    checklist.write_text(_checklist(year, tax, in_force), encoding="utf-8")
    return [summary, details, checklist]
