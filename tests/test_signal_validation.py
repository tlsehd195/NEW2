from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.backtest.event_engine import FuturesTerms
from cointrader.data.models import Candle, Timeframe
from cointrader.evolution.status import CandidateStatus, Evidence, PromotionCriteria, StatusTransition
from cointrader.research.hypotheses import Budget, HypothesisRefused, check_new_hypothesis, register_checked
from cointrader.research.lifecycle import CandidateLedger
from cointrader.risk.engine import RiskConfig, RiskEngine, SymbolFilters
from cointrader.strategies.base import Signal, flat
from cointrader.strategies.swing import BollingerReversion, TrendEmaAtr
from cointrader.validation.integrity import check_signal_strategy
from cointrader.validation.locked_windows import LockedWindow, LockedWindowViolation, load_locked_windows
from cointrader.validation.policies import ValidationPolicy
from cointrader.validation.preregistration import Hypothesis, PreregistrationLog
from cointrader.validation.signal_study import criteria_from, lock_test_window, run_signal_study
from cointrader.validation.time_alignment import closed_higher_bars
from tests.helpers import make_candles

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def usdt_candles(n, **kw):
    cs = make_candles(n, market="BTCUSDT", volume=5_000.0, **kw)
    return [Candle(c.market, c.timeframe, c.open_time, c.open / 1000, c.high / 1000, c.low / 1000, c.close / 1000,
                   c.volume, c.source, c.received_at) for c in cs]


# ------------------------------------------------------------ integrity --
@dataclass
class Cheater:
    """Reads the future through a closure over the full series."""

    full: list
    strategy_id: str = "cheater"
    family: str = "swing"
    version: str = "1"
    timeframe: str = "1h"
    warmup: int = 5
    parameters: dict = None

    def signal(self, history, context=None):
        i = len(history) - 1
        if i + 1 < len(self.full) and self.full[i + 1].close > history[-1].close:
            return Signal(1, strength=1.0, reason="peek", stop_distance=1.0, regime="RANGE")
        return flat("x", regime="RANGE")


@dataclass
class OldHistoryDependent(Cheater):
    strategy_id: str = "old_history"

    def signal(self, history, context=None):
        return flat("x", regime="RANGE", features={"n": len(history)})


def test_integrity_passes_for_real_strategies():
    cs = usdt_candles(600, seed=2)
    for s in (TrendEmaAtr(), BollingerReversion()):
        rep = check_signal_strategy(cs, s, samples=15)
        assert rep.passed, rep.findings[:3]


def test_integrity_catches_lookahead_and_warmup_dependence():
    cs = usdt_candles(200, seed=3)
    rep = check_signal_strategy(cs, Cheater(cs), samples=20, factory=lambda data: Cheater(list(data)))
    assert not rep.passed and any(f.check == "lookahead" for f in rep.findings)
    rep = check_signal_strategy(cs, OldHistoryDependent(cs), samples=20)
    assert any(f.check == "warmup_sensitivity" for f in rep.findings)


# -------------------------------------------------------- time alignment --
def test_only_closed_complete_higher_timeframe_bars_are_visible():
    five = make_candles(36, timeframe=Timeframe.MINUTE_5, start=T0, market="BTCUSDT")
    as_of = T0 + timedelta(hours=2, minutes=55)  # third hour still forming
    res = closed_higher_bars(five, Timeframe.HOUR_1, as_of)
    assert [b.open_time for b in res.bars] == [T0, T0 + timedelta(hours=1)]
    assert res.bars[0].open == five[0].open and res.bars[0].close == five[11].close
    assert res.bars[0].source.startswith("resampled:")
    gappy = five[:5] + five[6:]
    res = closed_higher_bars(gappy, Timeframe.HOUR_1, T0 + timedelta(hours=3))
    assert res.incomplete_buckets == (T0,) and res.bars[0].open_time == T0 + timedelta(hours=1)
    with pytest.raises(ValueError):
        closed_higher_bars(five, Timeframe.MINUTE_3, as_of)


# ------------------------------------------------------------ the study --
POLICY = ValidationPolicy("swing", fold_train=timedelta(days=10), fold_test=timedelta(days=4), num_groups=8,
                          integrity_samples=8)


