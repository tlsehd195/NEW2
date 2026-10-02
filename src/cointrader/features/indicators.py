"""Technical indicators as pure functions over already-closed history.

Rules every function here follows (ADR-0015):

- **No future data.** Inputs are the history up to and including the
  current closed bar; the value returned is "as of" that bar. Nothing
  is centred, shifted backwards or normalised with statistics of the
  whole series.
- **Bounded, declared memory.** Recursive indicators (EMA, Wilder RSI/
  ATR, MACD) are computed over a fixed trailing window (`lookback`,
  default 4x the period) seeded from a simple average, instead of over
  "all history the caller happened to pass". That makes the value a
  function of exactly the last `lookback` bars, so a strategy's signal
  does not change with how much older history is available
  (`validation.lookahead.check_warmup_sensitivity`) and a live process
  that keeps only a bounded deque computes the SAME number as the
  backtest. The truncation error vs. an infinite-memory EMA is
  (1-alpha)^(3*period) ~ e^-6, and it is identical in research and live.
- **Fail-closed.** Insufficient or non-finite input returns `None`, never
  a guess. Callers treat `None` as "no signal".
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from cointrader.data.models import Candle

FEATURE_VERSION = "1.0.0"


def _finite(values: Sequence[float]) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def _tail(values: Sequence[float], n: int) -> Optional[list[float]]:
    if n < 1 or len(values) < n:
        return None
    out = list(values[len(values) - n:])
    return out if _finite(out) else None


# ------------------------------------------------------------------ trend --
def sma(values: Sequence[float], period: int) -> Optional[float]:
    tail = _tail(values, period)
    return None if tail is None else math.fsum(tail) / period


def ema(values: Sequence[float], period: int, *, lookback: Optional[int] = None) -> Optional[float]:
    """EMA over exactly the last `lookback` values (default 4*period),
    seeded with the SMA of the first `period` of them."""
    if period < 1:
        raise ValueError("period must be >= 1")
    lookback = lookback or 4 * period
    if lookback < period:
        raise ValueError("lookback must be >= period")
    tail = _tail(values, lookback)
    if tail is None:
        return None
    alpha = 2.0 / (period + 1)
    value = math.fsum(tail[:period]) / period
    for v in tail[period:]:
        value = alpha * v + (1 - alpha) * value
    return value


def ema_slope(values: Sequence[float], period: int, bars: int) -> Optional[float]:
    """Relative change of the EMA over the last `bars` bars."""
    now = ema(values, period)
    past = ema(values[: len(values) - bars], period) if len(values) > bars else None
    if now is None or past is None or past == 0:
        return None
    return now / past - 1


def price_vs_ema(values: Sequence[float], period: int) -> Optional[float]:
    e = ema(values, period)
    if e is None or e == 0 or not values:
        return None
    return values[-1] / e - 1


def ma_alignment(values: Sequence[float], periods: Sequence[int]) -> Optional[int]:
    """+1 when EMAs are stacked fast > ... > slow, -1 when stacked the
    other way, 0 otherwise. `periods` ascending."""
    if list(periods) != sorted(periods) or len(periods) < 2:
        raise ValueError("periods must be ascending with at least 2 entries")
    emas = [ema(values, p) for p in periods]
    if any(e is None for e in emas):
        return None
    if all(a > b for a, b in zip(emas, emas[1:])):
        return 1
    if all(a < b for a, b in zip(emas, emas[1:])):
        return -1
    return 0


# --------------------------------------------------------------- momentum --
def roc(values: Sequence[float], period: int) -> Optional[float]:
    tail = _tail(values, period + 1)
    if tail is None or tail[0] == 0:
        return None
    return tail[-1] / tail[0] - 1


def momentum(values: Sequence[float], period: int) -> Optional[float]:
    tail = _tail(values, period + 1)
    return None if tail is None else tail[-1] - tail[0]


def rsi(values: Sequence[float], period: int = 14, *, lookback: Optional[int] = None) -> Optional[float]:
    """Wilder RSI over the last `lookback` changes (default 4*period)."""
    lookback = lookback or 4 * period
    tail = _tail(values, lookback + 1)
    if tail is None:
        return None
    changes = [b - a for a, b in zip(tail, tail[1:])]
    gain = math.fsum(max(c, 0.0) for c in changes[:period]) / period
    loss = math.fsum(max(-c, 0.0) for c in changes[:period]) / period
    for c in changes[period:]:
        gain = (gain * (period - 1) + max(c, 0.0)) / period
        loss = (loss * (period - 1) + max(-c, 0.0)) / period
    if gain == 0 and loss == 0:
        return 50.0
    if loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[tuple[float, float, float]]:
    """(macd, signal, histogram). The signal line is an EMA of the last
    `4*signal` MACD values, each computed with the windowed EMA."""
    if not fast < slow:
        raise ValueError("need fast < slow")
    need = 4 * slow + 4 * signal
    if len(values) < need:
        return None
    series = []
    for end in range(len(values) - 4 * signal + 1, len(values) + 1):
        f, s = ema(values[:end], fast), ema(values[:end], slow)
        if f is None or s is None:
            return None
        series.append(f - s)
    sig = ema(series, signal, lookback=4 * signal)
    if sig is None:
        return None
    return series[-1], sig, series[-1] - sig


# ------------------------------------------------------------- volatility --
def true_ranges(candles: Sequence[Candle]) -> list[float]:
    out = []
    for prev, cur in zip(candles, candles[1:]):
        out.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    return out


def atr(candles: Sequence[Candle], period: int = 14, *, lookback: Optional[int] = None) -> Optional[float]:
    """Wilder ATR over the last `lookback` true ranges (default 4*period)."""
    lookback = lookback or 4 * period
    if len(candles) < lookback + 1:
        return None
    trs = true_ranges(candles[len(candles) - lookback - 1:])
    if not _finite(trs):
        return None
    value = math.fsum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def log_returns(values: Sequence[float]) -> list[float]:
    return [math.log(b / a) for a, b in zip(values, values[1:]) if a > 0 and b > 0]


def realized_volatility(values: Sequence[float], period: int) -> Optional[float]:
    """Per-bar sample stdev of the last `period` log returns (not
    annualised; annualise with the caller's bars-per-year)."""
    tail = _tail(values, period + 1)
    if tail is None or min(tail) <= 0:
        return None
    rets = log_returns(tail)
    if len(rets) < 2:
        return None
    m = math.fsum(rets) / len(rets)
    return math.sqrt(math.fsum((r - m) ** 2 for r in rets) / (len(rets) - 1))


def bollinger(values: Sequence[float], period: int = 20, k: float = 2.0) -> Optional[dict]:
    tail = _tail(values, period)
    if tail is None:
        return None
    mid = math.fsum(tail) / period
    sd = math.sqrt(math.fsum((v - mid) ** 2 for v in tail) / period)
    upper, lower = mid + k * sd, mid - k * sd
    width = upper - lower
    return {
        "mid": mid, "upper": upper, "lower": lower,
        "pct_b": (tail[-1] - lower) / width if width > 0 else 0.5,
        "bandwidth": width / mid if mid else None,
        "zscore": (tail[-1] - mid) / sd if sd > 0 else 0.0,
    }


# ----------------------------------------------------------------- volume --
def volume_ma(candles: Sequence[Candle], period: int) -> Optional[float]:
    return sma([c.volume for c in candles], period)


def volume_zscore(candles: Sequence[Candle], period: int) -> Optional[float]:
    """z-score of the current bar's volume vs. the `period` bars BEFORE
    it (the current bar is not in its own baseline)."""
    if len(candles) < period + 1:
        return None
    base = [c.volume for c in candles[len(candles) - period - 1:-1]]
    if not _finite(base):
        return None
    m = math.fsum(base) / period
    sd = math.sqrt(math.fsum((v - m) ** 2 for v in base) / period)
    if sd == 0:
        return 0.0
    return (candles[-1].volume - m) / sd


def relative_volume(candles: Sequence[Candle], period: int) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    base = sma([c.volume for c in candles[:-1]], period)
    if base is None or base == 0:
        return None
    return candles[-1].volume / base


# ------------------------------------------------------- price / candles --
def vwap(candles: Sequence[Candle], period: int) -> Optional[float]:
    """Rolling VWAP of typical price over the last `period` bars."""
    if len(candles) < period:
        return None
    tail = candles[len(candles) - period:]
    vol = math.fsum(c.volume for c in tail)
    if vol <= 0:
        return None
    return math.fsum((c.high + c.low + c.close) / 3 * c.volume for c in tail) / vol


def vwap_deviation(candles: Sequence[Candle], period: int) -> Optional[float]:
    v = vwap(candles, period)
    if v is None or v == 0:
        return None
    return candles[-1].close / v - 1


def candle_shape(c: Candle) -> dict:
    rng = c.high - c.low
    if rng <= 0:
        return {"range_fraction": 0.0, "body_ratio": 0.0, "upper_wick_ratio": 0.0, "lower_wick_ratio": 0.0}
    return {
        "range_fraction": rng / c.open if c.open else 0.0,
        "body_ratio": abs(c.close - c.open) / rng,
        "upper_wick_ratio": (c.high - max(c.open, c.close)) / rng,
        "lower_wick_ratio": (min(c.open, c.close) - c.low) / rng,
    }


# --------------------------------------------------------------- breakout --
def donchian(candles: Sequence[Candle], period: int) -> Optional[tuple[float, float]]:
    """(high, low) of the `period` bars BEFORE the current one, so a
    breakout of the current close is well defined."""
    if len(candles) < period + 1:
        return None
    prior = candles[len(candles) - period - 1:-1]
    return max(c.high for c in prior), min(c.low for c in prior)


def breakout(candles: Sequence[Candle], period: int) -> Optional[int]:
    """+1 close above prior high, -1 below prior low, else 0."""
    d = donchian(candles, period)
    if d is None:
        return None
    close = candles[-1].close
    return 1 if close > d[0] else (-1 if close < d[1] else 0)
