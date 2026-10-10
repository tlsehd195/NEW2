#!/usr/bin/env python3
"""Phase A diagnostics for two strategy-research candidates (ADR-0076). MEASUREMENT ONLY.

S1  quarter-hour order-book imbalance: mean (bid - ask)/(bid + ask) of resting size within 1 % (and 3 %) of mid over
    the 5 minutes BEFORE each 15m bar opens (Binance `bookDepth` archive, ~30 s snapshots).
S2  intraday time-series momentum: return from the UTC-day open to the last closed bar.

Registers nothing, spends no budget, changes no strategy or constant. Like `calibrate_vote.py` (ADR-0035) it reads the
up/down OUTCOME that follows each decision, so what it prints is an exploratory read, not a validation result. The
range is refused if it overlaps a locked window or the reserved TEST window.

For each score and horizon it prints: coverage and signal frequency, how big the move after a signal is against the
round-trip cost, correlation (with an effective-sample t-stat because labels overlap), stability by quarter, whether
the score is just the past return, and a walk-forward Platt calibration against the always-base-rate Brier.

    python3 scripts/diagnose_signal_candidates.py --symbol BTCUSDT --start 2023-04-20 --end 2024-03-25 \
        --cache /tmp/depth_cache --out reports/signal_candidates_btc.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import urllib.error
import urllib.request
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.data import binance_vision as bv  # noqa: E402
from cointrader.data.models import Timeframe  # noqa: E402
from cointrader.features.indicator_votes import (  # noqa: E402
    PlattCalibrator, intraday_tsm_score, quarter_hour_imbalance_score,
)
from cointrader.research.market_data import load_candles  # noqa: E402
from cointrader.validation.calibration import beats_base_rate, calibration_report  # noqa: E402
from cointrader.validation.locked_windows import assert_not_locked, load_locked_windows  # noqa: E402
from cointrader.validation.screening import load_reserved, reserved_for  # noqa: E402

HORIZONS = (4, 16, 48)  # bars of 15m: 1h, 4h, 12h
COST_BPS = (12.0, 16.0, 20.0)  # round trip on perps (project memory: 0.12-0.20 %)
PAST_BARS = 4


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def cached_transport(cache: Path):
    cache.mkdir(parents=True, exist_ok=True)

    def get(url: str):
        f = cache / hashlib.sha1(url.encode()).hexdigest()
        if f.exists():
            return None if f.stat().st_size == 0 else f.read_bytes()
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as r:
                body = r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                f.write_bytes(b"")
                return None
            raise
        f.write_bytes(body)
        return body

    return get


def prefetch(symbol: str, start: datetime, end: datetime, get) -> None:
    urls = [f"{bv.BASE_URL}/daily/bookDepth/{symbol}/{symbol}-bookDepth-{d:%Y-%m-%d}.zip" for d in bv._days(start, end)]
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(get, urls))


def corr(x, y):
    n = len(x)
    if n < 10:
        return None
    mx, my = math.fsum(x) / n, math.fsum(y) / n
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx, syy = math.fsum((a - mx) ** 2 for a in x), math.fsum((b - my) ** 2 for b in y)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def ranks(v):
    order = sorted(range(len(v)), key=v.__getitem__)
    r = [0.0] * len(v)
    for k, i in enumerate(order):
        r[i] = float(k)
    return r


def tstat(mean, sd, n_eff):
    return None if n_eff < 2 or not sd else mean / (sd / math.sqrt(n_eff))


def load_marks(path):
    """Top-of-book readings from extract_book_ticker_marks.py, keyed by the quarter-hour mark."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        out[datetime.fromisoformat(r["t"])] = r
    return out


def build_rows(candles, snaps, tag, marks=None):
    """One row per decision bar i (decision at the OPEN of bar i, using only data before it)."""
    times = [s.at for s in snaps]
    step = Timeframe.MINUTE_15.delta
    rows = []
    for i in range(PAST_BARS, len(candles)):
        c = candles[i]
        if candles[i].open_time - candles[i - PAST_BARS].open_time != step * PAST_BARS:
            continue  # candle hole right behind the decision
        lo = bisect_left(times, c.open_time - timedelta(minutes=5))
        hi = bisect_left(times, c.open_time)
        win = snaps[lo:hi]
        row = {"i": i, "t": c.open_time,
               "s1": quarter_hour_imbalance_score(win, c.open_time, pct=1),
               "s1_3": quarter_hour_imbalance_score(win, c.open_time, pct=3),
               "s2": intraday_tsm_score(candles[:i]) if i >= 1 else None,
               "s1_top": marks[c.open_time]["mean"] if marks and c.open_time in marks else None,
               "s1_top_last": marks[c.open_time]["last"] if marks and c.open_time in marks else None,
               "past": candles[i].open / candles[i - PAST_BARS].open - 1.0}
        rows.append(row)
    return rows


