import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Candlestick } from "@/components/charts/candlestick";
import { CandlestickChart } from "@/components/charts/candlestick-chart";
import { Grid } from "@/components/charts/grid";
import { Line, LineChart } from "@/components/charts/line-chart";
import { ChartTooltip } from "@/components/charts/tooltip";
import { XAxis } from "@/components/charts/x-axis";
import { YAxis } from "@/components/charts/y-axis";
import { BandFill, FillLegend, FillMarks, Guides, PriceLevels, SeriesPath, type Level } from "./overlays";
import { bollingerSeries, donchianSeries, emaSeries, obvSeries, rocSeries, rsiSeries } from "./indicators";
import { PLOT_LEFT, PLOT_RIGHT, usePanZoom, windowOf, type View } from "./panzoom";
import { equityPoints, type EquityUnit } from "./equity";
import { CalendarPage } from "./Calendar";
import HeroDock from "@/components/dock";
import { SettingsPanel } from "./SettingsPanel";
import { clampInt, useSettings, type PanelId, type Settings } from "./settings";
import type { Snapshot } from "./types";

const fmt = (n: number | null | undefined) => (n == null ? "-" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: 2 }));
const sgn = (n: number | null | undefined) => (n == null ? "" : n >= 0 ? "up" : "down");
const signedPct = (x: number | null | undefined, digits: number) => (x == null ? "-" : (x >= 0 ? "+" : "") + (x * 100).toFixed(digits) + "%");
const pct = (x: number | null | undefined) => (x == null ? "-" : (x * 100).toFixed(1) + "%");
const kst = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "-";
const EXIT_REASONS: Record<string, string> = {
  stop_loss: "손절", take_profit: "익절", trailing_stop: "추적 손절", protective_stop_failed: "보호 손절 실패로 청산",
  retry_exit: "청산 재시도", unknown: "알 수 없음",
};
const exitReason = (r: string | null) => (!r ? "" : r.startsWith("signal_exit:") ? "신호 청산" : EXIT_REASONS[r] ?? r);
// "daytrade_indicator_vote_h16_c0.6_v1" -> "4시간 예측(h16) c0.6": h = 앞으로 볼 봉 수(15분봉), 그 기간을 시간으로 풀어 쓴다.
const strategyName = (id: string) => {
  const m = /^daytrade_indicator_vote_(side_)?h(\d+)_(c[\d.]+)/.exec(id);
  if (!m) return id.replace("daytrade_indicator_vote_", "");
  const hours = (Number(m[2]) * 15) / 60;
  return `${m[1] ? "방향별 " : ""}${Number.isInteger(hours) ? hours : hours.toFixed(1)}시간 예측(h${m[2]}) ${m[3]}`;
};
const whenFull = (d: Date) => d.toLocaleString("ko-KR", { hour12: false });
const NAMES: Record<string, string> = {
  ema_trend: "EMA 추세", donchian_pos: "돈치안 위치", roc: "ROC 모멘텀", rsi: "RSI", bollinger_b: "볼린저 %B", obv_slope: "OBV 기울기",
};
const ACTIONS: Record<string, string> = { enter_long: "롱 진입", enter_short: "숏 진입", exit: "청산", hold: "대기" };

type Row = { date: Date; open: number; high: number; low: number; close: number } & Record<string, number | Date | null>;

