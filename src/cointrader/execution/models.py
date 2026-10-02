"""Order model shared by paper and live execution (ADR-0015).

Strategies never talk to an exchange. The path is always

    Signal -> RiskDecision -> OrderIntent -> ExecutionEngine -> Broker

and the only difference between paper and live is which `Broker`
implementation sits at the end. An `OrderIntent` carries everything
needed to trace an order back to the decision that produced it
(strategy, candidate, signal and data timestamps, risk decision id,
config version) and a deterministic `client_order_id`, so a retry after
a network error can never become a second order.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from cointrader._time import require_aware

SIDES = ("BUY", "SELL")
POSITION_SIDES = ("BOTH", "LONG", "SHORT")
ORDER_TYPES = ("MARKET", "LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET")
PURPOSES = ("entry", "exit", "stop", "take_profit")

# Binance: newClientOrderId must match ^[\.A-Z\:/a-z0-9_-]{1,36}$ (official
# connector docstring / API docs as known to this session).
_CLIENT_ID_RE = re.compile(r"^[.A-Z:/a-z0-9_-]{1,36}$")


class OrderState(Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"  # written locally BEFORE the request goes out
    NEW = "NEW"  # acknowledged by the broker, resting
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"  # request outcome unknown: never resubmit, reconcile first

    @property
    def terminal(self) -> bool:
        return self in (OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED)


def client_order_id(strategy_id: str, symbol: str, side: str, purpose: str, signal_timestamp: datetime,
                    reduce_only: bool) -> str:
    blob = f"{strategy_id}|{symbol}|{side}|{purpose}|{signal_timestamp.isoformat()}|{reduce_only}"
    return "ct_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:29]


@dataclass(frozen=True)
class OrderIntent:
    strategy_id: str
    candidate_id: str
    symbol: str
    side: str
    position_side: str
    order_type: str
    quantity: float
    price: Optional[float]
    reduce_only: bool
    stop_price: Optional[float]
    take_profit: Optional[float]
    reason: str
    signal_timestamp: datetime
    data_timestamp: datetime
    config_version: str
    risk_decision_id: str
    purpose: str
    mode: str = "paper"  # "paper" | "live"
    client_order_id: str = ""

    def __post_init__(self) -> None:
        require_aware("OrderIntent.signal_timestamp", self.signal_timestamp)
        require_aware("OrderIntent.data_timestamp", self.data_timestamp)
        if self.data_timestamp > self.signal_timestamp:
            raise ValueError("an order cannot be based on data newer than its signal")
        if self.side not in SIDES or self.position_side not in POSITION_SIDES:
            raise ValueError("invalid side / position_side")
        if self.order_type not in ORDER_TYPES or self.purpose not in PURPOSES:
            raise ValueError("invalid order_type / purpose")
        if not math.isfinite(self.quantity) or self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.order_type == "LIMIT" and (self.price is None or self.price <= 0):
            raise ValueError("LIMIT needs a positive price")
        if self.order_type in ("STOP_MARKET", "TAKE_PROFIT_MARKET") and (self.stop_price is None or self.stop_price <= 0):
            raise ValueError(f"{self.order_type} needs a positive stop_price")
        if self.purpose == "entry" and not self.risk_decision_id:
            raise ValueError("an entry needs the risk decision that approved it")
        if self.purpose != "entry" and not self.reduce_only:
            raise ValueError("exits/stops/take-profits must be reduce_only")
        if self.mode not in ("paper", "live"):
            raise ValueError("mode must be paper or live")
        expected = client_order_id(self.strategy_id, self.symbol, self.side, self.purpose, self.signal_timestamp,
                                   self.reduce_only)
        if not self.client_order_id:
            object.__setattr__(self, "client_order_id", expected)
        elif self.client_order_id != expected:
            raise ValueError("client_order_id must be the deterministic one")
        if not _CLIENT_ID_RE.match(self.client_order_id):
            raise ValueError("client_order_id violates the exchange's format")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signal_timestamp"] = self.signal_timestamp.isoformat()
        d["data_timestamp"] = self.data_timestamp.isoformat()
        return d


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    liquidity: str  # "maker" | "taker"
    at: datetime
    reference_price: Optional[float] = None  # mid/best at decision time, for slippage
    exchange_trade_id: Optional[str] = None

    def __post_init__(self) -> None:
        require_aware("Fill.at", self.at)
        if self.quantity <= 0 or self.price <= 0:
            raise ValueError("fill quantity and price must be positive")

    @property
    def slippage(self) -> Optional[float]:
        if not self.reference_price:
            return None
        sign = 1 if self.side == "BUY" else -1
        return sign * (self.price - self.reference_price) / self.reference_price


@dataclass(frozen=True)
class OrderStatus:
    client_order_id: str
    state: OrderState
    filled_quantity: float = 0.0
    average_price: Optional[float] = None
    exchange_order_id: Optional[str] = None
    detail: str = ""
    fills: tuple[Fill, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    quantity: float  # signed: + long, - short
    entry_price: Optional[float]

    @property
    def side(self) -> int:
        return 0 if self.quantity == 0 else (1 if self.quantity > 0 else -1)


@dataclass(frozen=True)
class AccountSnapshot:
    wallet_balance: float
    equity: float
    available: float
    at: datetime