def setup_study(tmp_path, n=2400):
    cs = usdt_candles(n, seed=7, vol=0.01)
    cands = [TrendEmaAtr(), BollingerReversion()]
    hyp = Hypothesis("H-9001", "test", "BTCUSDT", "1h", cs[0].open_time, cs[-1].open_time + timedelta(hours=1),
                     tuple(c.strategy_id for c in cands), {"max_pbo": 0.2, "min_dsr": 0.95, "min_test_excess_return": 0.0},
                     "tester", NOW)
    log = PreregistrationLog(tmp_path / "prereg.jsonl")
    log.register(hyp)
    risk = RiskEngine(RiskConfig(), {"BTCUSDT": SymbolFilters("BTCUSDT", 0.01, 0.001, 0.001, 1.0)})
    return cs, cands, hyp, log, risk


def test_signal_study_end_to_end_and_lock(tmp_path):
    cs, cands, hyp, log, risk = setup_study(tmp_path)
    report = run_signal_study(hyp, log, cs, cands, (), policy=POLICY, risk=risk,
                              futures=FuturesTerms(assume_no_funding=True))
    assert report.fold_count >= 8 and 0.0 <= report.pbo <= 1.0
    assert report.must_lock_test_window and report.label.startswith("BACKTEST")
    for c in report.candidates:
        assert c.integrity_passed
        assert len(c.fold_returns) == report.fold_count
        assert set(c.test_summary["cost_breakdown"]) >= {"gross_pnl", "fees", "net_pnl"}
    registry = tmp_path / "locked.json"
    w = lock_test_window(report, "unit test", registry)
    assert w.name == "TEST-9001" and load_locked_windows(registry)[0].start == report.test_start
    with pytest.raises(LockedWindowViolation):
        run_signal_study(hyp, log, cs, cands, load_locked_windows(registry), policy=POLICY, risk=risk,
                         futures=FuturesTerms(assume_no_funding=True))


def test_signal_study_refuses_unregistered_or_changed(tmp_path):
    cs, cands, hyp, log, risk = setup_study(tmp_path)
    with pytest.raises(ValueError, match="differ"):
        run_signal_study(hyp, log, cs, cands[:1], (), policy=POLICY, risk=risk,
                         futures=FuturesTerms(assume_no_funding=True))
    other = Hypothesis("H-9002", "x", "BTCUSDT", "1h", hyp.data_start, hyp.data_end, hyp.candidates,
                       hyp.success_criteria, "t", NOW)
    with pytest.raises(ValueError, match="never pre-registered"):
        run_signal_study(other, log, cs, cands, (), policy=POLICY, risk=risk,
                         futures=FuturesTerms(assume_no_funding=True))
    with pytest.raises(ValueError, match="scalp"):
        run_signal_study(hyp, log, cs, cands, (), policy=ValidationPolicy("scalp", timedelta(days=1), timedelta(days=1)),
                         risk=risk, futures=FuturesTerms(assume_no_funding=True))


# ----------------------------------------------------------- lifecycle --
def test_ledger_advances_automatically_but_never_to_human_statuses(tmp_path):
    ledger = CandidateLedger(tmp_path / "status.jsonl")
    crit = PromotionCriteria(min_folds=8)
    good = Evidence(True, 20, 0.1, 0.97, 0.05)
    written = ledger.advance("c1", good, crit, NOW, hypothesis_id="H-9001")
    assert [t.to_status for t in written] == [CandidateStatus.BACKTESTED, CandidateStatus.VALIDATED,
                                              CandidateStatus.OOS_TESTED]
    assert ledger.current("c1") is CandidateStatus.OOS_TESTED
    assert ledger.advance("c1", good, crit, NOW) == []  # nothing automatic beyond OOS_TESTED
    ledger.advance("c2", Evidence(True, 20, 0.5, 0.97, 0.05), crit, NOW)
    assert ledger.current("c2") is CandidateStatus.REJECTED
    with pytest.raises(PermissionError):
        ledger._append(StatusTransition("c1", CandidateStatus.OOS_TESTED, CandidateStatus("APPROVED"), "x", "SYSTEM",
                                        NOW), None)


def test_integrity_failure_yields_no_walk_forward_evidence(tmp_path):
    from cointrader.validation.signal_study import SignalCandidateReport, SignalStudyReport
    c = SignalCandidateReport("x", False, ("lookahead",), (0.1,), (1,), 0.1, 0.99, 0.2, 0.0, {})
    r = SignalStudyReport("H", "swing", "BTCUSDT", "1h", NOW, NOW, NOW, NOW, 20, 0.0, 5, (c,))
    ev = r.evidence_for("x")
    assert ev.pbo is None and ev.deflated_sharpe is None


