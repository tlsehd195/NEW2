// Shape of /api/snapshot, produced by src/cointrader/monitoring/dashboard.py:read_snapshot.
export type FillKind = "long_entry" | "long_exit" | "short_entry" | "short_exit";

export interface Snapshot {
  symbol: string;
  as_of: string;
  candles: { time: number; open: number; high: number; low: number; close: number; volume: number }[];
  last_price: number | null;
  last_price_time: string | null;
  fills: { time: number; side: string; price: number; quantity: number; kind: FillKind | null }[];
  decisions: { time: string; action: string; reason: string }[];
  closed_trades: {
    entry_time: string | null; exit_time: string | null; direction: number | null; net_pnl: number; exit_reason: string | null;
    entry_price: number | null; exit_price: number | null; price_return: number | null; equity_return: number | null;
  }[];
  account: {
    balance: number; saved_at: string; open_positions: number;
    rate_krw?: number | null; balance_krw?: number | null; equity_krw?: number | null; krw_as_of?: string | null;
  } | null;
  position: {
    direction: "long" | "short"; state: string; quantity: number; entry_price: number | null;
    stop_price: number | null; strategy: string; entry_time: string | null; unrealized_pnl: number | null;
    notional?: number | null; leverage?: number | null; margin?: number | null;
  } | null;
  equity_curve?: { time: string; pnl: number }[];
  kill_switch?: { engaged: boolean; reason: string | null; triggered_by: string | null; at: string | null } | null;
  reconciliation?: { ok: boolean; detail: string | null; mismatches: string[]; at: string | null } | null;
  overlays: Record<string, (number | null)[]>;
  latest: Partial<Record<"rsi14" | "ema20_gap" | "bollinger_z" | "roc14" | "donchian_pos", number | null>>;
  votes: {
    strategy: string; bar_time: string; action: string; reason: string; p_long: number | null;
    agree_long: number | null; agree_short: number | null; vol_ratio: number | null; per_indicator: Record<string, number>;
  }[];
  rules: Partial<Record<"enter_confidence" | "exit_confidence" | "min_agree" | "vol_gate_lo" | "vol_gate_hi", number>>;
}
