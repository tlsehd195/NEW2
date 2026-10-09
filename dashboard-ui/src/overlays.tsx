// Chart layers the dashboard adds on top of bklit's charts. They read scales from bklit's chart context
// (the "custom indicator" pattern), so they line up with the candles and move with hover.
import { useChart } from "@/components/charts/chart-context";
import type { FillKind, Snapshot } from "./types";

const fmt = (n: number) => n.toLocaleString("ko-KR", { maximumFractionDigits: 2 });

/** Polyline for a numeric field of the chart data; a null value breaks the line. */
export function SeriesPath({ field, stroke }: { field: string; stroke: string }) {
  const { data, xScale, yScale, xAccessor } = useChart();
  let d = "";
  let pen = false;
  for (const row of data) {
    const v = row[field];
    if (typeof v !== "number") {
      pen = false;
      continue;
    }
    const x = xScale(xAccessor(row));
    const y = yScale(v);
    d += `${pen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    pen = true;
  }
  return <path d={d} fill="none" stroke={stroke} strokeWidth={1.4} strokeLinejoin="round" pointerEvents="none" />;
}

/** Translucent fill between two data fields (the Bollinger band), like TradingView's band shading. */
export function BandFill({ upper, lower, fill, fillOpacity }: { upper: string; lower: string; fill: string; fillOpacity: number }) {
  const { data, xScale, yScale, xAccessor } = useChart();
  const top: string[] = [];
  const bottom: string[] = [];
  for (const row of data) {
    const u = row[upper];
    const l = row[lower];
    if (typeof u !== "number" || typeof l !== "number") continue;
    const x = xScale(xAccessor(row)).toFixed(1);
    top.push(`${x},${yScale(u).toFixed(1)}`);
    bottom.push(`${x},${yScale(l).toFixed(1)}`);
  }
  if (top.length < 2) return null;
  return <polygon points={[...top, ...bottom.reverse()].join(" ")} fill={fill} fillOpacity={fillOpacity} pointerEvents="none" />;
}

export interface Level {
  price: number;
  label: string;
  color: string;
  dash?: string;
}

/** Horizontal price lines with a value tag on the right edge. A level outside the visible price range is not drawn. */
export function PriceLevels({ levels }: { levels: Level[] }) {
  const { yScale, innerWidth, innerHeight } = useChart();
  return (
    <g pointerEvents="none">
      {levels.map((l) => {
        const y = yScale(l.price);
        if (y < 0 || y > innerHeight) return null;
        const text = `${l.label} ${fmt(l.price)}`;
        const w = text.length * 6.4 + 12;
        return (
          <g key={l.label}>
            <line x1={0} x2={innerWidth} y1={y} y2={y} stroke={l.color} strokeDasharray={l.dash} strokeWidth={1} />
            <rect x={innerWidth + 4} y={y - 9} width={w} height={18} fill={l.color} />
            <text x={innerWidth + 10} y={y + 4} fontSize={11} fill="#fff" fontFamily="system-ui, sans-serif">
              {text}
            </text>
          </g>
        );
      })}
    </g>
  );
}

// How a fill looks: entries are solid triangles pointing the way the trade bets (long up / short down),
// exits are hollow diamonds; green = long, red = short. Long entry and short exit sit under the candle, the rest above.
export const FILL_STYLES: Record<FillKind, { label: string; name: string; color: string; entry: boolean; below: boolean }> = {
  long_entry: { label: "L진입", name: "롱 진입", color: "var(--up)", entry: true, below: true },
  long_exit: { label: "L청산", name: "롱 청산", color: "var(--up)", entry: false, below: false },
  short_entry: { label: "S진입", name: "숏 진입", color: "var(--down)", entry: true, below: false },
  short_exit: { label: "S청산", name: "숏 청산", color: "var(--down)", entry: false, below: true },
};

/** Entry / exit marks for the candle each fill landed in. Text labels only when the view is zoomed in enough to read. */
export function FillMarks({ fills }: { fills: Snapshot["fills"] }) {
  const { data, xScale, yScale, xAccessor } = useChart();
  const labels = data.length <= 140;
  return (
    <g pointerEvents="none">
      {fills.map((f, k) => {
        const row = data.find((r) => {
          const t = xAccessor(r).getTime() / 1000;
          return t <= f.time && f.time < t + 900;
        });
        if (!row) return null;
        const x = xScale(xAccessor(row));
        const st = f.kind ? FILL_STYLES[f.kind] : null;
        const below = st ? st.below : f.side.toLowerCase() === "buy";
        const color = st ? st.color : "var(--mute)";
        const y = below ? yScale(row.low as number) + 14 : yScale(row.high as number) - 14;
        const s = 6;
        let shape;
        if (st && !st.entry) {
          shape = <polygon points={`${x},${y - s - 1} ${x + s + 1},${y} ${x},${y + s + 1} ${x - s - 1},${y}`} fill="var(--panel)" stroke={color} strokeWidth={2} />;
        } else {
          const up = st ? st.label.startsWith("L") : below;
          const pts = up ? `${x},${y - s} ${x - s},${y + s} ${x + s},${y + s}` : `${x},${y + s} ${x - s},${y - s} ${x + s},${y - s}`;
          shape = <polygon points={pts} fill={color} />;
        }
        return (
          <g key={k}>
            {shape}
            {st && labels && (
              <text x={x} y={below ? y + s + 13 : y - s - 5} fontSize={11} fontWeight={600} textAnchor="middle" fill={color} fontFamily="system-ui, sans-serif">
                {st.label}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}

/** Small key for the marks above, shown over the chart. */
export function FillLegend() {
  const kinds = Object.keys(FILL_STYLES) as FillKind[];
  return (
    <>
      {kinds.map((k) => {
        const st = FILL_STYLES[k];
        const long = k.startsWith("long");
        return (
          <span key={k} style={{ color: st.color, display: "inline-flex", alignItems: "center", gap: 4 }}>
            <svg width="14" height="14" viewBox="-7 -7 14 14" aria-hidden="true">
              {st.entry ? (
                <polygon points={long ? "0,-6 -6,6 6,6" : "0,6 -6,-6 6,-6"} fill="currentColor" />
              ) : (
                <polygon points="0,-6 6,0 0,6 -6,0" fill="var(--panel)" stroke="currentColor" strokeWidth="2" />
              )}
            </svg>
            {st.label} <span className="muted">({st.name})</span>
          </span>
        );
      })}
    </>
  );
}

/** Dashed horizontal guides at fixed values (RSI 30/50/70, ROC 0). */
export function Guides({ values }: { values: number[] }) {
  const { yScale, innerWidth } = useChart();
  return (
    <g pointerEvents="none">
      {values.map((v) => (
        <g key={v}>
          <line x1={0} x2={innerWidth} y1={yScale(v)} y2={yScale(v)} stroke="var(--chart-segment-line)" strokeDasharray="2,4" />
          <text x={innerWidth + 6} y={yScale(v) + 4} fontSize={10} fill="var(--mute)" fontFamily="ui-monospace, Menlo, monospace">
            {v}
          </text>
        </g>
      ))}
    </g>
  );
}
