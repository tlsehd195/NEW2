"use client"

import { useEffect, useMemo, useRef } from "react"
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts"
import { bollinger, donchian, ema, rsi } from "@/lib/indicators"
import type { Snapshot } from "@/lib/types"

export type OverlayKey = "ema" | "bollinger" | "donchian" | "volume" | "rsi"

const C = {
  up: "#2ebd85",
  down: "#f0545e",
  text: "#9aa0ab",
  grid: "rgba(255,255,255,0.04)",
  border: "rgba(255,255,255,0.08)",
  ema: "#f4b740",
  bb: "#5aa9f5",
  dc: "#b48cf2",
  rsi: "#f4b740",
}
const BAR = 900

const kstTime = (t: Time, withDate: boolean) =>
  new Date((t as number) * 1000).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    hour12: false,
    ...(withDate ? { month: "numeric", day: "numeric" } : {}),
    hour: "2-digit",
    minute: "2-digit",
  })

type Line = ISeriesApi<"Line">

interface Props {
  snapshot: Snapshot
  overlays: Record<OverlayKey, boolean>
}

export function PriceChart({ snapshot, overlays }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<{
    candle: ISeriesApi<"Candlestick">
    volume: ISeriesApi<"Histogram">
    ema: Line
    bbUp: Line
    bbLo: Line
    dcUp: Line
    dcLo: Line
    rsi: Line | null
    markers: ISeriesMarkersPluginApi<Time>
  } | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])
  const fittedSymbolRef = useRef<string | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: C.text,
        fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
        fontSize: 11,
        panes: { separatorColor: C.border, separatorHoverColor: "rgba(255,255,255,0.12)" },
      },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: C.border },
      timeScale: {
        borderColor: C.border,
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (t: Time) => kstTime(t, false),
      },
      localization: { locale: "ko-KR", timeFormatter: (t: Time) => kstTime(t, true) },
    })
    const line = (color: string, pane = 0, dashed = false) =>
      chart.addSeries(
        LineSeries,
        {
          color,
          lineWidth: 1,
          lineStyle: dashed ? LineStyle.Dashed : LineStyle.Solid,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        },
        pane,
      )
    const candle = chart.addSeries(CandlestickSeries, {
      upColor: C.up,
      downColor: C.down,
      borderVisible: false,
      wickUpColor: C.up,
      wickDownColor: C.down,
    })
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    })
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } })

    seriesRef.current = {
      candle,
      volume,
      ema: line(C.ema),
      bbUp: line(C.bb, 0, true),
      bbLo: line(C.bb, 0, true),
      dcUp: line(C.dc),
      dcLo: line(C.dc),
      rsi: null,
      markers: createSeriesMarkers(candle, []),
    }
    chartRef.current = chart
    return () => {
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
      priceLinesRef.current = []
      fittedSymbolRef.current = null
    }
  }, [])

  const computed = useMemo(() => {
    const cs = snapshot.candles
    const closes = cs.map((c) => c.close)
    const toLine = (vals: (number | null)[]) =>
      vals.flatMap((v, i) => (v == null ? [] : [{ time: cs[i].time as UTCTimestamp, value: v }]))
    const bb = bollinger(closes, 20, 2)
    const dc = donchian(cs.map((c) => c.high), cs.map((c) => c.low), 20)
    return {
      candles: cs.map((c) => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })),
      volume: cs.map((c) => ({
        time: c.time as UTCTimestamp,
        value: c.volume,
        color: c.close >= c.open ? "rgba(46,189,133,0.28)" : "rgba(240,84,94,0.28)",
      })),
      ema: toLine(ema(closes, 20)),
      bbUp: toLine(bb.upper),
      bbLo: toLine(bb.lower),
      dcUp: toLine(dc.upper),
      dcLo: toLine(dc.lower),
      rsi: toLine(rsi(closes, 14)),
    }
  }, [snapshot.candles])

  useEffect(() => {
    const s = seriesRef.current
    if (!s) return
    s.candle.setData(computed.candles)
    s.volume.setData(computed.volume)
    s.ema.setData(computed.ema)
    s.bbUp.setData(computed.bbUp)
    s.bbLo.setData(computed.bbLo)
    s.dcUp.setData(computed.dcUp)
    s.dcLo.setData(computed.dcLo)
    s.rsi?.setData(computed.rsi)
    if (fittedSymbolRef.current !== snapshot.symbol && computed.candles.length) {
      const n = computed.candles.length
      chartRef.current?.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 120), to: n + 4 })
      fittedSymbolRef.current = snapshot.symbol
    }
  }, [computed, snapshot.symbol])

  useEffect(() => {
    const s = seriesRef.current
    if (!s) return
    s.ema.applyOptions({ visible: overlays.ema })
    s.bbUp.applyOptions({ visible: overlays.bollinger })
    s.bbLo.applyOptions({ visible: overlays.bollinger })
    s.dcUp.applyOptions({ visible: overlays.donchian })
    s.dcLo.applyOptions({ visible: overlays.donchian })
    s.volume.applyOptions({ visible: overlays.volume })

    // Removing the RSI series (instead of hiding it) collapses its pane so the price chart reclaims the space.
    const chart = chartRef.current
    if (!chart) return
    if (overlays.rsi && !s.rsi) {
      const rsiSeries = chart.addSeries(
        LineSeries,
        { color: C.rsi, lineWidth: 1, priceLineVisible: false, lastValueVisible: true },
        1,
      )
      for (const level of [70, 30]) {
        rsiSeries.createPriceLine({ price: level, color: C.border, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: "" })
      }
      rsiSeries.setData(computed.rsi)
      chart.panes()[1]?.setHeight(110)
      s.rsi = rsiSeries
    } else if (!overlays.rsi && s.rsi) {
      chart.removeSeries(s.rsi)
      s.rsi = null
    }
  }, [overlays, computed.rsi])

  useEffect(() => {
    const s = seriesRef.current
    if (!s || !snapshot.candles.length) return
    const first = snapshot.candles[0].time
    const last = snapshot.candles[snapshot.candles.length - 1].time
    const markers: SeriesMarker<Time>[] = snapshot.fills
      .filter((f) => f.kind)
      .map((f) => ({ ...f, bar: Math.floor(f.time / BAR) * BAR }))
      .filter((f) => f.bar >= first && f.bar <= last)
      .sort((a, b) => a.bar - b.bar)
      .map((f) => {
        const isEntry = f.kind!.endsWith("entry")
        const isLong = f.kind!.startsWith("long")
        const buy = isEntry === isLong
        return {
          time: f.bar as UTCTimestamp,
          position: buy ? "belowBar" : "aboveBar",
          shape: isEntry ? (isLong ? "arrowUp" : "arrowDown") : "square",
          color: isLong ? C.up : C.down,
          text: isEntry ? (isLong ? "L" : "S") : isLong ? "롱청산" : "숏청산",
          size: 1,
        }
      })
    s.markers.setMarkers(markers)

    for (const pl of priceLinesRef.current) s.candle.removePriceLine(pl)
    priceLinesRef.current = []
    const pos = snapshot.position
    if (pos?.entry_price) {
      priceLinesRef.current.push(
        s.candle.createPriceLine({
          price: pos.entry_price,
          color: pos.direction === "long" ? C.up : C.down,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          axisLabelVisible: true,
          title: "진입",
        }),
      )
    }
    if (pos?.stop_price) {
      priceLinesRef.current.push(
        s.candle.createPriceLine({
          price: pos.stop_price,
          color: C.down,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: "손절",
        }),
      )
    }
  }, [snapshot.fills, snapshot.position, snapshot.candles])

  return <div ref={containerRef} className="h-full w-full" role="img" aria-label={`${snapshot.symbol} 15분봉 캔들 차트와 RSI`} />
}
