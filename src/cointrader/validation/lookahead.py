"""Automatic look-ahead and warm-up sensitivity checks for a strategy.

Borrowed idea: Freqtrade's `lookahead-analysis` and `recursive-analysis`
commands (ADR-0014). `backtest.engine.PrefixView` already stops a
strategy from *indexing* a bar that has not closed, but it cannot stop a
strategy that reads future bars some other way (a closure over the full
candle list, a precomputed indicator array, a cache keyed by object id).
These checks catch that from the outside, by behaviour, without trusting
the strategy's code:

- `check_lookahead`: the signal at bar t computed while the full history
  exists must equal the signal computed when the history physically ends
  at t. Any difference means the strategy saw the future. A strategy
  that precomputes from the dataset it is built on (the classic
  Freqtrade bug: a centred rolling window or `shift(-1)` over the whole
  dataframe) is checked by passing `factory`, which builds the strategy
  once from the full data and once from the data truncated at t.
- `check_warmup_sensitivity`: the signal at bar t must not depend on how
  much earlier history happened to be available (as long as at least
  `warmup` bars are). `validation.study` slices every walk-forward fold
  from a different start, so a strategy whose signals drift with the
  start point gives each fold a slightly different strategy, and its
  fold returns stop being comparable.

Both are pure and deterministic. A strategy that raises is a finding,
not a crash (fail-closed: the study must not trust it).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from cointrader.backtest.engine import PrefixView
from cointrader.data.models import Candle

Strategy = Callable[[Sequence[Candle]], float]


@dataclass(frozen=True)
class SignalMismatch:
    bar_index: int
    reference: float  # signal with the full history present
    observed: float  # signal with truncated history (NaN if the call raised)
    detail: str


@dataclass(frozen=True)
class SignalCheckReport:
    check: str  # "lookahead" | "warmup_sensitivity"
    bars_checked: int
    mismatches: tuple[SignalMismatch, ...]

    @property
    def passed(self) -> bool:
        return self.bars_checked > 0 and not self.mismatches


def _sample_indices(start: int, stop: int, samples: int) -> list[int]:
    if stop <= start:
        return []
    if stop - start <= samples:
        return list(range(start, stop))
    step = (stop - 1 - start) / (samples - 1)
    return sorted({start + round(i * step) for i in range(samples)})


def _call(strategy: Strategy, history: Sequence[Candle]) -> tuple[float, str]:
    try:
        return float(strategy(history)), ""
    except Exception as exc:  # noqa: BLE001 - any failure is a finding
        return math.nan, f"strategy raised {type(exc).__name__}: {exc}"


def _differs(a: float, b: float, tolerance: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return True
    return abs(a - b) > tolerance


def check_lookahead(
    candles: Sequence[Candle],
    strategy: Optional[Strategy] = None,
    *,
    factory: Optional[Callable[[Sequence[Candle]], Strategy]] = None,
    warmup: int = 0,
    samples: int = 50,
    tolerance: float = 1e-12,
) -> SignalCheckReport:
    """Compare the signal at sampled bars with full history available vs.
    a fresh list that ends at that bar. The reference call goes through
    `PrefixView` over the full list, exactly as the backtest engine does,
    so a strategy that reaches past the view is caught. Pass exactly one
    of `strategy` (a stateless callable) or `factory` (data -> strategy)."""
    if (strategy is None) == (factory is None):
        raise ValueError("pass exactly one of strategy or factory")
    full = strategy if factory is None else factory(candles)
    mismatches: list[SignalMismatch] = []
    indices = _sample_indices(warmup, len(candles), samples)
    for t in indices:
        truncated = list(candles[: t + 1])
        reference, ref_err = _call(full, PrefixView(candles, t + 1))
        observed, obs_err = _call(strategy if factory is None else factory(truncated), truncated)
        if ref_err or obs_err or _differs(reference, observed, tolerance):
            mismatches.append(SignalMismatch(
                t, reference, observed,
                ref_err or obs_err or "signal changed when future bars were removed",
            ))
    return SignalCheckReport("lookahead", len(indices), tuple(mismatches))


def check_warmup_sensitivity(
    candles: Sequence[Candle],
    strategy: Strategy,
    *,
    warmup: int,
    samples: int = 50,
    tolerance: float = 1e-12,
) -> SignalCheckReport:
    """Compare the signal at sampled bars computed from the full history
    vs. from only the shortest history the engine ever hands it: at its
    first decision (`run_backtest(..., warmup=W)`, t = W) a strategy sees
    W + 1 bars. Bars earlier than that are not checked. `warmup` must be
    the strategy's declared warm-up, the same number `validation.study`
    relies on."""
    if warmup < 1:
        raise ValueError("warmup must be >= 1")
    mismatches: list[SignalMismatch] = []
    window = warmup + 1
    indices = _sample_indices(warmup, len(candles), samples)
    for t in indices:
        reference, ref_err = _call(strategy, list(candles[: t + 1]))
        observed, obs_err = _call(strategy, list(candles[t + 1 - window: t + 1]))
        if ref_err or obs_err or _differs(reference, observed, tolerance):
            mismatches.append(SignalMismatch(
                t, reference, observed,
                ref_err or obs_err or f"signal depends on history older than warmup={warmup}",
            ))
    return SignalCheckReport("warmup_sensitivity", len(indices), tuple(mismatches))
