import type { ClosedTrade, Fill, Snapshot, Vote } from "./types"

export const DEMO_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
const BASE_PRICE: Record<string, number> = { BTCUSDT: 98000, ETHUSDT: 3600 }
const BAR = 900
const STRATEGIES = ["daytrade_indicator_vote_h16_c0.6_v1", "daytrade_indicator_vote_h48_c0.6_v1"]
const INDICATORS = ["ema_trend", "donchian_pos", "roc", "rsi", "bollinger_b", "obv_slope"]
const RULES = { enter_confidence: 0.6, exit_confidence: 0.5, min_agree: 0.6, vol_gate_lo: 0.5, vol_gate_hi: 2.5 }

function hash(str: string) {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function rng(seed: number) {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const iso = (sec: number) => new Date(sec * 1000).toISOString()

export function demoSnapshot(symbol: string, bars: number, now = Date.now()): Snapshot {
  const nowSec = Math.floor(now / 1000)
  const lastOpen = Math.floor(nowSec / BAR) * BAR - BAR
  const firstOpen = lastOpen - (bars - 1) * BAR
  const rand = rng(hash(symbol + Math.floor(firstOpen / 86400)))
  const base = BASE_PRICE[symbol] ?? 100

  const candles = []
  let price = base
  let drift = 0
  for (let i = 0; i < bars; i++) {
    if (i % 40 === 0) drift = (rand() - 0.5) * 0.0012
    const open = price
    const close = open * (1 + drift + (rand() - 0.5) * 0.006)
    const high = Math.max(open, close) * (1 + rand() * 0.0025)
    const low = Math.min(open, close) * (1 - rand() * 0.0025)
    candles.push({ time: firstOpen + i * BAR, open, high, low, close, volume: Math.round(200 + rand() * 900) })
    price = close
  }

  const fills: Fill[] = []
  const closed: ClosedTrade[] = []
  const decisions: Snapshot["decisions"] = []
  let i = 20
  let balance = 10000
  while (i < bars - 40) {
    const dir = rand() > 0.45 ? 1 : -1
    const hold = 8 + Math.floor(rand() * 40)
    const entry = candles[i]
    const exit = candles[Math.min(i + hold, bars - 30)]
    const qty = +((balance * 0.3) / entry.close).toFixed(4)
    const priceReturn = ((exit.close - entry.close) / entry.close) * dir
    const pnl = priceReturn * qty * entry.close * 3 - qty * entry.close * 0.0008
    balance += pnl
    fills.push(
      { time: entry.time + 60, side: dir > 0 ? "buy" : "sell", price: entry.close, quantity: qty, kind: dir > 0 ? "long_entry" : "short_entry" },
      { time: exit.time + 60, side: dir > 0 ? "sell" : "buy", price: exit.close, quantity: qty, kind: dir > 0 ? "long_exit" : "short_exit" },
    )
    const reasons = ["signal_exit:p_long", "stop_loss", "take_profit", "time_stop"]
    closed.push({
      entry_time: iso(entry.time + 60),
      exit_time: iso(exit.time + 60),
      direction: dir,
      net_pnl: pnl,
      exit_reason: pnl < 0 && rand() > 0.5 ? "stop_loss" : reasons[Math.floor(rand() * reasons.length)],
      entry_price: entry.close,
      exit_price: exit.close,
      price_return: priceReturn,
      equity_return: pnl / 10000,
    })
    decisions.push({ time: iso(entry.time), action: dir > 0 ? "enter_long" : "enter_short", reason: "vote_confident" })
    decisions.push({ time: iso(exit.time), action: "exit", reason: "confidence_dropped" })
    i += hold + 6 + Math.floor(rand() * 20)
  }

  const entryCandle = candles[bars - 18]
  const last = candles[bars - 1]
  const jitter = Math.sin(nowSec / 7) * 0.0009 + Math.cos(nowSec / 13) * 0.0006
  const lastPrice = last.close * (1 + jitter)
  const qty = +((balance * 0.3) / entryCandle.close).toFixed(4)
  fills.push({ time: entryCandle.time + 60, side: "buy", price: entryCandle.close, quantity: qty, kind: "long_entry" })
  decisions.push({ time: iso(entryCandle.time), action: "enter_long", reason: "vote_confident" })
  for (let k = bars - 6; k < bars; k++) decisions.push({ time: iso(candles[k].time), action: "hold", reason: "in_position" })

  const votes: Vote[] = STRATEGIES.map((strategy, si) => {
    const per: Record<string, number> = {}
    for (const name of INDICATORS) per[name] = Math.min(0.95, Math.max(0.05, 0.62 + (rand() - 0.5) * 0.5 - si * 0.04))
    const values = Object.values(per)
    const pLong = values.reduce((a, b) => a + b, 0) / values.length
    return {
      strategy,
      bar_time: iso(last.time),
      action: "hold",
      reason: si === 0 ? "in_position" : "below_confidence",
      p_long: pLong,
      agree_long: values.filter((v) => v > 0.5).length,
      agree_short: values.filter((v) => v < 0.5).length,
      vol_ratio: 0.8 + rand() * 0.9,
      per_indicator: per,
    }
  })

  return {
    symbol,
    as_of: new Date(now).toISOString(),
    candles,
    last_price: lastPrice,
    last_price_time: new Date(now).toISOString(),
    fills,
    decisions: decisions.slice(-12),
    closed_trades: closed.slice(-10),
    account: { balance, saved_at: new Date(now - 30_000).toISOString(), open_positions: 1 },
    position: {
      direction: "long",
      state: "OPEN",
      quantity: qty,
      entry_price: entryCandle.close,
      stop_price: entryCandle.close * 0.985,
      strategy: STRATEGIES[0],
      entry_time: iso(entryCandle.time + 60),
      unrealized_pnl: (lastPrice - entryCandle.close) * qty,
    },
    overlays: {},
    latest: {},
    votes,
    rules: RULES,
  }
}
