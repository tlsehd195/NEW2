import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Candlestick } from "@/components/charts/candlestick";
import { CandlestickChart } from "@/components/charts/candlestick-chart";
import { Grid } from "@/components/charts/grid";
import { Line, LineChart } from "@/components/charts/line-chart";
import { ChartTooltip } from "@/components/charts/tooltip";
import { XAxis } from "@/components/charts/x-axis";
import { YAxis } from "@/components/charts/y-axis";
import { FillMarks, Guides, PriceLevels, SeriesPath, type Level } from "./overlays";
import type { Snapshot } from "./types";

const fmt = (n: number | null | undefined) => (n == null ? "-" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: 2 }));
const sgn = (n: number | null | undefined) => (n == null ? "" : n >= 0 ? "up" : "down");
const pct = (x: number | null | undefined) => (x == null ? "-" : (x * 100).toFixed(1) + "%");
const whenFull = (d: Date) => d.toLocaleString("ko-KR", { hour12: false });
const NAMES: Record<string, string> = {
  ema_trend: "EMA 추세", donchian_pos: "돈치안 위치", roc: "ROC 모멘텀", rsi: "RSI", bollinger_b: "볼린저 %B", obv_slope: "OBV 기울기",
};
const ACTIONS: Record<string, string> = { enter_long: "롱 진입", enter_short: "숏 진입", exit: "청산", hold: "대기" };
const OVERLAYS: [string, string, string?][] = [
  ["ema20", "var(--chart-1)"], ["bb_upper", "var(--chart-2)", "3,3"], ["bb_lower", "var(--chart-2)", "3,3"],
  ["don_high", "var(--chart-3)", "1,3"], ["don_low", "var(--chart-3)", "1,3"],
];

function Row({ k, v, cls }: { k: string; v: ReactNode; cls?: string }) {
  return (
    <div className="row">
      <span className="muted">{k}</span>
      <span className={cls}>{v}</span>
    </div>
  );
}

function Card({ title, children, wide }: { title: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className="card frame" style={wide ? { gridColumn: "1 / -1" } : undefined}>
      <h2 className="mono">{title}</h2>
      {children}
    </div>
  );
}

function OhlcTip({ point }: { point: Record<string, unknown> }) {
  const n = (k: string) => fmt(point[k] as number);
  return (
    <div className="px-3 py-2 text-xs tabular-nums text-chart-tooltip-foreground">
      <div className="text-chart-tooltip-muted">{whenFull(point.date as Date)}</div>
      <div>시 {n("open")} · 고 {n("high")}</div>
      <div>저 {n("low")} · 종 {n("close")}</div>
    </div>
  );
}

function PriceChart({ d, showOverlays }: { d: Snapshot; showOverlays: boolean }) {
  const data = useMemo(
    () => d.candles.map((c, i) => ({
      date: new Date(c.time * 1000), open: c.open, high: c.high, low: c.low, close: c.close,
      ...Object.fromEntries(OVERLAYS.map(([k]) => [k, d.overlays[k]?.[i] ?? null])),
    })),
    [d.candles, d.overlays],
  );
  const levels: Level[] = [];
  if (d.position?.entry_price) levels.push({ price: d.position.entry_price, label: "진입", color: "var(--acc)" });
  if (d.position?.stop_price) levels.push({ price: d.position.stop_price, label: "손절", color: "var(--down)", dash: "5,4" });
  if (d.last_price) levels.push({ price: d.last_price, label: "현재", color: "#71717a", dash: "2,3" });
  return (
    <CandlestickChart data={data} aspectRatio="2.1 / 1" margin={{ top: 24, right: 96, bottom: 36, left: 12 }} animationDuration={700} candleGap={0.3}>
      <Grid horizontal numTicksRows={6} />
      {showOverlays && OVERLAYS.map(([k, col, dash]) => <SeriesPath key={k} field={k} stroke={col} dash={dash} />)}
      <Candlestick />
      <FillMarks fills={d.fills} />
      <PriceLevels levels={levels} />
      <YAxis orientation="right" numTicks={6} formatValue={(v) => fmt(v)} />
      <XAxis numTicks={7} />
      <ChartTooltip showDots={false} content={({ point }) => <OhlcTip point={point} />} />
    </CandlestickChart>
  );
}

