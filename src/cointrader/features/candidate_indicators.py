"""Candidate indicators that carry information the six default votes do not (ADR-0070).

Each feature is a pure function of the bars up to and including index t (no look-ahead). Windows are
fixed in advance (96 bars = 1 day, 480 bars = 5 days) and no value was tuned on any result. The
cumulative sums in `CandleSeries` make every feature O(1) or O(60) per bar, so the same code serves the
research script and the strategy variant.

Two families:
- directional features (`DIRECTIONAL`): a signed number whose sign may predict the next move;
- non-directional features (`NONDIRECTIONAL`): they describe the regime (volatility, compression,
  trending vs mean-reverting, volume), so alone they cannot vote on a side. As a vote they are multiplied
  by the recent move's direction (`vote_scores`), and the Platt slope decides whether that helps.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from cointrader.data.models import Candle

DAY = 96  # 15m bars
BASE = 480  # five days: the long window for every ratio (keeps warm-up short)
MIN_INDEX = BASE + 1  # first bar index with every feature defined (the first bar has no return, so no window may include index 0)

DIRECTIONAL = ("vwap_dist", "skew96", "clv16", "tod_seasonal", "volspike_signed", "ac_dir", "vr_dir")
NONDIRECTIONAL = ("rv_ratio", "vov", "squeeze", "nr_rank", "volspike", "ac1", "vr", "kurt96")
ALL_FEATURES = DIRECTIONAL + NONDIRECTIONAL


class CandleSeries:
    """Cumulative sums over a candle list, extended in place (a value at index t only reads bars <= t)."""

    def __init__(self) -> None:
        self.n = 0
        self.x: list[float] = []  # ln(close)
        self.high: list[float] = []
        self.low: list[float] = []
        self.vol: list[float] = []
        # Per-bar terms. Every window is summed directly with fsum (never as a difference of running totals), so a
        # value does not depend on where the history starts (the integrity checks compare runs on shorter histories).
        self.lr: list[float] = []
        self.lr2: list[float] = []
        self.lr3: list[float] = []
        self.lr4: list[float] = []
        self.lag: list[float] = []
        self.tpv: list[float] = []
        self.clv: list[float] = []
        self.d8: list[float] = []

    def extend(self, candles: Sequence[Candle], upto: Optional[int] = None) -> None:
        end = len(candles) if upto is None else min(upto, len(candles))
        for i in range(self.n, end):
            c = candles[i]
            xi = math.log(c.close) if c.close > 0 else (self.x[-1] if self.x else 0.0)
            lr = xi - self.x[-1] if self.x else 0.0
            prev = self.lr[-1] if self.lr else 0.0
            self.x.append(xi)
            self.high.append(c.high)
            self.low.append(c.low)
            self.vol.append(c.volume)
            self.lr.append(lr)
            self.lr2.append(lr ** 2)
            self.lr3.append(lr ** 3)
            self.lr4.append(lr ** 4)
            self.lag.append(lr * prev)
            self.tpv.append((c.high + c.low + c.close) / 3.0 * c.volume)
            span = c.high - c.low
            self.clv.append((2 * c.close - c.high - c.low) / span if span > 0 else 0.0)
            self.d8.append((xi - self.x[i - 8]) ** 2 if i >= 8 else 0.0)
            self.n = i + 1

    @staticmethod
    def _sum(arr: list[float], a: int, b: int) -> float:
        return math.fsum(arr[a:b + 1])

    def _sd(self, t: int, w: int) -> float:
        return math.sqrt(max(self._sum(self.lr2, t - w + 1, t), 0.0) / w)

    def _level_sd(self, t: int, w: int) -> float:
        win = self.x[t - w + 1:t + 1]
        m = math.fsum(win) / w
        return math.sqrt(math.fsum((v - m) ** 2 for v in win) / w)

    def features(self, t: int, names: Sequence[str] = ALL_FEATURES) -> Optional[dict[str, float]]:
        """Raw feature values at bar t, or None when the history is too short or an input is degenerate."""
        if t < MIN_INDEX or t >= self.n:
            return None
        sd96, sd_base = self._sd(t, DAY), self._sd(t, BASE)
        if sd96 <= 0 or sd_base <= 0:
            return None
        ret16 = self.x[t] - self.x[t - 16]
        dir16 = math.tanh(ret16 / (sd96 * 4.0))  # one 4h sigma = sd96 * sqrt(16)
        out: dict[str, float] = {}
        want = set(names)
        if want & {"rv_ratio"}:
            out["rv_ratio"] = math.log(sd96 / sd_base)
        if "vov" in want:
            blocks = [math.sqrt(max(self._sum(self.lr2, t - 24 * (k + 1) + 1, t - 24 * k), 0.0) / 24) for k in range(20)]
            m = math.fsum(blocks) / 20
            if m <= 0:
                return None
            out["vov"] = math.sqrt(math.fsum((b - m) ** 2 for b in blocks) / 20) / m
        if "squeeze" in want:
            now = self._level_sd(t, DAY)
            past = [self._level_sd(t - 8 * k, DAY) for k in range(1, 49)]
            mp = math.fsum(past) / len(past)
            if now <= 0 or mp <= 0:
                return None
            out["squeeze"] = math.log(now / mp)
        if "nr_rank" in want:
            rngs = []
            for j in range(5):  # five one-day blocks = BASE bars
                a, b = t - DAY * (j + 1) + 1, t - DAY * j
                rngs.append(max(self.high[a:b + 1]) - min(self.low[a:b + 1]))
            out["nr_rank"] = sum(1 for r in rngs[1:] if r > rngs[0]) / 4.0
        if want & {"volspike", "volspike_signed"}:
            v4, vb = self._sum(self.vol, t - 3, t) / 4, self._sum(self.vol, t - BASE + 1, t) / BASE
            if v4 <= 0 or vb <= 0:
                return None
            vs = math.log(v4 / vb)
            out["volspike"] = vs
            out["volspike_signed"] = max(vs, 0.0) * (1.0 if ret16 > 0 else -1.0 if ret16 < 0 else 0.0)
        if "vwap_dist" in want:
            v = self._sum(self.vol, t - DAY + 1, t)
            if v <= 0:
                return None
            vwap = self._sum(self.tpv, t - DAY + 1, t) / v
            out["vwap_dist"] = (self.x[t] - math.log(vwap)) / (sd96 * math.sqrt(DAY))
        if want & {"ac1", "ac_dir"}:
            den = self._sum(self.lr2, t - BASE + 1, t)
            ac = self._sum(self.lag, t - BASE + 1, t) / den
            out["ac1"] = ac
            out["ac_dir"] = ac * dir16
        if want & {"vr", "vr_dir"}:
            vr = (self._sum(self.d8, t - BASE + 1, t) / BASE) / (8.0 * self._sum(self.lr2, t - BASE + 1, t) / BASE) - 1.0
            out["vr"] = vr
            out["vr_dir"] = vr * dir16
        if want & {"skew96", "kurt96"}:
            m2 = self._sum(self.lr2, t - DAY + 1, t) / DAY
            out["skew96"] = (self._sum(self.lr3, t - DAY + 1, t) / DAY) / m2 ** 1.5
            out["kurt96"] = (self._sum(self.lr4, t - DAY + 1, t) / DAY) / (m2 * m2)
        if "clv16" in want:
            out["clv16"] = self._sum(self.clv, t - 15, t) / 16.0
        if "tod_seasonal" in want:
            fw = [self.x[t - DAY * k + 16] - self.x[t - DAY * k] for k in range(1, 5)]  # same 15m slot, previous 4 days
            out["tod_seasonal"] = (math.fsum(fw) / 4) / (sd_base * 4.0)
        if not all(math.isfinite(v) for v in out.values()):
            return None
        return {k: out[k] for k in names}

    def vote_scores(self, t: int, names: Sequence[str]) -> Optional[dict[str, float]]:
        """Signed scores in (-1, 1) for voting. Directional features squash directly; non-directional ones are
        multiplied by the direction of the last 4h move. Constants are fixed here, before any result."""
        f = self.features(t, tuple(names))
        if f is None:
            return None
        sd96 = self._sd(t, DAY)
        dir16 = math.tanh((self.x[t] - self.x[t - 16]) / (sd96 * 4.0))
        squash = {
            "vwap_dist": lambda v: -math.tanh(v),
            "skew96": lambda v: math.tanh(v),
            "clv16": lambda v: v,
            "tod_seasonal": lambda v: math.tanh(v),
            "volspike_signed": lambda v: math.tanh(v),
            "ac_dir": lambda v: math.tanh(v / 0.05),
            "vr_dir": lambda v: math.tanh(v / 0.3),
            "rv_ratio": lambda v: math.tanh(v / 0.3) * dir16,
            "vov": lambda v: math.tanh((v - 0.4) / 0.3) * dir16,
            "squeeze": lambda v: math.tanh(-v / 0.3) * dir16,
            "nr_rank": lambda v: (2 * v - 1) * dir16,
            "volspike": lambda v: math.tanh(v) * dir16,
            "ac1": lambda v: math.tanh(v / 0.05) * dir16,
            "vr": lambda v: math.tanh(v / 0.3) * dir16,
            "kurt96": lambda v: math.tanh((v - 6.0) / 4.0) * dir16,
        }
        return {k: squash[k](f[k]) for k in names}
