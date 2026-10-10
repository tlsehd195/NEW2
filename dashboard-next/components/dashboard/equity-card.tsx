"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { ColorType, createChart, LineSeries, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from "lightweight-charts"
import type { Snapshot } from "@/lib/types"
import { equityPoints, type EquityUnit } from "@/lib/equity"
import { signedUsd, tone } from "@/lib/format"
import { cn } from "@/lib/utils"

const kstLabel = (t: Time, unit: EquityUnit) =>
  new Date((t as number) * 1000).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "numeric",
    day: "numeric",
    ...(unit === "hour" ? { hour: "2-digit", hour12: false } : {}),
  })

export function EquityCard({ snapshot }: { snapshot: Snapshot }) {
  const [unit, setUnit] = useState<EquityUnit>("hour")
  const unitRef = useRef(unit)
  unitRef.current = unit
  const points = useMemo(() => equityPoints(snapshot, unit), [snapshot, unit])
  const ref = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null)

  useEffect(() => {
    if (!ref.current) return
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9aa0ab", attributionLogo: false },
      grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.04)" } },
      rightPriceScale: { borderColor: "rgba(255,255,255,0.08)" },
      timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, tickMarkFormatter: (t: Time) => kstLabel(t, unitRef.current) },
      localization: { timeFormatter: (t: Time) => kstLabel(t, unitRef.current) },
    })
    seriesRef.current = chart.addSeries(LineSeries, { color: "#5aa9f5", lineWidth: 2, priceFormat: { type: "price", precision: 2, minMove: 0.01 } })
    chartRef.current = chart
    return () => {
      chart.remove()
      chartRef.current = seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    seriesRef.current?.setData(points.map((p) => ({ time: p.t as UTCTimestamp, value: p.pnl })))
    chartRef.current?.timeScale().fitContent()
  }, [points])

  const last = points.length ? points[points.length - 1].pnl : null
  return (
    <section aria-label="자산곡선" className="rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
        <h2 className="text-sm font-semibold">
          자산곡선 (누적 손익) <span className="font-normal text-muted-foreground">{unit === "hour" ? "시간별 · 최근 30일" : "날짜별 · 전체 기록"}</span>
        </h2>
        <div className="flex items-center gap-3">
          <div role="group" aria-label="자산곡선 단위" className="flex gap-1 rounded-md bg-secondary p-0.5">
            {(["hour", "day"] as const).map((u) => (
              <button
                key={u}
                type="button"
                aria-pressed={unit === u}
                onClick={() => setUnit(u)}
                className={cn("rounded px-2.5 py-0.5 text-xs", unit === u ? "bg-background font-medium shadow-sm" : "text-muted-foreground")}
              >
                {u === "hour" ? "시간" : "날"}
              </button>
            ))}
          </div>
          <span className={cn("font-mono text-sm tabular-nums", tone(last))}>{last != null ? signedUsd(last) : "-"}</span>
        </div>
      </div>
      <div className="relative h-[220px] px-2 py-2">
        <div ref={ref} className="h-full w-full" />
        {points.length === 0 && (
          <p className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">청산된 거래가 아직 없어요</p>
        )}
      </div>
    </section>
  )
}
