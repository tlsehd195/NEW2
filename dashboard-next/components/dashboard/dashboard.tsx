"use client"

import { useEffect, useState } from "react"
import { AlertTriangle } from "lucide-react"
import { useSnapshot, useTraderConfig } from "@/hooks/use-trader"
import { DashboardHeader } from "./header"
import { StatStrip } from "./stat-strip"
import { ChartPanel } from "./chart-panel"
import { PositionCard } from "./position-card"
import { VotesCard } from "./votes-card"
import { EquityCard } from "./equity-card"
import { SafetyCard } from "./safety-card"
import { CalendarPage } from "./calendar-page"
import { cn } from "@/lib/utils"
import { ActivityTabs } from "./activity-tabs"
import { kst } from "@/lib/format"

const BARS = 300

export function Dashboard() {
  const { data: config } = useTraderConfig()
  const [selected, setSelected] = useState<string>()
  const [refreshSec, setRefreshSec] = useState(10)
  const [page, setPage] = useState<1 | 2>(1) // 1 = daily calendar, 2 = the full dashboard
  // Remember the symbol and refresh interval in this browser (unavailable storage just means no memory).
  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("dashboard-next:prefs") ?? "{}")
      if (typeof saved.symbol === "string") setSelected(saved.symbol)
      if (typeof saved.refreshSec === "number" && saved.refreshSec > 0) setRefreshSec(saved.refreshSec)
    } catch {}
  }, [])
  useEffect(() => {
    try {
      localStorage.setItem("dashboard-next:prefs", JSON.stringify({ symbol: selected, refreshSec }))
    } catch {}
  }, [selected, refreshSec])
  const symbols = config?.symbols ?? []
  const symbol = selected && symbols.includes(selected) ? selected : symbols[0]
  const { data, error, isValidating } = useSnapshot(symbol, BARS, refreshSec)
  const snapshot = data?.snapshot

  return (
    <div className="min-h-dvh">
      <DashboardHeader
        symbols={symbols}
        symbol={symbol ?? ""}
        onSymbolChange={setSelected}
        refreshSec={refreshSec}
        onRefreshChange={setRefreshSec}
        source={data?.source}
        error={data?.error}
        asOf={snapshot?.as_of}
        isValidating={isValidating}
      />
      <main className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-4 lg:px-6">
        {error && (
          <div role="alert" className="flex items-center gap-2 rounded-lg border border-down/30 bg-down/10 px-4 py-2.5 text-sm text-down">
            <AlertTriangle className="size-4" aria-hidden />
            데이터를 불러오지 못했어요. 잠시 후 다시 시도해요.
          </div>
        )}
        {snapshot ? (
          <>
            <div role="tablist" aria-label="페이지" className="flex gap-1">
              {([1, 2] as const).map((n) => (
                <button
                  key={n}
                  role="tab"
                  aria-selected={page === n}
                  onClick={() => setPage(n)}
                  className={cn("rounded px-3 py-1.5 text-sm", page === n ? "bg-foreground text-background" : "text-muted-foreground hover:text-foreground")}
                >
                  {n === 1 ? "1 캘린더" : "2 대시보드"}
                </button>
              ))}
            </div>
            {page === 1 && <CalendarPage snapshot={snapshot} />}
            {page === 2 && <StatStrip snapshot={snapshot} />}
            {page === 2 && <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
              <div className="flex min-w-0 flex-col gap-4">
                <ChartPanel snapshot={snapshot} />
                <EquityCard snapshot={snapshot} />
                <ActivityTabs snapshot={snapshot} />
              </div>
              <aside className="flex flex-col gap-4" aria-label="포지션과 신호">
                <PositionCard snapshot={snapshot} />
                <SafetyCard snapshot={snapshot} />
                <VotesCard snapshot={snapshot} />
              </aside>
            </div>}
            <p className="pb-2 text-center text-xs text-muted-foreground">
              마지막 갱신 {kst(snapshot.as_of)} · 이 화면은 조회 전용이며 주문을 낼 수 없어요
            </p>
          </>
        ) : (
          <LoadingSkeleton />
        )}
      </main>
    </div>
  )
}

function LoadingSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true" aria-label="불러오는 중">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-[86px] animate-pulse rounded-lg bg-card" />
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="h-[600px] animate-pulse rounded-lg bg-card" />
        <div className="h-[600px] animate-pulse rounded-lg bg-card" />
      </div>
    </div>
  )
}
