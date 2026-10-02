from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from cointrader.data.models import Candle, Timeframe

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def make_candles(n: int, *, start: datetime = T0, timeframe: Timeframe = Timeframe.HOUR_1,
                 seed: int = 0, drift: float = 0.0, vol: float = 0.01, source: str = "test",
                 market: str = "KRW-BTC", volume: float = 100.0) -> list[Candle]:
    rng = random.Random(seed)
    price = 50_000_000.0
    out = []
    for i in range(n):
        open_ = price
        price = price * math.exp(drift + rng.gauss(0, vol))
        close = price
        out.append(Candle(
            market=market, timeframe=timeframe, open_time=start + i * timeframe.delta,
            open=open_, high=max(open_, close) * 1.001, low=min(open_, close) * 0.999, close=close,
            volume=volume, source=source, received_at=start + (i + 1) * timeframe.delta,
        ))
    return out
