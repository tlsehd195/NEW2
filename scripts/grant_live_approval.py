#!/usr/bin/env python3
"""Human-only: records a LiveActivationApproval to a JSON file.

Run this yourself, in a terminal, after reviewing the strategy's
walk-forward, PBO/DSR and held-out TEST evidence. It asks you to type the
confirmation phrase; it never accepts it as an argument, so no script or
agent can pass it through.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.live.approval import (  # noqa: E402
    REQUIRED_CONFIRMATION_TOKEN, LiveActivationApproval, approval_to_payload,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--operator", required=True, help="your own name")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if not sys.stdin.isatty():
        print("refusing: must be run interactively by a human", file=sys.stderr)
        return 2
    print(f'Type exactly: {REQUIRED_CONFIRMATION_TOKEN}')
    token = input("> ")
    checklist = input("Live checklist completed? (yes/no) ").strip().lower() == "yes"
    reviewed = input("Strategy evidence (walk-forward, PBO/DSR, TEST) reviewed? (yes/no) ").strip().lower() == "yes"
    approval = LiveActivationApproval(args.operator, datetime.now(timezone.utc), token, checklist, reviewed)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(approval_to_payload(approval), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
