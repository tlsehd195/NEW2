from __future__ import annotations

import random

import pytest

from cointrader.validation.preregistration import PreregistrationLog
from cointrader.validation.reality_check_spa import excess_returns_vs_benchmark, hansen_spa, white_reality_check
from cointrader.validation.signal_ic import indicator_ic, indicator_redundancy_matrix, rank_average, spearman
from cointrader.validation.trial_ledger import prior_trials
from tests.helpers import make_candles


def _noise(n_cand=5, n=24, seed=0):
    rng = random.Random(seed)
    return {f"c{i}": [rng.gauss(0, 0.05) for _ in range(n)] for i in range(n_cand)}


def test_reality_check_not_significant_on_pure_noise():
    assert white_reality_check(_noise(), seed=1).p_value > 0.1
    assert hansen_spa(_noise(), seed=1).p_value > 0.1


def test_reality_check_detects_real_edge_and_is_seeded():
    data = _noise()
    data["good"] = [0.08 + r * 0.2 for r in data["c0"]]
    r1, r2 = white_reality_check(data, seed=3), white_reality_check(data, seed=3)
    assert r1 == r2 and r1.best_candidate == "good" and r1.p_value < 0.05
    assert hansen_spa(data, seed=3).p_value < 0.05


def test_excess_and_validation_errors():
    assert excess_returns_vs_benchmark({"a": [0.1, 0.2]}, [0.05, 0.05]) == {"a": [0.05, 0.15000000000000002]}
    with pytest.raises(ValueError):
        excess_returns_vs_benchmark({"a": [0.1]}, [0.0, 0.0])
    with pytest.raises(ValueError):
        white_reality_check({"a": [0.1]})


def test_spearman_and_ranks():
    assert rank_average([3, 1, 1]) == [3.0, 1.5, 1.5]
    assert spearman([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None
    assert spearman([1, 2], [1, 2]) is None


def test_indicator_ic_and_redundancy_run():
    candles = make_candles(500, seed=7, vol=0.01)
    ic = indicator_ic(candles, horizon=5, window=60, step=30)
    assert set(ic) and all(s.n_windows > 0 for s in ic.values())
    red = indicator_redundancy_matrix(candles)
    assert all(v is None or -1 <= v <= 1 for v in red.values())


def test_prior_trials_counts_registered_minus_current(tmp_path):
    import json
    path = tmp_path / "p.jsonl"
    rows = [{"hypothesis_id": f"H-{i}", "candidates": ["x", "y", "z"]} for i in (1, 2)]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    log = PreregistrationLog(path)
    assert log.total_registered_candidates() == 6
    assert prior_trials(log, ["x", "y", "z"]) == 3
    assert prior_trials(PreregistrationLog(tmp_path / "none.jsonl"), ["a"]) == 0
