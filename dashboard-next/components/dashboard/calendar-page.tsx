"use client"

import { useMemo, useState } from "react"
import type { Snapshot } from "@/lib/types"
import { cn } from "@/lib/utils"
import { tone } from "@/lib/format"

const fmt = (n: number, d = 2) => n.toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d })
const signed = (n: number) => (n >= 0 ? "+" : "") + fmt(n)
const pct = (x: number | null | undefined) => (x == null ? "-" : (x >= 0 ? "+" : "") + (x * 100).toFixed(2) + "%")
const kstToday = () => new Date(Date.now() + 9 * 3_600_000).toISOString().slice(0, 10)

function Stat({ label, value, valueClass, sub }: { label: string; value: string; valueClass?: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("my-1 font-mono text-xl font-semibold tabular-nums", valueClass)}>{value}</p>
      {sub && <p className="font-mono text-xs text-muted-foreground">{sub}</p>}
    </div>
  )
}

export function CalendarPage({ snapshot }: { snapshot: Snapshot }) {
  const today = kstToday()
  const [month, setMonth] = useState(today.slice(0, 7))
  const [picked, setPicked] = useState<string | null>(null) // the day found with the date search
  const daily = snapshot.daily ?? []
  const byDate = useMemo(() => new Map(daily.map((x) => [x.date, x])), [daily])
  const inMonth = daily.filter((x) => x.date.startsWith(month))
  const monthPnl = inMonth.reduce((a, x) => a + x.pnl, 0)
  const trades = inMonth.reduce((a, x) => a + x.trades, 0)
  const wins = inMonth.reduce((a, x) => a + x.wins, 0)
  const best = inMonth.reduce<(typeof daily)[number] | null>((b, x) => (!b || x.pnl > b.pnl ? x : b), null)
  const worst = inMonth.reduce<(typeof daily)[number] | null>((b, x) => (!b || x.pnl < b.pnl ? x : b), null)
  const total = daily.reduce((a, x) => a + x.pnl, 0)
  const t = byDate.get(today)
  const acct = snapshot.account
  const [y, m] = month.split("-").map(Number)
  const lead = new Date(Date.UTC(y, m - 1, 1)).getUTCDay()
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate()
  const cells: (string | null)[] = [
    ...Array<null>(lead).fill(null),
    ...Array.from({ length: last }, (_, i) => `${month}-${String(i + 1).padStart(2, "0")}`),
  ]
  const shift = (n: number) => setMonth(new Date(Date.UTC(y, m - 1 + n, 1)).toISOString().slice(0, 7))
  const maxAbs = Math.max(1e-9, ...inMonth.map((x) => Math.abs(x.pnl)))

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <Stat
          label="오늘 손익 (한국시간)"
          value={t ? `${signed(t.pnl)} USDT` : "-"}
          valueClass={tone(t?.pnl ?? null)}
          sub={t ? `${pct(t.return_pct)} · ${t.trades}건 (승 ${t.wins})` : "오늘 청산된 거래 없음"}
        />
        <Stat
          label="계좌 잔고"
          value={acct ? `${fmt(acct.balance)} USDT` : "-"}
          sub={acct?.balance_krw != null ? `≈ ${Math.round(acct.balance_krw).toLocaleString("ko-KR")}원` : undefined}
        />
        <Stat
          label={`${m}월 손익`}
          value={`${signed(monthPnl)} USDT`}
          valueClass={tone(monthPnl)}
          sub={trades ? `${trades}건 · 승률 ${((wins / trades) * 100).toFixed(0)}%` : "거래 없음"}
        />
        <Stat
          label="최고 / 최악의 날"
          value={best ? `${best.date.slice(8)}일 ${signed(best.pnl)}` : "-"}
          valueClass="text-up"
          sub={worst ? `${worst.date.slice(8)}일 ${signed(worst.pnl)}` : undefined}
        />
        <Stat label="누적 손익 (기록 전체)" value={`${signed(total)} USDT`} valueClass={tone(total)} sub={`${daily.length}일 거래`} />
      </div>
      <section aria-label="월간 캘린더" className="rounded-lg border border-border bg-card p-4">
        <div className="mb-3 flex items-center gap-3">
          <button className="rounded border border-border px-3 py-1 text-sm" onClick={() => shift(-1)} aria-label="이전 달">
            ‹
          </button>
          <strong className="font-mono text-sm">
            {y}년 {m}월
          </strong>
          <button className="rounded border border-border px-3 py-1 text-sm" onClick={() => shift(1)} aria-label="다음 달">
            ›
          </button>
          <button className="rounded border border-border px-3 py-1 text-xs" onClick={() => { setMonth(today.slice(0, 7)); setPicked(null) }}>
            오늘
          </button>
          <label className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            날짜 검색
            <input
              type="date"
              value={picked ?? ""}
              onChange={(e) => {
                setPicked(e.target.value || null)
                if (e.target.value) setMonth(e.target.value.slice(0, 7))
              }}
              className="rounded border border-border bg-background px-2 py-1 font-mono text-xs text-foreground"
            />
            {picked && (
              <button type="button" className="rounded border border-border px-2 py-1" onClick={() => setPicked(null)} aria-label="검색 해제">
                ✕ 해제
              </button>
            )}
          </label>
        </div>
        {picked && (
          <p className="mb-3 rounded border border-border px-3 py-2 text-sm" role="status">
            {picked} ·{" "}
            {byDate.get(picked) ? (
              <>
                <span className={cn("font-mono", tone(byDate.get(picked)!.pnl))}>{signed(byDate.get(picked)!.pnl)} USDT</span>
                {` (${pct(byDate.get(picked)!.return_pct)}) · ${byDate.get(picked)!.trades}건 (승 ${byDate.get(picked)!.wins})`}
              </>
            ) : (
              <span className="text-muted-foreground">이 날은 청산된 거래가 없어요</span>
            )}
          </p>
        )}
        <div className="grid grid-cols-7 gap-1">
          {["일", "월", "화", "수", "목", "금", "토"].map((w) => (
            <div key={w} className="py-1 text-center text-xs text-muted-foreground">
              {w}
            </div>
          ))}
          {cells.map((c, i) => {
            const x = c ? byDate.get(c) : undefined
            const a = x ? 0.1 + 0.3 * Math.min(1, Math.abs(x.pnl) / maxAbs) : 0
            return (
              <div
                key={i}
                title={c === picked ? "검색한 날" : c === today ? "오늘" : undefined}
                className={cn(
                  "min-h-16 rounded border p-1.5 text-xs md:min-h-24",
                  c ? "border-border" : "border-transparent",
                  c === today && "ring-1 ring-foreground",
                  c === picked && "ring-2 ring-primary",
                )}
                style={x ? { background: x.pnl >= 0 ? `rgba(46,189,133,${a})` : `rgba(240,84,94,${a})` } : undefined}
              >
                {c && <p className="font-mono text-muted-foreground">{Number(c.slice(8))}</p>}
                {x && (
                  <>
                    <p className={cn("font-mono tabular-nums", tone(x.pnl))}>{signed(x.pnl)}</p>
                    <p className={cn("font-mono text-[10px] tabular-nums", tone(x.return_pct ?? x.pnl))}>{pct(x.return_pct)}</p>
                    <p className="text-[10px] text-muted-foreground">{x.trades}건</p>
                  </>
                )}
              </div>
            )
          })}
        </div>
        <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <span className="size-3 rounded-sm ring-1 ring-foreground" aria-hidden /> 오늘
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="size-3 rounded-sm ring-2 ring-primary" aria-hidden /> 검색한 날 (날짜 검색에서 고른 날)
          </span>
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          하루는 한국시간 기준이에요. 퍼센트는 그날 첫 진입 때 계좌 잔고 대비 손익(수수료·슬리피지·펀딩 반영 후)이에요.
        </p>
      </section>
    </div>
  )
}
