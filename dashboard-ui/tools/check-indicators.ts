// Manual check that the browser-side indicators (src/indicators.ts) match the server's (features/indicators.py).
//   python3 scripts/run_dashboard.py &      # then, from dashboard-ui/:
//   npm run check-indicators                # optional: PORT=8765 SYMBOL=BTCUSDT
import { bollingerSeries, donchianSeries, emaSeries, obvSeries, rocSeries, rsiSeries, type Series } from "../src/indicators.ts";

const port = process.env.PORT ?? "8765";
const symbol = process.env.SYMBOL ?? "BTCUSDT";
const snap = await (await fetch(`http://127.0.0.1:${port}/api/snapshot?bars=600&symbol=${symbol}`)).json();
const cs = snap.candles as { high: number; low: number; close: number; volume: number }[];
const close = cs.map((c) => c.close);
const bb = bollingerSeries(close, 20, 2);
const don = donchianSeries(cs.map((c) => c.high), cs.map((c) => c.low), 20);
const mine: Record<string, Series> = {
  ema20: emaSeries(close, 20), bb_upper: bb.upper, bb_lower: bb.lower, don_high: don.high, don_low: don.low,
  rsi14: rsiSeries(close, 14), roc14: rocSeries(close, 14), obv: obvSeries(close, cs.map((c) => c.volume)),
};
let bad = 0;
for (const [key, theirs] of Object.entries(snap.overlays as Record<string, Series>)) {
  let compared = 0;
  let worst = 0;
  // OBV's level is arbitrary (it starts at 0 on the first bar each side sees), so compare its changes from the first bar
  const shift = key === "obv" ? (theirs[0] ?? 0) - (mine[key]?.[0] ?? 0) : 0;
  theirs.forEach((t, i) => {
    const m = mine[key]?.[i] == null ? null : (mine[key][i] as number) + shift;
    // the server has 60 extra bars of warm-up before the first one drawn, so it can have values where the browser has none
    if (t == null || m == null) return;
    compared++;
    worst = Math.max(worst, Math.abs(m - t) / (Math.abs(t) || 1));
  });
  const ok = compared > 0 && worst < 1e-9;
  if (!ok) bad++;
  console.log(`${ok ? "ok  " : "FAIL"} ${key}: ${compared} bars compared, worst relative difference ${worst.toExponential(2)}`);
}
process.exit(bad ? 1 : 0);
