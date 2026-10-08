#!/usr/bin/env python3
"""How much do the six vote indicators overlap? (ADR-0046, measurement only)

Reads no returns and no outcomes: it only correlates the indicators' own scores bar by bar, so it
cannot leak a result into any threshold and spends no preregistration budget. Prints the correlation
matrix of the default panel and an effective number of independent indicators (participation ratio
and entropy of the correlation matrix's eigenvalues; 6 = fully independent, 1 = all the same).

    python3 scripts/diagnose_indicator_overlap.py --symbol BTCUSDT --start 2023-04-20 --end 2024-06-18
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.features.indicator_votes import DEFAULT_PANEL, MIN_BARS, raw_scores, redundancy  # noqa: E402
from cointrader.strategies.daytrade import DayTradeVote  # noqa: E402


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def correlation_matrix(rows: list[list[float]]) -> list[list[float]]:
    k, n = len(rows[0]), len(rows)
    means = [math.fsum(r[j] for r in rows) / n for j in range(k)]
    cov = [[math.fsum((r[a] - means[a]) * (r[b] - means[b]) for r in rows) for b in range(k)] for a in range(k)]
    sd = [math.sqrt(cov[j][j]) for j in range(k)]
    return [[1.0 if a == b else (0.0 if sd[a] == 0 or sd[b] == 0 else cov[a][b] / (sd[a] * sd[b])) for b in range(k)]
            for a in range(k)]


def eigenvalues(m: list[list[float]], sweeps: int = 100) -> list[float]:
    """Jacobi rotations for a small symmetric matrix (standard library only)."""
    a = [row[:] for row in m]
    n = len(a)
    for _ in range(sweeps):
        off = math.fsum(a[i][j] ** 2 for i in range(n) for j in range(i + 1, n))
        if off < 1e-18:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-15:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                c = 1.0 / math.sqrt(t * t + 1.0)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
    return sorted((a[i][i] for i in range(n)), reverse=True)


def effective_count(eigs: list[float]) -> dict:
    pos = [max(e, 0.0) for e in eigs]
    total = math.fsum(pos)
    pr = total * total / math.fsum(e * e for e in pos)
    p = [e / total for e in pos if e > 0]
    return {"participation_ratio": pr, "entropy_exp": math.exp(-math.fsum(x * math.log(x) for x in p)),
            "eigenvalues": eigs}


def analyse(candles: list, first_index: int, scale: float, stride: int) -> dict:
    names = list(DEFAULT_PANEL)
    series = []
    for t in range(max(first_index, MIN_BARS), len(candles), stride):
        sc = raw_scores(candles[: t + 1], DEFAULT_PANEL, scale=scale)
        if sc is not None:
            series.append(sc)
    if len(series) < 100:
        return {"error": f"only {len(series)} complete panels"}
    rows = [[d[k] for k in names] for d in series]
    corr = correlation_matrix(rows)
    out = effective_count(eigenvalues(corr))
    pairs = sorted(((abs(corr[i][j]), names[i], names[j], corr[i][j]) for i in range(len(names))
                    for j in range(i + 1, len(names))), reverse=True)
    out.update({"panels": len(series), "names": names, "correlation": corr,
                "mean_abs_pairwise": redundancy(series, names),
                "top_pairs": [{"a": a, "b": b, "corr": c} for _, a, b, c in pairs[:5]]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True, help="exclusive; must not touch a locked window")
    ap.add_argument("--stride", type=int, default=4, help="bars between samples (default 4 = hourly)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    from cointrader.data.models import Timeframe  # noqa: E402
    from cointrader.research.market_data import load_candles  # noqa: E402
    from cointrader.validation.locked_windows import assert_not_locked, load_locked_windows  # noqa: E402

    tf, start, end = Timeframe("15m"), _utc(args.start), _utc(args.end)
    scale = DayTradeVote().score_scale
    warm_start = start - (MIN_BARS + 1) * tf.delta
    assert_not_locked(load_locked_windows(REPO / "configs" / "locked_windows.json"), args.symbol, warm_start, end)
    candles, notes = load_candles(args.symbol, tf, warm_start, end)
    first = next(i for i, c in enumerate(candles) if c.open_time >= start)
    mid = (first + len(candles)) // 2
    out = {"label": "OVERLAP MEASUREMENT (indicator scores only; no returns read; not a validation result)",
           "symbol": args.symbol, "timeframe": "15m", "range": [start.isoformat(), end.isoformat()],
           "source": "binance_vision_archive", "candles": len(candles), "data_notes": notes[:10],
           "all": analyse(candles, first, scale, args.stride),
           "first_half": analyse(candles[: mid + 1], first, scale, args.stride),
           "second_half": analyse(candles, mid, scale, args.stride)}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    names = out["all"].get("names", [])
    for part in ("all", "first_half", "second_half"):
        r = out[part]
        if "error" in r:
            print(part, r["error"])
            continue
        print(f"[{part}] panels={r['panels']} mean|corr|={r['mean_abs_pairwise']:.2f} "
              f"effective indicators: participation={r['participation_ratio']:.2f} entropy={r['entropy_exp']:.2f} (of {len(names)})")
        for p in r["top_pairs"]:
            print(f"    {p['a']:>13} ~ {p['b']:<13} {p['corr']:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
