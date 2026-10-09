#!/usr/bin/env python3
"""Sends the weekly paper-performance summary to Discord (T14).

Off by default (configs/weekly_summary.json "enabled": false) and never sends
without the DISCORD_WEBHOOK_URL environment variable. At most one send per ISO
week. Run it from a scheduler, e.g. Sunday evening:

    python3 scripts/send_weekly_summary.py              # honours the config
    python3 scripts/send_weekly_summary.py --dry-run    # print the text, send nothing
    python3 scripts/send_weekly_summary.py --force      # ignore "enabled" and the once-a-week check
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.journal.store import LayeredStore  # noqa: E402
from cointrader.notifications.weekly_summary import (  # noqa: E402
    build_summary, format_summary, load_weekly_config, maybe_send_weekly,
)
from cointrader.settings import load_paper  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO / load_paper()["data_root"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    cfg, store, now = load_weekly_config(), LayeredStore(args.data_root), datetime.now(timezone.utc)
    if args.dry_run:
        print(format_summary(build_summary(store, now, mode=cfg["mode"], days=int(cfg["days"]))))
        return 0
    sent, reason = maybe_send_weekly(store, now, cfg=cfg, env=os.environ, force=args.force)
    print(reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
