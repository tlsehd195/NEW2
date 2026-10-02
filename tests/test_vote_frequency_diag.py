"""Plumbing test for scripts/diagnose_vote_frequency.py on generated candles (not a validation result)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from cointrader.strategies.indicator_vote import IndicatorVote
from tests.helpers import make_candles

_spec = importlib.util.spec_from_file_location("diag", Path(__file__).resolve().parents[1] / "scripts" / "diagnose_vote_frequency.py")
diag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(diag)


def test_diagnose_counts_every_evaluated_bar_once():
    candles = make_candles(420, seed=3)
    strat = IndicatorVote()
    out = diag.diagnose(candles, strat, strat.warmup)
    n = len(candles) - strat.warmup
    assert out["bars_evaluated"] + sum(v for k, v in out["reason_counts"].items()
                                       if k.startswith(("no_verdict", "atr_or_vol"))) == n
    assert sum(v for k, v in out["reason_counts"].items() if not k.startswith(("no_verdict", "atr_or_vol"))) \
        == out["bars_evaluated"]
    assert out["threshold_grid_signal_counts_only"]["enter0.55_agree0.5"]["entry_bars"] \
        >= out["threshold_grid_signal_counts_only"]["enter0.65_agree0.7"]["entry_bars"]
