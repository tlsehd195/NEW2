"""One supervised sample: features known at `as_of_time`, a target that
only becomes known at `target_time` (> `as_of_time`)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from cointrader._time import require_aware


@dataclass(frozen=True)
class MLSample:
    as_of_time: datetime
    target_time: datetime
    features: dict
    target: float

    def __post_init__(self) -> None:
        require_aware("MLSample.as_of_time", self.as_of_time)
        require_aware("MLSample.target_time", self.target_time)
        if self.target_time <= self.as_of_time:
            raise ValueError("MLSample.target_time must be after as_of_time (target would leak)")
        if not math.isfinite(self.target):
            raise ValueError("MLSample.target must be finite")
        if not all(math.isfinite(v) for v in self.features.values()):
            raise ValueError("MLSample.features must all be finite; exclude the sample instead of imputing")