function IndicatorPanel({ d, field, name, color, guides, show }: {
  d: Snapshot; field: string; name: string; color: string; guides: number[]; show: (v: number) => string;
}) {
  const data = useMemo(
    () => d.candles.map((c, i) => ({ date: new Date(c.time * 1000), v: d.overlays[field]?.[i] })).filter((r) => typeof r.v === "number"),
    [d.candles, d.overlays, field],
  );
  const last = data.length ? (data[data.length - 1].v as number) : null;
  return (
    <div className="frame">
      <div className="panel-title mono">{name} {last == null ? "" : show(last)}</div>
      {data.length > 1 ? (
        <LineChart data={data} aspectRatio="7 / 1" margin={{ top: 30, right: 96, bottom: 10, left: 12 }} animationDuration={700}>
          <Grid horizontal numTicksRows={3} />
          <Guides values={guides} />
          <Line dataKey="v" stroke={color} strokeWidth={1.6} fadeEdges={false} />
          <ChartTooltip showDatePill={false} rows={(p) => [{ color, label: name, value: show(p.v as number) }]} />
        </LineChart>
      ) : (
        <div className="muted" style={{ padding: "40px 14px 16px" }}>데이터를 기다리는 중…</div>
      )}
    </div>
  );
}

function Votes({ d }: { d: Snapshot }) {
  const rules = d.rules || {};
  if (!d.votes.length) return <div className="muted">아직 판단 기록이 없어요</div>;
  return (
    <div className="stack">
      {d.votes.map((v) => {
        const tot = Object.keys(v.per_indicator).length;
        const need = Math.ceil((rules.min_agree ?? 0.6) * tot - 1e-9);
        const bar = new Date(v.bar_time).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
        return (
          <div key={v.strategy}>
            <div className="muted">
              {v.strategy.replace("daytrade_indicator_vote_", "")} · {bar} 봉 · 결정: {ACTIONS[v.action] ?? v.action} ({v.reason})
            </div>
            {Object.entries(v.per_indicator).map(([k, pr]) => (
              <div className="row" key={k}>
                <span style={{ width: 110 }}>{NAMES[k] ?? k}</span>
                <div style={{ flex: 1, height: 10, margin: "0 10px", background: "var(--line)", position: "relative" }}>
                  <div style={{
                    position: "absolute", top: 0, bottom: 0, background: pr >= 0.5 ? "var(--up)" : "var(--down)",
                    left: `${Math.min(pr, 0.5) * 100}%`, width: `${Math.abs(pr - 0.5) * 100}%`,
                  }} />
                  <div style={{ position: "absolute", left: "50%", top: -2, bottom: -2, width: 1, background: "var(--mute)" }} />
                </div>
                <span className={pr > 0.5 ? "up" : "down"}>{pct(pr)} {pr > 0.5 ? "롱" : pr < 0.5 ? "숏" : "중립"}</span>
              </div>
            ))}
            <Row k="합성 롱 확률" v={`${pct(v.p_long)}  (진입 ≥${pct(rules.enter_confidence)}, 청산 <${pct(rules.exit_confidence)})`}
              cls={v.p_long != null && v.p_long >= (rules.enter_confidence ?? 1) ? "up" : ""} />
            <Row k="동의 수" v={`롱 ${v.agree_long} / 숏 ${v.agree_short} (필요 ${need}/${tot})`} />
            <Row k="변동성 비율" v={`${v.vol_ratio == null ? "-" : v.vol_ratio.toFixed(2)}  (허용 ${rules.vol_gate_lo}~${rules.vol_gate_hi})`} />
          </div>
        );
      })}
    </div>
  );
}

