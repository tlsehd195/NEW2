// Cumulative PnL points for the equity curve, per hour (last value in each hour, from the 30-day trade curve)
// or per Korea-time day (running total of the daily results, whole record).
import type { Snapshot } from "./types";

export type EquityUnit = "hour" | "day";

export function equityPoints(d: Snapshot | null, unit: EquityUnit): { date: Date; pnl: number }[] {
  if (!d) return [];
  if (unit === "day") {
    let total = 0;
    return [...(d.daily ?? [])].sort((a, b) => a.date.localeCompare(b.date))
      .map((x) => ({ date: new Date(`${x.date}T00:00:00+09:00`), pnl: (total += x.pnl) }));
  }
  const byHour = new Map<number, number>();
  for (const p of d.equity_curve ?? []) byHour.set(Math.floor(new Date(p.time).getTime() / 3_600_000), p.pnl);
  return [...byHour].sort((a, b) => a[0] - b[0]).map(([h, pnl]) => ({ date: new Date(h * 3_600_000), pnl }));
}
