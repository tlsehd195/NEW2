#!/usr/bin/env python3
"""Shows var/paper/krw_live.json as it updates (ADR-0041). Run in a second terminal while the paper
trader runs:  python3 scripts/watch_krw_pnl.py [--file var/paper/krw_live.json] [--every 5]"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.accounting.krw_live import one_line  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path, default=REPO / load_paper()["state_dir"] / "krw_live.json")
    ap.add_argument("--every", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    while True:
        try:
            snap = json.loads(args.file.read_text(encoding="utf-8"))
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(snap["as_of"])).total_seconds()
            warn = "  [오래됨: 갱신이 멈췄을 수 있음]" if age > 120 else ""
            print(f"{one_line(snap)}  ({age:.0f}초 전){warn}", flush=True)
        except FileNotFoundError:
            print(f"{args.file} 없음: 모의투자가 돌고 있고 첫 환율·시작 매수가 기록됐는지 확인", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"읽기 실패: {exc}", flush=True)
        if args.once:
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
