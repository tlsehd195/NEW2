"""Chronological TRAIN/VALIDATION/TEST split and rolling walk-forward
windows, in hours/minutes instead of months.

Methodology adopted as-is from tlsehd195/NEW-'s `strategy_research/
splits.py`; only the unit changed. Pure datetime arithmetic: no data
access, no wall clock, no randomness.

`embargo` (optional) leaves a gap between each train and test window so
a signal computed from the last train bars cannot overlap the first test
bars' labels. For bar-by-bar swing strategies with a lookback of N bars,
an embargo of at least one bar is recommended.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from cointrader._time import require_aware


@dataclass(frozen=True)
class TrainValidationTestSplit:
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    test_start: datetime
    test_end: datetime

    def __post_init__(self) -> None:
        ordered = (
            self.train_start, self.train_end,
            self.validation_start, self.validation_end,
            self.test_start, self.test_end,
        )
        for value in ordered:
            require_aware("split boundary", value)
        if list(ordered) != sorted(ordered):
            raise ValueError("train/validation/test windows must be non-overlapping and strictly ordered")


def build_chronological_split(
    start: datetime, end: datetime, *, train_fraction: float = 0.6, validation_fraction: float = 0.2,
    align_to: timedelta = timedelta(hours=1),
) -> TrainValidationTestSplit:
    """Splits `[start, end)` chronologically, never randomly. Boundaries
    are floored to `align_to` so each one falls on a bar boundary."""
    require_aware("start", start)
    require_aware("end", end)
    if not (0.0 < train_fraction < 1.0 and 0.0 < validation_fraction < 1.0):
        raise ValueError("fractions must be in (0, 1)")
    if train_fraction + validation_fraction >= 1.0:
        raise ValueError("train_fraction + validation_fraction must leave a positive test window")
    if end <= start:
        raise ValueError("end must be after start")

    def floor(value: datetime) -> datetime:
        offset = (value - start) % align_to
        return value - offset

    train_end = floor(start + (end - start) * train_fraction)
    validation_end = floor(start + (end - start) * (train_fraction + validation_fraction))
    if not (start < train_end < validation_end < end):
        raise ValueError("range too short for this split at this alignment")
    return TrainValidationTestSplit(start, train_end, train_end, validation_end, validation_end, end)


@dataclass(frozen=True)
class WalkForwardWindow:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def generate_walk_forward_windows(
    start: datetime, end: datetime, *, train: timedelta, test: timedelta, step: timedelta,
    embargo: timedelta = timedelta(0),
) -> list[WalkForwardWindow]:
    """Rolling windows: train on `[s, s+train)`, skip `embargo`, test on
    the next `test`, slide by `step`, until a test window would pass
    `end`. An empty list is the honest answer for insufficient history,
    not an error."""
    require_aware("start", start)
    require_aware("end", end)
    if train <= timedelta(0) or test <= timedelta(0) or step <= timedelta(0):
        raise ValueError("train, test and step must be positive")
    if embargo < timedelta(0):
        raise ValueError("embargo must be >= 0")
    if end <= start:
        raise ValueError("end must be after start")
    windows = []
    s = start
    while True:
        train_end = s + train
        test_start = train_end + embargo
        test_end = test_start + test
        if test_end > end:
            break
        windows.append(WalkForwardWindow(s, train_end, test_start, test_end))
        s += step
    return windows
