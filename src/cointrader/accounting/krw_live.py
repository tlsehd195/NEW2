"""Live KRW valuation of the paper (or live) account while it runs (ADR-0041).

Two pieces:

- `UpbitTicker.rate()`: the current KRW-USDT price from Upbit's public REST ticker, polled every
  few seconds (REST, not WebSocket: one HTTPS GET per poll is far below Upbit's rate limit, needs
  no connection management, and a failed poll just keeps the last value until it goes stale).
  A rate older than `max_age`, implausible, or from an unexpected market is refused.
- `live_snapshot()`: values the whole account at that rate right now. USDT equity (wallet balance
  plus unrealized PnL, from the broker) is converted at the current rate and compared with the KRW
  actually paid. The split is exact for what it claims:

      total = equity * r_now - krw_paid
            = trading   (equity - start_usdt) * r_now   # everything valued at today's rate
            + fx        start_usdt * (r_now - r_buy)    # USDT bought at r_buy
            + domestic_fee  -fee_krw

  This is the quick "if I looked now" view. The per-event ledger (`krw_ledger`, `show_krw_pnl.py`)
  values each closed trade at the rate of its own moment and stays the number to use for filing.
  Cash-out and tax lines reuse the same configured fees and tax estimate, and carry the same
  "estimate, check with an accountant" status.

Nothing here decides or places a trade.
"""

from __future__ import annotations

import json
import math
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Callable, Optional

from cointrader._time import require_aware
from cointrader.accounting.krw_ledger import ExitCostConfig, KrwTaxConfig
from cointrader.accounting.krw_rates import MARKET, PLAUSIBLE_KRW_PER_USDT

TICKER_URL = "https://api.upbit.com/v1/ticker?markets=" + MARKET

Transport = Callable[[str], bytes]


def _urllib_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.read()


@dataclass(frozen=True)
class Quote:
    rate: float
    traded_at: datetime
    fetched_at: datetime


class UpbitTicker:
    def __init__(self, *, transport: Transport = _urllib_get, now: Callable[[], datetime],
                 max_age: timedelta = timedelta(minutes=5)) -> None:
        self._transport, self._now, self._max_age = transport, now, max_age

    def rate(self) -> Quote:
        """Raises ValueError (or the transport's error) rather than returning a doubtful rate."""
        now = self._now()
        require_aware("now", now)
        rows = json.loads(self._transport(TICKER_URL))
        if not isinstance(rows, list) or len(rows) != 1 or rows[0].get("market") != MARKET:
            raise ValueError(f"unexpected ticker response for {MARKET}")
        price = float(rows[0]["trade_price"])
        lo, hi = PLAUSIBLE_KRW_PER_USDT
        if not math.isfinite(price) or not lo <= price <= hi:
            raise ValueError(f"implausible {MARKET} price {price!r}")
        traded = datetime.fromtimestamp(int(rows[0]["trade_timestamp"]) / 1000, tz=now.tzinfo)
        if now - traded > self._max_age:
            raise ValueError(f"{MARKET} last trade is {(now - traded).total_seconds():.0f}s old; refusing a stale rate")
        return Quote(price, traded, now)


def live_snapshot(*, equity_usdt: float, start_usdt: float, krw_gross: float, fee_krw: float,
                  quote: Quote, tax: KrwTaxConfig, exit_costs: ExitCostConfig, mode: str = "paper") -> dict:
    """Pure function of its inputs. `krw_gross`/`fee_krw`/`start_usdt` come from the start purchase
    (flows file); `equity_usdt` from the broker's account (wallet + unrealized)."""
    for name, v in (("equity_usdt", equity_usdt), ("start_usdt", start_usdt), ("krw_gross", krw_gross)):
        if not math.isfinite(v) or v <= 0:
            raise ValueError(f"{name} must be a positive finite number: {v!r}")
    r = quote.rate
    r_buy = krw_gross / start_usdt
    value = equity_usdt * r
    trading = (equity_usdt - start_usdt) * r
    fx = start_usdt * (r - r_buy)
    total = value - krw_gross - fee_krw
    assert abs(total - (trading + fx - fee_krw)) < 1e-6 * max(1.0, abs(total))
    exit_cost = None if exit_costs.missing() else (
        min(equity_usdt, exit_costs.overseas_withdraw_fee_usdt) * r
        + max(0.0, equity_usdt - exit_costs.overseas_withdraw_fee_usdt) * r * exit_costs.domestic_sell_fee_rate
        + exit_costs.krw_withdraw_fee_krw)
    year = quote.fetched_at.astimezone(ZoneInfo("Asia/Seoul")).year
    est_tax = tax.estimate(year, total)
    return {
        "mode": mode, "as_of": quote.fetched_at.isoformat(), "rate_krw_per_usdt": r,
        "rate_traded_at": quote.traded_at.isoformat(), "rate_source": f"upbit_ticker_{MARKET}",
        "equity_usdt": equity_usdt, "krw_paid": krw_gross + fee_krw, "value_krw": value,
        "pnl_krw": {"total": total, "trading": trading, "fx": fx, "domestic_fee": -fee_krw},
        "cash_out": {"exit_cost_krw": exit_cost,
                     "estimated_tax_krw": est_tax,
                     "net_after_cash_out_and_tax_krw": None if exit_cost is None else total - exit_cost - est_tax},
        "tax_verified": tax.verified,
        "note": "현재 환율로 전부 평가한 빠른 추정. 신고용 정확한 값은 show_krw_pnl.py. 세금은 추정치(세무사 확인 필요).",
    }


def write_snapshot(path: Path, snap: dict) -> None:
    """Atomic: a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def one_line(s: dict) -> str:
    def w(x):
        return "알 수 없음" if x is None else f"{x:+,.0f}원"
    p = s["pnl_krw"]
    return (f"[{s['mode']}] 환율 {s['rate_krw_per_usdt']:,.1f} | 평가 {s['value_krw']:,.0f}원 | 손익 {w(p['total'])} "
            f"(매매 {w(p['trading'])}, 환차 {w(p['fx'])}) | 회수·세금 후 {w(s['cash_out']['net_after_cash_out_and_tax_krw'])}")


def refresh_snapshot(*, ticker: UpbitTicker, equity_usdt_fn: Callable[[], float], flows_path: Path,
                     out_path: Path, tax: KrwTaxConfig, exit_costs: ExitCostConfig, mode: str = "paper") -> dict:
    """One refresh: current rate + current account equity -> `out_path` (atomic). The start purchase is
    read from the flows file (first `usdt_purchase` of this mode). Any missing piece raises, and the previous
    file is left as it was, so a stale file's `as_of` shows how old the numbers are."""
    from cointrader.accounting.krw_ledger import UsdtPurchase, event_from_dict

    start = None
    if flows_path.exists():
        for line in flows_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ev = event_from_dict(json.loads(line))
                if isinstance(ev, UsdtPurchase) and ev.mode == mode:
                    start = ev
                    break
    if start is None:
        raise ValueError(f"no {mode} start purchase in {flows_path} yet")
    snap = live_snapshot(equity_usdt=equity_usdt_fn(), start_usdt=start.usdt, krw_gross=start.krw_gross,
                         fee_krw=start.fee_krw, quote=ticker.rate(), tax=tax, exit_costs=exit_costs, mode=mode)
    write_snapshot(out_path, snap)
    return snap