# ---------------------------------------------------------- guardrails --
def hyp(hid, cands, market="BTCUSDT", by="tester", at=NOW):
    return Hypothesis(hid, "s", market, "1h", T0, T0 + timedelta(days=300), tuple(cands),
                      {"max_pbo": 0.2, "min_dsr": 0.95, "min_test_excess_return": 0.0}, by, at)


RATIONALE = "Regime-conditioned entries with ATR stops; differs from H-0007 grid by regime gate."


def row(h):
    return {"hypothesis_id": h.hypothesis_id, "candidates": list(h.candidates), "registered_at": h.registered_at.isoformat()}


def test_dead_families_refused():
    with pytest.raises(HypothesisRefused, match="dead family"):
        check_new_hypothesis(hyp("H-1", ["ts_momentum_28"]), [], [], rationale=RATIONALE)
    with pytest.raises(HypothesisRefused, match="dead family"):
        check_new_hypothesis(hyp("H-1", ["funding_carry_3_0.0001"]), [], [], rationale=RATIONALE)


def test_same_grid_on_another_asset_refused_unless_human_replication():
    first = hyp("H-1", ["swing_a", "swing_b"])
    again = hyp("H-2", ["swing_b", "swing_a"], market="ETHUSDT")
    with pytest.raises(HypothesisRefused, match="identical candidate set"):
        check_new_hypothesis(again, [row(first)], [], rationale=RATIONALE)
    check_new_hypothesis(again, [row(first)], [], rationale=RATIONALE, replication=True)
    robot = hyp("H-3", ["swing_a", "swing_b"], by="RESEARCH_LOOP")
    with pytest.raises(HypothesisRefused):
        check_new_hypothesis(robot, [row(first)], [], rationale=RATIONALE, replication=True)


def test_locked_observed_candidates_rationale_and_budget():
    w = LockedWindow("TEST-X", "BTCUSDT", T0, T0 + timedelta(days=1), ("swing_a",), "n")
    with pytest.raises(HypothesisRefused, match="already observed"):
        check_new_hypothesis(hyp("H-1", ["swing_a"]), [], [w], rationale=RATIONALE)
    with pytest.raises(HypothesisRefused, match="rationale"):
        check_new_hypothesis(hyp("H-1", ["swing_z"]), [], [], rationale="because")
    prior = [row(hyp(f"H-{i}", [f"swing_{i}"], at=NOW - timedelta(days=i))) for i in range(3)]
    with pytest.raises(HypothesisRefused, match="budget"):
        check_new_hypothesis(hyp("H-9", ["swing_new"]), prior, [], rationale=RATIONALE, budget=Budget(3))
    # per-kind buckets stay independent when the combined cap is not the binding one
    check_new_hypothesis(hyp("H-9", ["scalp_new"]), prior, [], rationale=RATIONALE,
                         budget=Budget(3, max_all_kinds=99))


def test_combined_cap_blocks_a_new_kind_while_other_kinds_fill_the_window():
    prior = [row(hyp(f"H-{i}", [f"swing_{i}"], at=NOW - timedelta(days=i))) for i in range(3)]
    with pytest.raises(HypothesisRefused, match="combined registration cap"):
        check_new_hypothesis(hyp("H-9", ["daytrade_new"]), prior, [], rationale=RATIONALE)
    # the 30-day window frees it: 31 days later every swing row has expired
    later = hyp("H-9", ["daytrade_new"], at=NOW + timedelta(days=31))
    check_new_hypothesis(later, prior, [], rationale=RATIONALE)
    # an explicit ADR-backed raise lifts both limits together (run_validation passes both)
    check_new_hypothesis(hyp("H-9", ["daytrade_new"]), prior, [], rationale=RATIONALE,
                         budget=Budget(4, max_all_kinds=4))


