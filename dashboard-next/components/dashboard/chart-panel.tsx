"use client"

import { useState } from "react"
import dynamic from "next/dynamic"
import type { OverlayKey } from "./price-chart"
import type { Snapshot } from "@/lib/types"
import { cn } from "@/lib/utils"

const PriceChart = dynamic(() => import("./price-chart").then((m) => m.PriceChart), {
  ssr: false,
  loading: () => <div className="h-full w-full animate-pulse rounded bg-secondary/40" />,
})

const OVERLAYS: { key: OverlayKey; label: string; swatch: string }[] = [
  { key: "ema", label: "EMA 20", swatch: "bg-[#f4b740]" },
  { key: "bollinger", label: "볼린저 20·2", swatch: "bg-[#5aa9f5]" },
  { key: "donchian", label: "돈치안 20", swatch: "bg-[#b48cf2]" },
  { key: "volume", label: "거래량", swatch: "bg-muted-foreground" },
  { key: "rsi", label: "RSI 14", swatch: "bg-[#f4b740]" },
]

export function ChartPanel({ snapshot }: { snapshot: Snapshot }) {
  const [overlays, setOverlays] = useState<Record<OverlayKey, boolean>>({
    ema: true,
    bollinger: true,
    donchian: false,
    volume: true,
    rsi: true,
  })

  return (
    <section aria-label="가격 차트" className="flex flex-col rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <div className="flex items-baseline gap-2">
          <h2 className="font-mono text-sm font-semibold">{snapshot.symbol}</h2>
          <span className="text-xs text-muted-foreground">15분봉 · 한국시간</span>
        </div>
        <div className="flex flex-wrap items-center gap-1" role="group" aria-label="지표 표시">
          {OVERLAYS.map((o) => (
            <button
              key={o.key}
              type="button"
              aria-pressed={overlays[o.key]}
              onClick={() => setOverlays((prev) => ({ ...prev, [o.key]: !prev[o.key] }))}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors",
                overlays[o.key] ? "bg-secondary text-foreground" : "text-muted-foreground/70 hover:text-foreground",
              )}
            >
              <span className={cn("h-0.5 w-3 rounded-full", o.swatch, !overlays[o.key] && "opacity-40")} aria-hidden />
              {o.label}
            </button>
          ))}
        </div>
      </div>
      <div className="h-[460px] px-1 pb-1 lg:h-[540px]">
        <PriceChart snapshot={snapshot} overlays={overlays} />
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border px-4 py-2 text-xs text-muted-foreground">
        <span>
          <span className="text-up">▲ L</span> 롱 진입
        </span>
        <span>
          <span className="text-down">▼ S</span> 숏 진입
        </span>
          <span className="text-up">■ 롱청산</span>
          <span className="text-down">■ 숏청산</span>
        <span>점선 = 진입가 · 손절가</span>
        {overlays.rsi && <span className="ml-auto">아래 패널: RSI 14</span>}
      </div>
    </section>
  )
}
