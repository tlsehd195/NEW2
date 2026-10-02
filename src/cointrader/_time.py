"""Shared datetime guards. Every timestamp in this package is
timezone-aware UTC; a naive datetime is a bug, never silently assumed."""

from __future__ import annotations

from datetime import datetime


def require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"{name} must be timezone-aware: {value!r}")
