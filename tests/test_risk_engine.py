from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cointrader.live.config import HealthStatus
from cointrader.risk.engine import (
    AccountRiskState,
    EntryRequest,
    RiskConfig,
    RiskEngine,
    SymbolFilters,
    account_state_from_history,
)
from cointrader.risk.protections import ClosedTrade, EquityPoint

T0 = datetime(2026, 3, 1, 12, tzinfo=timezone.utc)
FILTERS = {"BTCUSDT": SymbolFilters("BTCUSDT", 0.1, 0.001, 0.001, 100.0)}


def req(**kw):
    base = dict(now=T0, symbol="BTCUSDT", direction=1, reference_price=50_000.0, stop_distance=500.0,
                regime="TREND_UP", atr=400.0, spread_fraction=0.0001, feed_health=HealthStatus.HEALTHY,
                strategy_id="s")
    base.update(kw)
    return EntryRequest(**base)


def acct(equity=10_000.0, peak=None, day=None, **kw):
    return AccountRiskState(equity, peak or equity, day or equity, **kw)


def engine(**kw):
    return RiskEngine(RiskConfig(**kw), FILTERS)


def test_sizes_by_risk_per_trade_and_stop_distance():
    d = engine(risk_per_trade=0.01).evaluate_entry(req(), acct())
    assert d.approved
    assert d.quantity == pytest.approx(0.2)  # 100 USDT risk / 500 stop
    assert d.risk_amount == pytest.approx(100.0)
    assert d.stop_price == 49_500.0 and d.leverage == pytest.approx(1.0)
    assert d.decision_id == engine(risk_per_trade=0.01).evaluate_entry(req(), acct()).decision_id


def test_quantity_rounds_down_to_step():
    d = engine(risk_per_trade=0.01).evaluate_entry(req(stop_distance=333.0), acct())
    assert d.quantity == 0.3  # 0.3003 -> 0.300


def test_leverage_and_notional_caps():
    d = engine(risk_per_trade=0.05, max_leverage=1.0).evaluate_entry(req(stop_distance=50.0), acct())
    assert d.approved and d.notional <= 10_000.0 + 1e-6 and d.inputs["capped_by_limits"]
    d = RiskEngine(RiskConfig(max_position_notional=2_000.0), FILTERS).evaluate_entry(req(stop_distance=50.0), acct())
    assert d.notional <= 2_000.0
    d = engine().evaluate_entry(req(), acct(open_position_notional=20_000.0))
    assert d.reasons == ("position_limit_reached",)


def test_min_notional_refused():
    d = engine(risk_per_trade=0.0001).evaluate_entry(req(stop_distance=5_000.0), acct())
    assert not d.approved and d.reasons[0].startswith("below_min")


@pytest.mark.parametrize("change,reason", [
    (dict(kill_switch_engaged=True), "kill_switch_engaged"),
    (dict(feed_health=HealthStatus.DEGRADED), "feed_degraded"),
    (dict(feed_health=None), "feed_unknown"),
    (dict(regime="UNDEFINED"), "regime_undefined"),
    (dict(atr=5_000.0), "volatility_kill"),
    (dict(spread_fraction=None), "spread_unknown"),
    (dict(spread_fraction=0.01), "spread_too_wide"),
    (dict(stop_distance=None), "stop_distance_unknown"),
    (dict(reference_price=float("nan")), "reference_price_unknown"),
    (dict(data_quality_reasons=("candle_gap",)), "data_quality:candle_gap"),
    (dict(symbol="DOGEUSDT"), "symbol_filters_unknown"),
])
def test_each_protection_blocks_entries(change, reason):
    d = engine().evaluate_entry(req(**change), acct())
    assert not d.approved and any(r.startswith(reason) for r in d.reasons), d.reasons


def test_daily_loss_and_drawdown_limits():
    d = engine(max_daily_loss=0.03).evaluate_entry(req(), acct(equity=9_600, day=10_000))
    assert "max_daily_loss_breached" in d.reasons
    d = engine(max_drawdown=0.15).evaluate_entry(req(), acct(equity=8_000, peak=10_000, day=8_000))
    assert "max_drawdown_breached" in d.reasons
    assert "equity_unknown" in engine().evaluate_entry(req(), AccountRiskState(None, None, None)).reasons


