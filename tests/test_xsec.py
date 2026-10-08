"""Cross-sectional portfolio candidates, engine and validation (ADR-0017)."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from cointrader.backtest.xsec_engine import run_xsec_backtest
from cointrader.data.models import Candle, Timeframe
from cointrader.risk.engine import RiskConfig
from cointrader.settings import load_risk
from cointrader.strategies.cross_sectional import (
    CoinHistory,
    LowVolumeLongShort,
    MomentumLongOnly,
    MomentumLongShort,
    XSEC_FACTORIES,
)
from cointrader.validation.locked_windows import LockedWindowViolation
from tests.helpers import make_candles

REPO = __import__("pathlib").Path(__file__).resolve().parents[1]
T0 = datetime(2020, 1, 1, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def _coins(n_coins=10, days=200, start=T0, vol=0.03):
    out = {}
    for k in range(n_coins):
        raw = make_candles(days, timeframe=Timeframe.DAY_1, market=f"C{k}USDT", start=start, seed=k,
                           drift=0.002 * (k - n_coins / 2), vol=vol, volume=1e6 * (k + 1))
        out[f"C{k}USDT"] = [replace(c, open=c.open / 1e6, high=c.high / 1e6, low=c.low / 1e6, close=c.close / 1e6)
                            for c in raw]
    return out


def _funding(symbols, start=T0, days=400, rate=0.0001):
    return {s: {start + timedelta(hours=8 * i, milliseconds=i % 5): rate for i in range(days * 3)} for s in symbols}


def _risk():
    return load_risk()


# ---- strategies -------------------------------------------------------------

def _hist(closes, vols=None):
    return CoinHistory(closes=tuple(closes), dollar_volumes=tuple(vols or [1.0] * len(closes)))


def test_momentum_long_short_ranks_by_past_return_and_is_dollar_neutral():
    s = MomentumLongShort(21)
    hist = {f"S{k}": _hist([100.0] * 21 + [100.0 + k]) for k in range(9)}
    w = s.target_weights(hist)
    assert set(k for k, v in w.items() if v > 0) == {"S6", "S7", "S8"}
    assert set(k for k, v in w.items() if v < 0) == {"S0", "S1", "S2"}
    assert abs(sum(w.values())) < 1e-12 and abs(sum(abs(v) for v in w.values()) - 1.0) < 1e-12


def test_long_only_holds_the_top_third_fully_invested():
    w = MomentumLongOnly(21).target_weights({f"S{k}": _hist([100.0] * 21 + [100.0 + k]) for k in range(9)})
    assert set(w) == {"S6", "S7", "S8"} and abs(sum(w.values()) - 1.0) < 1e-12


def test_low_volume_longs_the_smallest_dollar_volume():
    hist = {f"S{k}": _hist([1.0] * 8, [10.0 ** k] * 8) for k in range(6)}
    w = LowVolumeLongShort(7).target_weights(hist)
    assert w["S0"] > 0 and w["S1"] > 0 and w["S4"] < 0 and w["S5"] < 0


def test_too_few_coins_or_short_history_means_flat():
    s = MomentumLongShort(21)
    assert s.target_weights({f"S{k}": _hist([1.0] * 22) for k in range(5)}) == {}
    assert s.target_weights({f"S{k}": _hist([1.0] * 10) for k in range(9)}) == {}


def _wiggle(n, amp, trend, seed):
    import random
    rng = random.Random(seed)
    out, p = [], 100.0
    for _ in range(n):
        p *= 1 + trend + rng.gauss(0, amp)
        out.append(p)
    return out


def test_inverse_vol_gives_the_calmer_coin_the_bigger_weight_inside_each_leg():
    from cointrader.strategies.cross_sectional import InverseVolMomentumLongShort
    hist = {f"S{k}": _hist(_wiggle(40, 0.01 if k % 2 else 0.05, 0.002 * (k - 4), k)) for k in range(9)}
    w = InverseVolMomentumLongShort(21, 30).target_weights(hist)
    longs = {s: v for s, v in w.items() if v > 0}
    shorts = {s: v for s, v in w.items() if v < 0}
    assert abs(sum(longs.values()) - 0.5) < 1e-12 and abs(sum(shorts.values()) + 0.5) < 1e-12
    calm = [s for s in longs if int(s[1:]) % 2]
    wild = [s for s in longs if not int(s[1:]) % 2]
    if calm and wild:
        assert min(longs[s] for s in calm) > max(longs[s] for s in wild)


def test_vol_target_scales_the_spread_to_the_target_and_caps_gross():
    from cointrader.strategies.cross_sectional import VolTargetMomentumLongShort, _daily_returns, _stdev
    import math
    hist = {f"S{k}": _hist(_wiggle(40, 0.03, 0.002 * (k - 4), k)) for k in range(9)}
    s = VolTargetMomentumLongShort(21, 30, 0.20)
    w = s.target_weights(hist)
    rets = {k: _daily_returns(hist[k].closes, 30) for k in w}
    port = [sum(v * rets[k][d] for k, v in w.items()) for d in range(30)]
    gross = sum(abs(v) for v in w.values())
    assert gross <= 2.0 + 1e-12
    if gross < 2.0 - 1e-9:
        assert abs(_stdev(port) * math.sqrt(365) - 0.20) < 1e-9
    calm = {f"S{k}": _hist(_wiggle(40, 0.0005, 0.0001 * (k - 4), k)) for k in range(9)}
    assert abs(sum(abs(v) for v in s.target_weights(calm).values()) - 2.0) < 1e-9  # capped


def test_registered_ids_are_the_xsec_family():
    assert set(XSEC_FACTORIES) == {"xsec_momentum_21d_ls_v1", "xsec_momentum_21d_long_v1",
                                   "xsec_low_volume_7d_ls_v1", "xsec_momentum_21d_ls_invvol30_v1",
                                   "xsec_momentum_21d_ls_voltarget20_v1"}
    assert all(s.family == "xsec" for s in XSEC_FACTORIES.values())


# ---- engine -----------------------------------------------------------------

def test_engine_rebalances_weekly_and_reports_costs():
    coins = _coins()
    s = MomentumLongShort(21)
    res = run_xsec_backtest(coins, s, _risk(), score_from=T0 + 60 * DAY, end=T0 + 130 * DAY,
                            funding=_funding(coins))
    assert res.rebalances == 10  # decisions at day 59, 66, ..., 122 -> executed 60, 67, ..., 123
    cb = res.summary()["cost_breakdown"]
    assert cb["fees"] > 0 and cb["slippage"] > 0 and cb["funding"] != 0
    assert abs(cb["gross_pnl"] - cb["fees"] - cb["spread"] - cb["slippage"] - cb["funding"] - cb["net_pnl"]) < 1e-6


def test_engine_never_sees_the_future():
    coins = _coins()
    s = MomentumLongShort(21)
    end = T0 + 150 * DAY
    base = run_xsec_backtest(coins, s, _risk(), score_from=T0 + 60 * DAY, end=end, funding=_funding(coins))
    cut = T0 + 100 * DAY
    shocked = {k: [c if c.open_time < cut else replace(c, open=c.open * 3, high=c.high * 3, low=c.low * 3,
                                                          close=c.close * 3) for c in v] for k, v in coins.items()}
    alt = run_xsec_backtest(shocked, s, _risk(), score_from=T0 + 60 * DAY, end=end, funding=_funding(coins))
    before = [e for t, e in base.equity_curve if t < cut - DAY]
    assert before == [e for t, e in alt.equity_curve if t < cut - DAY]


def test_long_pays_positive_funding_short_receives():
    coins = _coins()
    lo = run_xsec_backtest(coins, MomentumLongOnly(21), _risk(), score_from=T0 + 60 * DAY, end=T0 + 90 * DAY,
                           funding=_funding(coins, rate=0.001))
    assert lo.funding > 0


def test_funding_is_required_unless_explicitly_assumed_away():
    coins = _coins()
    with pytest.raises(ValueError):
        run_xsec_backtest(coins, MomentumLongShort(21), _risk(), score_from=T0 + 60 * DAY, end=T0 + 90 * DAY)
    res = run_xsec_backtest(coins, MomentumLongShort(21), _risk(), score_from=T0 + 60 * DAY, end=T0 + 90 * DAY,
                            assume_no_funding=True)
    assert any("no funding" in a for a in res.assumptions)


def test_coin_without_funding_records_is_not_traded():
    coins = _coins()
    funding = _funding(coins)
    funding["C9USDT"] = {}
    res = run_xsec_backtest(coins, MomentumLongOnly(21), _risk(), score_from=T0 + 60 * DAY, end=T0 + 90 * DAY,
                            funding=funding)
    assert res.ineligible_no_funding > 0


def test_frozen_or_untraded_coin_is_not_ranked_and_nothing_crashes():
    from cointrader.strategies.cross_sectional import InverseVolMomentumLongShort, VolTargetMomentumLongShort
    coins = _coins()
    # a delisted-looking coin: price frozen and no volume for its last 60 days
    frozen = coins["C9USDT"]
    coins["C9USDT"] = frozen[:100] + [replace(c, open=frozen[99].close, high=frozen[99].close, low=frozen[99].close,
                                              close=frozen[99].close, volume=0.0) for c in frozen[100:]]
    for s in (InverseVolMomentumLongShort(21, 30), VolTargetMomentumLongShort(21, 30, 0.2)):
        res = run_xsec_backtest(coins, s, _risk(), score_from=T0 + 60 * DAY, end=T0 + 190 * DAY,
                                funding=_funding(coins))
        assert res.ineligible_stale > 0 and res.rebalances > 0


def test_held_coin_losing_its_funding_records_is_closed_not_crashed():
    coins = _coins()
    s = MomentumLongOnly(21)
    # a coin bought at day 67 (decided at day 66's close) whose market then dries up
    # (so the exit is participation-capped) and whose funding records stop
    held = sorted(s.target_weights({k: CoinHistory(tuple(c.close for c in v[45:67]), tuple(1.0 for _ in v[45:67]))
                                    for k, v in coins.items()}))[0]
    coins[held] = [c if c.open_time < T0 + 70 * DAY else replace(c, volume=1e-4) for c in coins[held]]
    funding = _funding(coins)
    funding[held] = {t: r for t, r in funding[held].items() if t < T0 + 75 * DAY}
    res = run_xsec_backtest(coins, s, _risk(), score_from=T0 + 60 * DAY, end=T0 + 120 * DAY, funding=funding)
    assert res.partial_fills > 0 and res.funding_gap_closes == 1


def test_inverse_vol_stays_flat_instead_of_dividing_by_zero():
    from cointrader.strategies.cross_sectional import InverseVolMomentumLongShort
    hist = {f"S{k}": _hist(_wiggle(40, 0.001, 0.01 + 0.002 * k, k)) for k in range(8)}
    hist["S8"] = _hist([200.0] * 40)  # lowest momentum -> short leg, zero volatility
    assert InverseVolMomentumLongShort(21, 30).target_weights(hist) == {}


def test_drawdown_halt_goes_flat_and_stays_flat():
    coins = _coins(vol=0.15)
    risk = RiskConfig(**{**_risk().__dict__, "max_drawdown": 0.02})
    res = run_xsec_backtest(coins, MomentumLongOnly(21), risk, score_from=T0 + 60 * DAY, end=T0 + 190 * DAY,
                            funding=_funding(coins))
    assert res.halted_at is not None
    after = [e for t, e in res.equity_curve if t > res.halted_at]
    assert len(set(after[:-1])) <= 1


def test_missing_bar_for_a_held_coin_forces_a_close():
    coins = _coins()
    s = MomentumLongOnly(21)
    first = run_xsec_backtest(coins, s, _risk(), score_from=T0 + 60 * DAY, end=T0 + 62 * DAY,
                              funding=_funding(coins))
    assert first.rebalances == 1
    held = s.target_weights({k: CoinHistory(tuple(c.close for c in v[38:60]), tuple(c.close * c.volume for c in v[38:60]))
                             for k, v in coins.items()})
    gone = next(iter(held))
    coins[gone] = [c for c in coins[gone] if c.open_time != T0 + 62 * DAY]
    res = run_xsec_backtest(coins, s, _risk(), score_from=T0 + 60 * DAY, end=T0 + 66 * DAY, funding=_funding(coins))
    assert res.forced_closes >= 1


# ---- validation script ------------------------------------------------------

def _load_script():
    spec = importlib.util.spec_from_file_location("run_xsec_validation", REPO / "scripts" / "run_xsec_validation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _universe_file(tmp_path, symbols):
    p = tmp_path / "universes.json"
    p.write_text(json.dumps({"SYN": {"rule": "synthetic", "symbols": symbols}}), encoding="utf-8")
    return p


def _args(tmp_path, universes, locked, hid="H-9101"):
    return ["run_xsec_validation.py", "--hypothesis-id", hid, "--universe", "SYN", "--start", "2020-02-01",
            "--end", "2022-06-01", "--strategies", *sorted(XSEC_FACTORIES)[:3], "--statement", "synthetic xsec dry run",
            "--rationale", "synthetic end-to-end dry run of the cross-sectional validation script",
            "--registered-by", "test", "--universes", str(universes), "--log", str(tmp_path / "prereg.jsonl"),
            "--locked-path", str(locked), "--ledger", str(tmp_path / "ledger.jsonl"),
            "--out", str(tmp_path / "report.json")]


def _loaders(coins, funding):
    def lc(sym, tf, a, b):
        return [c for c in coins[sym] if a <= c.open_time < b], []

    def lf(sym, a, b):
        return {t: r for t, r in funding[sym].items() if a <= t < b}, []
    return lc, lf


def test_xsec_validation_end_to_end_locks_basket_and_every_coin(tmp_path, monkeypatch):
    mod = _load_script()
    coins = _coins(n_coins=9, days=900)
    # late listing: one coin only starts trading a year in
    coins["C8USDT"] = [c for c in coins["C8USDT"] if c.open_time >= T0 + 365 * DAY]
    universes = _universe_file(tmp_path, sorted(coins))
    locked = tmp_path / "locked.json"
    locked.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", _args(tmp_path, universes, locked))
    assert mod.main(loader=_loaders(coins, _funding(coins, days=900))) == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    windows = json.loads(locked.read_text(encoding="utf-8"))
    assert [w["name"] for w in windows] == ["TEST-9101"] + [f"TEST-9101:{s}" for s in sorted(coins)]
    assert windows[0]["market"] == "XS:SYN" and {w["start"] for w in windows} == {report["test_window_locked"]["start"]}
    assert report["label"].startswith("BACKTEST") and report["fold_count"] >= 16
    assert all(sum(c["fold_rebalances"]) > 0 for c in report["candidates"])
    prereg = [json.loads(l) for l in (tmp_path / "prereg.jsonl").read_text(encoding="utf-8").splitlines()]
    assert prereg[0]["statement"].endswith("universe: " + ",".join(sorted(coins)))
    rows = [json.loads(l) for l in (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows and all(r["to_status"] not in ("APPROVED", "DEPLOYED") for r in rows)
    # the same run again hits the lock before anything is registered or loaded
    with pytest.raises(LockedWindowViolation):
        mod.main(loader=_loaders(coins, _funding(coins, days=900)))


def test_test_range_is_locked_before_the_test_runs(tmp_path, monkeypatch):
    """A crash inside the TEST run must not leave the seen range unlocked."""
    import cointrader.validation.xsec_study as study
    mod = _load_script()
    coins = _coins(n_coins=9, days=900)
    universes = _universe_file(tmp_path, sorted(coins))
    locked = tmp_path / "locked.json"
    locked.write_text("[]", encoding="utf-8")
    real = study.equal_weight_buy_and_hold

    def boom(*a, **k):  # first thing the TEST phase does
        assert json.loads(locked.read_text(encoding="utf-8")), "TEST ran before its range was locked"
        raise RuntimeError("crash inside TEST")
    monkeypatch.setattr(study, "equal_weight_buy_and_hold", boom)
    monkeypatch.setattr(sys, "argv", _args(tmp_path, universes, locked, hid="H-9103"))
    with pytest.raises(RuntimeError):
        mod.main(loader=_loaders(coins, _funding(coins, days=900)))
    assert len(json.loads(locked.read_text(encoding="utf-8"))) == 1 + len(coins)
    monkeypatch.setattr(study, "equal_weight_buy_and_hold", real)


def test_a_lock_on_any_single_coin_blocks_the_basket(tmp_path, monkeypatch):
    mod = _load_script()
    coins = _coins(n_coins=9, days=900)
    universes = _universe_file(tmp_path, sorted(coins))
    locked = tmp_path / "locked.json"
    locked.write_text(json.dumps([{"name": "TEST-1", "market": "C3USDT", "start": "2021-03-01T00:00:00+00:00",
                                   "end": "2021-04-01T00:00:00+00:00", "observed_by": ["x"], "note": "t"}]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", _args(tmp_path, universes, locked, hid="H-9102"))
    with pytest.raises(LockedWindowViolation):
        mod.main(loader=_loaders(coins, _funding(coins, days=900)))
    assert not (tmp_path / "prereg.jsonl").exists()  # refused before registration


def test_funding_series_charges_every_recorded_settlement_at_any_interval():
    from cointrader.backtest.xsec_engine import FundingSeries
    h = timedelta(hours=1)
    eight = FundingSeries({T0 + 8 * k * h: 0.0001 for k in range(10)})
    four = FundingSeries({T0 + 4 * k * h: 0.0001 for k in range(20)})
    assert len(eight.charges(T0, T0 + DAY)) == 3 and len(four.charges(T0, T0 + DAY)) == 6
    assert eight.covers(T0, T0 + DAY) and four.covers(T0 + h, T0 + DAY)
    holed = FundingSeries({T0 + 8 * k * h: 0.0001 for k in range(10) if k != 2})
    assert not holed.covers(T0, T0 + DAY)
    assert not eight.covers(T0 + 3 * DAY, T0 + 4 * DAY)  # records stopped
    assert not eight.covers(T0 - DAY, T0)  # nothing before the first record
