import type { Snapshot } from "./types"

export type EquityUnit = "hour" | "day"

/** Cumulative PnL per hour (last value in each hour, 30-day trade curve) or per Korea-time day (running total of daily results). */
export function equityPoints(snapshot: Snapshot, unit: EquityUnit): { t: number; pnl: number }[] {
  if (unit === "day") {
    let total = 0
    return [...(snapshot.daily ?? [])]
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((x) => ({ t: Math.floor(new Date(`${x.date}T00:00:00+09:00`).getTime() / 1000), pnl: (total += x.pnl) }))
  }
  const byHour = new Map<number, number>()
  for (const p of snapshot.equity_curve ?? []) byHour.set(Math.floor(new Date(p.time).getTime() / 3_600_000), p.pnl)
  return [...byHour].sort((a, b) => a[0] - b[0]).map(([h, pnl]) => ({ t: h * 3600, pnl }))
}
