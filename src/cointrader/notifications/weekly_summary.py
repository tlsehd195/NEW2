"""Weekly paper-performance summary for Discord (T14).

Pure functions plus one guarded sender. Nothing here can place an order or
touch a config other than its own; the webhook URL comes only from the
environment (see `notifier`). Numbers are labelled by mode: paper results are
simulated fills on real market data, not live results.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Optional

from cointrader.analytics.performance import trade_metrics
from cointrader.journal.records import trade_from_outcome
from cointrader.journal.store import LayeredStore
from cointrader.notifications.discord_webhook import send_discord_message, truncate_for_discord

DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "weekly_summary.json"
_LABEL = {"paper": "모의투자(실제 시세 기반 가상 체결, 실거래 성과 아님)", "backtest": "백테스트", "live": "실거래"}


def load_weekly_config(path: Path = DEFAULT_CONFIG) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    if set(d) - {"_comment", "enabled", "mode", "days"} or d["mode"] not in _LABEL or int(d["days"]) < 1:
        raise ValueError("configs/weekly_summary.json has unexpected keys or values")
    return d


def week_key(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def build_summary(store: LayeredStore, now: datetime, *, mode: str = "paper", days: int = 7) -> dict:
    since: date = (now - timedelta(days=days)).date()
    rows = [r for r in store.read("outcome", start=since) if r["mode"] == mode
            and datetime.fromisoformat(r["recorded_at"]) >= now - timedelta(days=days)]
    trades = [trade_from_outcome(r) for r in rows]
    out: dict = {"mode": mode, "days": days, "until": now.isoformat(), "trades": len(trades)}
    if trades:
        m = trade_metrics(trades, days=days)
        out.update(win_rate=m["win_rate"], profit_factor=m["profit_factor"], expectancy=m["expectancy"],
                   cost=m["cost_breakdown"], gross_positive_net_negative=m["gross_positive_net_negative"])
        by: dict[str, float] = Counter()
        for t in trades:
            by[t.strategy_id] += t.net_pnl
        out["net_by_strategy"] = dict(by)
    drift = [r for r in store.read("learning", start=since)
             if r.get("event") == "feature_drift" and r.get("status") == "DRIFT_DETECTED"]
    out["drift_days"] = len({r["day"] for r in drift})
    return out


def format_summary(s: Mapping) -> str:
    lines = [f"**\U0001f4c8 주간 성과 요약** (최근 {s['days']}일)", f"구분: {_LABEL[s['mode']]}"]
    if not s["trades"]:
        lines.append("청산된 거래가 없습니다.")
    else:
        pf = s.get("profit_factor")
        lines.append(f"거래 {s['trades']}건, 승률 {s['win_rate']:.0%}, 손익비(PF) {'n/a' if pf is None else f'{pf:.2f}'}")
        c = s["cost"]
        lines.append(f"총손익 {c['gross_pnl']:+,.2f} → 수수료·슬리피지·펀딩 차감 후 {c['net_pnl']:+,.2f} (USDT)")
        if s.get("gross_positive_net_negative"):
            lines.append("⚠ 비용을 빼면 손실입니다.")
        for sid, pnl in sorted(s.get("net_by_strategy", {}).items()):
            lines.append(f"- {sid}: {pnl:+,.2f}")
    if s.get("drift_days"):
        lines.append(f"데이터 분포 변화 감지: {s['drift_days']}일 (관측 전용, 거래에 영향 없음)")
    return "\n".join(lines)


def maybe_send_weekly(store: LayeredStore, now: datetime, *, cfg: Mapping, env: Mapping[str, str],
                      force: bool = False, send: Callable[[str, str], None] = send_discord_message) -> tuple[bool, str]:
    """-> (sent, reason). Off by default, needs a webhook, and sends at most once per ISO week."""
    if not cfg["enabled"] and not force:
        return False, "disabled (configs/weekly_summary.json enabled=false)"
    url = env.get("DISCORD_WEBHOOK_URL")
    if not url:
        return False, "DISCORD_WEBHOOK_URL is not set; nothing sent"
    week = week_key(now)
    if not force and any(r.get("event") == "weekly_summary_sent" and r.get("week") == week
                         for r in store.read("audit", start=(now - timedelta(days=8)).date())):
        return False, f"already sent for {week}"
    send(url, truncate_for_discord(format_summary(build_summary(store, now, mode=cfg["mode"], days=int(cfg["days"])))))
    store.append("audit", {"event": "weekly_summary_sent", "week": week}, at=now)
    return True, f"sent for {week}"
