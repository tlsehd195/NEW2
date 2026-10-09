import json

from cointrader.validation.ledger_check import check_ledgers

W = lambda name, market, a, b: {"name": name, "market": market, "start": f"{a}T00:00:00+00:00",  # noqa: E731
                                "end": f"{b}T00:00:00+00:00", "observed_by": [], "note": ""}
SCREEN = {"market": "BTCUSDT", "timeframe": "15m", "start": "2023-01-01T00:00:00+00:00",
          "validation_end": "2023-10-01T00:00:00+00:00", "end": "2024-01-01T00:00:00+00:00"}
RES = {"market": "BTCUSDT", "start": "2023-10-01T00:00:00+00:00", "end": "2024-01-01T00:00:00+00:00", "note": ""}


def _setup(tmp_path, locked=(), reserved=(), screening=(), prereg=(), status=()):
    c, r = tmp_path / "configs", tmp_path / "research"
    c.mkdir()
    r.mkdir()
    (c / "locked_windows.json").write_text(json.dumps(list(locked)), encoding="utf-8")
    (c / "reserved_windows.json").write_text(json.dumps(list(reserved)), encoding="utf-8")
    for name, rows in (("screening", screening), ("preregistration", prereg), ("candidate_status", status)):
        (r / f"{name}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")
    return check_ledgers(c, r)


def codes(problems, level="ERROR"):
    return sorted(p.code for p in problems if p.level == level)


def test_clean_ledgers(tmp_path):
    assert _setup(tmp_path, reserved=[RES], screening=[SCREEN]) == []


def test_duplicate_and_child_mismatch(tmp_path):
    p = _setup(tmp_path, locked=[W("TEST-1", "A", "2020-01-01", "2020-02-01"), W("TEST-1", "A", "2020-01-01", "2020-02-01"),
                                 W("TEST-2", "XS", "2021-01-01", "2021-02-01"), W("TEST-2:A", "A", "2021-01-01", "2021-03-01"),
                                 W("TEST-9:B", "B", "2021-01-01", "2021-03-01")])
    assert codes(p) == ["locked-child-range", "locked-duplicate-name", "locked-orphan-child"]


def test_reserved_overlaps_locked_and_duplicates(tmp_path):
    p = _setup(tmp_path, locked=[W("TEST-1", "*", "2023-11-01", "2023-12-01")], reserved=[RES, RES])
    assert codes(p) == ["reserved-duplicate", "reserved-in-locked", "reserved-in-locked"]


def test_screening_reading_reserved_or_locked(tmp_path):
    late = {**SCREEN, "validation_end": "2023-12-01T00:00:00+00:00"}
    p = _setup(tmp_path, locked=[W("TEST-1", "BTCUSDT", "2023-03-01", "2023-04-01")], reserved=[RES], screening=[late])
    assert codes(p) == ["screening-reads-locked", "screening-reads-reserved"]


def test_unreserved_screening_is_warning(tmp_path):
    assert codes(_setup(tmp_path, screening=[SCREEN]), "WARN") == ["screening-unreserved"]


def test_prereg_without_lock_warns_and_unknown_status_errors(tmp_path):
    h = {"hypothesis_id": "H-1", "market": "BTCUSDT", "data_end": "2024-01-01T00:00:00+00:00"}
    p = _setup(tmp_path, prereg=[h, h], status=[{"candidate_id": "c", "hypothesis_id": "H-9"}])
    assert codes(p) == ["prereg-duplicate", "status-unknown-hypothesis"]
    assert codes(p, "WARN") == ["prereg-not-locked", "prereg-not-locked"]


def test_real_repo_ledgers_are_consistent():
    assert codes(check_ledgers()) == []
