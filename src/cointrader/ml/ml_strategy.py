"""ML signal strategy: a ridge or bagged-tree model retrained on a trailing
window of ALREADY-REALIZED history, emitting a signal with a confidence.

Determinism (so `validation.integrity` look-ahead / warm-up checks apply):
the model used at a decision depends only on the refit bucket of the
bar's timestamp (`refit_every` bars wide) and trains on samples whose
target bar is at or before that bucket's start, so it is a pure function
of the last `warmup` closed bars and does not depend on call order. Nothing is fitted on the bar being
decided or on anything after it.

`confidence` is model agreement / signal size (see the model docs). It is
carried in `Signal.strength` and, as a percentage, in
`Signal.features["confidence_pct"]`. It is NOT a win probability.

This class is a CANDIDATE. It is deliberately not in the strategy
registry: registering and testing it needs a pre-registered hypothesis id
first (CLAUDE.md rule 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from cointrader.data.models import Candle
from cointrader.features import indicators as ind
from cointrader.features.regime import Regime
from cointrader.ml.dataset import sample_at
from cointrader.ml.features import FEATURE_IDS, FEATURE_WINDOW, compute_feature_vector
from cointrader.ml.linear_model import RidgeModel, select_ridge_via_expanding_window_cv
from cointrader.ml.tree_model import BaggedTreeModel
from cointrader.strategies.base import MarketContext, Signal, flat

FAMILIES = ("ridge", "forest")


@dataclass(frozen=True)
class MLStrategy:
    model_family: str = "ridge"
    horizon: int = 12            # bars ahead the target looks
    train_bars: int = 500        # trailing realized bars used for training
    min_train: int = 150         # fewer usable samples than this -> no trade
    refit_every: int = 48        # bars between refits
    confidence_min: float = 0.6
    edge_min: float = 0.002      # |predicted log return| must beat round-trip cost
    stop_atr: float = 2.0
    long_only: bool = True
    timeframe: str = "1h"
    family: str = "swing"
    version: str = "1"
    _cache: dict = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.model_family not in FAMILIES:
            raise ValueError(f"model_family must be one of {FAMILIES}")
        if self.horizon < 1 or self.refit_every < 1 or self.min_train < len(FEATURE_IDS) + 2:
            raise ValueError("horizon, refit_every and min_train must be positive/large enough")
        if self.train_bars < self.min_train:
            raise ValueError("train_bars must be at least min_train")
        if not 0.0 <= self.confidence_min <= 1.0 or self.edge_min < 0 or self.stop_atr <= 0:
            raise ValueError("confidence_min in [0,1], edge_min >= 0, stop_atr > 0")

    @property
    def strategy_id(self) -> str:
        side = "lo" if self.long_only else "ls"
        return f"ml_{self.model_family}_h{self.horizon}_tr{self.train_bars}_{side}_v{self.version}"

    @property
    def warmup(self) -> int:
        # Everything the decision can depend on: the newest refit cutoff is
        # at most `refit_every` bars back, and training reaches back
        # `train_bars + horizon` bars from there, plus the feature window.
        return FEATURE_WINDOW + self.horizon + self.train_bars + self.refit_every

    @property
    def parameters(self) -> dict:
        return {"model_family": self.model_family, "horizon": self.horizon, "train_bars": self.train_bars,
                "min_train": self.min_train, "refit_every": self.refit_every,
                "confidence_min": self.confidence_min, "edge_min": self.edge_min,
                "stop_atr": self.stop_atr, "long_only": self.long_only}

    # ------------------------------------------------------------ internals
    def _state(self, underlying) -> dict:
        key = (id(underlying), underlying[0].open_time if len(underlying) else None)
        if self._cache.get("key") != key:
            self._cache.clear()
            self._cache.update(key=key, feats=[], bucket=None, model=None)
        return self._cache

    def _features_at(self, st: dict, candles: Sequence[Candle], i: int) -> Optional[dict]:
        feats: list = st["feats"]
        while len(feats) <= i:
            k = len(feats)
            feats.append(compute_feature_vector(candles[k + 1 - FEATURE_WINDOW: k + 1]) if k + 1 >= FEATURE_WINDOW else None)
        return feats[i]

    def _cutoff(self, history: Sequence[Candle]) -> tuple[int, int]:
        """(bucket id, number of bars visible to that bucket's model). The
        bucket is a function of the bar TIMESTAMP, not of how much history
        exists, so a decision never depends on bars older than `warmup`."""
        n = len(history)
        span = int(self.refit_every * history[n - 1].timeframe.delta.total_seconds())
        bucket = int(history[n - 1].close_time.timestamp()) // span
        start = bucket * span
        k = n
        while k > 0 and history[k - 1].close_time.timestamp() > start:
            k -= 1
        return bucket, k

    def _fit(self, st: dict, candles: Sequence[Candle], cutoff: int):
        last = cutoff - 1 - self.horizon  # newest bar whose target is realized by the cutoff
        first = max(FEATURE_WINDOW - 1, last - self.train_bars + 1)
        samples = []
        for i in range(first, last + 1):
            smp = sample_at(candles, i, self.horizon, self._features_at(st, candles, i))
            if smp is not None:
                samples.append(smp)
        if len(samples) < self.min_train:
            return None
        try:
            if self.model_family == "ridge":
                model = RidgeModel(FEATURE_IDS, select_ridge_via_expanding_window_cv(samples, FEATURE_IDS))
            else:
                model = BaggedTreeModel(FEATURE_IDS)
            model.fit(samples)
        except ValueError:
            return None
        return model

    # --------------------------------------------------------------- signal
    def signal(self, history: Sequence[Candle], context: Optional[MarketContext] = None) -> Signal:
        n = len(history)
        if n < self.warmup:
            return flat("warmup")
        underlying = getattr(history, "_candles", history)
        st = self._state(underlying)
        bucket, cutoff = self._cutoff(history)
        if st["bucket"] != bucket:
            st["model"], st["bucket"] = self._fit(st, underlying, cutoff), bucket
        model = st["model"]
        if model is None:
            return flat("model_unavailable")
        now = self._features_at(st, underlying, n - 1)
        h = history[n - 80:]
        atr = ind.atr(h, 14)
        if now is None or atr is None or atr <= 0:
            return flat("indicator_unavailable", features={"atr": atr})
        pred, conf = model.predict_with_confidence(now)
        feats = {**now, "ml_prediction": pred, "confidence": conf, "confidence_pct": round(conf * 100, 1),
                 "atr": atr, "close": history[n - 1].close}
        exit_long, exit_short = pred <= 0, pred >= 0
        if conf >= self.confidence_min and pred >= self.edge_min:
            return Signal(1, strength=conf, reason=f"ml_{self.model_family}_long", stop_distance=self.stop_atr * atr,
                          regime=Regime.UNDEFINED.value, features=feats)
        if conf >= self.confidence_min and pred <= -self.edge_min and not self.long_only:
            return Signal(-1, strength=conf, reason=f"ml_{self.model_family}_short", stop_distance=self.stop_atr * atr,
                          regime=Regime.UNDEFINED.value, features=feats)
        return flat("below_threshold", features=feats, exit_long=exit_long, exit_short=exit_short and not self.long_only)
