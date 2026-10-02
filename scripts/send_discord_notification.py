#!/usr/bin/env python3
"""Sends a report JSON to Discord. The webhook URL comes from the
DISCORD_WEBHOOK_URL environment variable, never from a file in the repo.

    DISCORD_WEBHOOK_URL=... python3 scripts/send_discord_notification.py \
        --report-type swing_study --report reports/H-0001.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.notifications.discord_webhook import (  # noqa: E402
    format_swing_study_report, send_discord_message,
)

FORMATTERS = {"swing_study": format_swing_study_report}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--report-type", choices=sorted(FORMATTERS), required=True)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--dry-run", action="store_true", help="print instead of sending")
    args = p.parse_args()
    content = FORMATTERS[args.report_type](json.loads(args.report.read_text(encoding="utf-8")))
    if args.dry_run:
        print(content)
        return 0
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("DISCORD_WEBHOOK_URL is not set", file=sys.stderr)
        return 2
    send_discord_message(url, content)
    return 0


if __name__ == "__main__":
    sys.exit(main())
