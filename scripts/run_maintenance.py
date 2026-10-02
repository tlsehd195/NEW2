#!/usr/bin/env python3
"""Data maintenance: compress old partitions, apply retention (raw layer
only -- protected layers are never deleted), report storage. Safe to run
while the paper trader is running: it never touches today's partition
and each run is bounded by --max-files.

    python3 scripts/run_maintenance.py [--max-files 50]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.journal.store import LayeredStore, RetentionPolicy, apply_retention, storage_report  # noqa: E402
from cointrader.notifications.notifier import Notifier, Severity  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402


def main() -> int:
    cfg = load_paper()
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=REPO / cfg["data_root"])
    ap.add_argument("--max-files", type=int, default=50)
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    store = LayeredStore(args.data_root)
    policy = RetentionPolicy.load(REPO / "configs" / "retention.json")
    rep = apply_retention(store, policy, today=now.date(), max_files=args.max_files)
    storage = storage_report(store, policy)
    store.append("audit", {"event": "maintenance", "compressed": len(rep.compressed), "deleted": len(rep.deleted),
                           "skipped": len(rep.skipped), "storage_level": storage.level,
                           "total_bytes": storage.total_bytes}, at=now)
    if storage.level != "ok":
        Notifier(mode="paper").notify(Severity.CRITICAL if storage.level == "critical" else Severity.WARNING,
                                      "storage", storage.detail, at=now)
    print(json.dumps({"compressed": rep.compressed, "deleted": rep.deleted, "skipped": len(rep.skipped),
                      "storage": {"level": storage.level, "bytes_by_layer": storage.bytes_by_layer,
                                  "detail": storage.detail}}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
