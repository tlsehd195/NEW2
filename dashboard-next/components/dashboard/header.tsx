"use client"

import { Activity, RefreshCw, ShieldCheck } from "lucide-react"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { ConnectionHelp } from "./connection-help"
import type { DataSource } from "@/lib/types"
import { cn } from "@/lib/utils"

interface Props {
  symbols: string[]
  symbol: string
  onSymbolChange: (s: string) => void
  refreshSec: number
  onRefreshChange: (n: number) => void
  source: DataSource | undefined
  error?: string
  asOf?: string
  isValidating: boolean
}

const REFRESH_OPTIONS = [5, 10, 30, 60]

export function DashboardHeader(p: Props) {
  const live = p.source === "live"
  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 lg:px-6">
        <div className="flex items-center gap-2.5">
          <div className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Activity className="size-4" aria-hidden />
          </div>
          <div className="leading-tight">
            <h1 className="text-sm font-semibold tracking-tight">NEW2 트레이더</h1>
            <p className="text-xs text-muted-foreground">15분봉 롱·숏 단타</p>
          </div>
        </div>

        <nav aria-label="심볼 선택" className="flex items-center gap-1 rounded-lg bg-secondary p-1">
          {p.symbols.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => p.onSymbolChange(s)}
              aria-pressed={s === p.symbol}
              className={cn(
                "rounded-md px-3 py-1 font-mono text-xs font-medium transition-colors",
                s === p.symbol ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {s.replace("USDT", "")}
              <span className="text-muted-foreground">/USDT</span>
            </button>
          ))}
        </nav>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-md border border-primary/30 bg-primary/10 px-2 py-1 text-xs font-medium text-primary">
            <ShieldCheck className="size-3.5" aria-hidden />
            페이퍼 · 읽기 전용
          </span>

          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium",
              live ? "border-up/30 bg-up/10 text-up" : "border-border bg-secondary text-muted-foreground",
            )}
            title={p.error}
          >
            <span className={cn("size-1.5 rounded-full", live ? "animate-pulse bg-up" : "bg-muted-foreground")} aria-hidden />
            {p.source === undefined ? "연결 중" : live ? "실시간 연결" : p.error ? "연결 실패 · 데모" : "데모 데이터"}
          </span>

          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <RefreshCw className={cn("size-3.5", p.isValidating && "animate-spin")} aria-hidden />
            <Select value={String(p.refreshSec)} onValueChange={(v) => p.onRefreshChange(Number(v))}>
              <SelectTrigger size="sm" className="h-7 w-[84px] text-xs" aria-label="새로고침 주기">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {REFRESH_OPTIONS.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}초
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <ConnectionHelp live={live} error={p.error} />
        </div>
      </div>
    </header>
  )
}
