"""Pre-registration of hypotheses.

Before any evaluation runs, the hypothesis -- market, timeframe, data
ranges, the exact candidate list, the success criteria -- is written to
an append-only log with a content hash. After the result is known, the
evaluation must be matched against that record; any change produces a
different hash and therefore a *new* registration, visible in the log,
rather than a silent edit. The number of registered candidates is the
trial count DSR must deflate against.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from cointrader._time import require_aware


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    statement: str
    market: str
    timeframe: str
    data_start: datetime
    data_end: datetime
    candidates: tuple[str, ...]
    success_criteria: dict  # e.g. {"max_pbo": 0.2, "min_dsr": 0.95, "min_test_excess_return": 0.0}
    registered_by: str
    registered_at: datetime

    def __post_init__(self) -> None:
        require_aware("Hypothesis.data_start", self.data_start)
        require_aware("Hypothesis.data_end", self.data_end)
        require_aware("Hypothesis.registered_at", self.registered_at)
        if not self.candidates:
            raise ValueError("a hypothesis must list at least one candidate")
        if len(set(self.candidates)) != len(self.candidates):
            raise ValueError("candidate names must be unique")

    def _payload(self) -> dict:
        d = asdict(self)
        d["data_start"] = self.data_start.isoformat()
        d["data_end"] = self.data_end.isoformat()
        d["registered_at"] = self.registered_at.isoformat()
        d["candidates"] = list(self.candidates)
        return d

    def content_hash(self) -> str:
        """Hash of everything except who/when registered it."""
        payload = self._payload()
        payload.pop("registered_at")
        payload.pop("registered_by")
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


class PreregistrationLog:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _rows(self) -> list[dict]:
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def register(self, hypothesis: Hypothesis) -> str:
        """Appends; refuses to reuse an id with different content."""
        digest = hypothesis.content_hash()
        for row in self._rows():
            if row["hypothesis_id"] == hypothesis.hypothesis_id:
                if row["content_hash"] != digest:
                    raise ValueError(
                        f"hypothesis {hypothesis.hypothesis_id!r} is already registered with different "
                        "content; register the change under a new id"
                    )
                return digest
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**hypothesis._payload(), "content_hash": digest}, ensure_ascii=False) + "\n")
        return digest

    def get_hash(self, hypothesis_id: str) -> Optional[str]:
        for row in self._rows():
            if row["hypothesis_id"] == hypothesis_id:
                return row["content_hash"]
        return None

    def verify(self, hypothesis: Hypothesis) -> None:
        """Raises unless `hypothesis` matches its registered record exactly."""
        registered = self.get_hash(hypothesis.hypothesis_id)
        if registered is None:
            raise ValueError(f"hypothesis {hypothesis.hypothesis_id!r} was never pre-registered")
        if registered != hypothesis.content_hash():
            raise ValueError(f"hypothesis {hypothesis.hypothesis_id!r} changed after registration")

    def total_registered_candidates(self) -> int:
        """Across every registration: the honest trial count for DSR."""
        return sum(len(row["candidates"]) for row in self._rows())
