export const num = (n: number | null | undefined, digits = 2) =>
  n == null ? "-" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: digits })

export const usd = (n: number | null | undefined) =>
  n == null ? "-" : Number(n).toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 })

export const signedUsd = (n: number | null | undefined) => (n == null ? "-" : (n >= 0 ? "+" : "-") + usd(Math.abs(n)))

export const pct = (x: number | null | undefined, digits = 1) => (x == null ? "-" : (x * 100).toFixed(digits) + "%")

export const signedPct = (x: number | null | undefined, digits = 2) =>
  x == null ? "-" : (x >= 0 ? "+" : "") + (x * 100).toFixed(digits) + "%"

export const tone = (n: number | null | undefined) =>
  n == null || n === 0 ? "text-muted-foreground" : n > 0 ? "text-up" : "text-down"

export const kst = (iso: string | number | null | undefined) => {
  if (iso == null) return "-"
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso)
  return d.toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })
}

// "daytrade_indicator_vote_h16_c0.6_v1" -> "4시간 예측(h16) c0.6": h는 앞으로 볼 15분봉 개수.
export const shortStrategy = (id: string) => {
  const m = /^daytrade_indicator_vote_(side_)?h(\d+)_(c[\d.]+)/.exec(id)
  if (!m) return id.replace("daytrade_indicator_vote_", "")
  const hours = (Number(m[2]) * 15) / 60
  return `${m[1] ? "방향별 " : ""}${Number.isInteger(hours) ? hours : hours.toFixed(1)}시간 예측(h${m[2]}) ${m[3]}`
}

export const INDICATOR_NAMES: Record<string, string> = {
  ema_trend: "EMA 추세",
  donchian_pos: "돈치안 위치",
  roc: "ROC 모멘텀",
  rsi: "RSI",
  bollinger_b: "볼린저 %B",
  obv_slope: "OBV 기울기",
}

export const ACTIONS: Record<string, string> = {
  enter_long: "롱 진입",
  enter_short: "숏 진입",
  exit: "청산",
  hold: "대기",
}

const EXIT_REASONS: Record<string, string> = {
  stop_loss: "손절",
  take_profit: "익절",
  trailing_stop: "추적 손절",
  time_stop: "시간 손절",
  protective_stop_failed: "보호 손절 실패",
  retry_exit: "청산 재시도",
  unknown: "알 수 없음",
}

export const exitReason = (r: string | null) =>
  !r ? "-" : r.startsWith("signal_exit:") ? "신호 청산" : (EXIT_REASONS[r] ?? r)

export const FILL_KINDS: Record<string, string> = {
  long_entry: "롱 진입",
  long_exit: "롱 청산",
  short_entry: "숏 진입",
  short_exit: "숏 청산",
}
