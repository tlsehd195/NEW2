#!/usr/bin/env python3
"""Paper/live status from persisted state and the journal (read-only).

    python3 scripts/show_status.py [--state-dir var/paper] [--data-root var/data]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.journal.store import LayeredStore, RetentionPolicy, storage_report  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402


def main() -> int:
    cfg = load_paper()
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", type=Path, default=REPO / cfg["state_dir"])
    ap.add_argument("--data-root", type=Path, default=REPO / cfg["data_root"])
    args = ap.parse_args()
    out = {"mode": "PAPER", "environment": cfg["environment"], "live_trading_enabled": cfg["live_trading_enabled"]}
    state_path = args.state_dir / "paper_state.json"
    if state_path.exists():
        s = json.loads(state_path.read_text(encoding="utf-8"))
        out["saved_at"] = s["saved_at"]
        out["balance"] = s["broker"]["balance"]
        out["positions"] = {k: v["quantity"] for k, v in s["broker"]["positions"].items() if v["quantity"]}
        out["open_trades"] = {k: {"strategy": v["strategy_id"], "direction": v["direction"], "state": v["state"],
                                  "stop": v["stop_price"]} for k, v in s["open_trades"].items()}
        out["resting_orders"] = len(s["broker"]["resting"])
    else:
        out["state"] = "no paper state yet (trader never ran here)"
    kill = REPO / cfg["kill_switch_path"]
    if kill.exists():
        lines = [l for l in kill.read_text(encoding="utf-8").splitlines() if l.strip()]
        out["paper_kill_switch"] = json.loads(lines[-1]) if lines else "empty log"
    else:
        out["paper_kill_switch"] = "no log (paper: not engaged)"
    store = LayeredStore(args.data_root)
    since = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    safety = [r for r in store.read("safety", start=since)]
    out["safety_events_24h"] = len(safety)
    out["last_reconciliation"] = next((r for r in reversed(safety) if r["event"] == "reconciliation"), None)
    out["quality_events_24h"] = sum(1 for _ in store.read("quality", start=since))
    rep = storage_report(store, RetentionPolicy.load(REPO / "configs" / "retention.json"))
    out["storage"] = {"level": rep.level, "total_mb": round(rep.total_bytes / 1e6, 2), "detail": rep.detail}
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
