#!/usr/bin/env python3
"""Compares configs/markets.json with Binance USD-M exchangeInfo (T7). Run it on a PC that can reach Binance.

    python3 scripts/verify_markets.py                          # fetch fapi.binance.com, print differences
    python3 scripts/verify_markets.py --from-file info.json    # use a saved copy of /fapi/v1/exchangeInfo
    python3 scripts/verify_markets.py --write                  # also fix the file and set "verified": true

Exit 0 only if every configured symbol matches and is TRADING. `--write` changes only the four exchange
rule fields (tick, step, min quantity, min notional) and `verified`; `large_trade_quantity` is our own
setting and is never touched. Commit the result with a note of the date you ran it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.execution.binance_client import BinanceFuturesClient, parse_exchange_info  # noqa: E402
from cointrader.execution.market_check import FIELDS, compare_markets  # noqa: E402
from cointrader.settings import load_markets  # noqa: E402

PATH = REPO / "configs" / "markets.json"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-file", type=Path)
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    raw = json.loads(PATH.read_text(encoding="utf-8"))
    if args.from_file:
        filters = parse_exchange_info(json.loads(args.from_file.read_text(encoding="utf-8")))
    else:
        filters = BinanceFuturesClient().exchange_filters()
    diffs, missing = compare_markets(raw["markets"], filters)
    for sym in missing:
        print(f"MISSING  {sym}: not TRADING on the exchange (or absent)")
    for d in diffs:
        print(f"DIFF     {d.symbol}.{d.field}: config {d.configured} -> exchange {d.exchange}")
    ok = not diffs and not missing
    print("all configured markets match the exchange" if ok else f"{len(diffs)} difference(s), {len(missing)} missing")
    if args.write:
        if missing:
            print("not writing: fix the missing symbols first", file=sys.stderr)
            return 1
        for d in diffs:
            raw["markets"][d.symbol][d.field] = d.exchange
        raw["verified"] = True
        PATH.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        load_markets(PATH)  # must still parse
        print(f"wrote {PATH.relative_to(REPO)} with verified=true")
        return 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
