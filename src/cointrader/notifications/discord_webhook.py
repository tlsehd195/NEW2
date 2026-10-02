"""Discord webhook delivery.

`_utf16_length`, `truncate_for_discord` and `send_discord_message` are
copied verbatim from tlsehd195/NEW- (src/notifications/discord_webhook.py,
commit 2251c2e): the 2000 UTF-16-code-unit content limit handling and the
browser User-Agent fix were each learned from a real production failure
there. Only the `format_*` functions are new, one per coin report shape;
they are pure and never touch the network.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request

# Discord's documented hard limit for a webhook message's `content`.
_DISCORD_CONTENT_LIMIT = 2000
_TRUNCATION_SUFFIX = "\n... (truncated)"


def _pct(x: float) -> str:
    return f"{x:+.2%}"


def format_swing_study_report(report: dict) -> str:
    """`report` is the JSON `scripts/run_swing_study.py` writes. Missing
    fields are skipped, never shown as a made-up 0."""
    lines = ["**\U0001f4ca 스윙 검증 리포트**"]
    if report.get("market"):
        lines.append(f"마켓: {report['market']} ({report.get('timeframe', '?')})")
    if report.get("test_start") and report.get("test_end"):
        lines.append(f"TEST 구간: {report['test_start'][:16]} ~ {report['test_end'][:16]}")
    if report.get("fold_count") is not None:
        lines.append(f"워크포워드 폴드: {report['fold_count']}개")
    if report.get("pbo") is not None:
        lines.append(f"PBO(과최적화 확률): {report['pbo']:.2f}")
    if report.get("trials_deflated_against") is not None:
        lines.append(f"DSR 보정 기준 시행 수: {report['trials_deflated_against']}")
    for c in report.get("candidates", []):
        dsr = c.get("deflated_sharpe")
        dsr_text = "n/a" if dsr is None or (isinstance(dsr, float) and math.isnan(dsr)) else f"{dsr:.2f}"
        test = c.get("test_return")
        bh = c.get("test_buy_and_hold_return")
        test_text = f"TEST {_pct(test)} (보유 {_pct(bh)})" if test is not None and bh is not None else ""
        capped = c.get("test_liquidity_capped_bars")
        capped_text = f" [유동성 부족으로 {capped}개 봉 부분체결]" if capped else ""
        lines.append(f"- {c.get('name')}: DSR {dsr_text} {test_text}{capped_text}".rstrip())
    if report.get("must_lock_test_window"):
        lines.append("_이 TEST 구간은 이제 잠가야 합니다 (configs/locked_windows.json)._")
    return "\n".join(lines)


def format_kill_switch_alert(reason: str, occurred_at: str) -> str:
    return (
        f"**\U0001f6d1 킬 스위치 작동**\n사유: {reason}\n시각: {occurred_at}\n"
        "해제는 사람이 직접 승인해야 합니다."
    )


def _utf16_length(s: str) -> int:
    """Discord (like most JS-originated web APIs) measures a string's
    length the same way JavaScript's own `.length` does: UTF-16 CODE
    UNITS, not Unicode codepoints -- confirmed via independent search,
    not assumed (multiple sources agree Discord/Telegram-style chunking
    tools have to special-case this exact gap). Python's `len()` counts
    codepoints, so a character outside the Basic Multilingual Plane
    (e.g. most emoji, U+10000 and above) counts as ONE codepoint in
    Python but TWO UTF-16 code units in Discord's own accounting --
    `len()` alone under-counts exactly those characters."""
    return len(s.encode("utf-16-le")) // 2


def truncate_for_discord(content: str, *, limit: int = _DISCORD_CONTENT_LIMIT) -> str:
    """Pure. Discord's webhook API rejects (HTTP 400) any `content` over
    `limit` UTF-16 code units (see `_utf16_length`) -- truncate rather
    than let a long report crash the send outright.

    External review, LOW-2 (Session 38 continued): previously compared
    `len(content)` (Python codepoints) against `limit` directly. Every
    report this module formats today has at most one astral character
    (a single leading emoji), so the previous version never actually
    mis-truncated in practice -- but a future formatter combining
    several emoji/rare CJK characters near the boundary could have
    silently sent oversized content and gotten a real HTTP 400. Never
    splits a surrogate pair mid-character -- iterates by Python
    codepoint (each one already a complete, valid character) and stops
    BEFORE a codepoint that would push the running UTF-16 count over
    budget, so truncation always lands on a whole-character boundary."""
    if _utf16_length(content) <= limit:
        return content
    budget = limit - _utf16_length(_TRUNCATION_SUFFIX)
    kept: list[str] = []
    used = 0
    for ch in content:
        ch_length = 2 if ord(ch) > 0xFFFF else 1
        if used + ch_length > budget:
            break
        kept.append(ch)
        used += ch_length
    return "".join(kept) + _TRUNCATION_SUFFIX


# ADR-0166 (external review, real production failure): the first real
# scheduled/workflow_dispatch run to actually exercise this call (the
# account owner's webhook secret was only registered 2026-09-18) got a
# real HTTP 403 straight back from Discord's own infrastructure --
# connection succeeded, Discord itself rejected the request. No
# `User-Agent` header was ever sent, leaving Python's default
# `Python-urllib/x.y` string, a well-known bot signature -- the same
# root cause this project already found and fixed for Stooq (ADR-0157)
# and already worked around for SEC EDGAR (a required descriptive UA by
# policy). A real, current desktop-browser User-Agent is the same
# category of fix, applied here for the same reason. This is a
# best-effort fix against a plausible but not independently confirmed
# cause (this session's own egress blocks discord.com entirely, so it
# cannot be verified here) -- needs re-verification against a real
# scheduled/workflow_dispatch run, not assumed fixed from this reasoning
# alone.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def send_discord_message(webhook_url: str, content: str, *, timeout: float = 10.0) -> None:
    """The one real network call this module makes: a POST to a real
    Discord webhook URL (the account owner's own, created via Discord's
    "Integrations -> Webhooks" UI -- never guessed or hardcoded here).
    Raises `urllib.error.URLError`/`RuntimeError` on any transport error
    or unexpected response rather than swallowing it -- a failed
    notification is a real, visible failure for the caller to report,
    never silently dropped."""
    body = json.dumps({"content": truncate_for_discord(content)}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status not in (200, 204):
            raise RuntimeError(f"Discord webhook returned unexpected HTTP {response.status}")
