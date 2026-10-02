#!/usr/bin/env python3
"""Compare local position/order state with the broker's and REPORT.

    python3 scripts/reconcile_positions.py                 # paper state
    python3 scripts/reconcile_positions.py --binance       # read-only check vs a real Binance account

Never places, cancels or changes an order: a mismatch is printed (exit
code 1) for a human to investigate. `--binance` uses only signed
READ-ONLY endpoints (positions, open orders) with BINANCE_API_KEY /
BINANCE_API_SECRET from the environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.execution.engine import OrderStore  # noqa: E402
from cointrader.execution.paper_broker import PaperBroker  # noqa: E402
from cointrader.execution.reconciliation import reconcile  # noqa: E402
from cointrader.settings import load_markets, load_paper  # noqa: E402


def main() -> int:
    cfg = load_paper()
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", type=Path, default=REPO / cfg["state_dir"])
    ap.add_argument("--binance", action="store_true")
    args = ap.parse_args()
    filters, _, _ = load_markets()
    state_path = args.state_dir / "paper_state.json"
    if not state_path.exists():
        print("no local state to reconcile")
        return 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    local_positions = {s: t["direction"] * max(t["entry_qty"] - t["exit_qty"], 0.0)
                       for s, t in state["open_trades"].items()}
    local_orders = [r["client_order_id"] for r in OrderStore(args.state_dir / "orders.jsonl").open()]
    if args.binance:
        from cointrader.execution.binance_client import BinanceFuturesClient, Credentials
        client = BinanceFuturesClient(Credentials.from_env())
        positions, orders = client.positions(), client.open_orders()
    else:
        broker = PaperBroker.from_dict(state["broker"])
        positions, orders = broker.positions(), broker.open_orders()
    rep = reconcile(at=datetime.now(timezone.utc), local_positions=local_positions, exchange_positions=positions,
                    local_open_order_ids=local_orders, exchange_open_orders=orders,
                    step_sizes={s: f.step_size for s, f in filters.items()})
    print(json.dumps({"ok": rep.ok, "mismatches": list(rep.mismatches), "symbols": list(rep.checked_symbols)},
                     indent=2))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
