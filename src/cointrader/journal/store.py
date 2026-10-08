"""Layered, append-only data store (ADR-0015).

Every fact the system produces lands in exactly one layer:

    raw         exchange messages as received (large; retention configurable)
    normalized  parsed candles / trades / book tickers / 1m trade stats
    feature     feature + regime snapshots that a decision actually used
    decision    signal -> quality gate -> risk -> intent, per evaluation
    execution   orders, fills, cancels, broker answers
    outcome     closed trades with cost decomposition, MFE/MAE
    quality     DATA_QUALITY_EVENT records
    safety      kill switch reads, reconciliation results, blocks, risk events
    audit       process start/stop, config/version changes, maintenance runs
    learning    daily drift checks and shadow (challenger) evaluations (ADR-0044)

Files are partitioned `<root>/<layer>/<YYYY-MM-DD>.jsonl` by the record's
UTC time. Nothing is ever rewritten in place. The only mutations are the
maintenance job's: gzip a past partition (content unchanged, readable
transparently) and -- for layers whose retention explicitly allows it --
delete a partition older than its retention. Layers in
`PROTECTED_LAYERS` can never be deleted: the retention loader refuses a
config that tries, and `apply_retention` refuses again at run time.

This extends the existing append-only JSONL approach of
`journal/trade_journal.py` and `execution/engine.OrderStore` instead of
adding a second storage technology (no database: `src/` is stdlib-only
and the data volumes of a few symbols fit comfortably in daily files).
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

from cointrader._time import require_aware

DATA_SCHEMA_VERSION = "1.0.0"

LAYERS = ("raw", "normalized", "feature", "decision", "execution", "outcome", "quality", "safety", "audit", "learning")

# Never deleted by any automatic process: orders, fills, positions, PnL,
# risk decisions, safety events, audit trail, outcomes, strategy versions,
# and the features/candles the research dataset is rebuilt from.
PROTECTED_LAYERS = frozenset({"normalized", "feature", "decision", "execution", "outcome", "quality", "safety", "audit",
                              "learning"})

# Minimal required fields per layer: a record missing them is refused
# (fail-closed) rather than stored half-empty.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "raw": ("source", "stream"),
    "normalized": ("kind", "symbol", "source"),
    "feature": ("symbol", "timeframe", "feature_version", "features"),
    "decision": ("decision_id", "symbol", "strategy_id", "strategy_version", "action", "reason", "mode"),
    "execution": ("event", "client_order_id", "symbol", "mode"),
    "outcome": ("trade_id", "symbol", "strategy_id", "net_pnl", "mode"),
    "quality": ("kind", "symbol", "detail", "blocks_trading"),
    "safety": ("event", "detail"),
    "audit": ("event",),
    "learning": ("event", "symbol", "timeframe", "day"),
}


class StoreError(ValueError):
    pass


def _partition_name(at: datetime) -> str:
    return at.date().isoformat()


class LayeredStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dir(self, layer: str) -> Path:
        if layer not in LAYERS:
            raise StoreError(f"unknown layer {layer!r}")
        return self.root / layer

    def append(self, layer: str, record: dict, *, at: datetime) -> dict:
        require_aware("LayeredStore.append at", at)
        d = self._dir(layer)
        missing = [k for k in REQUIRED_FIELDS[layer] if k not in record]
        if missing:
            raise StoreError(f"{layer} record missing required fields {missing}")
        row = {"layer": layer, "recorded_at": at.isoformat(), "data_schema_version": DATA_SCHEMA_VERSION, **record}
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{_partition_name(at)}.jsonl"
        if (d / f"{_partition_name(at)}.jsonl.gz").exists():
            # A late record for an already-archived day goes to a sibling file; archives are never reopened.
            path = d / f"{_partition_name(at)}.late.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return row

    def partitions(self, layer: str) -> list[Path]:
        d = self._dir(layer)
        if not d.exists():
            return []
        return sorted(p for p in d.iterdir() if p.name.endswith((".jsonl", ".jsonl.gz")))

    def read(self, layer: str, *, start: Optional[date] = None, end: Optional[date] = None) -> Iterator[dict]:
        for p in self.partitions(layer):
            day = date.fromisoformat(p.name[:10])
            if (start and day < start) or (end and day > end):
                continue
            opener = gzip.open if p.name.endswith(".gz") else open
            with opener(p, "rt", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        yield json.loads(line)


# ---------------------------------------------------------------------------
# retention


@dataclass(frozen=True)
class LayerRetention:
    compress_after_days: Optional[int]  # gzip partitions older than this
    delete_after_days: Optional[int]  # None = keep forever

    def __post_init__(self) -> None:
        for name in ("compress_after_days", "delete_after_days"):
            v = getattr(self, name)
            if v is not None and v < 1:
                raise StoreError(f"{name} must be >= 1 day")


@dataclass(frozen=True)
class RetentionPolicy:
    layers: dict[str, LayerRetention]
    warn_total_bytes: int
    critical_total_bytes: int

    @classmethod
    def from_dict(cls, d: dict) -> "RetentionPolicy":
        layers = {}
        for name in LAYERS:
            cfg = d.get("layers", {}).get(name)
            if cfg is None:
                raise StoreError(f"retention config has no entry for layer {name!r}")
            r = LayerRetention(cfg.get("compress_after_days"), cfg.get("delete_after_days"))
            if name in PROTECTED_LAYERS and r.delete_after_days is not None:
                raise StoreError(f"layer {name!r} is protected; delete_after_days must be null")
            layers[name] = r
        return cls(layers, int(d["warn_total_bytes"]), int(d["critical_total_bytes"]))

    @classmethod
    def load(cls, path: Path) -> "RetentionPolicy":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass(frozen=True)
class MaintenanceReport:
    compressed: tuple[str, ...]
    deleted: tuple[str, ...]
    skipped: tuple[str, ...]


def _gzip_file(src: Path) -> Path:
    dst = src.with_name(src.name + ".gz")
    tmp = dst.with_name(dst.name + ".tmp")
    with src.open("rb") as fi, gzip.open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    # verify before removing the original: the archive must decompress to identical bytes
    with gzip.open(tmp, "rb") as check:
        if check.read() != src.read_bytes():
            tmp.unlink()
            raise StoreError(f"gzip verification failed for {src}")
    os.replace(tmp, dst)
    src.unlink()
    return dst


def apply_retention(store: LayeredStore, policy: RetentionPolicy, *, today: date,
                    max_files: Optional[int] = None) -> MaintenanceReport:
    """Compress then (only for unprotected layers) delete old partitions.

    Today's partition is never touched. `max_files` bounds one run so the
    job can be sliced into small steps that never stall trading."""
    compressed, deleted, skipped = [], [], []
    budget = max_files if max_files is not None else 1 << 30
    for layer in LAYERS:
        rule = policy.layers[layer]
        for p in store.partitions(layer):
            if budget <= 0:
                skipped.append(str(p))
                continue
            age = (today - date.fromisoformat(p.name[:10])).days
            if age <= 0:
                continue
            if rule.delete_after_days is not None and age > rule.delete_after_days:
                if layer in PROTECTED_LAYERS:  # defence in depth; the loader already refuses this
                    raise StoreError(f"refusing to delete protected layer {layer}")
                p.unlink()
                deleted.append(str(p))
                budget -= 1
                continue
            if (rule.compress_after_days is not None and age > rule.compress_after_days
                    and not p.name.endswith(".gz")):
                if p.name.endswith(".late.jsonl"):
                    skipped.append(str(p))  # merged by a human; archives are never reopened
                    continue
                compressed.append(str(_gzip_file(p)))
                budget -= 1
    return MaintenanceReport(tuple(compressed), tuple(deleted), tuple(skipped))


# ---------------------------------------------------------------------------
# storage monitoring


@dataclass(frozen=True)
class StorageReport:
    bytes_by_layer: dict[str, int]
    files_by_layer: dict[str, int]
    total_bytes: int
    free_bytes: Optional[int]
    level: str  # "ok" | "warning" | "critical"
    detail: str


def storage_report(store: LayeredStore, policy: RetentionPolicy, *, min_free_bytes: int = 1 << 30) -> StorageReport:
    by_layer, files = {}, {}
    for layer in LAYERS:
        parts = store.partitions(layer)
        by_layer[layer] = sum(p.stat().st_size for p in parts)
        files[layer] = len(parts)
    total = sum(by_layer.values())
    free = None
    if store.root.exists():
        free = shutil.disk_usage(store.root).free
    level, reasons = "ok", []
    if total >= policy.critical_total_bytes or (free is not None and free < min_free_bytes):
        level = "critical"
    elif total >= policy.warn_total_bytes:
        level = "warning"
    if total >= policy.warn_total_bytes:
        reasons.append(f"data store {total / 1e9:.2f} GB (warn {policy.warn_total_bytes / 1e9:.2f} GB)")
    if free is not None and free < min_free_bytes:
        reasons.append(f"free disk {free / 1e9:.2f} GB below {min_free_bytes / 1e9:.2f} GB")
    return StorageReport(by_layer, files, total, free, level, "; ".join(reasons) or "ok")


def growth_per_day(store: LayeredStore, layer: str, days: int = 7, *, today: date) -> Optional[float]:
    """Average bytes per day over the last `days` partitions (None if no data)."""
    start = today - timedelta(days=days)
    sizes = [p.stat().st_size for p in store.partitions(layer) if start <= date.fromisoformat(p.name[:10]) < today]
    return sum(sizes) / len(sizes) if sizes else None
