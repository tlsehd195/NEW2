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
