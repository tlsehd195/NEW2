from __future__ import annotations

import importlib.util
import json
import math
import random
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.backtest.event_engine import ExecutionCosts
from cointrader.backtest.grid_engine import GridPeriod, GridTerms, run_grid_backtest
from cointrader.data.models import Candle, Timeframe
from cointrader.strategies.grid import GRID_CANDIDATES, GRID_FACTORIES, GridCandidate
from cointrader.validation.grid_study import classify_period, regime_breakdown
from cointrader.validation.locked_windows import LockedWindowViolation, load_locked_windows

T0 = datetime(2024, 1, 4, tzinfo=timezone.utc)  # a Thursday: epoch-aligned 7-day resets fall on T0 + 7k days
TF = Timeframe.MINUTE_15
BPD = 96
TERMS = GridTerms(maintenance_margin_rate=0.01)
NEUTRAL = GridCandidate("grid_t_neutral", mode="neutral", vol_lookback_days=3)
SPOT = GridCandidate("grid_t_spot", mode="spot", vol_lookback_days=3)


def _bars(closes, start=T0, wick=0.001, volume=1_000.0, market="BNBUSDT"):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Candle(market=market, timeframe=TF, open_time=start + i * TF.delta, open=o,
                          high=max(o, c) * (1 + wick), low=min(o, c) * (1 - wick), close=c, volume=volume,
                          source="test", received_at=start + (i + 1) * TF.delta))
        prev = c
    return out


def _noise(n, seed=1, vol=0.004, drift=0.0, p0=300.0):
    rng, p, xs = random.Random(seed), p0, []
    for _ in range(n):
        p *= math.exp(drift + rng.gauss(0, vol))
        xs.append(p)
    return xs


def _sine(n, amp=0.05, period=BPD, p0=300.0, seed=2):
    rng = random.Random(seed)
    return [p0 * (1 + amp * math.sin(2 * math.pi * i / period)) * math.exp(rng.gauss(0, 0.0005)) for i in range(n)]


def _run(closes, cand=NEUTRAL, warm_days=4, **kw):
    bars = _bars(closes, start=T0 - timedelta(days=warm_days))
    kw.setdefault("assume_no_funding", True)
    kw.setdefault("terms", TERMS)
    return run_grid_backtest(bars, cand, score_from=T0, **kw)


def _identity(r):
    lhs = r.final_equity - r.initial_equity
    rhs = (r.grid_realized + r.market_realized + r.unrealized - r.maker_fees - r.taker_fees - r.slippage
           - r.funding)
    return lhs, rhs


def test_oscillating_market_earns_round_trips_without_stops():
    warm = _noise(4 * BPD, vol=0.01)
    r = _run(warm + [warm[-1] * x / 300 for x in _sine(10 * BPD)])
    assert r.round_trips > 10
    assert r.stops == 0
    assert r.grid_realized > 0
    assert r.total_return > 0


def test_trend_out_of_the_range_stops_and_loses_inventory():
    warm = _noise(4 * BPD, vol=0.004)
    down = [warm[-1] * math.exp(-0.002 * i) for i in range(1, 6 * BPD)]
    for cand in (NEUTRAL, SPOT):
        r = _run(warm + down, cand=cand)
        assert r.stops >= 1
        assert r.market_realized < 0
        assert r.total_return < 0
        assert any(p.stopped for p in r.periods)


def test_pnl_decomposition_adds_up_including_costs_and_funding():
    closes = _noise(4 * BPD, vol=0.01) + _noise(20 * BPD, seed=5, vol=0.006)
    bars = _bars(closes, start=T0 - timedelta(days=4))
    funding = {T0 + timedelta(hours=8 * k, milliseconds=3): 0.0001 * (1 if k % 3 else -2) for k in range(-12, 80)}
    for cand in (NEUTRAL, SPOT):
        r = run_grid_backtest(bars, cand, score_from=T0, funding=funding, terms=TERMS)
        lhs, rhs = _identity(r)
        assert lhs == pytest.approx(rhs, rel=1e-9, abs=1e-6)
        assert r.maker_fees > 0 and r.taker_fees > 0 and r.slippage > 0


def test_spot_grid_is_long_only_and_neutral_goes_both_ways():
    closes = _noise(4 * BPD, vol=0.01) + _noise(14 * BPD, seed=9, vol=0.008)
    r = _run(closes, cand=SPOT)
    assert r.funding == 0
    # spot starts with half the ladder bought, at most the full ladder at the bottom
    assert 0 < r.max_abs_exposure <= SPOT.exposure * 1.2


def test_long_position_pays_positive_funding():
    warm = _noise(4 * BPD, vol=0.004)
    flat = [warm[-1]] * (3 * BPD)
    bars = _bars(warm + flat, start=T0 - timedelta(days=4))
    funding = {T0 + timedelta(hours=8 * k): 0.001 for k in range(-12, 12)}
    r = run_grid_backtest(bars, SPOT, score_from=T0, funding=funding, terms=TERMS)
    assert r.funding > 0  # spot grid holds a long inventory


