// Shape of /api/snapshot produced by NEW2's src/cointrader/monitoring/dashboard.py:read_snapshot.
export type FillKind = "long_entry" | "long_exit" | "short_entry" | "short_exit"

export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface Fill {
  time: number
  side: string
  price: number
  quantity: number
  kind: FillKind | null
}

export interface ClosedTrade {
  entry_time: string | null
  exit_time: string | null
  direction: number | null
  net_pnl: number
  exit_reason: string | null
  entry_price: number | null
  exit_price: number | null
  price_return: number | null
  equity_return: number | null
}

export interface Position {
  direction: "long" | "short"
  state: string
  quantity: number
  entry_price: number | null
  stop_price: number | null
  strategy: string
  entry_time: string | null
  unrealized_pnl: number | null
  notional?: number | null
  leverage?: number | null
  margin?: number | null
}

export interface Vote {
  strategy: string
  bar_time: string
  action: string
  reason: string
  p_long: number | null
  agree_long: number | null
  agree_short: number | null
  vol_ratio: number | null
  per_indicator: Record<string, number>
}

export type RuleKey = "enter_confidence" | "exit_confidence" | "min_agree" | "vol_gate_lo" | "vol_gate_hi"

export interface Snapshot {
  symbol: string
  as_of: string
  candles: Candle[]
  last_price: number | null
  last_price_time: string | null
  fills: Fill[]
  decisions: { time: string; action: string; reason: string }[]
  closed_trades: ClosedTrade[]
  account: {
    balance: number; saved_at: string; open_positions: number
    rate_krw?: number | null; balance_krw?: number | null; equity_krw?: number | null; krw_as_of?: string | null
  } | null
  position: Position | null
  overlays: Record<string, (number | null)[]>
  latest: Partial<Record<"rsi14" | "ema20_gap" | "bollinger_z" | "roc14" | "donchian_pos", number | null>>
  votes: Vote[]
  rules: Partial<Record<RuleKey, number>>
}

export type DataSource = "live" | "demo"

export interface SnapshotResponse {
  source: DataSource
  error?: string
  snapshot: Snapshot
}

export interface ConfigResponse {
  source: DataSource
  error?: string
  symbols: string[]
}