function Row({ k, v, cls }: { k: string; v: ReactNode; cls?: string }) {
  return (
    <div className="row">
      <span className="muted">{k}</span>
      <span className={cls}>{v}</span>
    </div>
  );
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="card frame">
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

const PRICE_ASPECT = { low: "2.8 / 1", normal: "2.1 / 1", high: "1.6 / 1" };
const PANEL_ASPECT = { low: "10 / 1", normal: "7 / 1", high: "5 / 1" };

function PriceChart({ d, rows, s }: { d: Snapshot; rows: Row[]; s: Settings }) {
  const levels: Level[] = [];
  if (d.position?.entry_price) levels.push({ price: d.position.entry_price, label: "진입", color: "var(--acc)" });
  if (d.position?.stop_price) levels.push({ price: d.position.stop_price, label: "손절", color: "var(--down)", dash: "5,4" });
  if (d.last_price) levels.push({ price: d.last_price, label: "현재", color: "#71717a", dash: "2,3" });
  return (
    <CandlestickChart data={rows} aspectRatio={PRICE_ASPECT[s.chartHeight]} margin={{ top: 24, right: PLOT_RIGHT, bottom: 36, left: PLOT_LEFT }} animationDuration={700} candleGap={0.3}>
      <Grid horizontal numTicksRows={6} strokeDasharray="none" strokeOpacity={0.7} />
      {s.bb.on && s.bb.fill && <BandFill upper="bb_upper" lower="bb_lower" fill={s.bb.color} fillOpacity={0.07} />}
      {s.bb.on && <SeriesPath field="bb_upper" stroke={s.bb.color} width={s.bb.width} />}
      {s.bb.on && <SeriesPath field="bb_lower" stroke={s.bb.color} width={s.bb.width} />}
      {s.don.on && <SeriesPath field="don_high" stroke={s.don.color} width={s.don.width} />}
      {s.don.on && <SeriesPath field="don_low" stroke={s.don.color} width={s.don.width} />}
      {s.ema.on && <SeriesPath field="ema" stroke={s.ema.color} width={s.ema.width} />}
      <Candlestick />
      {s.fills && <FillMarks fills={d.fills} showLabels={s.fillLabels} />}
      {s.levels && <PriceLevels levels={levels} />}
      <YAxis orientation="right" numTicks={6} formatValue={(v) => fmt(v)} />
      <XAxis numTicks={6} />
      <ChartTooltip showDots={false} content={({ point }) => <OhlcTip point={point} />} />
    </CandlestickChart>
  );
}

function IndicatorPanel({ rows, field, name, color, width, aspect, guides, show }: {
  rows: Row[]; field: string; name: string; color: string; width: number; aspect: string; guides: number[]; show: (v: number) => string;
}) {
  const data = useMemo(() => rows.map((r) => ({ date: r.date, v: r[field] })).filter((r) => typeof r.v === "number"), [rows, field]);
  const last = data.length ? (data[data.length - 1].v as number) : null;
  const domain: [Date, Date] | undefined = rows.length > 1 ? [rows[0].date, rows[rows.length - 1].date] : undefined;
  return (
    <div className="frame">
      <div className="panel-title mono">{name} {last == null ? "" : show(last)}</div>
      {data.length > 1 ? (
        <LineChart data={data} aspectRatio={aspect} margin={{ top: 30, right: PLOT_RIGHT, bottom: 10, left: PLOT_LEFT }} animationDuration={700}
          xDomain={domain} xDomainSlotCount={rows.length} yDomainTween={false}>
          <Grid horizontal numTicksRows={3} strokeDasharray="none" strokeOpacity={0.7} />
          <Guides values={guides} />
          <Line dataKey="v" stroke={color} strokeWidth={width} fadeEdges={false} />
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
              {strategyName(v.strategy)} · {bar} 봉 · 결정: {ACTIONS[v.action] ?? v.action} ({v.reason})
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
  const [s, update, reset] = useSettings();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [page, setPage] = useState<1 | 2 | 3>(1); // 1 = dock (main page), 2 = daily calendar, 3 = the full dashboard
  const [side, setSide] = useState<"all" | "long" | "short">("all");
  const home: View = { count: clampInt(s.startBars, 20, 1000), endTime: null };
  const [view, setView] = useState<View>(home);
  const stackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/config").then((r) => r.json()).then((c: { symbols: string[] }) => {
      setSymbols(c.symbols);
      const h = location.hash.slice(1);
      setSym(c.symbols.includes(h) ? h : c.symbols[0]);
    }).catch((e) => setErr("불러오지 못했어요: " + e));
  }, []);

  const refreshSec = clampInt(s.refreshSec, 2, 120);
  const historyBars = clampInt(s.historyBars, 200, 2000);
  useEffect(() => {
    if (!sym) return;
    let live = true;
    const load = async () => {
      try {
        const snap: Snapshot = await (await fetch("/api/snapshot?bars=" + historyBars + "&symbol=" + sym)).json();
        if (!live) return;
        setD(snap); setErr("");
        setAsOf("갱신 " + new Date().toLocaleTimeString("ko-KR", { hour12: false }) + " · " + refreshSec + "초마다 자동 갱신 · 차트는 닫힌 15분봉");
      } catch (e) {
        if (live) setErr("불러오지 못했어요: " + e);
      }
    };
    load();
    const t = setInterval(load, refreshSec * 1000);
    return () => { live = false; clearInterval(t); };
  }, [sym, refreshSec, historyBars]);

  // every candle the server sent, with the indicator values drawn from the display settings (see indicators.ts);
  // the chart shows the window chosen by drag / wheel
  const allRows = useMemo<Row[]>(() => {
    const cs = d?.candles ?? [];
    const close = cs.map((c) => c.close);
    const per = (n: number) => clampInt(n, 2, 200);
    const ema = emaSeries(close, per(s.ema.period));
    const bb = bollingerSeries(close, per(s.bb.period), Math.min(Math.max(s.bb.k, 0.5), 5));
    const don = donchianSeries(cs.map((c) => c.high), cs.map((c) => c.low), per(s.don.period));
    const rsi = rsiSeries(close, per(s.rsi.period));
    const roc = rocSeries(close, per(s.roc.period));
    const obv = obvSeries(close, cs.map((c) => c.volume));
    return cs.map((c, i) => ({
      date: new Date(c.time * 1000), open: c.open, high: c.high, low: c.low, close: c.close,
      ema: ema[i], bb_upper: bb.upper[i], bb_lower: bb.lower[i], don_high: don.high[i], don_low: don.low[i],
      rsi: rsi[i], roc: roc[i], obv: obv[i],
    }));
  }, [d, s.ema.period, s.bb.period, s.bb.k, s.don.period, s.rsi.period, s.roc.period]);
  const times = useMemo(() => (d?.candles ?? []).map((c) => c.time), [d]);
  usePanZoom(stackRef, times, view, setView, home);
  const [from, to] = windowOf(times, view);
  const rows = useMemo(() => allRows.slice(from, to), [allRows, from, to]);
  const p = d?.position, a = d?.account, L = d?.latest ?? {};
  const tradeCols = s.tradeColumns;
  const [eqUnit, setEqUnit] = useState<EquityUnit>("hour");
  const equityRows = useMemo(() => equityPoints(d, eqUnit) as unknown as Row[], [d, eqUnit]);
  const panels: Record<PanelId, ReactNode> = {
    chart: (
      <>
        <div className="legend">
        {s.ema.on && <span style={{ color: s.ema.color }}>━ EMA{clampInt(s.ema.period, 2, 200)}</span>}
        {s.bb.on && <span style={{ color: s.bb.color }}>━ 볼린저({clampInt(s.bb.period, 2, 200)},{s.bb.k})</span>}
        {s.don.on && <span style={{ color: s.don.color }}>━ 돈치안({clampInt(s.don.period, 2, 200)})</span>}
        {s.fills && <><span className="muted">· 체결:</span><FillLegend /></>}
        <span className="muted">· 드래그: 과거로 이동 · 휠: 확대/축소 · 더블클릭: 처음으로</span>
        {view.endTime != null && <button className="latest" onClick={() => setView({ ...view, endTime: null })}>최신으로 ▶</button>}
      </div>
      <div className="stack chartstack" ref={stackRef}>
        <div className="frame">
          {d && rows.length ? <PriceChart d={d} rows={rows} s={s} /> : <div className="muted" style={{ padding: 20, height: 300 }}>봉 데이터를 기다리는 중…</div>}
        </div>
        {d && rows.length > 0 && (
          <>
            {s.rsi.on && <IndicatorPanel rows={rows} field="rsi" name={`RSI(${clampInt(s.rsi.period, 2, 200)})`} color={s.rsi.color} width={s.rsi.width} aspect={PANEL_ASPECT[s.chartHeight]} guides={[30, 50, 70]} show={(v) => v.toFixed(1)} />}
            {s.roc.on && <IndicatorPanel rows={rows} field="roc" name={`ROC(${clampInt(s.roc.period, 2, 200)})`} color={s.roc.color} width={s.roc.width} aspect={PANEL_ASPECT[s.chartHeight]} guides={[0]} show={(v) => (v * 100).toFixed(2) + "%"} />}
            {s.obv.on && <IndicatorPanel rows={rows} field="obv" name="OBV (거래량 누적 흐름)" color={s.obv.color} width={s.obv.width} aspect={PANEL_ASPECT[s.chartHeight]} guides={[]} show={(v) => fmt(v)} />}
          </>
        )}
      </div>
      </>
    ),
    price: (
      <Card title="현재가">
        <div className="big">{fmt(d?.last_price)}</div>
        <div className="muted">{d?.last_price_time ? "호가 기준 " + d.last_price_time.slice(11, 16) + " UTC" : ""}</div>
      </Card>
    ),
    position: (
      <Card title="포지션">
        {p ? (
          <>
            <Row k="방향" v={p.direction === "long" ? "롱" : "숏"} />
            <Row k="상태" v={p.state} />
            <Row k="수량" v={fmt(p.quantity)} />
            <Row k="진입가" v={fmt(p.entry_price)} />
            <Row k="손절가" v={fmt(p.stop_price)} />
            <Row k="포지션 금액(USDT)" v={fmt(p.notional)} />
            <Row k={p.leverage ? `사용 증거금(USDT, ${p.leverage}배 추정)` : "사용 증거금(USDT)"} v={fmt(p.margin)} />
            <Row k="평가손익(USDT)" v={(p.unrealized_pnl != null && p.unrealized_pnl >= 0 ? "+" : "") + fmt(p.unrealized_pnl)} cls={sgn(p.unrealized_pnl)} />
            <Row k="전략" v={strategyName(p.strategy)} />
          </>
        ) : <div className="muted">포지션 없음</div>}
      </Card>
    ),
    account: (
      <Card title="계좌">
        {a ? (
          <>
            <Row k="잔고(USDT)" v={fmt(a.balance)} />
            <Row k="잔고(원화)" v={a.balance_krw != null ? Math.round(a.balance_krw).toLocaleString("ko-KR") + "원" : "-"} />
            <Row k="평가금액(원화, 미실현 포함)" v={a.equity_krw != null ? Math.round(a.equity_krw).toLocaleString("ko-KR") + "원" : "-"} />
            <Row k="환율(원/USDT)" v={fmt(a.rate_krw)} />
            <Row k="열린 포지션" v={a.open_positions + "개"} />
            <Row k="저장 시각" v={a.saved_at.slice(11, 19) + " UTC"} />
          </>
        ) : <div className="muted">저장된 상태 없음</div>}
      </Card>
    ),
    equity: (
      <Card title={eqUnit === "hour" ? "자산곡선 (누적 손익, 시간별 · 최근 30일)" : "자산곡선 (누적 손익, 날짜별 · 전체 기록)"}>
        <div className="sidepick" role="group" aria-label="자산곡선 단위">
          {(["hour", "day"] as const).map((u) => (
            <button key={u} className={eqUnit === u ? "on" : ""} onClick={() => setEqUnit(u)}>{u === "hour" ? "시간" : "날"}</button>
          ))}
        </div>
        <IndicatorPanel rows={equityRows} field="pnl" name="누적 손익(USDT)" color="#3aa0c0" width={1.8}
          aspect={PANEL_ASPECT[s.chartHeight]} guides={[0]} show={(v) => (v >= 0 ? "+" : "") + fmt(v)} />
      </Card>
    ),
    safety: (
      <Card title="안전 상태 (조회 전용)">
        {d?.kill_switch ? (
          <>
            <Row k="킬 스위치" v={d.kill_switch.engaged ? "작동 중 (거래 중지)" : "해제됨"} cls={d.kill_switch.engaged ? "down" : "up"} />
            <Row k="사유" v={(d.kill_switch.reason ?? "-") + (d.kill_switch.triggered_by ? " · " + d.kill_switch.triggered_by : "")} />
            <Row k="시각" v={d.kill_switch.at ? whenFull(new Date(d.kill_switch.at)) : "-"} />
          </>
        ) : <Row k="킬 스위치" v="알 수 없음" />}
        {d?.reconciliation ? (
          <>
            <Row k="재조정(내부 vs 거래소)" v={d.reconciliation.ok ? "일치" : "불일치 (신규 진입 차단)"} cls={d.reconciliation.ok ? "up" : "down"} />
            {!d.reconciliation.ok && <Row k="불일치 내용" v={(d.reconciliation.detail ?? "") + " " + d.reconciliation.mismatches.join(", ")} />}
            <Row k="확인 시각" v={d.reconciliation.at ? whenFull(new Date(d.reconciliation.at)) : "-"} />
          </>
        ) : <Row k="재조정" v="최근 기록 없음" />}
      </Card>
    ),
    votes: <Card title="지표별 판단 (닫힌 15분봉마다 갱신)">{d ? <Votes d={d} /> : null}</Card>,
    ivals: (
      <Card title="지표 현재값 (전략이 쓰는 값)">
        <Row k="RSI(14)" v={L.rsi14 == null ? "-" : L.rsi14.toFixed(1)} />
        <Row k="EMA20 대비" v={L.ema20_gap == null ? "-" : (L.ema20_gap * 100).toFixed(2) + "%"} cls={sgn(L.ema20_gap)} />
        <Row k="볼린저 z" v={L.bollinger_z == null ? "-" : L.bollinger_z.toFixed(2)} cls={sgn(L.bollinger_z)} />
        <Row k="돈치안 위치" v={L.donchian_pos == null ? "-" : (L.donchian_pos * 100).toFixed(0) + "% (0=하단 100=상단)"} />
        <Row k="ROC(14)" v={L.roc14 == null ? "-" : (L.roc14 * 100).toFixed(2) + "%"} cls={sgn(L.roc14)} />
      </Card>
    ),
    decisions: (
      <Card title="최근 판단">
        <ul className="list">
          {(d?.decisions ?? []).slice().reverse().map((x, i) => (
            <li key={i}>{whenFull(new Date(x.time))}  {x.action}  {x.reason}</li>
          ))}
        </ul>
      </Card>
    ),
    trades: (
      <Card title="청산된 거래">
        <div className="sidepick" role="group" aria-label="롱·숏 보기">
          {(["all", "long", "short"] as const).map((v) => (
            <button key={v} className={side === v ? "on" : ""} onClick={() => setSide(v)}>{v === "all" ? "전체" : v === "long" ? "롱" : "숏"}</button>
          ))}
        </div>
        {d?.closed_trades.length ? (
          <table className="trades">
            <thead>
              <tr>
                {tradeCols.times && <th title="한국시간(KST)">진입 / 청산 시각</th>}
                {tradeCols.dir && <th>방향</th>}
                {tradeCols.prices && <th>진입 → 청산가</th>}
                {tradeCols.priceRet && <th title="진입가에서 청산가까지 가격이 내 방향으로 움직인 %. 수수료·레버리지 반영 전">가격 수익률 %<small>(수수료 전)</small></th>}
                {tradeCols.equityRet && <th title="수수료·슬리피지·펀딩을 뺀 손익을, 진입 때 계좌 잔고로 나눈 %">계좌 수익률 %<small>(수수료 후)</small></th>}
                {tradeCols.pnl && <th>손익 USDT</th>}
                {tradeCols.reason && <th>청산 사유</th>}
              </tr>
            </thead>
            <tbody>
              {d.closed_trades.filter((x) => side === "all" || x.direction === (side === "long" ? 1 : -1)).reverse().map((x, i) => (
                <tr key={i}>
                  {tradeCols.times && <td style={{ textAlign: "left" }}>{kst(x.entry_time)}<br /><span className="muted">{kst(x.exit_time)}</span></td>}
                  {tradeCols.dir && <td className={x.direction === 1 ? "up" : x.direction === -1 ? "down" : ""}>{x.direction === 1 ? "롱" : x.direction === -1 ? "숏" : "-"}</td>}
                  {tradeCols.prices && <td>{fmt(x.entry_price)} → {fmt(x.exit_price)}</td>}
                  {tradeCols.priceRet && <td className={sgn(x.price_return)}>{signedPct(x.price_return, 2)}</td>}
                  {tradeCols.equityRet && <td className={sgn(x.equity_return)}>{signedPct(x.equity_return, 3)}</td>}
                  {tradeCols.pnl && <td className={sgn(x.net_pnl)}>{(x.net_pnl >= 0 ? "+" : "") + fmt(x.net_pnl)}</td>}
                  {tradeCols.reason && <td className="muted" title={x.exit_reason ?? ""}>{exitReason(x.exit_reason)}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="muted">아직 없음</div>}
      </Card>
    ),
  };
  return (
    <main>
      <h1>모의투자 대시보드<span className="badge">읽기 전용 · 실제 돈 아님</span></h1>
      <div className="mono">{asOf}</div>
      <div className="tabs" role="tablist" aria-label="페이지">
        <button className={page === 1 ? "on" : ""} onClick={() => setPage(1)}>1 메인</button>
        <button className={page === 2 ? "on" : ""} onClick={() => setPage(2)}>2 캘린더</button>
        <button className={page === 3 ? "on" : ""} onClick={() => setPage(3)}>3 대시보드</button>
        <button className="gear" style={{ marginLeft: "auto" }} onClick={() => setSettingsOpen(true)}>⚙ 설정</button>
      </div>
      <div className="err">{err}</div>
      {page === 1 && <HeroDock />}
      {page === 2 && <CalendarPage d={d} />}
      {page === 3 && <>
      <div className="tabs">
        {symbols.map((sy) => (
          <button key={sy} className={sy === sym ? "on" : ""} onClick={() => { setSym(sy); location.hash = sy; setD(null); setView(home); }}>{sy}</button>
        ))}
      </div>
      {!s.layout.some((l) => l.show) && <div className="muted">보이는 패널이 없어요. ⚙ 설정에서 패널을 켜 주세요.</div>}
      <div className="board">
        {s.layout.filter((l) => l.show).map((l, i) => (
          <div key={`${l.id}-${i}-${l.size}-${s.fontScale}-${s.chartHeight}`} className={`slot span-${l.size}`}>{panels[l.id]}</div>
        ))}
      </div>
      </>}
      {settingsOpen && <SettingsPanel s={s} update={update} reset={reset} close={() => setSettingsOpen(false)} />}
    </main>
  );
}
