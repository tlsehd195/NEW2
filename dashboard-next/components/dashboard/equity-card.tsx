"use client"

import { useEffect, useRef } from "react"
import { ColorType, createChart, LineSeries, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from "lightweight-charts"
import type { Snapshot } from "@/lib/types"
import { signedUsd, tone } from "@/lib/format"
import { cn } from "@/lib/utils"

const kstDay = (t: Time) =>
  new Date((t as number) * 1000).toLocaleString("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", hour12: false })

/** Strictly increasing, one point per second (the chart rejects anything else). */
function toSeries(points: NonNullable<Snapshot["equity_curve"]>) {
  const byTime = new Map<number, number>()
  for (const p of points) byTime.set(Math.floor(new Date(p.time).getTime() / 1000), p.pnl)
  return [...byTime].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ time: t as UTCTimestamp, value: v }))
}

export function EquityCard({ snapshot }: { snapshot: Snapshot }) {
  const curve = snapshot.equity_curve ?? []
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
      timeScale: { borderColor: "rgba(255,255,255,0.08)", timeVisible: true, tickMarkFormatter: kstDay },
      localization: { timeFormatter: (t: Time) => kstDay(t) },
    })
    seriesRef.current = chart.addSeries(LineSeries, { color: "#5aa9f5", lineWidth: 2, priceFormat: { type: "price", precision: 2, minMove: 0.01 } })
    chartRef.current = chart
    return () => {
      chart.remove()
      chartRef.current = seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    seriesRef.current?.setData(toSeries(curve))
    chartRef.current?.timeScale().fitContent()
  }, [curve])

  const last = curve.length ? curve[curve.length - 1].pnl : null
  return (
    <section aria-label="자산곡선" className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <h2 className="text-sm font-semibold">자산곡선 (누적 손익)</h2>
        <span className={cn("font-mono text-sm tabular-nums", tone(last))}>
          {last != null ? `${signedUsd(last)} · ${curve.length}건` : "-"}
        </span>
      </div>
      <div className="relative h-[220px] px-2 py-2">
        <div ref={ref} className="h-full w-full" />
        {curve.length === 0 && (
          <p className="absolute inset-0 flex items-center justify-center text-xs text-muted-foreground">
            청산된 거래가 아직 없어요 (최근 30일, 모든 종목)
          </p>
        )}
      </div>
    </section>
  )
}
