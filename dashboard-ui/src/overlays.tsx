// Chart layers the dashboard adds on top of bklit's charts. They read scales from bklit's chart context
// (the "custom indicator" pattern), so they line up with the candles and move with hover.
import { useChart } from "@/components/charts/chart-context";

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

/** Buy / sell triangles under / over the candle each fill landed in. */
export function FillMarks({ fills }: { fills: { time: number; side: string }[] }) {
  const { data, xScale, yScale, xAccessor } = useChart();
  return (
    <g pointerEvents="none">
      {fills.map((f, k) => {
        const row = data.find((r) => {
          const t = xAccessor(r).getTime() / 1000;
          return t <= f.time && f.time < t + 900;
        });
        if (!row) return null;
        const buy = f.side.toLowerCase() === "buy";
        const x = xScale(xAccessor(row));
        const y = buy ? yScale(row.low as number) + 12 : yScale(row.high as number) - 12;
        const s = 6;
        const pts = buy ? `${x},${y - s} ${x - s},${y + s} ${x + s},${y + s}` : `${x},${y + s} ${x - s},${y - s} ${x + s},${y - s}`;
        return <polygon key={k} points={pts} fill={buy ? "var(--up)" : "var(--down)"} />;
      })}
    </g>
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
