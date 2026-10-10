import pytest

from cointrader.risk.leverage import MarginTier
from cointrader.risk.margin_policy import MarginPolicy, liquidation_vs_stop_reason, margin_settings_mismatches

TIERS = [MarginTier(0, 50_000, 0.004, 0.0), MarginTier(50_000, None, 0.005, 50.0)]


def reason(direction=1, entry=100.0, stop=99.0, qty=10.0, tiers=TIERS, policy=MarginPolicy()):
    return liquidation_vs_stop_reason(direction=direction, entry_price=entry, stop_price=stop, quantity=qty,
                                      tiers=tiers, policy=policy)


def test_normal_day_trade_stop_passes_both_sides():
    assert reason() is None  # 1% stop, liq ~33% away at 3x isolated
    assert reason(direction=-1, stop=101.0) is None


def test_refuses_when_liquidation_near_stop():
    assert reason(stop=80.0) == "liquidation_too_close_to_stop"  # 20% stop vs ~33% liq at 3x
    assert reason(stop=99.0, policy=MarginPolicy(exchange_leverage=20)) is None  # 1% stop, liq ~4.6%
    assert reason(stop=98.0, policy=MarginPolicy(exchange_leverage=20)) == "liquidation_too_close_to_stop"


def test_fail_closed_inputs():
    assert reason(tiers=None) == "margin_tiers_unknown"
    assert reason(stop=101.0) == "stop_on_wrong_side"
    assert reason(qty=0.0) == "liquidation_inputs_invalid"
    assert reason(policy=MarginPolicy(margin_type="CROSSED")) == "liquidation_check_needs_isolated_margin"
    with pytest.raises(ValueError):
        MarginPolicy(exchange_leverage=50)


def test_symbol_config_mismatch():
    p = MarginPolicy()
    ok = [{"symbol": "BTCUSDT", "marginType": "ISOLATED", "leverage": 3}]
    assert margin_settings_mismatches(ok, ["BTCUSDT"], p) == []
    bad = [{"symbol": "BTCUSDT", "marginType": "CROSSED", "leverage": 20}]
    assert len(margin_settings_mismatches(bad, ["BTCUSDT", "ETHUSDT"], p)) == 3


def test_repo_exchange_leverage_covers_risk_max_leverage():
    """ADR-0059: isolated margin is funded by notional / exchange_leverage, so the exchange setting must be at least the
    largest position leverage the RiskEngine can produce."""
    from cointrader.settings import load_margin_policy, load_risk
    assert load_margin_policy()[0].exchange_leverage >= load_risk().max_leverage
