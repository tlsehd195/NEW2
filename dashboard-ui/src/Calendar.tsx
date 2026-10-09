// Page 1: a month calendar of daily results (Korea time) plus the key numbers. Read-only, from the snapshot's `daily`.
import { useMemo, useState } from "react";
import type { Snapshot } from "./types";

const fmt = (n: number, d = 2) => n.toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (n: number, d = 2) => (n >= 0 ? "+" : "") + fmt(n, d);
const pct = (x: number | null | undefined, d = 2) => (x == null ? "-" : (x >= 0 ? "+" : "") + (x * 100).toFixed(d) + "%");
const cls = (n: number | null | undefined) => (n == null || n === 0 ? "" : n > 0 ? "up" : "down");
const kstToday = () => new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);

function Stat({ k, v, c, sub }: { k: string; v: string; c?: string; sub?: string }) {
  return (
    <div className="calstat">
      <div className="muted">{k}</div>
      <div className={`calnum mono ${c ?? ""}`}>{v}</div>
      {sub && <div className="muted mono">{sub}</div>}
    </div>
  );
}

export function CalendarPage({ d }: { d: Snapshot | null }) {
  const today = kstToday();
  const [month, setMonth] = useState(today.slice(0, 7)); // "YYYY-MM"
  const [picked, setPicked] = useState<string | null>(null); // the day found with the date search
  const daily = d?.daily ?? [];
  const byDate = useMemo(() => new Map(daily.map((x) => [x.date, x])), [daily]);
  const inMonth = daily.filter((x) => x.date.startsWith(month));
  const monthPnl = inMonth.reduce((a, x) => a + x.pnl, 0);
  const monthTrades = inMonth.reduce((a, x) => a + x.trades, 0);
  const monthWins = inMonth.reduce((a, x) => a + x.wins, 0);
  const best = inMonth.reduce<typeof daily[number] | null>((b, x) => (!b || x.pnl > b.pnl ? x : b), null);
  const worst = inMonth.reduce<typeof daily[number] | null>((b, x) => (!b || x.pnl < b.pnl ? x : b), null);
  const total = daily.reduce((a, x) => a + x.pnl, 0);
  const t = byDate.get(today);
  const acct = d?.account;
  const balance = acct?.balance ?? null;

  const [y, m] = month.split("-").map(Number);
  const lead = new Date(Date.UTC(y, m - 1, 1)).getUTCDay(); // Sunday = 0
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const cells: (string | null)[] = [...Array(lead).fill(null), ...Array.from({ length: last }, (_, i) => `${month}-${String(i + 1).padStart(2, "0")}`)];
  const shift = (n: number) => { const x = new Date(Date.UTC(y, m - 1 + n, 1)); setMonth(x.toISOString().slice(0, 7)); };
  const maxAbs = Math.max(1e-9, ...inMonth.map((x) => Math.abs(x.pnl)));

  return (
    <div>
      <div className="calstats">
        <Stat k="오늘 손익 (한국시간)" v={t ? signed(t.pnl) + " USDT" : "-"} c={cls(t?.pnl)}
          sub={t ? `${pct(t.return_pct)} · ${t.trades}건 (승 ${t.wins})` : "오늘 청산된 거래 없음"} />
        <Stat k="계좌 잔고" v={balance != null ? fmt(balance) + " USDT" : "-"}
          sub={acct?.balance_krw != null ? "≈ " + Math.round(acct.balance_krw).toLocaleString("ko-KR") + "원" : undefined} />
        <Stat k={`${m}월 손익`} v={signed(monthPnl) + " USDT"} c={cls(monthPnl)}
          sub={monthTrades ? `${monthTrades}건 · 승률 ${((monthWins / monthTrades) * 100).toFixed(0)}%` : "거래 없음"} />
        <Stat k="최고 / 최악의 날" v={best ? `${best.date.slice(8)}일 ${signed(best.pnl)}` : "-"} c="up"
          sub={worst ? `${worst.date.slice(8)}일 ${signed(worst.pnl)}` : undefined} />
        <Stat k="누적 손익 (기록 전체)" v={signed(total) + " USDT"} c={cls(total)} sub={`${daily.length}일 거래`} />
      </div>
      <div className="calhead">
        <button onClick={() => shift(-1)} aria-label="이전 달">‹</button>
        <strong className="mono">{y}년 {m}월</strong>
        <button onClick={() => shift(1)} aria-label="다음 달">›</button>
        <button className="today" onClick={() => { setMonth(today.slice(0, 7)); setPicked(null); }}>오늘</button>
        <label className="muted" style={{ marginLeft: "auto" }}>날짜 검색{" "}
          <input type="date" className="mono" value={picked ?? ""} onChange={(e) => {
            setPicked(e.target.value || null);
            if (e.target.value) setMonth(e.target.value.slice(0, 7));
          }} />
        </label>
      </div>
      {picked && (
        <div className="calfound" role="status">
          {picked} · {byDate.get(picked)
            ? <><span className={`mono ${cls(byDate.get(picked)!.pnl)}`}>{signed(byDate.get(picked)!.pnl)} USDT</span>{` (${pct(byDate.get(picked)!.return_pct)}) · ${byDate.get(picked)!.trades}건 (승 ${byDate.get(picked)!.wins})`}</>
            : <span className="muted">이 날은 청산된 거래가 없어요</span>}
        </div>
      )}
      <div className="calgrid">
        {["일", "월", "화", "수", "목", "금", "토"].map((w) => <div key={w} className="calwd muted">{w}</div>)}
        {cells.map((c, i) => {
          const x = c ? byDate.get(c) : undefined;
          const alpha = x ? 0.1 + 0.3 * Math.min(1, Math.abs(x.pnl) / maxAbs) : 0;
          const tint = x ? (x.pnl >= 0 ? `rgba(16,196,138,${alpha})` : `rgba(242,54,74,${alpha})`) : undefined;
          return (
            <div key={i} className={`calcell${c === today ? " now" : ""}${c && c === picked ? " picked" : ""}${c ? "" : " empty"}`} style={{ background: tint }}>
              {c && <div className="muted mono">{Number(c.slice(8))}</div>}
              {x && (
                <>
                  <div className={`mono ${cls(x.pnl)}`}>{signed(x.pnl)}</div>
                  <div className={`mono small ${cls(x.return_pct ?? x.pnl)}`}>{pct(x.return_pct)}</div>
                  <div className="muted small">{x.trades}건</div>
                </>
              )}
            </div>
          );
        })}
      </div>
      <div className="muted" style={{ marginTop: 10 }}>
        하루는 한국시간 기준이에요. 퍼센트는 그날 첫 진입 때 계좌 잔고 대비 손익(수수료·슬리피지·펀딩 반영 후)이에요.
      </div>
    </div>
  );
}
