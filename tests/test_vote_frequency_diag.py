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


def test_engine_view_reports_counts_per_fold_and_no_returns():
    from datetime import timedelta
    from cointrader.backtest.event_engine import FuturesTerms
    from cointrader.data.models import Timeframe
    from cointrader.risk.engine import RiskEngine
    from cointrader.settings import load_markets, load_risk
    from cointrader.validation.walk_forward import generate_walk_forward_windows

    candles = make_candles(420, timeframe=Timeframe.DAY_1, seed=4, market="SOLUSDT")
    strat = IndicatorVote()
    start = candles[strat.warmup].open_time
    windows = generate_walk_forward_windows(start, candles[-1].open_time, train=timedelta(days=20),
                                            test=timedelta(days=10), step=timedelta(days=10))
    filters, _, _ = load_markets()
    out = diag.engine_view(candles, strat, windows, RiskEngine(load_risk(), filters), FuturesTerms(assume_no_funding=True))
    assert out["folds"] == len(windows) > 0
    assert all({"engine_signals", "trades", "rejected_entries"} <= set(f) for f in out["per_fold"])
    assert "return" not in str(out.keys()).lower()


def test_daytrade_kind_is_wired_to_the_15m_strategy_and_policy():
    cls, tf, horizons = diag.KINDS["daytrade"]
    s = cls()
    assert (tf, horizons, s.family, s.timeframe) == ("15m", (16, 48), "daytrade", "15m")
    assert diag.KINDS["swing"][1:] == ("1d", (5, 10))  # the original diagnostic is unchanged
    from cointrader.validation.policies import POLICIES
    assert "daytrade" in POLICIES


def test_diagnose_runs_on_the_daytrade_strategy_with_its_own_vol_windows():
    from datetime import timedelta
    from cointrader.data.models import Timeframe
    from cointrader.strategies.daytrade import DayTradeVote
    strat = DayTradeVote(horizon=16, fit_lookback=200, vol_short=8, vol_long=48)  # small windows keep the test fast
    candles = make_candles(strat.warmup + 60, timeframe=Timeframe.MINUTE_15, seed=5, market="BTCUSDT")
    out = diag.diagnose(candles, strat, strat.warmup)
    n = len(candles) - strat.warmup
    assert out["bars_evaluated"] + sum(v for k, v in out["reason_counts"].items()
                                       if k.startswith(("no_verdict", "atr_or_vol"))) == n
    assert out["strategy_id"].startswith("daytrade_indicator_vote")
