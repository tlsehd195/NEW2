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
from cointrader.learning.cycle import DailyLearningCycle, challenger_step, drift_step, lag_for
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

LEARNING_TIMEFRAME = "15m"  # single timeframe (project decision 2026-10-02)
WARMUP_MARGIN = 20  # extra bars so one missed stream bar does not drop a strategy back to warm-up


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
    # Keep (and bootstrap) enough closed bars for the longest strategy warm-up; a shorter buffer
    # would leave every decision at "warmup" forever (the 15m day-trade votes need ~1,190 bars).
    need = max([getattr(s, "warmup", 0) for s, _ in strategies.values()] + [0])
    config = PaperConfig(
        state_dir=root / paper_cfg["state_dir"], kill_switch_path=root / paper_cfg["kill_switch_path"],
        max_candles=max(PaperConfig.max_candles, need + WARMUP_MARGIN),
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


def bootstrap(trader: PaperTrader, history: CandleHistory, now: datetime, bars: Optional[int] = None) -> int:
    """Fetch closed bars over REST before the stream starts; by default as many as the trader keeps."""
    bars = bars or trader.cfg.max_candles
    n = 0
    for sym in trader.symbols:
        for tf in timeframes_of(trader):
            candles = history.fetch(sym, tf, now - tf.delta * bars, now)
            trader.bootstrap_history([c for c in candles if c.close_time <= now])
            n += len(candles)
    return n


def run(trader: PaperTrader, source, *, history: Optional[CandleHistory], now: Callable[[], datetime],
        max_events: Optional[int] = None, sleep=None, limits: FeedLimits = FeedLimits(),
        learning: Optional[DailyLearningCycle] = None) -> int:
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
            if learning is not None and trader._now is not None:
                _run_learning(trader, learning)
            if max_events is not None and processed >= max_events:
                break
    except FeedUnavailable as exc:
        trader.notifier.notify(Severity.CRITICAL, "feed unavailable", str(exc), at=now())
        raise
    finally:
        if trader._now is not None:
            trader.save_state()
    return processed


def _run_learning(trader: PaperTrader, learning: DailyLearningCycle) -> None:
    """The daily learning cycle (ADR-0044) is observation only, so its failure must never stop trading:
    it is recorded and reported, and the day is retried an hour later."""
    try:
        learning.maybe_run(trader._now)
    except Exception as exc:  # noqa: BLE001
        learning.next_try = trader._now + timedelta(hours=1)
        trader.store.append("audit", {"event": "learning_cycle_failed", "detail": f"{type(exc).__name__}: {exc}"},
                            at=trader._now)
        trader.notifier.notify(Severity.WARNING, "learning cycle failed", f"{type(exc).__name__}: {exc}",
                               at=trader._now, key="learning_cycle_failed")


def build_learning(trader: PaperTrader) -> DailyLearningCycle:
    """Drift check plus a shadow challenger against every running strategy that has a forecast horizon.
    Only strategy ids and horizons leave the trader; the learning package never sees a strategy object."""
    champions = {sid: s.horizon for sid, (s, _) in trader.strategies.items()
                 if s.timeframe == LEARNING_TIMEFRAME and isinstance(getattr(s, "horizon", None), int)}
    steps = [drift_step] + ([challenger_step(champions)] if champions else [])
    return DailyLearningCycle(trader.store, trader.symbols, timeframe=LEARNING_TIMEFRAME, steps=steps,
                              lag=lag_for(champions, LEARNING_TIMEFRAME), notifier=trader.notifier)


def live_stream_url(trader: PaperTrader) -> str:
    streams = []
    for sym in trader.symbols:
        streams += standard_streams(sym, timeframes_of(trader))
    return combined_stream_url(streams)


def load_default_paper_config() -> dict:
    return load_paper()


def dump_status(trader: PaperTrader) -> str:
    return json.dumps(trader.status(), ensure_ascii=False, indent=2, default=str)
