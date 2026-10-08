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
#wrap{position:relative;height:720px;background:var(--panel);border:1px solid var(--line);border-radius:10px}
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
<div class="sub"><label><input type="checkbox" id="ov" checked onchange="draw()"> 지표선 표시</label> <span style="color:#e0a030">━ EMA20</span> <span style="color:#9b7fe8">┅ 볼린저(20,2)</span> <span style="color:#3aa0c0">┈ 돈치안(20)</span> <span class="mute">· 아래 패널: RSI, ROC, OBV</span></div>
<div id="wrap"><canvas id="chart"></canvas><div id="tip"></div></div><div id="err"></div>
<div class="grid">
<div class="card"><h2>현재가</h2><div class="big" id="price">-</div><div class="mute" id="ptime"></div></div>
<div class="card"><h2>포지션</h2><div id="pos">-</div></div>
<div class="card"><h2>계좌</h2><div id="acct">-</div></div>
<div class="card" id="votes" style="grid-column:1/-1"><h2>지표별 판단 (닫힌 15분봉마다 갱신)</h2><div id="votebody"></div></div>
<div class="card"><h2>지표 현재값</h2><div id="ivals"></div></div>
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
  const c = view.candles, o = view.overlays, R = 84, B = 24, T = 24, L = 8, pw = W - L - R;
  const sub = 3, gap = 16, ph = Math.round((H - T - B) * 0.52), rh = Math.floor((H - T - B - ph - sub * gap) / sub);
  const marks = [];
  if (view.position && view.position.entry_price) marks.push([view.position.entry_price, "진입", css("--acc"), []]);
  if (view.position && view.position.stop_price) marks.push([view.position.stop_price, "손절", css("--down"), [5, 4]]);
  if (view.last_price) marks.push([view.last_price, "현재", css("--mute"), [2, 3]]);
  const lineSets = [["ema20", "#e0a030", []], ["bb_upper", "#9b7fe8", [3, 3]], ["bb_lower", "#9b7fe8", [3, 3]], ["don_high", "#3aa0c0", [1, 3]], ["don_low", "#3aa0c0", [1, 3]]];
  const ext = [...c.map(x => x.low), ...c.map(x => x.high), ...marks.map(m => m[0])];
  let lo = Math.min(...ext), hi = Math.max(...ext);
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
  const path = (vals, Yf) => { g.beginPath(); let on = false; vals.forEach((v, i) => { if (v == null) { on = false; return; } if (on) g.lineTo(X(i), Yf(v)); else { g.moveTo(X(i), Yf(v)); on = true; } }); g.stroke(); };
  lineSets.forEach(([k, col, dash]) => { if (!document.getElementById("ov").checked) return; g.strokeStyle = col; g.setLineDash(dash); g.lineWidth = 1.2; path(o[k], Y); g.setLineDash([]); g.lineWidth = 1; });
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
  // sub-panels: RSI, ROC and OBV, the other indicators the vote reads
  const subs = [["rsi14", "RSI(14)", "#e0a030", [30, 50, 70], [0, 100], v => v.toFixed(1)],
                ["roc14", "ROC(14)", "#d06090", [0], null, v => (v * 100).toFixed(2) + "%"],
                ["obv", "OBV (거래량 누적 흐름)", "#60b060", [], null, v => fmt(v)]];
  subs.forEach(([key, name, col, guides, fixed, show], n) => {
    const top = T + ph + gap + n * (rh + gap), vals = o[key], real = vals.filter(v => v != null);
    let a = fixed ? fixed[0] : Math.min(...real, ...guides), z = fixed ? fixed[1] : Math.max(...real, ...guides);
    if (!fixed) { const m = (z - a) * 0.08 || 1; a -= m; z += m; }
    const SY = v => top + (z - v) / (z - a) * rh;
    g.strokeStyle = css("--line"); g.strokeRect(L, top, pw, rh);
    guides.forEach(v => { g.setLineDash(v === 50 ? [2, 4] : []); g.beginPath(); g.moveTo(L, SY(v)); g.lineTo(L + pw, SY(v)); g.stroke(); g.setLineDash([]); g.fillStyle = css("--mute"); g.textAlign = "left"; g.textBaseline = "middle"; g.fillText(String(key === "roc14" ? v : v), L + pw + 6, SY(v)); });
    const last = vals[vals.length - 1];
    g.fillStyle = css("--mute"); g.textAlign = "left"; g.textBaseline = "middle"; g.fillText(name + "  " + (last == null ? "" : show(last)), L + 6, top + 9);
    g.strokeStyle = col; g.lineWidth = 1.4; path(vals, SY); g.lineWidth = 1;
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
    const NAMES = {ema_trend: "EMA 추세", donchian_pos: "돈치안 위치", roc: "ROC 모멘텀", rsi: "RSI", bollinger_b: "볼린저 %B", obv_slope: "OBV 기울기"};
    const pct = x => x == null ? "-" : (x * 100).toFixed(1) + "%";
    const rules = d.rules || {};
    fill("votebody", d.votes.length ? d.votes.map(v => {
      const box = el("div", ""); box.style.marginBottom = "14px";
      const act = v.action === "enter_long" ? "롱 진입" : v.action === "enter_short" ? "숏 진입" : v.action === "exit" ? "청산" : v.action === "hold" ? "대기" : v.action;
      box.append(el("div", v.strategy.replace("daytrade_indicator_vote_", "") + " · " + new Date(v.bar_time).toLocaleString("ko-KR", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false}) + " 봉 · 결정: " + act + " (" + v.reason + ")", "mute"));
      Object.entries(v.per_indicator).forEach(([k, pr]) => {
        const row = el("div", "", "row"); row.style.alignItems = "center";
        const bar = document.createElement("div"); bar.style.cssText = "flex:1;height:10px;margin:0 10px;background:var(--line);position:relative;border-radius:5px";
        const fillw = document.createElement("div"); fillw.style.cssText = "position:absolute;top:0;bottom:0;border-radius:5px;background:" + (pr >= 0.5 ? "var(--up)" : "var(--down)") + ";left:" + Math.min(pr, 0.5) * 100 + "%;width:" + Math.abs(pr - 0.5) * 100 + "%";
        const mid = document.createElement("div"); mid.style.cssText = "position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--mute)";
        bar.append(fillw, mid);
        const nm = el("span", NAMES[k] || k); nm.style.width = "110px"; row.append(nm, bar, el("span", pct(pr) + "  " + (pr > 0.5 ? "롱" : pr < 0.5 ? "숏" : "중립"), pr > 0.5 ? "up" : "down"));
        box.append(row);
      });
      const tot = Object.keys(v.per_indicator).length, need = Math.ceil((rules.min_agree || 0.6) * tot - 1e-9);
      box.append(kv("합성 롱 확률", pct(v.p_long) + "  (진입 ≥" + pct(rules.enter_confidence) + ", 청산 <" + pct(rules.exit_confidence) + ")", v.p_long >= (rules.enter_confidence || 1) ? "up" : ""));
      box.append(kv("동의 수", "롱 " + v.agree_long + " / 숏 " + v.agree_short + " (필요 " + need + "/" + tot + ")"));
      box.append(kv("변동성 비율", (v.vol_ratio == null ? "-" : v.vol_ratio.toFixed(2)) + "  (허용 " + rules.vol_gate_lo + "~" + rules.vol_gate_hi + ")"));
      return box;
    }) : [el("div", "아직 판단 기록이 없어요", "mute")]);
    const L2 = d.latest || {};
    fill("ivals", [kv("RSI(14)", L2.rsi14 == null ? "-" : L2.rsi14.toFixed(1)), kv("EMA20 대비", L2.ema20_gap == null ? "-" : (L2.ema20_gap * 100).toFixed(2) + "%", sgn(L2.ema20_gap)),
      kv("볼린저 z", L2.bollinger_z == null ? "-" : L2.bollinger_z.toFixed(2), sgn(L2.bollinger_z)), kv("돈치안 위치", L2.donchian_pos == null ? "-" : (L2.donchian_pos * 100).toFixed(0) + "% (0=하단 100=상단)"),
      kv("ROC(14)", L2.roc14 == null ? "-" : (L2.roc14 * 100).toFixed(2) + "%", sgn(L2.roc14))]);
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
