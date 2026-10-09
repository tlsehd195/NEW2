from datetime import datetime, timedelta, timezone

import pytest

from cointrader.backtest.event_engine import TradeRecord
from cointrader.journal.records import outcome_record
from cointrader.journal.store import LayeredStore
from cointrader.notifications.weekly_summary import (
    build_summary, format_summary, load_weekly_config, maybe_send_weekly, week_key,
)

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)  # a Sunday
ON = {"enabled": True, "mode": "paper", "days": 7}
ENV = {"DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/1/x"}


def _trade(i, net, exit_time, sid="s1"):
    return TradeRecord(
        trade_id=i, strategy_id=sid, symbol="BTCUSDT", direction=1, entry_time=exit_time - timedelta(hours=2),
        exit_time=exit_time, entry_reference=100.0, exit_reference=101.0, entry_fill=100.0, exit_fill=101.0,
        quantity=1.0, entry_liquidity="taker", exit_liquidity="taker", filled_fraction=1.0, gross_pnl=net + 1,
        fees=1.0, spread_cost=0.0, slippage_cost=0.0, funding=0.0, net_pnl=net, equity_at_entry=1000.0, mfe=0.01,
        mae=0.0, holding_bars=8, exit_reason="target", regime_at_entry="x", signal_reason="y",
        features_at_entry={}, risk_decision_id="r")


def _store(tmp_path, specs):
    store = LayeredStore(tmp_path)
    for i, (net, ago_days, mode) in enumerate(specs):
        t = NOW - timedelta(days=ago_days)
        store.append("outcome", outcome_record(_trade(i, net, t), mode=mode, timeframe="15m", strategy_version="v",
                                               feature_version="f"), at=t)
    return store


def test_config_is_off_by_default():
    assert load_weekly_config()["enabled"] is False


def test_summary_counts_only_this_week_and_mode(tmp_path):
    store = _store(tmp_path, [(10.0, 1, "paper"), (-4.0, 2, "paper"), (99.0, 20, "paper"), (50.0, 1, "live")])
    s = build_summary(store, NOW)
    assert s["trades"] == 2 and s["cost"]["net_pnl"] == pytest.approx(6.0)
    text = format_summary(s)
    assert "모의투자" in text and "거래 2건" in text and "+6.00" in text


def test_empty_week_says_so(tmp_path):
    assert "청산된 거래가 없습니다" in format_summary(build_summary(LayeredStore(tmp_path), NOW))


def test_nothing_sent_when_disabled_or_without_webhook(tmp_path):
    store, sent = _store(tmp_path, [(1.0, 1, "paper")]), []
    off = {**ON, "enabled": False}
    assert maybe_send_weekly(store, NOW, cfg=off, env=ENV, send=lambda u, c: sent.append(c))[0] is False
    assert maybe_send_weekly(store, NOW, cfg=ON, env={}, send=lambda u, c: sent.append(c))[0] is False
    assert sent == []


def test_sends_once_per_week(tmp_path):
    store, sent = _store(tmp_path, [(1.0, 1, "paper")]), []
    send = lambda u, c: sent.append((u, c))  # noqa: E731
    assert maybe_send_weekly(store, NOW, cfg=ON, env=ENV, send=send)[0] is True
    ok, why = maybe_send_weekly(store, NOW + timedelta(hours=1), cfg=ON, env=ENV, send=send)
    assert not ok and week_key(NOW) in why and len(sent) == 1
    assert maybe_send_weekly(store, NOW + timedelta(hours=1), cfg={**ON, "enabled": False}, env=ENV, force=True,
                             send=send)[0] is True
