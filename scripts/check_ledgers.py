#!/usr/bin/env python3
"""Checks candidate ledgers against locked/reserved windows (T9). Exit 1 on any ERROR.

    python3 scripts/check_ledgers.py            # print problems
    python3 scripts/check_ledgers.py --strict   # warnings fail too
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.validation.ledger_check import check_ledgers  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--strict", action="store_true")
    p.add_argument("--configs", type=Path, default=REPO / "configs")
    p.add_argument("--research", type=Path, default=REPO / "research")
    args = p.parse_args()
    problems = check_ledgers(args.configs, args.research)
    for pr in problems:
        print(pr)
    errors = [x for x in problems if x.level == "ERROR"]
    print(f"{len(errors)} error(s), {len(problems) - len(errors)} warning(s)")
    return 1 if errors or (args.strict and problems) else 0


if __name__ == "__main__":
    sys.exit(main())
