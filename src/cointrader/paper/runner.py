"""Wiring for `scripts/run_paper_trader.py`: builds a PaperTrader from
the configs and runs it against a message source (live WebSocket or a
recorded replay file). Kept in `src/` so it is importable by tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

from cointrader.data.binance_ws import combined_stream_url, standard_streams
from cointrader.data.feed import CandleHistory, FeedUnavailable
from cointrader.data.models import Timeframe
from cointrader.data.realtime import FeedLimits, ResilientEventFeed
from cointrader.journal.store import LayeredStore
from cointrader.notifications.notifier import Notifier, Severity
from cointrader.paper.engine import PaperConfig, PaperTrader
from cointrader.risk.engine import RiskEngine
from cointrader.settings import REPO, load_margin_policy, load_markets, load_paper, load_risk
from cointrader.strategies.registry import StrategyRegistry


class ReplayFileSource:
    """MessageSource over a JSONL file of raw exchange messages (one per
    line), for offline runs and tests. `received_at` is the replay clock."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lines: Optional[Iterator[str]] = None

    def connect(self) -> None:
        self._lines = iter(self._path.read_text(encoding="utf-8").splitlines())

    def messages(self) -> Iterator[str]:
        for line in self._lines or ():
            if line.strip():
                yield line

    def close(self) -> None:
        self._lines = None


def build_trader(paper_cfg: dict, *, notifier: Optional[Notifier] = None, root: Path = REPO) -> PaperTrader:
    filters, large, verified = load_markets()
    registry = StrategyRegistry.load()
    strategies = {}
    for sid in paper_cfg["strategies"]:
        spec = registry.get(sid)
        for sym in paper_cfg["symbols"]:
            if sym not in spec.markets:
                raise ValueError(f"{sid} is not registered for {sym}")
        strategies[sid] = (registry.build(sid), spec.version)
    for sym in paper_cfg["symbols"]:
        if sym not in filters:
            raise ValueError(f"{sym} missing from configs/markets.json")
    config = PaperConfig(
        state_dir=root / paper_cfg["state_dir"], kill_switch_path=root / paper_cfg["kill_switch_path"],
        initial_balance=paper_cfg["initial_balance"], record_raw=paper_cfg["record_raw"],
        save_every=timedelta(seconds=paper_cfg["save_every_seconds"]),
        reconcile_every=timedelta(seconds=paper_cfg["reconcile_every_seconds"]),
        large_trade_quantity=large,
    )
    mp, mt, mp_verified = load_margin_policy()
    notifier = notifier or Notifier(mode="paper", min_severity=Severity[paper_cfg["notify_min_severity"]])
    trader = PaperTrader(config=config, strategies=strategies, symbols=paper_cfg["symbols"],
                         risk=RiskEngine(load_risk(), filters), filters=filters,
                         margin_policy=mp, margin_tiers=mt,
                         store=LayeredStore(root / paper_cfg["data_root"]), notifier=notifier)
    if not mp_verified:
        trader.notifier.notify(Severity.WARNING, "margin tiers assumed",
                               "configs/margin_policy.json tiers are an unverified snapshot (strict on purpose)",
                               at=datetime.now(timezone.utc), key="margin_tiers_assumed")
    if not verified:
        trader.notifier.notify(Severity.WARNING, "market rules unverified",
                               "configs/markets.json is a placeholder; paper fills use it as-is",
                               at=datetime.now(timezone.utc), key="markets_unverified")
    return trader


def timeframes_of(trader: PaperTrader) -> list[Timeframe]:
    return sorted({Timeframe(s.timeframe) for s, _ in trader.strategies.values()}, key=lambda t: t.delta)


def bootstrap(trader: PaperTrader, history: CandleHistory, now: datetime, bars: int = 260) -> int:
    n = 0
    for sym in trader.symbols:
        for tf in timeframes_of(trader):
            candles = history.fetch(sym, tf, now - tf.delta * bars, now)
            trader.bootstrap_history([c for c in candles if c.close_time <= now])
            n += len(candles)
    return n


def run(trader: PaperTrader, source, *, history: Optional[CandleHistory], now: Callable[[], datetime],
        max_events: Optional[int] = None, sleep=None, limits: FeedLimits = FeedLimits()) -> int:
    feed_kwargs = {"history": history, "limits": limits, "now": now}
    if sleep is not None:
        feed_kwargs["sleep"] = sleep
    feed = ResilientEventFeed(source, **feed_kwargs)
    trader.start(now())
    processed = 0
    try:
        for item in feed.run():
            trader.process(item)
            processed += 1
            if max_events is not None and processed >= max_events:
                break
    except FeedUnavailable as exc:
        trader.notifier.notify(Severity.CRITICAL, "feed unavailable", str(exc), at=now())
        raise
    finally:
        if trader._now is not None:
            trader.save_state()
    return processed


def live_stream_url(trader: PaperTrader) -> str:
    streams = []
    for sym in trader.symbols:
        streams += standard_streams(sym, timeframes_of(trader))
    return combined_stream_url(streams)


def load_default_paper_config() -> dict:
    return load_paper()


def dump_status(trader: PaperTrader) -> str:
    return json.dumps(trader.status(), ensure_ascii=False, indent=2, default=str)
