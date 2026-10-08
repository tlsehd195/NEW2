from __future__ import annotations

import dataclasses
import math
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.evolution.status import (
    HUMAN_ONLY_STATUSES, CandidateStatus, Evidence, PromotionCriteria, approve_for_live, next_automatic_status,
)
from cointrader.journal.trade_journal import DecisionRecord, OutcomeRecord, TradeJournal
from cointrader.live.approval import (
    REQUIRED_CONFIRMATION_TOKEN, LiveActivationApproval, approval_to_payload, payload_to_approval,
)
from cointrader.live.config import HealthStatus, LiveTradingConfig
from cointrader.live.kill_switch import (
    KillSwitchLog, KillSwitchTriggerContext, engage_kill_switch, evaluate_kill_switch_triggers, release_kill_switch,
)
from cointrader.live.safety_gate import SafetyGateContext, evaluate_safety_gate
from cointrader.notifications.discord_webhook import format_swing_study_report, truncate_for_discord
from cointrader.risk.sizing import SizingConfig, realized_volatility, size_spot_position
from tests.helpers import T0

OK = HealthStatus.HEALTHY


def approval(**kw) -> LiveActivationApproval:
    base = dict(approved_by="동동", approved_at=T0, confirmation_token=REQUIRED_CONFIRMATION_TOKEN,
                checklist_completed=True, strategy_evidence_reviewed=True)
    base.update(kw)
    return LiveActivationApproval(**base)


class TestSizing:
    def test_inverse_volatility_and_cap(self):
        low = size_spot_position(signal_exposure=1.0, equity=1e7, annual_volatility=0.4)
        high = size_spot_position(signal_exposure=1.0, equity=1e7, annual_volatility=0.8)
        assert low.target_weight == pytest.approx(0.25)  # capped at max_weight
        assert high.target_weight == pytest.approx(0.25)
        cfg = SizingConfig(max_weight=1.0)
        assert size_spot_position(signal_exposure=1.0, equity=1e7, annual_volatility=0.4, config=cfg).target_weight == pytest.approx(0.5)
        assert size_spot_position(signal_exposure=1.0, equity=1e7, annual_volatility=0.8, config=cfg).target_weight == pytest.approx(0.25)

    @pytest.mark.parametrize("kw,reason", [
        (dict(signal_exposure=None), "signal_missing"),
        (dict(signal_exposure=math.nan), "signal_missing"),
        (dict(signal_exposure=1.5), "signal_out_of_range"),
        (dict(equity=None), "equity_unknown"),
        (dict(annual_volatility=0.0), "volatility_unknown"),
        (dict(equity=1000.0), "below_min_order_value"),
    ])
    def test_fail_closed(self, kw, reason):
        args = dict(signal_exposure=1.0, equity=1e7, annual_volatility=0.5)
        args.update(kw)
        r = size_spot_position(**args)
        assert r.reason == reason and r.target_value == 0.0 and not r.trade_allowed

    def test_realized_volatility(self):
        assert realized_volatility([0.01], 8760) is None
        assert realized_volatility([0.01, -0.01], 1) == pytest.approx(math.sqrt(0.0002))


class TestApproval:
    @pytest.mark.parametrize("kw", [
        dict(approved_by="AI"), dict(approved_by="claude"), dict(approved_by=""),
        dict(confirmation_token="yes"), dict(checklist_completed=False),
        dict(strategy_evidence_reviewed=False), dict(approved_at=datetime(2024, 1, 1)),
    ])
    def test_invalid_approvals_rejected(self, kw):
        with pytest.raises(ValueError):
            approval(**kw)

    def test_payload_round_trip_revalidates(self):
        payload = approval_to_payload(approval())
        assert payload_to_approval(payload) == approval()
        payload["confirmation_token"] = "tampered"
        with pytest.raises(ValueError):
            payload_to_approval(payload)


class TestKillSwitch:
    def _ctx(self, **kw):
        base = dict(exchange_health=OK, data_feed_health=OK, account_state_known=True,
                    position_state_known=True, daily_loss=0.0, orders_in_last_hour=0,
                    config=LiveTradingConfig(max_daily_loss=100_000, max_orders_per_hour=20))
        base.update(kw)
        return KillSwitchTriggerContext(**base)

    def test_healthy_state_does_not_trigger(self):
        assert evaluate_kill_switch_triggers(self._ctx()) is None

    @pytest.mark.parametrize("kw,reason", [
        (dict(exchange_health=None), "exchange_health_unknown"),
        (dict(data_feed_health=HealthStatus.UNAVAILABLE), "data_feed_health_unavailable"),
        (dict(account_state_known=False), "account_state_unknown"),
        (dict(daily_loss=None), "daily_loss_unmeasurable"),
        (dict(daily_loss=100_000), "daily_loss_limit_breached"),
        (dict(orders_in_last_hour=None), "order_frequency_unmeasurable"),
        (dict(orders_in_last_hour=21), "abnormal_order_frequency"),
    ])
    def test_triggers(self, kw, reason):
        assert evaluate_kill_switch_triggers(self._ctx(**kw)) == reason

    def test_engage_is_automatic_release_needs_human(self, tmp_path):
        log = KillSwitchLog(tmp_path / "ks.jsonl")
        assert log.is_engaged()  # no log at all => fail-closed
        log.record(release_kill_switch(approval=approval(), occurred_at=T0, configuration_version="v"))
        assert not log.is_engaged()
        log.record(engage_kill_switch(reason="x", occurred_at=T0 + timedelta(1), configuration_version="v"))
        assert log.is_engaged()
        assert log.latest().triggered_by == "SYSTEM"
        with pytest.raises((ValueError, TypeError)):
            release_kill_switch(approval=None, occurred_at=T0, configuration_version="v")

    def test_corrupt_log_reads_as_engaged(self, tmp_path):
        path = tmp_path / "ks.jsonl"
        path.write_text("{not json\n", encoding="utf-8")
        assert KillSwitchLog(path).is_engaged()


