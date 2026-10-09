// The chart's indicator lines, computed in the browser so the user can change the periods on screen.
// These mirror src/cointrader/features/indicators.py (same formulas, same 4x-period window for EMA and RSI),
// so at the default periods they match what the strategy sees. A value is null until enough bars exist.
// This is display only: the strategy's own calculation is untouched.
export type Series = (number | null)[];

const mean = (v: number[], from: number, to: number) => {
  let s = 0;
  for (let i = from; i < to; i++) s += v[i];
  return s / (to - from);
};

export function emaSeries(c: number[], period: number): Series {
  const lookback = 4 * period;
  const alpha = 2 / (period + 1);
  return c.map((_, i) => {
    if (i + 1 < lookback) return null;
    const start = i + 1 - lookback;
    let v = mean(c, start, start + period);
    for (let j = start + period; j <= i; j++) v = alpha * c[j] + (1 - alpha) * v;
    return v;
  });
}

export function rsiSeries(c: number[], period: number): Series {
  const lookback = 4 * period;
  return c.map((_, i) => {
    if (i < lookback) return null;
    const start = i - lookback;
    let gain = 0;
    let loss = 0;
    for (let j = start + 1; j <= start + period; j++) {
      const ch = c[j] - c[j - 1];
      gain += Math.max(ch, 0);
      loss += Math.max(-ch, 0);
    }
    gain /= period;
    loss /= period;
    for (let j = start + period + 1; j <= i; j++) {
      const ch = c[j] - c[j - 1];
      gain = (gain * (period - 1) + Math.max(ch, 0)) / period;
      loss = (loss * (period - 1) + Math.max(-ch, 0)) / period;
    }
    if (gain === 0 && loss === 0) return 50;
    if (loss === 0) return 100;
    return 100 - 100 / (1 + gain / loss);
  });
}

export function rocSeries(c: number[], period: number): Series {
  return c.map((v, i) => (i < period || c[i - period] === 0 ? null : v / c[i - period] - 1));
}

export function bollingerSeries(c: number[], period: number, k: number): { upper: Series; lower: Series } {
  const upper: Series = [];
  const lower: Series = [];
  c.forEach((_, i) => {
    if (i + 1 < period) {
      upper.push(null);
      lower.push(null);
      return;
    }
    const mid = mean(c, i + 1 - period, i + 1);
    let ss = 0;
    for (let j = i + 1 - period; j <= i; j++) ss += (c[j] - mid) ** 2;
    const sd = Math.sqrt(ss / period);
    upper.push(mid + k * sd);
    lower.push(mid - k * sd);
  });
  return { upper, lower };
}

/** High / low of the `period` bars before the current one, so a close above the high is a breakout. */
export function donchianSeries(high: number[], low: number[], period: number): { high: Series; low: Series } {
  const hi: Series = [];
  const lo: Series = [];
  high.forEach((_, i) => {
    if (i < period) {
      hi.push(null);
      lo.push(null);
      return;
    }
    hi.push(Math.max(...high.slice(i - period, i)));
    lo.push(Math.min(...low.slice(i - period, i)));
  });
  return { high: hi, low: lo };
}

/** On-balance volume: running total of volume, signed by the close-to-close direction (the level is arbitrary). */
export function obvSeries(c: number[], volume: number[]): Series {
  let acc = 0;
  return c.map((v, i) => {
    if (i) acc += v > c[i - 1] ? volume[i] : v < c[i - 1] ? -volume[i] : 0;
    return acc;
  });
}
