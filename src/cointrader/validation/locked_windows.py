"""Registry of permanently LOCKED held-out TEST windows.

Rule adopted as-is from tlsehd195/NEW- (`strategy_research/
locked_windows.py`, its RULE 0.8): once a time range has been used as
the held-out TEST for any evaluation whose result informed a decision,
it is retired for good -- never again TRAIN, VALIDATION, or TEST, for
that strategy family or any future one. Re-testing on an already-seen
range is grading against a known answer key. NEW- recorded 51 candidates
that all failed to show an edge, some of them sharply underperforming on
held-out TEST; this rule is what keeps that kind of result honest.

Windows are stored in `configs/locked_windows.json` (append-only; never
edit or delete an entry). Record the TEST range verbatim from the run's
own report, not re-derived.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

from cointrader._time import require_aware

DEFAULT_REGISTRY = Path(__file__).resolve().parents[3] / "configs" / "locked_windows.json"


@dataclass(frozen=True)
class LockedWindow:
    name: str
    market: str  # "*" locks the range for every market
    start: datetime
    end: datetime
    observed_by: tuple[str, ...]
    note: str

    def __post_init__(self) -> None:
        require_aware("LockedWindow.start", self.start)
        require_aware("LockedWindow.end", self.end)
        if self.end <= self.start:
            raise ValueError("LockedWindow.end must be after start")


class LockedWindowViolation(ValueError):
    pass


def load_locked_windows(path: Path = DEFAULT_REGISTRY) -> tuple[LockedWindow, ...]:
    if not path.exists():
        return ()
    rows = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        LockedWindow(
            name=r["name"], market=r["market"],
            start=datetime.fromisoformat(r["start"]), end=datetime.fromisoformat(r["end"]),
            observed_by=tuple(r["observed_by"]), note=r["note"],
        )
        for r in rows
    )


def overlaps(windows: Iterable[LockedWindow], market: str, start: datetime, end: datetime) -> tuple[LockedWindow, ...]:
    """Every locked window overlapping `[start, end)` for `market`."""
    return tuple(
        w for w in windows
        if (w.market == "*" or w.market == market) and start < w.end and end > w.start
    )


def assert_not_locked(windows: Sequence[LockedWindow], market: str, start: datetime, end: datetime) -> None:
    """Call on every TRAIN, VALIDATION and TEST range before evaluating.
    Raises instead of warning: an overlap is a hard stop."""
    hit = overlaps(windows, market, start, end)
    if hit:
        names = ", ".join(w.name for w in hit)
        raise LockedWindowViolation(
            f"{market} [{start.isoformat()}, {end.isoformat()}) overlaps locked window(s): {names}"
        )


def append_locked_window(window: LockedWindow, path: Path = DEFAULT_REGISTRY) -> None:
    """Append-only. Refuses a duplicate name."""
    existing = load_locked_windows(path)
    if any(w.name == window.name for w in existing):
        raise ValueError(f"locked window {window.name!r} already exists")
    rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    rows.append({
        "name": window.name, "market": window.market,
        "start": window.start.isoformat(), "end": window.end.isoformat(),
        "observed_by": list(window.observed_by), "note": window.note,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