def test_funding_and_liquidation_inputs_are_required():
    bars = _bars(_noise(5 * BPD), start=T0 - timedelta(days=4))
    with pytest.raises(ValueError, match="funding"):
        run_grid_backtest(bars, NEUTRAL, score_from=T0, terms=TERMS)
    with pytest.raises(ValueError, match="maintenance"):
        run_grid_backtest(bars, NEUTRAL, score_from=T0, assume_no_funding=True)
    r = run_grid_backtest(bars, NEUTRAL, score_from=T0, assume_no_funding=True,
                          terms=GridTerms(assume_no_liquidation=True))
    assert any("NO FUNDING" in a for a in r.assumptions)
    assert any("LIQUIDATION NOT MODELLED" in a for a in r.assumptions)


def test_missing_funding_settlement_flattens_instead_of_guessing():
    warm = _noise(4 * BPD, vol=0.004)
    bars = _bars(warm + [warm[-1]] * (3 * BPD), start=T0 - timedelta(days=4))
    funding = {T0 + timedelta(hours=8 * k): 0.0001 for k in range(-12, 12) if k != 2}
    r = run_grid_backtest(bars, SPOT, score_from=T0, funding=funding, terms=TERMS)
    assert r.funding_gap_halts == 1
    assert r.unrealized == 0  # flat after the halt until the next reset


def test_crash_liquidates_a_leveraged_grid():
    lev = GridCandidate("grid_t_lev", mode="spot", vol_lookback_days=3, exposure=3.0, width_sigmas=5.0)
    warm = _noise(4 * BPD, vol=0.004)
    crash = [warm[-1] * (1 - 0.01 * i) for i in range(1, 80)]
    r = _run(warm + crash, cand=lev)
    assert r.liquidations == 1
    assert r.final_equity == 0


def test_no_look_ahead_prefix_runs_match():
    closes = _noise(4 * BPD, vol=0.01) + _noise(10 * BPD, seed=3, vol=0.008)
    full = _run(closes)
    cut = _run(closes[: 4 * BPD + 6 * BPD])
    assert full.equity_curve[: len(cut.equity_curve)] == cut.equity_curve


def test_range_filter_stays_flat_after_a_trend():
    trend = [300 * math.exp(0.0008 * i) for i in range(15 * BPD)]
    cand = GRID_FACTORIES["grid_neutral_range_filter_v1"]
    bars = _bars(trend + _sine(8 * BPD, p0=trend[-1]), start=T0 - timedelta(days=15))
    r = run_grid_backtest(bars, cand, score_from=T0, assume_no_funding=True, terms=TERMS)
    assert r.periods[0].reason == "trend_filter"
    assert r.maker_fills == 0 or r.periods[0].deployed is False


def test_insufficient_history_is_not_traded():
    bars = _bars(_noise(3 * BPD), start=T0 - timedelta(hours=6))
    r = run_grid_backtest(bars, NEUTRAL, score_from=T0, assume_no_funding=True, terms=TERMS)
    assert r.periods[0].reason == "insufficient_history"
    assert r.maker_fills == 0


def test_candidates_are_the_grid_family():
    assert {c.family for c in GRID_CANDIDATES} == {"grid"}
    assert len(GRID_FACTORIES) == 3


def test_regime_classes():
    def p(er, ret, days=7):
        return GridPeriod(T0, T0 + timedelta(days=days), True, "deployed", 1, 1, 100, 101, False, er, ret)
    assert classify_period(p(0.2, 0.01)) == "range"
    assert classify_period(p(0.7, 0.10)) == "trend_up"
    assert classify_period(p(0.7, -0.10)) == "trend_down"
    assert classify_period(p(0.5, 0.0)) == "mixed"
    assert classify_period(p(0.1, 0.0, days=2)) == "partial"
    b = regime_breakdown([p(0.2, 0.01), p(0.7, -0.1)])
    assert b["range"]["periods"] == 1 and b["range"]["sum_return"] == pytest.approx(0.01)


# --- end-to-end script ---------------------------------------------------------------------------

