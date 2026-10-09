"use client"

import useSWR from "swr"
import type { ConfigResponse, SnapshotResponse } from "@/lib/types"

const fetcher = async <T,>(url: string): Promise<T> => {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`요청 실패 (${res.status})`)
  return res.json()
}

export function useTraderConfig() {
  return useSWR<ConfigResponse>("/api/trader/config", fetcher, { revalidateOnFocus: false })
}

export function useSnapshot(symbol: string | undefined, bars: number, refreshSec: number) {
  return useSWR<SnapshotResponse>(
    symbol ? `/api/trader/snapshot?symbol=${symbol}&bars=${bars}` : null,
    fetcher,
    { refreshInterval: refreshSec * 1000, keepPreviousData: true },
  )
}
