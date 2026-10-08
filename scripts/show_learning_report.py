"""Summarize the learning layer: drift flags and shadow champion-vs-challenger scores (ADR-0044).

    python3 scripts/show_learning_report.py            # last 30 days
    python3 scripts/show_learning_report.py --days 7

Shadow numbers only: nothing here is a validation result, and no model shown
here trades. Using a challenger needs a pre-registered hypothesis first.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.journal.store import LayeredStore  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return math.fsum(xs) / len(xs) if xs else None


def _fmt(x, pct=False):
    if x is None:
        return "-"
    return f"{x * 100:+.3f}%" if pct else f"{x:.3f}"


def summarize(records: list[dict]) -> list[str]:
    lines = ["그림자 평가(검증 아님). 챌린저는 매매에 쓰이지 않으며, 쓰려면 가설 사전등록부터 해야 함.", ""]
    drift = defaultdict(lambda: {"days": 0, "drift": 0, "unknown": 0, "features": defaultdict(int)})
    for r in records:
        if r.get("event") == "feature_drift":
            d = drift[r["symbol"]]
            d["days"] += 1
            d["drift"] += r["status"] == "DRIFT_DETECTED"
            d["unknown"] += r["status"] == "UNKNOWN"
            for f in r.get("drifted", []):
                d["features"][f] += 1
    lines.append("[드리프트]")
    for sym, d in sorted(drift.items()):
        top = ", ".join(f"{f}({n})" for f, n in sorted(d["features"].items(), key=lambda kv: -kv[1])[:3]) or "-"
        lines.append(f"  {sym}: {d['days']}일 중 드리프트 {d['drift']}일, 판단불가 {d['unknown']}일. 잦은 특징: {top}")
    if not drift:
        lines.append("  기록 없음")
    groups = defaultdict(list)
    for r in records:
        if r.get("event") != "challenger_eval" or r.get("result") != "SCORED":
            continue
        key = (r["symbol"], r["champion"]["strategy_id"])
        groups[key].append(("champion", r["champion"]))
        for c in r["challengers"]:
            if c.get("result") == "SCORED":
                groups[key].append((c["family"], c))
    lines += ["", "[챔피언 vs 챌린저] 일평균 IC / 방향 적중률 / 진입 수 / 진입당 평균 순수익(비용 차감)"]
    for (sym, sid), rows in sorted(groups.items()):
        lines.append(f"  {sym} {sid}")
        by = defaultdict(list)
        for name, m in rows:
            by[name].append(m)
        for name in ["champion"] + sorted(k for k in by if k != "champion"):
            ms = by.get(name, [])
            entries = sum(m["n_entries"] for m in ms)
            net = math.fsum(m["sum_net_return"] for m in ms)
            lines.append(f"    {name:9s} {len(ms):3d}일  IC {_fmt(_mean(m['ic'] for m in ms))}  "
                         f"적중 {_fmt(_mean(m['direction_hit_rate'] for m in ms))}  진입 {entries:4d}  "
                         f"평균 {_fmt(net / entries if entries else None, pct=True)}")
    if not groups:
        lines.append("  점수 낸 날 없음 (학습에 필요한 저널 기록이 아직 부족할 수 있음)")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    store = LayeredStore(REPO / load_paper()["data_root"])
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).date()
    records = [r for r in store.read("learning") if "day" in r and r["day"] != "*"
               and date.fromisoformat(r["day"]) >= since]
    print("\n".join(summarize(records)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
