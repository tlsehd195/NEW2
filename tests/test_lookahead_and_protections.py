from __future__ import annotations

import math
from datetime import timedelta

import pytest

from cointrader.risk.protections import (
    ClosedTrade, CooldownPeriod, EquityPoint, MaxDrawdownGuard, StoplossGuard, evaluate_protections,
)
from cointrader.strategies.adaptive_ensemble import adaptive_candidate_grid, adaptive_hysteresis_candidate_grid
from cointrader.strategies.baselines import (
    default_candidate_grid, momentum_candidate_grid, momentum_candidate_grid_daily_decorrelated,
)
from cointrader.validation.lookahead import check_lookahead, check_warmup_sensitivity
from tests.helpers import T0, make_candles


# --- look-ahead / warm-up checks -------------------------------------------

def test_strategy_precomputed_over_full_data_is_caught():
    candles = make_candles(300, seed=1)

    def build(data):  # "indicator" computed over the whole dataset, reads the next bar
        up_next = [i + 1 < len(data) and data[i + 1].close > data[i].close for i in range(len(data))]
        return lambda history: 1.0 if up_next[len(history) - 1] else 0.0

    report = check_lookahead(candles, factory=build, warmup=10)
    assert not report.passed
    assert report.mismatches


def test_strategy_reaching_past_prefix_view_is_caught():
    candles = make_candles(100, seed=7)

    def cheat(history):  # unwraps PrefixView to read the full list
        return 1.0 if getattr(history, "_candles", history)[-1].close > history[0].close else 0.0

    assert not check_lookahead(candles, cheat, warmup=5).passed


def test_needs_exactly_one_of_strategy_or_factory():
    with pytest.raises(ValueError):
        check_lookahead(make_candles(10))


def test_honest_strategy_passes_lookahead():
    candles = make_candles(300, seed=2)
    honest = lambda h: 1.0 if h[-1].close > h[0].close else 0.0  # noqa: E731
    assert check_lookahead(candles, honest).passed


def test_strategy_that_raises_is_a_finding_not_a_crash():
    candles = make_candles(50, seed=3)

    def broken(history):
        if len(history) == 30:
            raise RuntimeError("boom")
        return 0.0

    report = check_lookahead(candles, broken, samples=50)
    assert not report.passed
    assert math.isnan(report.mismatches[0].observed)
    assert "boom" in report.mismatches[0].detail


def test_unbounded_memory_strategy_is_warmup_sensitive():
    candles = make_candles(300, seed=4)
    uses_all_history = lambda h: 1.0 if h[-1].close > h[0].close else 0.0  # noqa: E731
    assert not check_warmup_sensitivity(candles, uses_all_history, warmup=20).passed


def test_empty_range_does_not_count_as_pass():
    report = check_lookahead(make_candles(5), lambda h: 0.0, warmup=10)
    assert report.bars_checked == 0 and not report.passed


@pytest.mark.parametrize(
    "candidate",
    default_candidate_grid() + momentum_candidate_grid() + momentum_candidate_grid_daily_decorrelated(),
    ids=lambda c: c.name,
)
def test_registered_baseline_candidates_have_no_lookahead_and_respect_warmup(candidate):
    candles = make_candles(candidate.warmup + 250, seed=5, vol=0.02)
    assert check_lookahead(candles, candidate, warmup=candidate.warmup, samples=25).passed
    assert check_warmup_sensitivity(candles, candidate, warmup=candidate.warmup, samples=25).passed


@pytest.mark.parametrize(
    "candidate", adaptive_candidate_grid() + adaptive_hysteresis_candidate_grid(), ids=lambda c: c.name,
)
def test_adaptive_candidates_have_no_lookahead(candidate):
    candles = make_candles(candidate.warmup + 120, seed=6, vol=0.02)
    assert check_lookahead(candles, candidate, warmup=candidate.warmup, samples=10).passed


# --- protections ------------------------------------------------------------

NOW = T0 + timedelta(days=10)


def _trade(hours_ago: float, r: float) -> ClosedTrade:
    return ClosedTrade(NOW - timedelta(hours=hours_ago), r)


def test_stoploss_guard_locks_after_streak_and_lapses():
    guard = StoplossGuard(lookback=timedelta(hours=24), max_losses=3, pause=timedelta(hours=12))
    trades = [_trade(10, -0.01), _trade(5, -0.02), _trade(2, 0.03), _trade(1, -0.01)]
    d = evaluate_protections(now=NOW, trades=trades, equity=(), stoploss_guards=[guard])
    assert not d.entries_allowed
    assert d.locked_until == NOW - timedelta(hours=1) + timedelta(hours=12)
    later = evaluate_protections(now=NOW + timedelta(hours=12), trades=trades, equity=(), stoploss_guards=[guard])
    assert later.entries_allowed


def test_stoploss_guard_ignores_old_losses():
    guard = StoplossGuard(lookback=timedelta(hours=24), max_losses=2, pause=timedelta(hours=12))
    trades = [_trade(48, -0.05), _trade(30, -0.05), _trade(1, -0.01)]
    assert evaluate_protections(now=NOW, trades=trades, equity=(), stoploss_guards=[guard]).entries_allowed


def test_max_drawdown_guard():
    guard = MaxDrawdownGuard(lookback=timedelta(days=3), max_drawdown=0.10, pause=timedelta(days=1))
    eq = [EquityPoint(NOW - timedelta(hours=h), v) for h, v in [(48, 100.0), (24, 110.0), (6, 97.0), (1, 99.0)]]
    d = evaluate_protections(now=NOW, trades=(), equity=eq, drawdown_guards=[guard])
    assert not d.entries_allowed and "drawdown" in d.reason
    assert d.locked_until == NOW - timedelta(hours=6) + timedelta(days=1)
    shallow = [EquityPoint(NOW - timedelta(hours=h), v) for h, v in [(24, 100.0), (1, 95.0)]]
    assert evaluate_protections(now=NOW, trades=(), equity=shallow, drawdown_guards=[guard]).entries_allowed


def test_cooldown_and_longest_lock_wins():
    trades = [_trade(1, 0.02)]
    d = evaluate_protections(now=NOW, trades=trades, equity=(),
                             cooldowns=[CooldownPeriod(timedelta(hours=2)), CooldownPeriod(timedelta(hours=5))])
    assert d.locked_until == NOW + timedelta(hours=4)


def test_non_finite_inputs_fail_closed():
    d = evaluate_protections(now=NOW, trades=[_trade(1, math.nan)], equity=())
    assert not d.entries_allowed and "non-finite" in d.reason


def test_no_protections_configured_allows_entries():
    assert evaluate_protections(now=NOW, trades=[_trade(1, -0.5)], equity=()).entries_allowed


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        evaluate_protections(now=NOW.replace(tzinfo=None), trades=(), equity=())