def forward(candles, rows, h):
    out = []
    step = Timeframe.MINUTE_15.delta
    for r in rows:
        i = r["i"]
        if i + h >= len(candles) or candles[i + h].open_time - candles[i].open_time != step * h:
            continue
        out.append((r, candles[i + h].open / candles[i].open - 1.0))
    return out


def walk_forward_calibration(pairs, key, h):
    """Non-overlapping labels (stride h), expanding Platt fit refit each month on outcomes already known."""
    samples = []
    nxt = -1
    for r, f in pairs:
        if r[key] is None or r["i"] <= nxt or f == 0:
            continue
        samples.append((r["i"], r["t"], r[key], 1 if f > 0 else 0))
        nxt = r["i"] + h - 1
    probs, outs = [], []
    fit_month, cal, mu, sd = None, PlattCalibrator(), 0.0, 1.0
    for k, (i, t, x, y) in enumerate(samples):
        if (t.year, t.month) != fit_month:
            fit_month = (t.year, t.month)
            train = [s for s in samples[:k] if s[0] + h <= i]
            xs = [s[2] for s in train]
            if len(xs) >= 30:
                mu = math.fsum(xs) / len(xs)
                sd = math.sqrt(math.fsum((v - mu) ** 2 for v in xs) / len(xs)) or 1.0
                cal = PlattCalibrator.fit([(v - mu) / sd for v in xs], [s[3] for s in train])
            else:
                cal = PlattCalibrator()
        probs.append(cal.predict((x - mu) / sd))
        outs.append(y)
    rep = calibration_report(probs, outs)
    p60 = [o for p, o in zip(probs, outs) if p >= 0.55]
    p40 = [o for p, o in zip(probs, outs) if p <= 0.45]
    return {"n": rep.samples, "brier": rep.brier, "brier_base_rate": rep.brier_baseline, "ece": rep.ece,
            "base_up_rate": rep.base_rate, "beats_base_rate": bool(beats_base_rate(rep)) if rep.brier is not None else None,
            "up_rate_p>=0.55": (round(sum(p60) / len(p60), 4), len(p60)) if p60 else None,
            "up_rate_p<=0.45": (round(sum(p40) / len(p40), 4), len(p40)) if p40 else None, "reason": rep.reason}


