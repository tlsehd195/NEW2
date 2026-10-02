"""Live trading configuration and component health states."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional


class HealthStatus(Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveTradingConfig:
    environment: str = "paper"  # "paper" | "live"
    live_trading_enabled: bool = False
    exchange: str = "upbit"
    # Limits. `None` means "not configured", which the safety gate treats
    # as a reason to refuse live trading (fail-closed), never as "no limit".
    max_daily_loss: Optional[float] = None  # KRW
    max_orders_per_hour: Optional[int] = None
    max_position_weight: Optional[float] = None

    def configuration_version(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:12]