export default function App() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [sym, setSym] = useState<string>("");
  const [d, setD] = useState<Snapshot | null>(null);
  const [err, setErr] = useState("");
  const [asOf, setAsOf] = useState("불러오는 중…");
  const [showOverlays, setShowOverlays] = useState(true);

  useEffect(() => {
    fetch("/api/config").then((r) => r.json()).then((c: { symbols: string[] }) => {
      setSymbols(c.symbols);
      const h = location.hash.slice(1);
      setSym(c.symbols.includes(h) ? h : c.symbols[0]);
    }).catch((e) => setErr("불러오지 못했어요: " + e));
  }, []);

  useEffect(() => {
    if (!sym) return;
    let live = true;
    const load = async () => {
      try {
        const snap: Snapshot = await (await fetch("/api/snapshot?symbol=" + sym)).json();
        if (!live) return;
        setD(snap); setErr("");
        setAsOf("갱신 " + new Date().toLocaleTimeString("ko-KR", { hour12: false }) + " · 5초마다 자동 갱신 · 차트는 닫힌 15분봉");
      } catch (e) {
        if (live) setErr("불러오지 못했어요: " + e);
      }
    };
    load();
    const t = setInterval(load, 5000);
    return () => { live = false; clearInterval(t); };
  }, [sym]);

  const p = d?.position, a = d?.account, L = d?.latest ?? {};
  return (
    <main>
      <h1>모의투자 대시보드<span className="badge">읽기 전용 · 실제 돈 아님</span></h1>
      <div className="mono">{asOf}</div>
      <div className="tabs">
        {symbols.map((s) => (
          <button key={s} className={s === sym ? "on" : ""} onClick={() => { setSym(s); location.hash = s; setD(null); }}>{s}</button>
        ))}
      </div>
      <div className="legend">
        <label><input type="checkbox" checked={showOverlays} onChange={(e) => setShowOverlays(e.target.checked)} /> 지표선 표시</label>
        <span style={{ color: "var(--chart-1)" }}>━ EMA20</span>
        <span style={{ color: "var(--chart-2)" }}>┅ 볼린저(20,2)</span>
        <span style={{ color: "var(--chart-3)" }}>┈ 돈치안(20)</span>
        <span className="muted">· 아래 패널: RSI, ROC, OBV</span>
      </div>
      <div className="stack">
        <div className="frame">
          {d && d.candles.length ? <PriceChart d={d} showOverlays={showOverlays} /> : <div className="muted" style={{ padding: 20, height: 300 }}>봉 데이터를 기다리는 중…</div>}
        </div>
        {d && d.candles.length > 0 && (
          <>
            <IndicatorPanel d={d} field="rsi14" name="RSI(14)" color="var(--chart-1)" guides={[30, 50, 70]} show={(v) => v.toFixed(1)} />
            <IndicatorPanel d={d} field="roc14" name="ROC(14)" color="var(--chart-4)" guides={[0]} show={(v) => (v * 100).toFixed(2) + "%"} />
            <IndicatorPanel d={d} field="obv" name="OBV (거래량 누적 흐름)" color="var(--chart-5)" guides={[]} show={(v) => fmt(v)} />
          </>
        )}
      </div>
      <div className="err">{err}</div>
      <div className="grid">
        <Card title="현재가">
          <div className="big">{fmt(d?.last_price)}</div>
          <div className="muted">{d?.last_price_time ? "호가 기준 " + d.last_price_time.slice(11, 16) + " UTC" : ""}</div>
        </Card>
        <Card title="포지션">
          {p ? (
            <>
              <Row k="방향" v={p.direction === "long" ? "롱" : "숏"} />
              <Row k="상태" v={p.state} />
              <Row k="수량" v={fmt(p.quantity)} />
              <Row k="진입가" v={fmt(p.entry_price)} />
              <Row k="손절가" v={fmt(p.stop_price)} />
              <Row k="평가손익(USDT)" v={(p.unrealized_pnl != null && p.unrealized_pnl >= 0 ? "+" : "") + fmt(p.unrealized_pnl)} cls={sgn(p.unrealized_pnl)} />
              <Row k="전략" v={p.strategy} />
            </>
          ) : <div className="muted">포지션 없음</div>}
        </Card>
        <Card title="계좌">
          {a ? (
            <>
              <Row k="잔고(USDT)" v={fmt(a.balance)} />
              <Row k="열린 포지션" v={a.open_positions + "개"} />
              <Row k="저장 시각" v={a.saved_at.slice(11, 19) + " UTC"} />
            </>
          ) : <div className="muted">저장된 상태 없음</div>}
        </Card>
        <Card title="지표별 판단 (닫힌 15분봉마다 갱신)" wide>{d ? <Votes d={d} /> : null}</Card>
        <Card title="지표 현재값">
          <Row k="RSI(14)" v={L.rsi14 == null ? "-" : L.rsi14.toFixed(1)} />
          <Row k="EMA20 대비" v={L.ema20_gap == null ? "-" : (L.ema20_gap * 100).toFixed(2) + "%"} cls={sgn(L.ema20_gap)} />
          <Row k="볼린저 z" v={L.bollinger_z == null ? "-" : L.bollinger_z.toFixed(2)} cls={sgn(L.bollinger_z)} />
          <Row k="돈치안 위치" v={L.donchian_pos == null ? "-" : (L.donchian_pos * 100).toFixed(0) + "% (0=하단 100=상단)"} />
          <Row k="ROC(14)" v={L.roc14 == null ? "-" : (L.roc14 * 100).toFixed(2) + "%"} cls={sgn(L.roc14)} />
        </Card>
        <Card title="최근 판단">
          <ul className="list">
            {(d?.decisions ?? []).slice().reverse().map((x, i) => (
              <li key={i}>{whenFull(new Date(x.time))}  {x.action}  {x.reason}</li>
            ))}
          </ul>
        </Card>
        <Card title="청산된 거래">
          <ul className="list">
            {d?.closed_trades.length
              ? d.closed_trades.slice().reverse().map((x, i) => (
                <li key={i} className={sgn(x.net_pnl)}>{(x.net_pnl >= 0 ? "+" : "") + fmt(x.net_pnl)} USDT  {x.exit_reason ?? ""}</li>
              ))
              : <li className="muted">아직 없음</li>}
          </ul>
        </Card>
      </div>
    </main>
  );
}
