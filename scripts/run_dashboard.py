#!/usr/bin/env python3
"""Read-only paper-trading dashboard in your browser (ADR-0048).

    python3 scripts/run_dashboard.py            # then open http://127.0.0.1:8765

Shows the 15m chart with fills, entry price and stop line, the current position and its open PnL, the
balance, and recent decisions and closed trades. It only reads `var/` files the paper trader writes; it
has no buttons and cannot place or cancel anything. Binds to this computer only (127.0.0.1).
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cointrader.monitoring.dashboard import read_snapshot  # noqa: E402
from cointrader.settings import load_paper  # noqa: E402

PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>모의투자 대시보드</title>
<style>
:root{--bg:#0f1218;--panel:#171c25;--line:#262d3a;--text:#e6e9ef;--mute:#8b94a5;--up:#26a69a;--down:#ef5350;--acc:#4c8dff}
@media (prefers-color-scheme: light){:root{--bg:#f4f6fa;--panel:#fff;--line:#dde2ea;--text:#1b2230;--mute:#667085}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,"Malgun Gothic",sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:16px;margin:0 0 4px}.sub{color:var(--mute);margin-bottom:12px}
.tabs{display:flex;gap:8px;margin-bottom:12px}
.tabs button{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:8px;padding:6px 14px;cursor:pointer}
.tabs button.on{border-color:var(--acc);color:var(--acc)}
#wrap{position:relative;height:420px;background:var(--panel);border:1px solid var(--line);border-radius:10px}
#chart{width:100%;height:100%;display:block}
#tip{position:absolute;left:10px;top:6px;font-size:12px;color:var(--mute);pointer-events:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px}
.card h2{font-size:13px;margin:0 0 8px;color:var(--mute);font-weight:600}
.big{font-size:22px;font-weight:700}.up{color:var(--up)}.down{color:var(--down)}.mute{color:var(--mute)}
.row{display:flex;justify-content:space-between;gap:8px;padding:2px 0}
.row span:first-child{white-space:nowrap}.row span:last-child{text-align:right;overflow-wrap:anywhere}
ul{margin:0;padding:0;list-style:none}li{padding:2px 0;border-bottom:1px solid var(--line);font-size:13px}li:last-child{border:0}
#err{color:var(--down);margin:8px 0;min-height:1em}
</style></head><body><main>
<h1>모의투자 대시보드 <span class="mute">(읽기 전용 · 실제 돈 아님)</span></h1>
<div class="sub" id="asof">불러오는 중…</div>
<div class="tabs" id="tabs"></div>
<div id="wrap"><canvas id="chart"></canvas><div id="tip"></div></div><div id="err"></div>
<div class="grid">
<div class="card"><h2>현재가</h2><div class="big" id="price">-</div><div class="mute" id="ptime"></div></div>
<div class="card"><h2>포지션</h2><div id="pos">-</div></div>
<div class="card"><h2>계좌</h2><div id="acct">-</div></div>
<div class="card"><h2>최근 판단</h2><ul id="dec"></ul></div>
<div class="card"><h2>청산된 거래</h2><ul id="trades"></ul></div>
</div></main>
<script>
const SYMBOLS = __SYMBOLS__;
let sym = SYMBOLS.includes(location.hash.slice(1)) ? location.hash.slice(1) : SYMBOLS[0];
const fmt = n => n == null ? "-" : Number(n).toLocaleString("ko-KR", {maximumFractionDigits: 2});
const sgn = n => n == null ? "" : (n >= 0 ? "up" : "down");
const kst = t => new Date(t * 1000).toLocaleString("ko-KR", {hour12: false});
const cv = document.getElementById("chart"), tip = document.getElementById("tip");
let view = null, hover = -1;
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function draw() {
  const wrap = document.getElementById("wrap"), dpr = window.devicePixelRatio || 1;
  const W = wrap.clientWidth, H = wrap.clientHeight;
  cv.width = W * dpr; cv.height = H * dpr;
  const g = cv.getContext("2d"); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
  if (!view || !view.candles.length) { g.fillStyle = css("--mute"); g.fillText("봉 데이터를 기다리는 중…", 20, 30); return; }
  const c = view.candles, R = 84, B = 24, T = 24, L = 8, pw = W - L - R, ph = H - T - B;
  const marks = [];
  if (view.position && view.position.entry_price) marks.push([view.position.entry_price, "진입", css("--acc"), []]);
  if (view.position && view.position.stop_price) marks.push([view.position.stop_price, "손절", css("--down"), [5, 4]]);
  if (view.last_price) marks.push([view.last_price, "현재", css("--mute"), [2, 3]]);
  let lo = Math.min(...c.map(x => x.low), ...marks.map(m => m[0])), hi = Math.max(...c.map(x => x.high), ...marks.map(m => m[0]));
  const pad = (hi - lo) * 0.06 || 1; lo -= pad; hi += pad;
  const X = i => L + (i + 0.5) * pw / c.length, Y = p => T + (hi - p) / (hi - lo) * ph, bw = Math.max(1, pw / c.length * 0.7);
  g.font = "11px system-ui"; g.textBaseline = "middle";
  for (let k = 0; k <= 5; k++) {
    const p = lo + (hi - lo) * k / 5, y = Y(p);
    g.strokeStyle = css("--line"); g.beginPath(); g.moveTo(L, y); g.lineTo(L + pw, y); g.stroke();
    g.fillStyle = css("--mute"); g.textAlign = "left"; g.fillText(fmt(p), L + pw + 6, y);
  }
  g.textAlign = "center"; g.textBaseline = "top"; g.fillStyle = css("--mute");
  const every = Math.max(1, Math.round(c.length / 7));
  for (let i = 0; i < c.length; i += every) g.fillText(new Date(c[i].time * 1000).toLocaleTimeString("ko-KR", {hour: "2-digit", minute: "2-digit", hour12: false}), X(i), H - B + 6);
  c.forEach((x, i) => {
    g.strokeStyle = g.fillStyle = x.close >= x.open ? css("--up") : css("--down");
    g.beginPath(); g.moveTo(X(i), Y(x.high)); g.lineTo(X(i), Y(x.low)); g.stroke();
    const y1 = Y(Math.max(x.open, x.close)), y2 = Y(Math.min(x.open, x.close));
    g.fillRect(X(i) - bw / 2, y1, bw, Math.max(1, y2 - y1));
  });
  view.fills.forEach(f => {
    const i = c.findIndex(x => x.time <= f.time && f.time < x.time + 900); if (i < 0) return;
    const buy = f.side.toLowerCase() === "buy", x = X(i), y = buy ? Y(c[i].low) + 10 : Y(c[i].high) - 10, s = 6;
    g.fillStyle = buy ? css("--up") : css("--down"); g.beginPath();
    if (buy) { g.moveTo(x, y - s); g.lineTo(x - s, y + s); g.lineTo(x + s, y + s); } else { g.moveTo(x, y + s); g.lineTo(x - s, y - s); g.lineTo(x + s, y - s); }
    g.fill();
  });
  g.textAlign = "left"; g.textBaseline = "middle";
  marks.forEach(([p, name, col, dash]) => {
    const y = Y(p); g.strokeStyle = col; g.setLineDash(dash); g.beginPath(); g.moveTo(L, y); g.lineTo(L + pw, y); g.stroke(); g.setLineDash([]);
    g.fillStyle = col; g.fillRect(L + pw, y - 8, R - 2, 16); g.fillStyle = "#fff"; g.fillText(name + " " + fmt(p), L + pw + 4, y);
  });
  if (hover >= 0 && hover < c.length) {
    const x = c[hover]; tip.textContent = new Date(x.time * 1000).toLocaleString("ko-KR", {hour12: false}) + "  시 " + fmt(x.open) + "  고 " + fmt(x.high) + "  저 " + fmt(x.low) + "  종 " + fmt(x.close);
  } else tip.textContent = "";
}
cv.addEventListener("mousemove", e => { if (!view) return; const r = cv.getBoundingClientRect(); hover = Math.floor((e.clientX - r.left - 8) / ((r.width - 92) / view.candles.length)); draw(); });
cv.addEventListener("mouseleave", () => { hover = -1; draw(); });
window.addEventListener("resize", draw);
function el(tag, text, cls) { const e = document.createElement(tag); e.textContent = text; if (cls) e.className = cls; return e; }
function fill(id, nodes) { const box = document.getElementById(id); box.replaceChildren(...nodes); }
function kv(k, v, cls) { const r = el("div", "", "row"); r.append(el("span", k, "mute"), el("span", v, cls)); return r; }
const tabs = document.getElementById("tabs");
SYMBOLS.forEach(s => { const b = el("button", s); b.onclick = () => { sym = s; location.hash = s; load(); }; tabs.append(b); });
async function load() {
  try {
    const d = await (await fetch("/api/snapshot?symbol=" + sym)).json();
    document.getElementById("err").textContent = "";
    [...tabs.children].forEach(b => b.classList.toggle("on", b.textContent === sym));
    view = d; draw();
    const p = d.position;
    document.getElementById("price").textContent = fmt(d.last_price);
    document.getElementById("ptime").textContent = d.last_price_time ? "호가 기준 " + d.last_price_time.slice(11, 16) + " UTC" : "";
    fill("pos", p ? [kv("방향", p.direction === "long" ? "롱" : "숏"), kv("상태", p.state), kv("수량", fmt(p.quantity)),
      kv("진입가", fmt(p.entry_price)), kv("손절가", fmt(p.stop_price)),
      kv("평가손익(USDT)", (p.unrealized_pnl >= 0 ? "+" : "") + fmt(p.unrealized_pnl), sgn(p.unrealized_pnl)), kv("전략", p.strategy)]
      : [el("div", "포지션 없음", "mute")]);
    const a = d.account;
    fill("acct", a ? [kv("잔고(USDT)", fmt(a.balance)), kv("열린 포지션", a.open_positions + "개"), kv("저장 시각", a.saved_at.slice(11, 19) + " UTC")]
      : [el("div", "저장된 상태 없음", "mute")]);
    fill("dec", d.decisions.slice().reverse().map(x => el("li", new Date(x.time).toLocaleString("ko-KR", {hour12: false}) + "  " + x.action + "  " + x.reason)));
    fill("trades", d.closed_trades.length ? d.closed_trades.slice().reverse().map(x => el("li", (x.net_pnl >= 0 ? "+" : "") + fmt(x.net_pnl) + " USDT  " + (x.exit_reason || ""), sgn(x.net_pnl))) : [el("li", "아직 없음", "mute")]);
    document.getElementById("asof").textContent = "갱신 " + new Date().toLocaleTimeString("ko-KR", {hour12: false}) + " · 5초마다 자동 갱신 · 차트는 닫힌 15분봉";
  } catch (e) { document.getElementById("err").textContent = "불러오지 못했어요: " + e; }
}
load(); setInterval(load, 5000);
</script></body></html>"""


def main() -> int:
    cfg = load_paper()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state-dir", type=Path, default=REPO / cfg["state_dir"])
    ap.add_argument("--data-root", type=Path, default=REPO / cfg["data_root"])
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    symbols = list(cfg["symbols"])
    page = PAGE.replace("__SYMBOLS__", json.dumps(symbols)).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif url.path == "/api/snapshot":
                symbol = parse_qs(url.query).get("symbol", [symbols[0]])[0]
                if symbol not in symbols:
                    self._send(404, b'{"error": "unknown symbol"}', "application/json")
                    return
                snap = read_snapshot(args.state_dir, args.data_root, symbol)
                self._send(200, json.dumps(snap, ensure_ascii=False, default=str).encode("utf-8"),
                           "application/json; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *_args) -> None:  # keep the console quiet
            pass

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"대시보드: http://127.0.0.1:{args.port}  (끄려면 Ctrl+C)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
