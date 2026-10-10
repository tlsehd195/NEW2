"""Risk engine: the one place that decides whether a NEW position may be
opened and how large it is. Used identically by the event backtest, the
paper trader and (behind the safety gate) the live path, so a result in
one is the same decision logic as in the others.

Order of checks (every failure is recorded as a reason; nothing is
silently skipped, and any unknown input refuses -- fail-closed):

1. kill switch engaged (read-only here; releasing it is human-only),
2. data quality gate / feed health not HEALTHY,
3. regime UNDEFINED,
4. `risk.protections` (StoplossGuard = consecutive losses, MaxDrawdownGuard
   = windowed drawdown pause, CooldownPeriod),
5. max daily loss (UTC day) and max total drawdown from peak,
6. volatility kill (ATR as a fraction of price above the limit),
7. spread protection (missing spread refuses when `require_spread`),
8. sizing: risk-per-trade / stop distance (clamped to [stop_min, stop_max] x price), capped by per-symbol max
   notional and max leverage, rounded DOWN to the exchange step, refused
   below min quantity / min notional.

It never closes a position and never touches the kill switch: exits are
always allowed (the engine only gates entries), and anything that needs a
human is the kill switch's job (`live.kill_switch`, ADR-0014).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from cointrader._time import require_aware
from cointrader.live.config import HealthStatus
from cointrader.risk.protections import (
    ClosedTrade,
    CooldownPeriod,
    EquityPoint,
    MaxDrawdownGuard,
    StoplossGuard,
    evaluate_protections,
)

RISK_ENGINE_VERSION = "1.1.0"


@dataclass(frozen=True)
class SymbolFilters:
    """Exchange trading rules for one symbol (Binance `exchangeInfo`
    PRICE_FILTER / LOT_SIZE / MIN_NOTIONAL). Defaults in configs are
    placeholders until refreshed from the exchange."""

    symbol: str
    tick_size: float
    step_size: float
    min_quantity: float
    min_notional: float

    def __post_init__(self) -> None:
        for name in ("tick_size", "step_size", "min_quantity"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"SymbolFilters.{name} must be positive")
        if self.min_notional < 0:
            raise ValueError("SymbolFilters.min_notional must be >= 0")

    def round_quantity(self, quantity: float) -> float:
        """Round DOWN to the step (never trade more than was sized)."""
        steps = math.floor(quantity / self.step_size + 1e-9)
        return round(steps * self.step_size, 12)

    def round_price(self, price: float, *, up: bool) -> float:
        ticks = price / self.tick_size
        ticks = math.ceil(ticks - 1e-9) if up else math.floor(ticks + 1e-9)
        return round(ticks * self.tick_size, 12)


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.005  # fraction of equity lost if the stop is hit
    max_leverage: float = 2.0
    # ADR-0060: the stop distance is clamped to [min, max] x entry price before sizing; None = no clamp.
    stop_min_fraction: Optional[float] = None
    stop_max_fraction: Optional[float] = None
    max_position_notional: Optional[float] = None  # absolute cap in quote currency; None = only leverage cap
    max_daily_loss: float = 0.03  # fraction of the UTC day's starting equity
    max_drawdown: float = 0.15  # fraction below the equity peak; entries stop (human review)
    max_atr_fraction: float = 0.05  # ATR/price of 1h bars above this = volatility kill (scaled for longer bars)
    max_spread_fraction: float = 0.001
    require_spread: bool = True
    stoploss_guard: Optional[StoplossGuard] = StoplossGuard(timedelta(hours=24), 3, timedelta(hours=6))
    drawdown_guard: Optional[MaxDrawdownGuard] = MaxDrawdownGuard(timedelta(days=2), 0.06, timedelta(hours=12))
    cooldown: Optional[CooldownPeriod] = CooldownPeriod(timedelta(minutes=30))
    # ADR-0063: with risk_per_trade_max set, the risk per trade rises linearly from risk_per_trade (confidence <=
    # risk_conf_low) to risk_per_trade_max (confidence >= risk_conf_high). No confidence on the request = risk_per_trade.
    risk_per_trade_max: Optional[float] = None
    risk_conf_low: float = 0.60
    risk_conf_high: float = 0.75

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade < 0.1:
            raise ValueError("risk_per_trade must be in (0, 0.1)")
        if not 0 < self.max_leverage <= 20:
            raise ValueError("max_leverage must be in (0, 20]")
        if self.risk_per_trade_max is not None:
            if not self.risk_per_trade <= self.risk_per_trade_max < 0.1:
                raise ValueError("risk_per_trade_max must be in [risk_per_trade, 0.1)")
            if not 0.5 < self.risk_conf_low < self.risk_conf_high <= 1.0:
                raise ValueError("need 0.5 < risk_conf_low < risk_conf_high <= 1")
        lo, hi = self.stop_min_fraction, self.stop_max_fraction
        if (lo is None) != (hi is None):
            raise ValueError("stop_min_fraction and stop_max_fraction must be set together")
        if lo is not None and not 0 < lo <= hi < 1:
            raise ValueError("stop fractions must satisfy 0 < min <= max < 1")
        for name in ("max_daily_loss", "max_drawdown", "max_atr_fraction", "max_spread_fraction"):
            if not 0 < getattr(self, name) < 1:
                raise ValueError(f"{name} must be in (0, 1)")

    def clamp_stop_distance(self, stop: float, price: float) -> float:
        if self.stop_min_fraction is None or self.stop_max_fraction is None:
            return stop
        return min(max(stop, self.stop_min_fraction * price), self.stop_max_fraction * price)

    def risk_fraction(self, confidence: Optional[float]) -> float:
        if self.risk_per_trade_max is None or confidence is None or not math.isfinite(confidence):
            return self.risk_per_trade
        t = min(1.0, max(0.0, (confidence - self.risk_conf_low) / (self.risk_conf_high - self.risk_conf_low)))
        return self.risk_per_trade + t * (self.risk_per_trade_max - self.risk_per_trade)

    def version(self) -> str:
        blob = json.dumps({k: str(v) for k, v in self.__dict__.items()}, sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]


@dataclass(frozen=True)
class AccountRiskState:
    """What the engine needs to know about the account right now."""

    equity: Optional[float]
    peak_equity: Optional[float]
    day_start_equity: Optional[float]
    closed_trades: tuple[ClosedTrade, ...] = ()
    equity_points: tuple[EquityPoint, ...] = ()
    open_position_notional: float = 0.0  # already open in THIS symbol


def atr_limit(max_atr_fraction: float, bar: Optional[timedelta]) -> float:
    """The volatility kill is calibrated on 1h bars. A longer bar's ATR is
    larger by about sqrt(time) in the same market (a quiet day's ATR is
    ~5x a quiet hour's), so its limit scales the same way; shorter bars
    keep the 1h limit rather than getting a looser one."""
    if bar is None or bar <= timedelta(hours=1):
        return max_atr_fraction
    return max_atr_fraction * math.sqrt(bar / timedelta(hours=1))


@dataclass(frozen=True)
class EntryRequest:
    now: datetime
    symbol: str
    direction: int  # +1 long, -1 short
    reference_price: Optional[float]
    stop_distance: Optional[float]
    regime: str
    atr: Optional[float]
    spread_fraction: Optional[float]
    feed_health: Optional[HealthStatus]
    data_quality_reasons: tuple[str, ...] = ()
    kill_switch_engaged: bool = False
    strategy_id: str = ""
    atr_bar: Optional[timedelta] = None  # bar length `atr` was measured on; None = 1h
    confidence: Optional[float] = None  # P of the entry side (0.5..1); scales risk when risk_per_trade_max is set


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    quantity: float = 0.0
    notional: float = 0.0
    leverage: float = 0.0
    risk_amount: float = 0.0
    stop_price: Optional[float] = None
    stop_distance: float = 0.0  # effective (clamped) distance; callers must use this for the real stop
    decision_id: str = ""
    config_version: str = ""
    evaluated_at: Optional[datetime] = None
    inputs: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.approved == bool(self.reasons):
            raise ValueError("approved must be True exactly when there are no reasons")


def _ok(x: Optional[float]) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def _decision_id(req: EntryRequest, config_version: str) -> str:
    blob = f"{req.strategy_id}|{req.symbol}|{req.direction}|{req.now.isoformat()}|{config_version}"
    return "rd_" + hashlib.sha256(blob.encode()).hexdigest()[:20]


class RiskEngine:
    def __init__(self, config: RiskConfig, filters: dict[str, SymbolFilters]) -> None:
        self.config = config
        self.filters = filters
        self.config_version = config.version()

    def evaluate_entry(self, req: EntryRequest, account: AccountRiskState) -> RiskDecision:
        require_aware("now", req.now)
        cfg = self.config
        reasons: list[str] = []
        if req.direction not in (-1, 1):
            reasons.append("invalid_direction")
        if req.kill_switch_engaged:
            reasons.append("kill_switch_engaged")
        if req.feed_health is not HealthStatus.HEALTHY:
            reasons.append(f"feed_{(req.feed_health or HealthStatus.UNKNOWN).value.lower()}")
        reasons += [f"data_quality:{r}" for r in req.data_quality_reasons]
        if req.regime == "UNDEFINED":
            reasons.append("regime_undefined")

        protection = evaluate_protections(
            now=req.now, trades=account.closed_trades, equity=account.equity_points,
            stoploss_guards=[cfg.stoploss_guard] if cfg.stoploss_guard else [],
            drawdown_guards=[cfg.drawdown_guard] if cfg.drawdown_guard else [],
            cooldowns=[cfg.cooldown] if cfg.cooldown else [],
        )
        if not protection.entries_allowed:
            until = protection.locked_until.isoformat() if protection.locked_until else "?"
            reasons.append(f"protection:{protection.reason} until {until}")

        equity = account.equity
        if not _ok(equity) or equity <= 0:
            reasons.append("equity_unknown")
        else:
            if not _ok(account.day_start_equity) or account.day_start_equity <= 0:
                reasons.append("day_start_equity_unknown")
            elif (account.day_start_equity - equity) / account.day_start_equity >= cfg.max_daily_loss:
                reasons.append("max_daily_loss_breached")
            if not _ok(account.peak_equity) or account.peak_equity <= 0:
                reasons.append("peak_equity_unknown")
            elif (account.peak_equity - equity) / account.peak_equity >= cfg.max_drawdown:
                reasons.append("max_drawdown_breached")

        price, stop = req.reference_price, req.stop_distance
        if not _ok(price) or price <= 0:
            reasons.append("reference_price_unknown")
        if not _ok(stop) or stop <= 0:
            reasons.append("stop_distance_unknown")
        if not _ok(req.atr) or req.atr <= 0:
            reasons.append("atr_unknown")
        elif _ok(price) and price > 0 and req.atr / price > atr_limit(cfg.max_atr_fraction, req.atr_bar):
            reasons.append(f"volatility_kill_atr_{req.atr / price:.4f}")
        if req.spread_fraction is None:
            if cfg.require_spread:
                reasons.append("spread_unknown")
        elif not _ok(req.spread_fraction) or req.spread_fraction > cfg.max_spread_fraction:
            reasons.append(f"spread_too_wide_{req.spread_fraction}")

        filters = self.filters.get(req.symbol)
        if filters is None:
            reasons.append("symbol_filters_unknown")

        inputs = {"equity": equity, "peak_equity": account.peak_equity, "day_start_equity": account.day_start_equity,
                  "reference_price": price, "stop_distance": stop, "atr": req.atr, "spread": req.spread_fraction,
                  "regime": req.regime, "feed_health": (req.feed_health or HealthStatus.UNKNOWN).value,
                  "risk_engine_version": RISK_ENGINE_VERSION}
        decision_id = _decision_id(req, self.config_version)
        if reasons:
            return RiskDecision(False, tuple(reasons), decision_id=decision_id, config_version=self.config_version,
                                evaluated_at=req.now, inputs=inputs)

        raw_stop = stop
        stop = cfg.clamp_stop_distance(stop, price)
        inputs["stop_distance_raw"], inputs["stop_distance_used"] = raw_stop, stop
        risk_fraction = cfg.risk_fraction(req.confidence)
        inputs["confidence"], inputs["risk_fraction"] = req.confidence, risk_fraction
        risk_amount = equity * risk_fraction
        quantity = risk_amount / stop
        cap = equity * cfg.max_leverage - account.open_position_notional
        if cfg.max_position_notional is not None:
            cap = min(cap, cfg.max_position_notional - account.open_position_notional)
        if cap <= 0:
            return RiskDecision(False, ("position_limit_reached",), decision_id=decision_id,
                                config_version=self.config_version, evaluated_at=req.now, inputs=inputs)
        capped = quantity * price > cap
        if capped:
            quantity = cap / price
        quantity = filters.round_quantity(quantity)
        notional = quantity * price
        if quantity < filters.min_quantity:
            return RiskDecision(False, (f"below_min_quantity_{quantity}",), decision_id=decision_id,
                                config_version=self.config_version, evaluated_at=req.now, inputs=inputs)
        if notional < filters.min_notional:
            return RiskDecision(False, (f"below_min_notional_{notional:.2f}",), decision_id=decision_id,
                                config_version=self.config_version, evaluated_at=req.now, inputs=inputs)
        stop_price = filters.round_price(price - stop if req.direction > 0 else price + stop, up=req.direction < 0)
        inputs["capped_by_limits"] = capped
        return RiskDecision(True, (), quantity, notional, notional / equity, quantity * stop, stop_price, stop, decision_id,
                            self.config_version, req.now, inputs)


def utc_day_start(at: datetime) -> datetime:
    require_aware("at", at)
    at = at.astimezone(timezone.utc)
    return at.replace(hour=0, minute=0, second=0, microsecond=0)


def account_state_from_history(
    *, equity_points: Sequence[EquityPoint], closed_trades: Sequence[ClosedTrade], now: datetime,
    open_position_notional: float = 0.0, keep: timedelta = timedelta(days=7),
) -> AccountRiskState:
    """Derive peak / day-start equity from the equity curve so far.
    Only points at or before `now` are used; the history kept for the
    windowed protections is bounded to `keep`."""
    past = [p for p in equity_points if p.at <= now]
    if not past:
        return AccountRiskState(None, None, None)
    day0 = utc_day_start(now)
    before_day = [p for p in past if p.at <= day0]
    day_start = before_day[-1].equity if before_day else past[0].equity
    peak = max(p.equity for p in past)
    recent_points = tuple(p for p in past if p.at >= now - keep)
    recent_trades = tuple(t for t in closed_trades if now - keep <= t.closed_at <= now)
    return AccountRiskState(past[-1].equity, peak, day_start, recent_trades, recent_points, open_position_notional)
