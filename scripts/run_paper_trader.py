#!/usr/bin/env python3
"""Always-on PAPER trader (no real orders can be sent from here).

    python3 scripts/run_paper_trader.py                 # live Binance market data, simulated fills
    python3 scripts/run_paper_trader.py --replay rec.jsonl --no-bootstrap   # offline replay

Configuration: configs/paper.json, configs/risk.json, configs/markets.json,
configs/strategies.json. State: var/paper/ (atomic JSON + append-only
order log). Journal: var/data/<layer>/<date>.jsonl. Optional Discord
alerts via the DISCORD_WEBHOOK_URL environment variable.

Binance's live API/WebSocket answers HTTP 451 from GitHub Actions (US
region, ADR-0012); run this on a machine in a permitted region.
Restarting is safe: the recovery sequence restores state and reconciles
before any new entry. Stop with Ctrl-C / SIGTERM (state is saved).
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.data.binance_futures import BinanceFuturesCandles  # noqa: E402
from cointrader.data.feed import FeedUnavailable  # noqa: E402
from cointrader.data.realtime import WebSocketMessageSource  # noqa: E402
from cointrader.paper.runner import (  # noqa: E402
    ReplayFileSource,
    bootstrap,
    build_trader,
    dump_status,
    live_stream_url,
    load_default_paper_config,
    run,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _start_krw_rates(cfg: dict) -> None:
    """Background Upbit KRW-USDT collection + the simulated paper purchase that funds the account
    (ADR-0040). Never blocks or stops trading: failures are printed and retried next round."""
    import threading

    from cointrader.accounting.krw_rates import collect, ensure_paper_start, load_series
    from cointrader.data.upbit_rest import UpbitRestCandles
    from cointrader.settings import load_krw_accounting

    csv_path = REPO / cfg["data_root"] / "krw_usdt.csv"
    flows = REPO / cfg["state_dir"] / "krw_flows.jsonl"
    _, _, staleness = load_krw_accounting()
    client = UpbitRestCandles()

    def loop() -> None:
        while True:
            try:
                collect(client, csv_path, now=_now())
                ensure_paper_start(flows, load_series(csv_path, staleness), usdt=cfg["initial_balance"],
                                   now=_now(), fee_rate=0.0005)
            except Exception as exc:  # noqa: BLE001
                print(f"krw rate collection failed: {exc}", file=sys.stderr, flush=True)
            time.sleep(900)

    threading.Thread(target=loop, name="krw-rates", daemon=True).start()


def _print_krw(cfg: dict) -> None:
    from cointrader.accounting.krw_report import build_krw_report, one_line
    try:
        rep, _ = build_krw_report(mode="paper", flows_path=REPO / cfg["state_dir"] / "krw_flows.jsonl",
                                  rates_path=REPO / cfg["data_root"] / "krw_usdt.csv",
                                  data_root=REPO / cfg["data_root"])
        print(one_line(rep), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"KRW report unavailable: {exc}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", type=Path, help="JSONL of raw exchange messages instead of the live stream")
    ap.add_argument("--no-bootstrap", action="store_true", help="skip the REST warmup history download")
    ap.add_argument("--max-events", type=int)
    ap.add_argument("--restart-pause", type=float, default=60.0, help="seconds before reconnecting after an outage")
    args = ap.parse_args()

    def stop(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    cfg = load_default_paper_config()
    history = None if args.replay else BinanceFuturesCandles()
    if not args.replay:
        _start_krw_rates(cfg)
    while True:
        trader = build_trader(cfg)
        if not args.no_bootstrap and history is not None:
            print(f"bootstrapped {bootstrap(trader, history, _now())} candles", flush=True)
        source = ReplayFileSource(args.replay) if args.replay else WebSocketMessageSource(live_stream_url(trader))
        try:
            n = run(trader, source, history=history, now=_now, max_events=args.max_events)
            print(f"processed {n} events", flush=True)
            print(dump_status(trader))
            _print_krw(cfg)
            return 0
        except FeedUnavailable as exc:
            print(f"feed unavailable: {exc}; restarting in {args.restart_pause:.0f}s", file=sys.stderr, flush=True)
            if args.replay:
                return 2
            time.sleep(args.restart_pause)
        except KeyboardInterrupt:
            print(dump_status(trader))
            _print_krw(cfg)
            return 0


if __name__ == "__main__":
    sys.exit(main())
