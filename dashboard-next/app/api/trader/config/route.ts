import { DEMO_SYMBOLS } from "@/lib/demo-data"
import { errorMessage, fetchTrader, traderBaseUrl } from "@/lib/trader-api"
import type { ConfigResponse } from "@/lib/types"

export const dynamic = "force-dynamic"

export async function GET() {
  if (!traderBaseUrl()) {
    return Response.json({ source: "demo", symbols: DEMO_SYMBOLS } satisfies ConfigResponse)
  }
  try {
    const { symbols } = await fetchTrader<{ symbols: string[] }>("/api/config")
    return Response.json({ source: "live", symbols } satisfies ConfigResponse)
  } catch (e) {
    return Response.json({ source: "demo", symbols: DEMO_SYMBOLS, error: errorMessage(e) } satisfies ConfigResponse)
  }
}
