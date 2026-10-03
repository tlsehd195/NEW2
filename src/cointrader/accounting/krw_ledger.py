"""KRW-denominated net PnL for the KRW -> USDT -> Binance -> USDT -> KRW loop.

The user keeps money in KRW, buys USDT on a domestic exchange (Upbit),
sends it to Binance, trades USDⓈ-M futures there, and eventually brings
USDT back and sells it for KRW (ADR-0002, ADR-0003). "Profit" that the
user actually keeps is measured in KRW and has to pay for every leg:

    domestic trading fee   KRW, on each USDT buy/sell
    network/withdraw fee   USDT, on each transfer between exchanges
    futures fees, spread, slippage, funding   USDT, on each closed trade
    FX (환차손익)           the KRW/USDT rate moves while USDT is held
    KRW fees               e.g. a bank withdrawal fee
    tax                    an *estimate*, see `KrwTaxConfig`

Accounting model (ADR-0036): USDT is an inventory with a moving-average
KRW cost. Every USDT inflow is added at its KRW value at that moment
(a USDT buy at the price paid; a futures profit at the event's KRW/USDT
rate). Every USDT outflow (a sale, a loss, a fee) leaves at the
inventory's average cost, and the gap between the event's rate and that
cost is the realized FX gain/loss. Each component is valued at the rate
of its own event, so the components add up exactly to

    KRW received - KRW paid + (USDT still held) * (mark rate)

which `KrwPnlReport.identity_error` checks.

One ledger is one mode: paper, live and backtest events never mix
(`journal/records.py` MODES), so paper results stay comparable to live
ones without ever being added to them. The same code runs for all three.

Fail-closed: an event without a rate, a rate older than the allowed
staleness, an outflow larger than the USDT held, or a mixed-mode ledger
raises instead of guessing. This module never moves money and never
places an order; it only reads records.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence, Union
from zoneinfo import ZoneInfo

from cointrader._time import require_aware

MODES = ("backtest", "paper", "live")
KST = ZoneInfo("Asia/Seoul")
_EPS = 1e-9


def _finite_nonneg(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite number >= 0: {value!r}")


def _positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite number > 0: {value!r}")


def _check_mode(mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}: {mode!r}")


@dataclass(frozen=True)
class UsdtPurchase:
    """KRW -> USDT on a domestic exchange. `krw_gross` is what the USDT
    itself cost (price * quantity); `fee_krw` is charged on top."""

    at: datetime
    mode: str
    krw_gross: float
    usdt: float
    fee_krw: float
    venue: str = "upbit"
    external_id: str = ""

    def __post_init__(self) -> None:
        require_aware("UsdtPurchase.at", self.at)
        _check_mode(self.mode)
        _positive("krw_gross", self.krw_gross)
        _positive("usdt", self.usdt)
        _finite_nonneg("fee_krw", self.fee_krw)

    @property
    def rate(self) -> float:
        return self.krw_gross / self.usdt


@dataclass(frozen=True)
class UsdtSale:
    """USDT -> KRW on a domestic exchange. `krw_gross` before `fee_krw`."""

    at: datetime
    mode: str
    usdt: float
    krw_gross: float
    fee_krw: float
    venue: str = "upbit"
    external_id: str = ""

    def __post_init__(self) -> None:
        require_aware("UsdtSale.at", self.at)
        _check_mode(self.mode)
        _positive("usdt", self.usdt)
        _positive("krw_gross", self.krw_gross)
        _finite_nonneg("fee_krw", self.fee_krw)

    @property
    def rate(self) -> float:
        return self.krw_gross / self.usdt


@dataclass(frozen=True)
class UsdtTransfer:
    """USDT moved between the user's own accounts. Only the network /
    withdrawal fee leaves the inventory; it is valued at `rate_krw`."""

    at: datetime
    mode: str
    network_fee_usdt: float
    rate_krw: float
    rate_source: str
    direction: str  # "to_overseas" | "to_domestic"
    external_id: str = ""

    def __post_init__(self) -> None:
        require_aware("UsdtTransfer.at", self.at)
        _check_mode(self.mode)
        _finite_nonneg("network_fee_usdt", self.network_fee_usdt)
        _positive("rate_krw", self.rate_krw)
        if not self.rate_source:
            raise ValueError("rate_source must not be empty")
        if self.direction not in ("to_overseas", "to_domestic"):
            raise ValueError(f"direction must be to_overseas or to_domestic: {self.direction!r}")


@dataclass(frozen=True)
class KrwFee:
    """A fee paid directly in KRW (e.g. a bank withdrawal fee)."""

    at: datetime
    mode: str
    krw: float
    label: str

    def __post_init__(self) -> None:
        require_aware("KrwFee.at", self.at)
        _check_mode(self.mode)
        _positive("krw", self.krw)
        if not self.label:
            raise ValueError("label must not be empty")


@dataclass(frozen=True)
class FuturesTradeClose:
    """One closed futures trade, all amounts in USDT, same sign convention
    as `TradeRecord`: net = gross - fees - spread - slippage - funding
    (funding positive = paid). The whole trade is valued at `rate_krw`,
    the KRW/USDT rate at its exit."""

    at: datetime
    mode: str
    gross_pnl_usdt: float
    fees_usdt: float
    spread_usdt: float
    slippage_usdt: float
    funding_usdt: float
    rate_krw: float
    rate_source: str
    trade_id: str = ""

    def __post_init__(self) -> None:
        require_aware("FuturesTradeClose.at", self.at)
        _check_mode(self.mode)
        for name in ("gross_pnl_usdt", "spread_usdt", "slippage_usdt", "funding_usdt"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")
        _finite_nonneg("fees_usdt", self.fees_usdt)
        _positive("rate_krw", self.rate_krw)
        if not self.rate_source:
            raise ValueError("rate_source must not be empty")

    @property
    def net_usdt(self) -> float:
        return self.gross_pnl_usdt - self.fees_usdt - self.spread_usdt - self.slippage_usdt - self.funding_usdt


LedgerEvent = Union[UsdtPurchase, UsdtSale, UsdtTransfer, KrwFee, FuturesTradeClose]

COMPONENTS = (
    "trading_gross",    # futures gross PnL
    "trading_fees",     # futures exchange fees (negative)
    "spread_slippage",  # futures spread + slippage (negative)
    "funding",          # funding received (+) / paid (-)
    "domestic_fees",    # domestic buy/sell fees (negative)
    "network_fees",     # withdrawal / network fees (negative)
    "krw_fees",         # other KRW fees (negative)
    "fx_realized",      # 환차손익 realized on USDT outflows
)


class InsufficientUsdt(ValueError):
    """An outflow larger than the USDT the ledger holds. Usually a missing
    purchase/transfer record; never papered over."""


class MixedModes(ValueError):
    """Paper, live and backtest events must never be summed together."""


@dataclass(frozen=True)
class KrwTaxConfig:
    """Korean 가상자산 소득 tax, as an *estimate* (ADR-0036).

    Defaults: current 소득세법 as reported in 2026 -- applies to transfers
    from 2027-01-01, 22% (20% + local 2%) separated taxation of 기타소득
    above a 2,500,000 KRW basic deduction, losses netted within a year
    with no carry-forward. `verified=False` until a tax accountant has
    confirmed how *futures* PnL on an overseas exchange is treated and
    which cost method (이동평균 vs 선입선출) applies; the report shows
    that flag next to every tax number.

    The tax base used here is the year's realized KRW net (every realized
    component above, KST calendar year). That is a simplification: it
    ignores the 2026-12-31 deemed acquisition price (의제취득가액), which
    can only lower the real base, so the estimate leans high.
    """

    effective_from_year: int = 2027
    rate: float = 0.22
    basic_deduction_krw: float = 2_500_000.0
    verified: bool = False

    def __post_init__(self) -> None:
        if not 0.0 <= self.rate < 1.0:
            raise ValueError("rate must be in [0, 1)")
        _finite_nonneg("basic_deduction_krw", self.basic_deduction_krw)

    def estimate(self, year: int, realized_net_krw: float) -> float:
        if year < self.effective_from_year:
            return 0.0
        return max(0.0, realized_net_krw - self.basic_deduction_krw) * self.rate


@dataclass(frozen=True)
class ExitCostConfig:
    """What it would cost to bring the USDT still held back to a KRW bank
    account right now. `None` = not configured: the report then says the
    exit estimate is unavailable instead of assuming zero."""

    overseas_withdraw_fee_usdt: Optional[float] = None
    domestic_sell_fee_rate: Optional[float] = None
    krw_withdraw_fee_krw: Optional[float] = None

    def missing(self) -> list[str]:
        return [k for k in ("overseas_withdraw_fee_usdt", "domestic_sell_fee_rate", "krw_withdraw_fee_krw")
                if getattr(self, k) is None]


@dataclass(frozen=True)
class YearRow:
    year: int  # KST calendar year
    realized_net_krw: float
    estimated_tax_krw: float
    tax_in_force: bool


@dataclass(frozen=True)
class LedgerLine:
    """One booked KRW amount, for the tax-filing detail export."""

    at: datetime
    component: str  # one of COMPONENTS
    krw: float
    event: str  # event class name
    ref: str  # exchange / trade id, if any


@dataclass(frozen=True)
class KrwPnlReport:
    mode: str
    as_of: datetime
    krw_paid: float  # KRW spent buying USDT, incl. domestic fee
    krw_received: float  # KRW received selling USDT, after domestic fee, minus KRW fees
    components: dict  # name -> KRW, see COMPONENTS
    realized_net_krw: float
    usdt_held: float
    usdt_cost_krw: float
    mark_rate: Optional[float]
    mark_rate_source: str
    fx_unrealized: Optional[float]
    exit_cost_krw: Optional[float]
    exit_cost_reason: str
    years: tuple[YearRow, ...]
    tax_verified: bool
    trade_count: int
    lines: tuple = ()

    @property
    def estimated_tax_krw(self) -> float:
        return math.fsum(y.estimated_tax_krw for y in self.years)

    @property
    def net_before_tax_krw(self) -> Optional[float]:
        """Realized + unrealized FX, before exit costs and tax."""
        if self.fx_unrealized is None:
            return None
        return self.realized_net_krw + self.fx_unrealized

    @property
    def net_if_cashed_out_krw(self) -> Optional[float]:
        """What would land in the bank, minus what went in, after every
        cost and the tax estimate -- None if the mark rate or exit costs
        are unknown."""
        if self.net_before_tax_krw is None or self.exit_cost_krw is None:
            return None
        return self.net_before_tax_krw - self.exit_cost_krw - self.estimated_tax_krw

    @property
    def identity_error(self) -> Optional[float]:
        """components + unrealized must equal the KRW cash flow plus the
        marked USDT; anything else is a bookkeeping bug."""
        if self.mark_rate is None or self.fx_unrealized is None:
            return None
        cash = self.krw_received - self.krw_paid + self.usdt_held * self.mark_rate
        return abs(cash - (self.realized_net_krw + self.fx_unrealized))


@dataclass
class _Inventory:
    usdt: float = 0.0
    cost_krw: float = 0.0

    @property
    def avg(self) -> float:
        return self.cost_krw / self.usdt if self.usdt > _EPS else 0.0

    def add(self, qty: float, krw: float) -> None:
        self.usdt += qty
        self.cost_krw += krw

    def remove(self, qty: float, at: datetime, what: str) -> float:
        """Removes `qty` at average cost; returns the KRW cost removed."""
        if qty > self.usdt + _EPS:
            raise InsufficientUsdt(
                f"{what} at {at.isoformat()} needs {qty:.6f} USDT but the ledger holds {self.usdt:.6f}; "
                "a purchase or transfer record is probably missing"
            )
        cost = qty * self.avg
        self.usdt -= qty
        self.cost_krw -= cost
        if self.usdt <= _EPS:
            self.usdt, self.cost_krw = 0.0, 0.0
        return cost


def build_report(
    events: Iterable[LedgerEvent],
    *,
    as_of: datetime,
    mark_rate: Optional[float] = None,
    mark_rate_source: str = "",
    tax: KrwTaxConfig = KrwTaxConfig(),
    exit_costs: ExitCostConfig = ExitCostConfig(),
) -> KrwPnlReport:
    """Processes events in time order (ties keep input order). Events after
    `as_of` are refused, not dropped, so a report never silently ignores
    data it was handed."""
    require_aware("as_of", as_of)
    ordered = [e for _, e in sorted(enumerate(events), key=lambda p: (p[1].at, p[0]))]
    modes = {e.mode for e in ordered}
    if len(modes) > 1:
        raise MixedModes(f"one ledger per mode; got {sorted(modes)}")
    mode = modes.pop() if modes else "paper"
    if ordered and ordered[-1].at > as_of:
        raise ValueError(f"event at {ordered[-1].at.isoformat()} is after as_of {as_of.isoformat()}")

    inv = _Inventory()
    comp = {k: 0.0 for k in COMPONENTS}
    by_year: dict[int, float] = {}
    krw_paid = krw_received = 0.0
    trades = 0

    lines: list[LedgerLine] = []
    cur: dict = {}

    def book(at: datetime, name: str, krw: float) -> None:
        lines.append(LedgerLine(at, name, krw, cur["event"], cur["ref"]))
        comp[name] += krw
        year = at.astimezone(KST).year
        by_year[year] = by_year.get(year, 0.0) + krw

    def outflow(e: LedgerEvent, qty: float, rate: float, what: str) -> None:
        cost = inv.remove(qty, e.at, what)
        book(e.at, "fx_realized", qty * rate - cost)

    for e in ordered:
        cur["event"] = type(e).__name__
        cur["ref"] = getattr(e, "external_id", "") or getattr(e, "trade_id", "")
        if isinstance(e, UsdtPurchase):
            inv.add(e.usdt, e.krw_gross)
            krw_paid += e.krw_gross + e.fee_krw
            book(e.at, "domestic_fees", -e.fee_krw)
        elif isinstance(e, UsdtSale):
            outflow(e, e.usdt, e.rate, "USDT sale")
            krw_received += e.krw_gross - e.fee_krw
            book(e.at, "domestic_fees", -e.fee_krw)
        elif isinstance(e, UsdtTransfer):
            if e.network_fee_usdt > 0:
                outflow(e, e.network_fee_usdt, e.rate_krw, "transfer fee")
                book(e.at, "network_fees", -e.network_fee_usdt * e.rate_krw)
        elif isinstance(e, KrwFee):
            krw_received -= e.krw
            book(e.at, "krw_fees", -e.krw)
        elif isinstance(e, FuturesTradeClose):
            trades += 1
            r = e.rate_krw
            book(e.at, "trading_gross", e.gross_pnl_usdt * r)
            book(e.at, "trading_fees", -e.fees_usdt * r)
            book(e.at, "spread_slippage", -(e.spread_usdt + e.slippage_usdt) * r)
            book(e.at, "funding", -e.funding_usdt * r)
            net = e.net_usdt
            if net > 0:
                inv.add(net, net * r)
            elif net < 0:
                outflow(e, -net, r, f"trade {e.trade_id or '?'} loss")
        else:  # pragma: no cover - typing guard
            raise TypeError(f"unknown ledger event: {type(e).__name__}")

    fx_unrealized: Optional[float] = None
    exit_cost: Optional[float] = None
    exit_reason = ""
    if mark_rate is not None:
        _positive("mark_rate", mark_rate)
        if not mark_rate_source:
            raise ValueError("mark_rate_source must not be empty when mark_rate is given")
        fx_unrealized = inv.usdt * mark_rate - inv.cost_krw
        missing = exit_costs.missing()
        if inv.usdt <= _EPS:
            exit_cost, exit_reason = 0.0, "nothing held"
        elif missing:
            exit_reason = "exit costs not configured: " + ", ".join(missing)
        else:
            sendable = max(0.0, inv.usdt - exit_costs.overseas_withdraw_fee_usdt)
            exit_cost = (
                min(inv.usdt, exit_costs.overseas_withdraw_fee_usdt) * mark_rate
                + sendable * mark_rate * exit_costs.domestic_sell_fee_rate
                + exit_costs.krw_withdraw_fee_krw
            )
            exit_reason = "configured"
    else:
        exit_reason = "no mark rate"

    years = tuple(
        YearRow(y, by_year[y], tax.estimate(y, by_year[y]), y >= tax.effective_from_year)
        for y in sorted(by_year)
    )
    return KrwPnlReport(
        mode=mode, as_of=as_of, krw_paid=krw_paid, krw_received=krw_received, components=comp,
        realized_net_krw=math.fsum(comp.values()), usdt_held=inv.usdt, usdt_cost_krw=inv.cost_krw,
        mark_rate=mark_rate, mark_rate_source=mark_rate_source, fx_unrealized=fx_unrealized,
        exit_cost_krw=exit_cost, exit_cost_reason=exit_reason, years=years,
        tax_verified=tax.verified, trade_count=trades, lines=tuple(lines),
    )


@dataclass(frozen=True)
class KrwRateSeries:
    """KRW per USDT over time, e.g. Upbit KRW-USDT candle closes. `at`
    returns the latest rate at or before `t`; older than `max_staleness`
    or before the first point -> ValueError (fail-closed, never
    interpolated or extrapolated)."""

    points: tuple[tuple[datetime, float], ...]
    source: str
    max_staleness: timedelta = timedelta(hours=1)
    _times: tuple = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("source must not be empty")
        last = None
        for t, r in self.points:
            require_aware("KrwRateSeries point", t)
            _positive("rate", r)
            if last is not None and t <= last:
                raise ValueError("rate points must be strictly increasing in time")
            last = t
        object.__setattr__(self, "_times", tuple(t for t, _ in self.points))

    def at(self, t: datetime) -> float:
        require_aware("t", t)
        i = bisect.bisect_right(self._times, t) - 1
        if i < 0:
            raise ValueError(f"no {self.source} rate at or before {t.isoformat()}")
        when, rate = self.points[i]
        if t - when > self.max_staleness:
            raise ValueError(f"{self.source} rate at {t.isoformat()} is stale (last {when.isoformat()})")
        return rate


def trade_events_from_outcomes(rows: Sequence[dict], rates: KrwRateSeries) -> list[FuturesTradeClose]:
    """Outcome rows (`journal/records.outcome_record`, any mode) ->
    ledger events, each valued at the KRW/USDT rate at its exit time.
    Funding is valued at the exit-time rate too (a stated simplification:
    funding settles during the trade, at most 12 hours earlier on 15m
    day trades)."""
    out = []
    for row in rows:
        at = datetime.fromisoformat(row["exit_time"])
        ev = FuturesTradeClose(
            at=at, mode=row["mode"], gross_pnl_usdt=float(row["gross_pnl"]), fees_usdt=float(row["fees"]),
            spread_usdt=float(row["spread_cost"]), slippage_usdt=float(row["slippage_cost"]),
            funding_usdt=float(row["funding"]), rate_krw=rates.at(at), rate_source=rates.source,
            trade_id=str(row.get("trade_id", "")),
        )
        if abs(ev.net_usdt - float(row["net_pnl"])) > 1e-6 * max(1.0, abs(float(row["net_pnl"]))):
            raise ValueError(f"outcome {ev.trade_id}: components do not add up to net_pnl")
        out.append(ev)
    return out


def events_from_fund_transfer(record, *, mode: str = "live") -> list[LedgerEvent]:
    """A `funding.bridge.FundTransferRecord` (one human-approved KRW ->
    USDT -> Binance transfer) -> purchase + transfer-fee events. The
    withdrawal fee is valued at that same purchase's price. Only reads
    the record; never calls the bridge."""
    p, w = record.purchase, record.withdrawal
    at = record.executed_at
    return [
        UsdtPurchase(at=at, mode=mode, krw_gross=p.filled_usdt * p.price_krw_per_usdt, usdt=p.filled_usdt,
                     fee_krw=p.fee_krw, external_id=p.external_id),
        UsdtTransfer(at=at, mode=mode, network_fee_usdt=w.network_fee_usdt, rate_krw=p.price_krw_per_usdt,
                     rate_source="domestic purchase price", direction="to_overseas", external_id=w.external_id),
    ]


_EVENT_TYPES = {
    "usdt_purchase": UsdtPurchase, "usdt_sale": UsdtSale, "usdt_transfer": UsdtTransfer,
    "krw_fee": KrwFee, "futures_trade_close": FuturesTradeClose,
}


def event_from_dict(d: dict) -> LedgerEvent:
    """One JSONL row -> event. `type` picks the class; every other key
    must be one of its fields (unknown or missing keys raise)."""
    d = dict(d)
    kind = d.pop("type", None)
    cls = _EVENT_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown event type {kind!r}; expected one of {sorted(_EVENT_TYPES)}")
    d["at"] = datetime.fromisoformat(d["at"])
    return cls(**d)
