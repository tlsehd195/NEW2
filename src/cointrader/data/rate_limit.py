"""Request budgeting for exchange REST APIs.

Concept taken from tlsehd195/NEW-'s `tiingo_budget.py` (spend a known
budget deliberately instead of discovering the limit by getting banned),
reimplemented for exchange-style limits: a per-second / per-minute
request window, plus whatever the exchange reports back about the
remaining budget.

Upbit reports its remaining budget in every response header, e.g.
`Remaining-Req: group=candles; min=1800; sec=29`. Binance instead uses a
request *weight* system (`X-MBX-USED-WEIGHT-1M`); a Binance adapter would
add its own parser and feed `observe_remaining` the same way.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class RemainingRequests:
    group: str
    per_minute: Optional[int]
    per_second: Optional[int]


def parse_upbit_remaining_req(header: Optional[str]) -> Optional[RemainingRequests]:
    """Parses Upbit's `Remaining-Req` header. Returns None when the header
    is absent or unparseable -- the caller then falls back to its own
    client-side window rather than assuming budget is available."""
    if not header:
        return None
    fields: dict[str, str] = {}
    for part in header.split(";"):
        if "=" in part:
            key, _, value = part.strip().partition("=")
            fields[key.strip()] = value.strip()
    if "group" not in fields:
        return None

    def as_int(key: str) -> Optional[int]:
        try:
            return int(fields[key])
        except (KeyError, ValueError):
            return None

    return RemainingRequests(group=fields["group"], per_minute=as_int("min"), per_second=as_int("sec"))


class RequestBudget:
    """Sliding one-second window of at most `max_per_second` requests,
    tightened further whenever the exchange reports fewer remaining than
    `reserve`. `acquire()` blocks (via the injected `sleep`) until a
    request may be sent; it never drops a request silently."""

    def __init__(
        self,
        max_per_second: int,
        *,
        reserve: int = 1,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_per_second < 1:
            raise ValueError("max_per_second must be >= 1")
        self._max = max_per_second
        self._reserve = reserve
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._exchange_says_wait_until: float = 0.0

    def observe_remaining(self, remaining: Optional[RemainingRequests]) -> None:
        if remaining is None:
            return
        now = self._clock()
        if remaining.per_second is not None and remaining.per_second <= self._reserve:
            self._exchange_says_wait_until = max(self._exchange_says_wait_until, now + 1.0)
        if remaining.per_minute is not None and remaining.per_minute <= self._reserve:
            self._exchange_says_wait_until = max(self._exchange_says_wait_until, now + 60.0)

    def acquire(self) -> None:
        while True:
            now = self._clock()
            while self._sent and now - self._sent[0] >= 1.0:
                self._sent.popleft()
            wait = 0.0
            if now < self._exchange_says_wait_until:
                wait = self._exchange_says_wait_until - now
            elif len(self._sent) >= self._max:
                wait = 1.0 - (now - self._sent[0])
            if wait <= 0:
                self._sent.append(now)
                return
            self._sleep(wait)
