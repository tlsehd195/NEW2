"""Integrity checks every `SignalStrategy` must pass before its results
count (ADR-0014 idea, extended to the `Signal` contract of ADR-0015).

- look-ahead: the signal at bar t with the full series present (through
  `PrefixView`, exactly as the engines call it) must equal the signal
  computed on a list that physically ends at t;
- warm-up sensitivity: the signal must not change when only the declared
  `warmup` bars of history are available instead of everything;
- determinism: the same input twice gives the same signal;
- robustness: a strategy that raises on any sampled input is a finding.

A failed check is recorded, never skipped: `validation.signal_study`
refuses to promote a candidate whose report did not pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from cointrader.backtest.engine import PrefixView
from cointrader.data.models import Candle
from cointrader.validation.lookahead import _sample_indices


@dataclass(frozen=True)
class IntegrityFinding:
    check: str
    bar_index: int
    detail: str


@dataclass(frozen=True)
class IntegrityReport:
    strategy_id: str
    bars_checked: int
    findings: tuple[IntegrityFinding, ...]

    @property
    def passed(self) -> bool:
        return self.bars_checked > 0 and not self.findings


def _signal(strategy, history):
    try:
        return strategy.signal(history), ""
    except Exception as exc:  # noqa: BLE001 - any failure is a finding
        return None, f"raised {type(exc).__name__}: {exc}"


def check_signal_strategy(candles: Sequence[Candle], strategy, *, samples: int = 40,
                          factory: Optional[Callable[[Sequence[Candle]], object]] = None) -> IntegrityReport:
    """`factory` (data -> strategy) is for strategies built FROM data
    (precomputed indicators, fitted weights): the truncated run then gets
    a strategy built only from the truncated data, which is how a
    closure over future bars is caught."""
    warmup = strategy.warmup
    findings: list[IntegrityFinding] = []
    indices = _sample_indices(warmup, len(candles), samples)
    for t in indices:
        ref, err = _signal(strategy, PrefixView(candles, t + 1))
        if err:
            findings.append(IntegrityFinding("robustness", t, err))
            continue
        cut = list(candles[: t + 1])
        truncated, err = _signal(strategy if factory is None else factory(cut), cut)
        if err or truncated != ref:
            findings.append(IntegrityFinding("lookahead", t, err or "signal changed when future bars were removed"))
        short, err = _signal(strategy, list(candles[t + 1 - warmup: t + 1]))
        if err or short != ref:
            findings.append(IntegrityFinding("warmup_sensitivity", t,
                                             err or f"signal depends on history older than warmup={warmup}"))
        again, err = _signal(strategy, PrefixView(candles, t + 1))
        if err or again != ref:
            findings.append(IntegrityFinding("determinism", t, err or "same input gave a different signal"))
    return IntegrityReport(strategy.strategy_id, len(indices), tuple(findings))
