"""Per-indicator Information Coefficient (IC) diagnostic.

Idea ported from tlsehd195/NEW- (`strategy_research/signal_ic.py`,
alphalens-style): before judging a whole strategy, ask whether each raw
signal has ANY forward-predictive power, independent of entry rules,
stops and costs. NEW- ranks many stocks per date (cross-section); NEW2
trades one coin's series, so this is the time-series version: the
Spearman correlation between an indicator's score at bar t and the
return over the next `horizon` bars, computed per rolling window.

Also measures redundancy as the Spearman correlation between indicators'
own scores -- the evidence for "different roles, not duplicates".

Point-in-time: a score at t uses history[: t + 1] only; the forward
return deliberately looks ahead (this is after-the-fact research, never
a strategy input). Windows must not touch a locked TEST window.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Optional, Sequence

from cointrader.data.models import Candle
from cointrader.features.indicator_votes import DEFAULT_PANEL, MIN_BARS, raw_scores


def rank_average(values: Sequence[float]) -> list[float]:
    """1-indexed average ranks; ties share the mean of the ranks they span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """None (never a fake 0.0) when undefined."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = rank_average(xs), rank_average(ys)
    mx, my = math.fsum(rx) / len(rx), math.fsum(ry) / len(ry)
    sxy = math.fsum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx, syy = math.fsum((a - mx) ** 2 for a in rx), math.fsum((b - my) ** 2 for b in ry)
    return None if sxx <= 0 or syy <= 0 else sxy / math.sqrt(sxx * syy)


@dataclass(frozen=True)
class IcSummary:
    n_windows: int
    mean_ic: Optional[float]
    ic_information_ratio: Optional[float]  # mean / stdev across windows
    positive_ratio: Optional[float]


def _summarize(ics: Sequence[float]) -> IcSummary:
    if not ics:
        return IcSummary(0, None, None, None)
    mean = statistics.fmean(ics)
    sd = statistics.pstdev(ics) if len(ics) >= 2 else 0.0
    return IcSummary(len(ics), mean, mean / sd if sd > 0 else None, sum(1 for v in ics if v > 0) / len(ics))


def indicator_ic(
    candles: Sequence[Candle], *, horizon: int, window: int = 120, step: int = 20,
    panel: Sequence[str] = DEFAULT_PANEL,
) -> dict[str, IcSummary]:
    """Rolling-window IC of every panel indicator vs the next-`horizon`
    return. Scores are computed once per bar; windows are non-overlapping
    by `step`."""
    if horizon < 1 or window < 10 or step < 1:
        raise ValueError("need horizon >= 1, window >= 10, step >= 1")
    rows: list[tuple[dict, float]] = []
    for t in range(MIN_BARS, len(candles) - horizon):
        sc = raw_scores(candles[: t + 1], panel)
        if sc is not None:
            rows.append((sc, candles[t + horizon].close / candles[t].close - 1.0))
    out: dict[str, list[float]] = {k: [] for k in panel}
    for a in range(0, len(rows) - window + 1, step):
        chunk = rows[a:a + window]
        fwd = [r for _, r in chunk]
        for k in panel:
            ic = spearman([s[k] for s, _ in chunk], fwd)
            if ic is not None:
                out[k].append(ic)
    return {k: _summarize(v) for k, v in out.items()}


def indicator_redundancy_matrix(
    candles: Sequence[Candle], *, panel: Sequence[str] = DEFAULT_PANEL, stride: int = 3,
) -> dict[tuple[str, str], Optional[float]]:
    """Spearman correlation between each pair of indicators' scores."""
    series = [raw_scores(candles[: t + 1], panel) for t in range(MIN_BARS, len(candles), stride)]
    series = [s for s in series if s is not None]
    out: dict[tuple[str, str], Optional[float]] = {}
    for i, a in enumerate(panel):
        for b in panel[i + 1:]:
            out[(a, b)] = spearman([s[a] for s in series], [s[b] for s in series])
    return out
