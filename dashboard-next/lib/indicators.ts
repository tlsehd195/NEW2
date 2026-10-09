type Series = (number | null)[]

export function ema(values: number[], period: number): Series {
  const out: Series = new Array(values.length).fill(null)
  if (values.length < period) return out
  const k = 2 / (period + 1)
  let prev = values.slice(0, period).reduce((a, b) => a + b, 0) / period
  out[period - 1] = prev
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k)
    out[i] = prev
  }
  return out
}

export function bollinger(values: number[], period: number, mult: number) {
  const upper: Series = new Array(values.length).fill(null)
  const lower: Series = new Array(values.length).fill(null)
  for (let i = period - 1; i < values.length; i++) {
    const window = values.slice(i - period + 1, i + 1)
    const mean = window.reduce((a, b) => a + b, 0) / period
    const sd = Math.sqrt(window.reduce((a, b) => a + (b - mean) ** 2, 0) / period)
    upper[i] = mean + mult * sd
    lower[i] = mean - mult * sd
  }
  return { upper, lower }
}

export function donchian(highs: number[], lows: number[], period: number) {
  const upper: Series = new Array(highs.length).fill(null)
  const lower: Series = new Array(highs.length).fill(null)
  for (let i = period - 1; i < highs.length; i++) {
    upper[i] = Math.max(...highs.slice(i - period + 1, i + 1))
    lower[i] = Math.min(...lows.slice(i - period + 1, i + 1))
  }
  return { upper, lower }
}

export function rsi(values: number[], period = 14): Series {
  const out: Series = new Array(values.length).fill(null)
  if (values.length <= period) return out
  let gain = 0
  let loss = 0
  for (let i = 1; i <= period; i++) {
    const d = values[i] - values[i - 1]
    if (d >= 0) gain += d
    else loss -= d
  }
  gain /= period
  loss /= period
  out[period] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss)
  for (let i = period + 1; i < values.length; i++) {
    const d = values[i] - values[i - 1]
    gain = (gain * (period - 1) + Math.max(d, 0)) / period
    loss = (loss * (period - 1) + Math.max(-d, 0)) / period
    out[i] = loss === 0 ? 100 : 100 - 100 / (1 + gain / loss)
  }
  return out
}
