"""Severity-levelled operator notifications (INFO / TRADE / WARNING / CRITICAL).

Delivery reuses `discord_webhook.send_discord_message`; the webhook URL
comes only from the `DISCORD_WEBHOOK_URL` environment variable. Every
message is passed through `redact` first, which removes webhook URLs,
anything that looks like an API key/secret, and the literal values of
known secret environment variables -- a message can quote an exception
without leaking a credential.

A failed delivery never raises into the trading loop: it is counted and
kept in `failures` (the caller logs it to the safety layer). Repeated
identical messages are rate-limited per key so a flapping feed cannot
spam the channel.
"""

from __future__ import annotations

import os
import re
from collections import deque
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Callable, Optional

from cointrader.notifications.discord_webhook import send_discord_message


class Severity(IntEnum):
    INFO = 10
    TRADE = 20
    WARNING = 30
    CRITICAL = 40


SECRET_ENV_VARS = ("BINANCE_API_KEY", "BINANCE_API_SECRET", "UPBIT_ACCESS_KEY", "UPBIT_SECRET_KEY",
                   "DISCORD_WEBHOOK_URL")
_PATTERNS = (
    re.compile(r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\S+", re.I),
    re.compile(r"(?i)(api[_-]?key|secret|signature|token|password)\s*[=:]\s*[^\s&,;]+"),
    re.compile(r"(?i)X-MBX-APIKEY\S*\s*[:=]?\s*\S+"),
    re.compile(r"\b[A-Za-z0-9]{48,}\b"),  # long opaque tokens (Binance keys are 64 chars)
)

_ICONS = {Severity.INFO: "ℹ️", Severity.TRADE: "\U0001f4b1", Severity.WARNING: "⚠️",
          Severity.CRITICAL: "\U0001f6a8"}


def redact(text: str, env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    for name in SECRET_ENV_VARS:
        v = env.get(name)
        if v and len(v) >= 6:
            text = text.replace(v, "<redacted>")
    for p in _PATTERNS:
        text = p.sub("<redacted>", text)
    return text


def format_notification(severity: Severity, title: str, body: str, *, mode: str, at: datetime) -> str:
    return f"{_ICONS[severity]} **[{severity.name}] [{mode.upper()}] {title}**\n{body}\n_{at.isoformat()}_"


class Notifier:
    def __init__(self, *, mode: str, min_severity: Severity = Severity.INFO,
                 sink: Optional[Callable[[str], None]] = None, repeat_after: timedelta = timedelta(minutes=10),
                 env: Optional[dict] = None) -> None:
        self.mode = mode
        self.min_severity = min_severity
        self._env = os.environ if env is None else env
        self._sink = sink if sink is not None else self._discord_sink()
        self._repeat_after = repeat_after
        self._last_sent: dict[str, datetime] = {}
        self.sent: deque = deque(maxlen=200)
        self.failures: deque = deque(maxlen=200)
        self.suppressed = 0

    def _discord_sink(self) -> Optional[Callable[[str], None]]:
        url = self._env.get("DISCORD_WEBHOOK_URL")
        if not url:
            return None
        return lambda content: send_discord_message(url, content)

    def notify(self, severity: Severity, title: str, body: str, *, at: datetime, key: Optional[str] = None) -> bool:
        if severity < self.min_severity:
            return False
        k = key or f"{severity.name}:{title}"
        last = self._last_sent.get(k)
        if last is not None and at - last < self._repeat_after and severity < Severity.CRITICAL:
            self.suppressed += 1
            return False
        content = redact(format_notification(severity, title, body, mode=self.mode, at=at), self._env)
        self._last_sent[k] = at
        if self._sink is None:
            self.failures.append((at, "no DISCORD_WEBHOOK_URL configured"))
            return False
        try:
            self._sink(content)
        except Exception as exc:  # noqa: BLE001 - delivery must never crash trading
            self.failures.append((at, redact(f"{type(exc).__name__}: {exc}", self._env)))
            return False
        self.sent.append((at, severity.name, title))
        return True