def analyse_score(candles, rows, key, h, days):
    pairs = [(r, f) for r, f in forward(candles, rows, h) if r[key] is not None]
    n = len(pairs)
    if n < 100:
        return {"n": n, "note": "too few samples"}
    x = [r[key] for r, _ in pairs]
    y = [f for _, f in pairs]
    n_eff = n / h
    c = corr(x, y)
    sp = corr(ranks(x), ranks(y))
    out = {"n_decisions": n, "n_eff_nonoverlap": round(n_eff, 1),
           "corr_pearson": round(c, 4) if c is not None else None,
           "corr_spearman": round(sp, 4) if sp is not None else None,
           "corr_t_eff": round(c * math.sqrt(max(n_eff - 2, 1)) / math.sqrt(1 - c * c), 2) if c is not None and abs(c) < 1 else None,
           "median_abs_move_bps": round(sorted(abs(v) for v in y)[n // 2] * 1e4, 1)}
    past = [r["past"] for r, _ in pairs]
    cp = corr(x, past)
    out["corr_with_past_1h_return"] = round(cp, 4) if cp is not None else None
    if c is not None and cp is not None and abs(cp) < 1:
        fp = corr(y, past)
        if fp is not None:
            out["partial_corr_given_past"] = round((c - cp * fp) / math.sqrt((1 - cp * cp) * (1 - fp * fp)), 4)
    # signal buckets by |score| quantile; direction = sign of the score (follow the score)
    absx = sorted(abs(v) for v in x)
    buckets = {}
    for q in (0.5, 0.8, 0.9, 0.95):
        th = absx[int(q * (n - 1))]
        sel = [(1 if r[key] > 0 else -1) * f for r, f in pairs if abs(r[key]) >= th and r[key] != 0]
        if len(sel) < 30:
            continue
        m = math.fsum(sel) / len(sel)
        sd = math.sqrt(math.fsum((v - m) ** 2 for v in sel) / (len(sel) - 1))
        ne = len(sel) / h
        b = {"threshold_abs_score": round(th, 4), "decisions_per_day": round(len(sel) / days, 2),
             "mean_signed_bps": round(m * 1e4, 2), "t_eff": round(tstat(m, sd, ne), 2) if tstat(m, sd, ne) is not None else None,
             "hit_rate": round(sum(1 for v in sel if v > 0) / len(sel), 4)}
        for cb in COST_BPS:
            b[f"net_bps_cost{int(cb)}"] = round(m * 1e4 - cb, 2)
        # same bucket by quarter of the window (stability)
        t0 = pairs[0][0]["t"]
        span = (pairs[-1][0]["t"] - t0) / 4
        qs = []
        for k in range(4):
            part = [(1 if r[key] > 0 else -1) * f for r, f in pairs
                    if abs(r[key]) >= th and r[key] != 0 and t0 + span * k <= r["t"] < t0 + span * (k + 1) + (timedelta(seconds=1) if k == 3 else timedelta(0))]
            qs.append(round(math.fsum(part) / len(part) * 1e4, 1) if len(part) >= 10 else None)
        b["mean_signed_bps_by_quarter"] = qs
        buckets[f"|score|>=q{int(q * 100)}"] = b
    out["signal_buckets"] = buckets
    out["calibration_walk_forward"] = walk_forward_calibration(pairs, key, h)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True, help="exclusive")
    ap.add_argument("--cache", type=Path, default=Path("/tmp/depth_cache"))
    ap.add_argument("--marks", type=Path, help="bookTicker marks JSONL (ADR-0078); adds s1_top scores")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    start, end = _utc(a.start), _utc(a.end)
    warm = start - timedelta(days=1)
    assert_not_locked(load_locked_windows(REPO / "configs" / "locked_windows.json"), a.symbol, warm, end)
    held = reserved_for(load_reserved(), a.symbol)
    if held and end > held.start:
        raise SystemExit(f"end {end} reaches the reserved TEST {held.start}")

    get = cached_transport(a.cache)
    prefetch(a.symbol, warm, end, get)
    fetcher = bv.BinanceVisionBookDepth(transport=get)
    snaps = fetcher.fetch(a.symbol, warm, end)
    candles, notes = load_candles(a.symbol, Timeframe.MINUTE_15, warm, end)
    first = next(i for i, c in enumerate(candles) if c.open_time >= start)
    rows = build_rows(candles, snaps, a.symbol, load_marks(a.marks) if a.marks else None)
    rows = [r for r in rows if r["i"] >= first]
    days = (end - start).days
    out = {"label": "PHASE A DIAGNOSTIC (exploratory read of up/down outcomes; not a validation result; "
                    "registers nothing; do not tune constants to it)",
           "symbol": a.symbol, "range": [start.isoformat(), end.isoformat()], "source": "binance_vision_archive",
           "candles": len(candles), "depth_snapshots": len(snaps), "depth_archive_gaps": len(fetcher.last_gaps),
           "candle_notes": notes[:10], "cost_bps_roundtrip": list(COST_BPS), "scores": {}}
    keys = ("s1", "s1_3", "s2") + (("s1_top", "s1_top_last") if a.marks else ())
    for key in keys:
        valid = [r for r in rows if r[key] is not None]
        if not valid:
            continue
        vals = sorted(r[key] for r in valid)
        sc = {"coverage": round(len(valid) / len(rows), 4) if rows else 0,
              "score_quantiles": {q: round(vals[int(q * (len(vals) - 1))], 4) for q in (0.05, 0.25, 0.5, 0.75, 0.95)} if vals else {}}
        for th in (0.1, 0.2, 0.3, 0.5):
            sc[f"share_abs>={th}"] = round(sum(1 for v in vals if abs(v) >= th) / len(vals), 4) if vals else 0
        sc["horizons"] = {f"h{h}": analyse_score(candles, rows, key, h, days) for h in HORIZONS}
        out["scores"][key] = sc
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