def test_consecutive_losses_and_cooldown_use_existing_protections():
    losses = tuple(ClosedTrade(T0 - timedelta(hours=h), -0.01) for h in (3, 2, 1))
    d = engine().evaluate_entry(req(), acct(closed_trades=losses))
    assert any("losing trades" in r for r in d.reasons)
    recent = (ClosedTrade(T0 - timedelta(minutes=5), 0.02),)
    d = engine().evaluate_entry(req(), acct(closed_trades=recent))
    assert any("cooldown" in r for r in d.reasons)


def test_windowed_drawdown_guard():
    pts = (EquityPoint(T0 - timedelta(hours=5), 10_000), EquityPoint(T0 - timedelta(hours=1), 9_300))
    d = engine(max_drawdown=0.5).evaluate_entry(req(), acct(equity=9_300, peak=9_300, day=9_300, equity_points=pts))
    assert any("drawdown above" in r for r in d.reasons)


def test_account_state_from_history_uses_utc_day_and_peak():
    day0 = T0.replace(hour=0)
    pts = [EquityPoint(day0 - timedelta(hours=1), 10_000), EquityPoint(day0 + timedelta(hours=1), 11_000),
           EquityPoint(T0, 10_500), EquityPoint(T0 + timedelta(hours=1), 1.0)]
    s = account_state_from_history(equity_points=pts, closed_trades=[], now=T0)
    assert (s.equity, s.peak_equity, s.day_start_equity) == (10_500, 11_000, 10_000)


def test_filters_rounding():
    f = FILTERS["BTCUSDT"]
    assert f.round_quantity(0.0019999) == 0.001
    assert f.round_price(100.04, up=True) == 100.1 and f.round_price(100.06, up=False) == 100.0
    with pytest.raises(ValueError):
        SymbolFilters("X", 0, 1, 1, 1)


def test_volatility_kill_scales_with_bar_length_but_never_loosens_below_1h():
    # ATR 4000 on a 50k price = 8% of price: extreme for an hour, ordinary for a day (limit 5% * sqrt(24) ~ 24.5%).
    vol = dict(atr=4_000.0, stop_distance=8_000.0)
    assert any(r.startswith("volatility_kill") for r in engine().evaluate_entry(req(**vol), acct()).reasons)
    assert any(r.startswith("volatility_kill")
               for r in engine().evaluate_entry(req(**vol, atr_bar=timedelta(hours=1)), acct()).reasons)
    assert not any(r.startswith("volatility_kill")
                   for r in engine().evaluate_entry(req(**vol, atr_bar=timedelta(days=1)), acct()).reasons)
    # 30% ATR on daily bars is still killed; a 1m bar keeps the 1h limit
    assert any(r.startswith("volatility_kill") for r in engine().evaluate_entry(
        req(atr=15_000.0, stop_distance=30_000.0, atr_bar=timedelta(days=1)), acct()).reasons)
    assert any(r.startswith("volatility_kill") for r in engine().evaluate_entry(
        req(**vol, atr_bar=timedelta(minutes=1)), acct()).reasons)


def test_stop_distance_is_clamped_to_one_to_two_percent_of_price():
    e = engine(risk_per_trade=0.01, stop_min_fraction=0.01, stop_max_fraction=0.02)
    tight = e.evaluate_entry(req(stop_distance=100.0), acct())  # 0.2% -> 1%
    wide = e.evaluate_entry(req(stop_distance=2_000.0), acct())  # 4% -> 2%
    inside = e.evaluate_entry(req(stop_distance=750.0), acct())  # 1.5% unchanged
    assert (tight.stop_distance, wide.stop_distance, inside.stop_distance) == (500.0, 1_000.0, 750.0)
    assert tight.stop_price == 49_500.0 and wide.stop_price == 49_000.0
    assert tight.risk_amount == pytest.approx(100.0, rel=1e-3)  # still 1% of equity
    assert tight.leverage == pytest.approx(1.0, rel=1e-2) and wide.leverage == pytest.approx(0.5, rel=1e-2)


def test_stop_clamp_config_validation():
    with pytest.raises(ValueError):
        RiskConfig(stop_min_fraction=0.01)
    with pytest.raises(ValueError):
        RiskConfig(stop_min_fraction=0.03, stop_max_fraction=0.02)
    assert RiskConfig(stop_min_fraction=0.01, stop_max_fraction=0.02).version() != RiskConfig().version()