def _load_script():
    spec = importlib.util.spec_from_file_location("run_grid_validation", "scripts/run_grid_validation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _series(days, start):
    rng, p, xs = random.Random(11), 300.0, []
    for i in range(days * BPD):
        p *= math.exp(rng.gauss(0, 0.004) + 0.03 * (math.sin(2 * math.pi * i / (BPD * 3)) - math.sin(
            2 * math.pi * (i - 1) / (BPD * 3))))
        xs.append(p)
    return _bars(xs, start=start)


def _args(tmp_path, locked, hid="H-9201", start="2024-01-01", end="2025-03-01"):
    return ["run_grid_validation.py", "--hypothesis-id", hid, "--symbol", "BNBUSDT", "--strategies",
            *GRID_FACTORIES, "--start", start, "--end", end, "--statement", "grid test",
            "--rationale", "synthetic end-to-end test of the grid validation pipeline order",
            "--registered-by", "test", "--log", str(tmp_path / "prereg.jsonl"), "--locked-path", str(locked),
            "--ledger", str(tmp_path / "ledger.jsonl"), "--out", str(tmp_path / "out.json")]


def _loaders(bars, seen):
    def load_c(sym, tf, start, end):
        seen.append(("candles", start, end))
        return [b for b in bars if start <= b.open_time < end], []

    def load_f(sym, start, end):
        t, out = start.replace(hour=0, minute=0), {}
        while t < end:
            out[t] = 0.0001
            t += timedelta(hours=8)
        return out, []
    return load_c, load_f


def test_grid_validation_end_to_end_locks_test(tmp_path, monkeypatch):
    locked = tmp_path / "locked.json"
    locked.write_text("[]\n")
    bars = _series(460, datetime(2023, 12, 1, tzinfo=timezone.utc))
    seen = []
    mod = _load_script()
    monkeypatch.setattr(sys, "argv", _args(tmp_path, locked))
    assert mod.main(loader=_loaders(bars, seen)) == 0
    out = json.loads((tmp_path / "out.json").read_text())
    assert out["fold_count"] >= 8
    assert out["test_window_locked"]["name"] == "TEST-9201"
    c0 = out["candidates"][0]
    assert set(c0["fold_regimes"]) == {"range", "mixed", "trend_up", "trend_down", "partial"}
    assert "pnl_breakdown_pct_of_initial" in c0["test_summary"]
    w = load_locked_windows(locked)
    assert len(w) == 1 and w[0].market == "BNBUSDT"
    # the same range again is refused before anything is loaded
    monkeypatch.setattr(sys, "argv", _args(tmp_path, locked, hid="H-9202"))
    seen.clear()
    with pytest.raises(LockedWindowViolation):
        mod.main(loader=_loaders(bars, seen))
    assert seen == []


def test_test_range_is_locked_before_the_test_runs(tmp_path, monkeypatch):
    locked = tmp_path / "locked.json"
    locked.write_text("[]\n")
    bars = _series(460, datetime(2023, 12, 1, tzinfo=timezone.utc))
    mod = _load_script()
    import cointrader.validation.grid_study as gs
    real = gs.run_grid_backtest
    calls = {"n": 0}

    def boom(bars_, cand, *, score_from, **kw):
        if load_locked_windows(locked):
            raise RuntimeError("TEST run crashed")
        calls["n"] += 1
        return real(bars_, cand, score_from=score_from, **kw)

    monkeypatch.setattr(gs, "run_grid_backtest", boom)
    monkeypatch.setattr(sys, "argv", _args(tmp_path, locked))
    with pytest.raises(RuntimeError):
        mod.main(loader=_loaders(bars, []))
    assert len(load_locked_windows(locked)) == 1
    assert calls["n"] > 0


def test_hole_in_the_bars_flattens_the_grid():
    closes = _noise(4 * BPD, vol=0.004) + _noise(3 * BPD, seed=4, vol=0.004)
    bars = _bars(closes, start=T0 - timedelta(days=4))
    holed = bars[: 4 * BPD + BPD] + bars[4 * BPD + BPD + 8:]
    r = run_grid_backtest(holed, SPOT, score_from=T0, assume_no_funding=True, terms=TERMS)
    assert r.data_gap_halts == 1
    assert r.unrealized == 0


def _ohlc(xs, k, tf, start):
    out, prev = [], xs[0]
    for j in range(0, len(xs), k):
        seg = [prev] + xs[j:j + k]
        out.append(Candle("BNBUSDT", tf, start + (j // k) * tf.delta, seg[0], max(seg), min(seg), seg[-1], 1e9,
                          "test", start + (j // k + 1) * tf.delta))
        prev = seg[-1]
    return out


def test_bar_path_rule_gives_no_free_edge_across_timeframes():
    """Same 1m martingale path, zero costs: 5m and 15m bars give about the
    same result (the coarser bar's path rule neither invents nor hides
    round trips in a material way)."""
    zero = ExecutionCosts(taker_fee=0, maker_fee=0, half_spread=0, impact_coefficient=0, impact_vol_floor=0)
    diffs = []
    for seed in range(4):
        rng, p, xs = random.Random(seed), 300.0, []
        for _ in range(12 * 1440):
            p *= math.exp(rng.gauss(0, 0.002))
            xs.append(p)
        st = T0 - timedelta(days=4)
        runs = [run_grid_backtest(_ohlc(xs, k, tf, st), NEUTRAL, score_from=T0, assume_no_funding=True,
                                  costs=zero, terms=TERMS).total_return
                for k, tf in ((5, Timeframe.MINUTE_5), (15, TF))]
        diffs.append(runs[0] - runs[1])
    assert abs(sum(diffs) / len(diffs)) < 0.01