def test_register_checked_writes_log_and_rationale(tmp_path):
    path = tmp_path / "prereg.jsonl"
    register_checked(PreregistrationLog(path), path, hyp("H-1", ["swing_q"]), [], rationale=RATIONALE)
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["hypothesis_id"] == "H-1"
    assert "Regime" in (tmp_path / "hypothesis_rationale.jsonl").read_text(encoding="utf-8")
    # Re-running the same registration (e.g. after a data-pipeline crash) adds no second row.
    register_checked(PreregistrationLog(path), path, hyp("H-1", ["swing_q"]), [], rationale=RATIONALE)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    assert len((tmp_path / "hypothesis_rationale.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_criteria_from_hypothesis():
    c = criteria_from(hyp("H-1", ["swing_q"]), POLICY)
    assert (c.max_pbo, c.min_deflated_sharpe, c.min_folds) == (0.2, 0.95, 16)


def test_daytrade_kind_has_its_own_policy_and_is_a_known_family():
    from cointrader.strategies.registry import FAMILIES
    from cointrader.validation.policies import POLICIES
    assert "daytrade" in FAMILIES
    p = POLICIES["daytrade"]
    assert (p.family, p.fold_train, p.fold_test) == ("daytrade", timedelta(days=14), timedelta(days=7))
    assert (p.num_groups, p.min_folds) == (8, 16)  # same promotion rigor as the other kinds


# ------------------------------------------------- screening (ADR-0051) --
def test_screening_reads_no_test_and_reserves_it(tmp_path):
    from cointrader.validation.screening import ScreeningRefused, load_reserved, reserve, run_screening
    from cointrader.validation.walk_forward import build_chronological_split
    cs, cands, hyp, log, risk = setup_study(tmp_path)
    start, end = hyp.data_start, hyp.data_end
    split = build_chronological_split(start, end)
    with pytest.raises(ScreeningRefused, match="past VALIDATION"):
        run_screening(cs, cands, "BTCUSDT", start, end, (), (), policy=POLICY, risk=risk, prior_trials=0,
                      futures=FuturesTerms(assume_no_funding=True))
    in_sample = [c for c in cs if c.open_time < split.validation_end]
    rep = run_screening(in_sample, cands, "BTCUSDT", start, end, (), (), policy=POLICY, risk=risk, prior_trials=5,
                        futures=FuturesTerms(assume_no_funding=True))
    assert rep.new_reservation and (rep.reservation.start, rep.reservation.end) == (split.test_start, split.test_end)
    assert rep.label.startswith("SCREENING") and rep.trials_deflated_against >= 7
    path = tmp_path / "reserved.json"
    reserve(rep.reservation, path)
    with pytest.raises(ScreeningRefused, match="already has"):
        reserve(rep.reservation, path)
    # the same range again keeps the reservation; a range reaching into it is refused
    again = run_screening(in_sample, cands, "BTCUSDT", start, end, (), load_reserved(path), policy=POLICY, risk=risk,
                          prior_trials=0, futures=FuturesTerms(assume_no_funding=True))
    assert not again.new_reservation
    with pytest.raises(ScreeningRefused, match="reserved TEST"):
        run_screening(cs, cands, "BTCUSDT", start, end + timedelta(days=30), (), load_reserved(path), policy=POLICY,
                      risk=risk, prior_trials=0, futures=FuturesTerms(assume_no_funding=True))


def test_final_exam_gate(tmp_path):
    from cointrader.validation.screening import ReservedWindow, ScreeningLedger, ScreeningRefused, check_finalists
    ledger = ScreeningLedger(tmp_path / "s.jsonl")
    ledger.append({"market": "BTCUSDT", "timeframe": "1h", "candidates": [{"strategy_id": f"swing_{i}"} for i in "abcd"]})
    ts, te = T0 + timedelta(days=240), T0 + timedelta(days=300)
    res = [ReservedWindow("BTCUSDT", ts, te, "n")]
    check_finalists(hyp("H-1", ["swing_a", "swing_b", "swing_c"]), ledger, res, ts, te)
    for bad, why in [(hyp("H-1", ["swing_a", "swing_b", "swing_c", "swing_d"]), "at most 3"),
                     (hyp("H-1", ["swing_z"]), "never screened"),
                     (hyp("H-1", ["swing_a"], market="ETHUSDT"), "no reserved"),
                     (hyp("H-1", ["swing_a"], by="RESEARCH_LOOP"), "only a human")]:
        with pytest.raises(ScreeningRefused, match=why):
            check_finalists(bad, ledger, res, ts, te)
    with pytest.raises(ScreeningRefused, match="not BTCUSDT's"):
        check_finalists(hyp("H-1", ["swing_a"]), ledger, res, ts, te + timedelta(days=1))
