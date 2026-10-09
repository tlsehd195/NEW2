const TIMEOUT_MS = 4000

export function traderBaseUrl() {
  const url = process.env.TRADER_API_URL?.trim()
  return url ? url.replace(/\/+$/, "") : null
}

export async function fetchTrader<T>(path: string): Promise<T> {
  const base = traderBaseUrl()
  if (!base) throw new Error("TRADER_API_URL이 설정되지 않았어요")
  const res = await fetch(base + path, { cache: "no-store", signal: AbortSignal.timeout(TIMEOUT_MS) })
  if (!res.ok) throw new Error(`트레이더 서버 응답 ${res.status}`)
  return (await res.json()) as T
}

export const errorMessage = (e: unknown) => (e instanceof Error ? e.message : String(e))
