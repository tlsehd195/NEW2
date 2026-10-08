import importlib.util
import math


def _mod():
    spec = importlib.util.spec_from_file_location("diagnose_indicator_overlap", "scripts/diagnose_indicator_overlap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_identity_matrix_counts_every_indicator():
    mod = _mod()
    eigs = mod.eigenvalues([[1.0 if i == j else 0.0 for j in range(6)] for i in range(6)])
    eff = mod.effective_count(eigs)
    assert math.isclose(eff["participation_ratio"], 6.0, rel_tol=1e-6)
    assert math.isclose(eff["entropy_exp"], 6.0, rel_tol=1e-6)


def test_identical_indicators_count_as_one():
    mod = _mod()
    eigs = mod.eigenvalues([[1.0] * 6 for _ in range(6)])
    assert math.isclose(max(eigs), 6.0, rel_tol=1e-6)
    eff = mod.effective_count(eigs)
    assert math.isclose(eff["participation_ratio"], 1.0, rel_tol=1e-6)


def test_correlation_matrix_sees_negative_copy():
    mod = _mod()
    rows = [[x, -2.0 * x, (x * 7919) % 13] for x in range(1, 40)]
    m = mod.correlation_matrix(rows)
    assert math.isclose(m[0][1], -1.0, rel_tol=1e-9)


def test_taker_flow_is_signed_imbalance_over_the_window():
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    mod = _mod()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cs = [SimpleNamespace(open_time=t0 + timedelta(minutes=15 * i), volume=10.0) for i in range(20)]
    flow = mod.taker_flow(cs, {c.open_time: 7.5 for c in cs}, bars=4)
    assert flow[2] is None and math.isclose(flow[10], 0.5)
    flow = mod.taker_flow(cs, {c.open_time: 2.5 for c in cs} | {cs[9].open_time: None}, bars=4)
    assert math.isclose(flow[8], -0.5) and flow[10] is None
