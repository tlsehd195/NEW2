import { demoSnapshot } from "@/lib/demo-data"
import { errorMessage, fetchTrader, traderBaseUrl } from "@/lib/trader-api"
import type { Snapshot, SnapshotResponse } from "@/lib/types"

export const dynamic = "force-dynamic"

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams
  const symbol = params.get("symbol") ?? "BTCUSDT"
  if (!/^[A-Z0-9]{2,20}$/.test(symbol)) {
    return Response.json({ error: "잘못된 심볼" }, { status: 400 })
  }
  const parsedBars = Number.parseInt(params.get("bars") ?? "200", 10)
  const bars = Math.min(Math.max(Number.isFinite(parsedBars) ? parsedBars : 200, 50), 2000)

  if (!traderBaseUrl()) {
    return Response.json({ source: "demo", snapshot: demoSnapshot(symbol, bars) } satisfies SnapshotResponse)
  }
  try {
    const snapshot = await fetchTrader<Snapshot>(`/api/snapshot?symbol=${symbol}&bars=${bars}`)
    return Response.json({ source: "live", snapshot } satisfies SnapshotResponse)
  } catch (e) {
    return Response.json({
      source: "demo",
      error: errorMessage(e),
      snapshot: demoSnapshot(symbol, bars),
    } satisfies SnapshotResponse)
  }
}