class TestSafetyGate:
    def _ctx(self, **kw):
        base = dict(
            as_of=T0,
            config=LiveTradingConfig(environment="live", live_trading_enabled=True, max_daily_loss=1e5,
                                     max_orders_per_hour=20, max_position_weight=0.25),
            approval=approval(), strategy_status="APPROVED", kill_switch_engaged=False,
            exchange_health=OK, data_feed_health=OK, account_state_known=True, position_state_known=True,
        )
        base.update(kw)
        return SafetyGateContext(**base)

    def test_all_conditions_met_passes(self):
        assert evaluate_safety_gate(self._ctx()).passed

    def test_default_config_fails_closed(self):
        r = evaluate_safety_gate(self._ctx(config=LiveTradingConfig()))
        assert not r.passed
        assert {"environment_not_live", "live_trading_not_enabled",
                "risk_limit_not_configured_max_daily_loss"} <= set(r.failed_conditions)

    @pytest.mark.parametrize("kw,cond", [
        (dict(approval=None), "activation_approval_missing_or_invalid"),
        (dict(strategy_status="OOS_TESTED"), "strategy_not_human_approved"),
        (dict(kill_switch_engaged=True), "kill_switch_engaged"),
        (dict(data_feed_health=None), "data_feed_not_healthy"),
    ])
    def test_each_condition_blocks(self, kw, cond):
        r = evaluate_safety_gate(self._ctx(**kw))
        assert not r.passed and cond in r.failed_conditions


class TestEvolution:
    C = PromotionCriteria()

    def test_full_automatic_path_stops_at_oos_tested(self):
        ev = Evidence(True, 20, 0.1, 0.97, 0.05)
        status = CandidateStatus.CANDIDATE
        for _ in range(10):
            status, _ = next_automatic_status(status, ev, self.C)
        assert status is CandidateStatus.OOS_TESTED

    def test_no_automatic_path_reaches_human_only_status(self):
        evidences = [Evidence(), Evidence(True), Evidence(True, 20, 0.0, 1.0, 1.0), Evidence(True, 1, 0.9, 0.0, -1.0)]
        for status in CandidateStatus:
            for ev in evidences:
                nxt, _ = next_automatic_status(status, ev, self.C)
                if nxt in HUMAN_ONLY_STATUSES:
                    assert nxt is status  # can only "stay", never be promoted into it

    @pytest.mark.parametrize("ev,reason", [
        (Evidence(True, 5, 0.1, 0.99), "too_few_folds"),
        (Evidence(True, 20, 0.5, 0.99), "pbo_above_max"),
        (Evidence(True, 20, 0.1, 0.5), "deflated_sharpe_below_min"),
    ])
    def test_rejections(self, ev, reason):
        assert next_automatic_status(CandidateStatus.BACKTESTED, ev, self.C) == (CandidateStatus.REJECTED, reason)

    def test_human_approval_path(self):
        t = approve_for_live("c1", CandidateStatus.OOS_TESTED, approval(), T0)
        assert t.to_status is CandidateStatus.APPROVED and t.decided_by == "동동"
        with pytest.raises(ValueError):
            approve_for_live("c1", CandidateStatus.VALIDATED, approval(), T0)


class TestJournal:
    def test_append_only_round_trip(self, tmp_path):
        j = TradeJournal(tmp_path / "j.jsonl")
        d = DecisionRecord("KRW-BTC", "ma", "OOS_TESTED", T0 + timedelta(hours=1), T0, 1.0, 0.2, "sized", "paper")
        j.record_decision(d)
        j.record_outcome(OutcomeRecord(d.decision_id, T0 + timedelta(hours=2), 0.01, 5e7, 25.0, None))
        rows = list(j.rows())
        assert [r["kind"] for r in rows] == ["decision", "outcome"]
        assert rows[1]["decision_id"] == rows[0]["decision_id"]

    def test_decision_cannot_use_unclosed_bar(self):
        with pytest.raises(ValueError):
            DecisionRecord("KRW-BTC", "ma", "x", T0, T0, 1.0, 0.2, "sized", "paper")


class TestDiscord:
    def test_format_skips_missing_fields(self):
        text = format_swing_study_report({"market": "KRW-BTC", "pbo": 0.31,
                                          "candidates": [{"name": "a", "deflated_sharpe": None}]})
        assert "PBO" in text and "a: DSR n/a" in text and "TEST 구간" not in text

    def test_truncate_counts_utf16(self):
        assert len(truncate_for_discord("\U0001f600" * 1500).encode("utf-16-le")) // 2 <= 2000
