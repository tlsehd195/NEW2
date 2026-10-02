"""Cross-sectional (coin-versus-coin) portfolio candidates (ADR-0017).

Unlike `SignalStrategy`, which decides long/flat/short for one symbol, a
cross-sectional strategy ranks every eligible coin on the same day and
returns target portfolio weights (fraction of equity, signed). The
engine (`backtest.xsec_engine`) hands it only history that had closed by
the decision time, one sequence per eligible coin, so it cannot see the
future by construction.

Source of the candidates: Liu, Tsyvinski & Wu (2022), "Common Risk
Factors in Cryptocurrency", Journal of Finance 77(2). Weekly portfolios
sorted on past 1-4 week returns (cross-sectional momentum) and on
size/volume earn a spread across coins. The paper sorts hundreds of
coins by market cap; here the universe is a fixed list of large
USDT-margined perpetuals (no market-cap data in the archive), so the
volume sort is this system's stand-in for the paper's size/volume
characteristic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class CoinHistory:
    """Closed daily bars of one coin up to and including the decision day."""

    closes: Sequence[float]
    dollar_volumes: Sequence[float]  # close * base volume per bar


class CrossSectionalStrategy:
    strategy_id: str = "xsec_base"
    family: str = "xsec"
    warmup: int = 0  # bars of history a coin needs before it can be ranked
    rebalance_bars: int = 7
    min_assets: int = 6  # fewer eligible coins than this -> flat (fail-closed)

    def target_weights(self, history: Mapping[str, CoinHistory]) -> dict[str, float]:
        raise NotImplementedError


def _terciles(scores: dict[str, float], fraction: float) -> tuple[list[str], list[str]]:
    """-> (lowest, highest); ties broken by symbol so the result is deterministic."""
    ordered = sorted(scores, key=lambda s: (scores[s], s))
    k = max(1, int(len(ordered) * fraction))
    return ordered[:k], ordered[-k:]


class _Sorted(CrossSectionalStrategy):
    fraction: float = 1 / 3
    long_short: bool = True
    long_high: bool = True  # long the coins with the highest score

    def score(self, h: CoinHistory) -> float:
        raise NotImplementedError

    def target_weights(self, history: Mapping[str, CoinHistory]) -> dict[str, float]:
        scores = {}
        for sym, h in history.items():
            if len(h.closes) < self.warmup + 1:
                continue
            s = self.score(h)
            if math.isfinite(s):
                scores[sym] = s
        if len(scores) < self.min_assets:
            return {}
        low, high = _terciles(scores, self.fraction)
        longs, shorts = (high, low) if self.long_high else (low, high)
        if not self.long_short:
            return {s: 1.0 / len(longs) for s in longs}
        w = {s: 0.5 / len(longs) for s in longs}
        w.update({s: -0.5 / len(shorts) for s in shorts})
        return w


class MomentumLongShort(_Sorted):
    """Long the top third by past `lookback`-day return, short the bottom
    third, dollar-neutral, gross exposure 1.0, weekly."""

    def __init__(self, lookback: int = 21) -> None:
        self.lookback = lookback
        self.warmup = lookback
        self.strategy_id = f"xsec_momentum_{lookback}d_ls_v1"

    def score(self, h: CoinHistory) -> float:
        return h.closes[-1] / h.closes[-1 - self.lookback] - 1


class MomentumLongOnly(MomentumLongShort):
    """Same ranking, long only the top third (equal weight, gross 1.0)."""

    long_short = False

    def __init__(self, lookback: int = 21) -> None:
        super().__init__(lookback)
        self.strategy_id = f"xsec_momentum_{lookback}d_long_v1"


class LowVolumeLongShort(_Sorted):
    """Long the third with the LOWEST average dollar volume over
    `lookback` days, short the highest third (size/volume effect)."""

    long_high = False

    def __init__(self, lookback: int = 7) -> None:
        self.lookback = lookback
        self.warmup = lookback
        self.strategy_id = f"xsec_low_volume_{lookback}d_ls_v1"

    def score(self, h: CoinHistory) -> float:
        vols = h.dollar_volumes[-self.lookback:]
        mean = sum(vols) / len(vols)
        return math.log(mean) if mean > 0 else math.nan


def _daily_returns(closes: Sequence[float], window: int) -> list[float]:
    c = closes[-window - 1:]
    return [b / a - 1 for a, b in zip(c, c[1:])]


def _stdev(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return math.nan
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


class InverseVolMomentumLongShort(MomentumLongShort):
    """Same 21-day momentum legs, but each coin's weight inside its leg is
    proportional to 1 / its `vol_window`-day return volatility (each leg
    still 0.5 of equity). A volatile coin no longer dominates the spread."""

    def __init__(self, lookback: int = 21, vol_window: int = 30) -> None:
        super().__init__(lookback)
        self.vol_window = vol_window
        self.warmup = max(lookback, vol_window)
        self.strategy_id = f"xsec_momentum_{lookback}d_ls_invvol{vol_window}_v1"

    def target_weights(self, history: Mapping[str, CoinHistory]) -> dict[str, float]:
        base = super().target_weights(history)
        if not base:
            return {}
        vols = {s: _stdev(_daily_returns(history[s].closes, self.vol_window)) for s in base}
        if not all(math.isfinite(v) and v > 0 for v in vols.values()):
            return {}  # a coin with no measurable volatility: stay flat (fail-closed)
        inv = {s: 1 / v for s, v in vols.items()}
        out = {}
        for sign in (1, -1):
            leg = [s for s, w in base.items() if w * sign > 0]
            total = sum(inv[s] for s in leg)
            out.update({s: sign * 0.5 * inv[s] / total for s in leg})
        return out


class VolTargetMomentumLongShort(MomentumLongShort):
    """Barroso & Santa-Clara (2015, Journal of Financial Economics,
    "Momentum has its moments"): scale the momentum long-short so its
    volatility, estimated from the last `vol_window` days of the chosen
    weights applied to the coins' own past returns, is `target_vol`
    annualised. Gross exposure is capped at `max_gross` (the engine
    also caps at the risk config's max leverage)."""

    def __init__(self, lookback: int = 21, vol_window: int = 30, target_vol: float = 0.20,
                 max_gross: float = 2.0) -> None:
        super().__init__(lookback)
        self.vol_window, self.target_vol, self.max_gross = vol_window, target_vol, max_gross
        self.warmup = max(lookback, vol_window)
        self.strategy_id = f"xsec_momentum_{lookback}d_ls_voltarget{int(target_vol * 100)}_v1"

    def target_weights(self, history: Mapping[str, CoinHistory]) -> dict[str, float]:
        base = super().target_weights(history)
        if not base:
            return {}
        rets = {s: _daily_returns(history[s].closes, self.vol_window) for s in base}
        port = [sum(w * rets[s][d] for s, w in base.items()) for d in range(self.vol_window)]
        vol = _stdev(port) * math.sqrt(365)
        if not math.isfinite(vol) or vol <= 0:
            return {}
        scale = min(self.target_vol / vol, self.max_gross / sum(abs(w) for w in base.values()))
        return {s: w * scale for s, w in base.items()}


def xsec_candidate_grid_v1() -> list[CrossSectionalStrategy]:
    return [MomentumLongShort(21), MomentumLongOnly(21), LowVolumeLongShort(7)]


def xsec_candidate_grid_v2() -> list[CrossSectionalStrategy]:
    """H-0018 (ADR-0018): volatility-managed versions of the 21-day
    momentum long-short, the only H-0017 candidate that beat buy-and-hold on TEST."""
    return [InverseVolMomentumLongShort(21, 30), VolTargetMomentumLongShort(21, 30, 0.20)]


XSEC_FACTORIES = {s.strategy_id: s for s in xsec_candidate_grid_v1() + xsec_candidate_grid_v2()}
