"""Append-only trade journal: every decision, its inputs and rationale,
and its outcome, stored as raw JSONL. This is the dataset the learning
cycle trains on (stage 1 of the NEW- self-improvement loop), so nothing
is ever rewritten or deleted -- an outcome is a new record that points
at its decision.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from cointrader._time import require_aware


@dataclass(frozen=True)
class DecisionRecord:
    market: str
    strategy: str
    strategy_status: str
    decided_at: datetime
    bar_open_time: datetime  # the last closed bar the decision was based on
    signal_exposure: float
    target_weight: float
    sizing_reason: str
    mode: str  # "backtest" | "paper" | "live"
    inputs: dict = field(default_factory=dict)  # features/indicator values actually used
    decision_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        require_aware("DecisionRecord.decided_at", self.decided_at)
        require_aware("DecisionRecord.bar_open_time", self.bar_open_time)
        if self.bar_open_time >= self.decided_at:
            raise ValueError("a decision cannot be based on a bar that opened at or after the decision time")


@dataclass(frozen=True)
class OutcomeRecord:
    decision_id: str
    recorded_at: datetime
    filled_quantity: float
    average_price: Optional[float]
    fee: float
    realized_return: Optional[float]
    note: str = ""

    def __post_init__(self) -> None:
        require_aware("OutcomeRecord.recorded_at", self.recorded_at)


def _encode(obj: object) -> dict:
    d = asdict(obj)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


class TradeJournal:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _append(self, kind: str, obj: object) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, **_encode(obj)}, ensure_ascii=False) + "\n")

    def record_decision(self, record: DecisionRecord) -> str:
        self._append("decision", record)
        return record.decision_id

    def record_outcome(self, record: OutcomeRecord) -> None:
        self._append("outcome", record)

    def rows(self) -> Iterator[dict]:
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
