"""Validation policy per strategy family.

Swing and scalp share the method (pre-registration -> locked-window
check -> integrity checks -> walk-forward OOS folds -> PBO/DSR -> one
held-out TEST -> immediate lock) but not the numbers: fold lengths,
annualisation and the minimum number of trades a fold needs differ, so
each family has its own fixed policy, and a hypothesis records which one
it used.

Split vocabulary used throughout:

- TRAIN + VALIDATION: the first 80% of the range. Walk-forward folds run
  inside it; each fold's test slice is an out-of-sample (OOS) observation
  that feeds PBO/DSR. Rule-based candidates have no fitted parameters, so
  a fold's train slice only warms indicators up.
- TEST: the last 20%, run once per candidate, then LOCKED forever
  (`configs/locked_windows.json`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from cointrader.data.models import Timeframe


@dataclass(frozen=True)
class ValidationPolicy:
    family: str
    fold_train: timedelta
    fold_test: timedelta
    num_groups: int = 8
    min_folds: int = 16
    success_criteria: dict = field(default_factory=lambda: {"max_pbo": 0.2, "min_dsr": 0.95,
                                                             "min_test_excess_return": 0.0})
    integrity_samples: int = 40

    def periods_per_year(self, timeframe: Timeframe) -> float:
        return timedelta(days=365) / timeframe.delta


SWING_POLICY = ValidationPolicy("swing", fold_train=timedelta(days=60), fold_test=timedelta(days=30))
SCALP_POLICY = ValidationPolicy("scalp", fold_train=timedelta(days=3), fold_test=timedelta(days=4))

POLICIES = {"swing": SWING_POLICY, "scalp": SCALP_POLICY}
