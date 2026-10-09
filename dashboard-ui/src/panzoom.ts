// TradingView-style navigation for the chart stack: drag to scroll through history, wheel to zoom,
// double-click to jump back to the latest bars. The view is kept as "N bars ending at time T" (T = null follows
// the newest bar), so the 5-second data refreshes do not move what the user is looking at.
import { useEffect, useRef, type RefObject } from "react";

export interface View {
  count: number;
  endTime: number | null;
}

export const DEFAULT_VIEW: View = { count: 200, endTime: null };
export const MIN_BARS = 20;
// Must match the chart margins in App.tsx: the plot area is the container minus these.
export const PLOT_LEFT = 12;
export const PLOT_RIGHT = 96;

/** [start, end) indexes of the visible bars. */
export function windowOf(times: number[], v: View): [number, number] {
  const n = times.length;
  const count = Math.min(Math.max(v.count, Math.min(MIN_BARS, n)), n);
  let end = n;
  if (v.endTime != null) {
    let i = n - 1;
    while (i >= 0 && times[i] > v.endTime) i--;
    end = i + 1;
  }
  end = Math.min(Math.max(end, count), n);
  return [end - count, end];
}

const viewFor = (times: number[], count: number, end: number): View => ({
  count,
  endTime: end >= times.length ? null : times[end - 1],
});

export function usePanZoom(ref: RefObject<HTMLElement | null>, times: number[], view: View, setView: (v: View) => void) {
  const live = useRef({ times, view, setView });
  live.current = { times, view, setView };

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let drag: { x: number; end: number; count: number; active: boolean } | null = null;
    const plotWidth = () => Math.max(el.getBoundingClientRect().width - PLOT_LEFT - PLOT_RIGHT, 100);

    const pan = (startEnd: number, count: number, dxPx: number) => {
      const { times: t, setView: set } = live.current;
      const shift = Math.round((dxPx * count) / plotWidth());
      set(viewFor(t, count, Math.min(Math.max(startEnd - shift, count), t.length)));
    };

    const onWheel = (e: WheelEvent) => {
      const { times: t, view: v, setView: set } = live.current;
      if (t.length < 2) return;
      e.preventDefault();
      const [start, end] = windowOf(t, v);
      const count = end - start;
      if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        pan(end, count, -e.deltaX);
        return;
      }
      const next = Math.min(Math.max(Math.round(count * Math.exp(e.deltaY * 0.0015)), Math.min(MIN_BARS, t.length)), t.length);
      if (next === count) return;
      const r = Math.min(Math.max((e.clientX - el.getBoundingClientRect().left - PLOT_LEFT) / plotWidth(), 0), 1);
      const anchor = start + r * count; // the bar under the cursor stays under the cursor
      const nextEnd = Math.min(Math.max(Math.round(anchor - r * next + next), next), t.length);
      set(viewFor(t, next, nextEnd));
    };
    const onDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      const { times: t, view: v } = live.current;
      const [start, end] = windowOf(t, v);
      drag = { x: e.clientX, end, count: end - start, active: false };
    };
    const onMove = (e: PointerEvent) => {
      if (!drag) return;
      if (!drag.active && Math.abs(e.clientX - drag.x) < 4) return;
      if (!drag.active) {
        drag.active = true;
        el.setPointerCapture(e.pointerId);
        el.style.cursor = "grabbing";
      }
      pan(drag.end, drag.count, e.clientX - drag.x);
    };
    const onUp = () => {
      drag = null;
      el.style.cursor = "";
    };
    const onDouble = () => live.current.setView(DEFAULT_VIEW);

    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
    el.addEventListener("dblclick", onDouble);
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onUp);
      el.removeEventListener("dblclick", onDouble);
    };
  }, [ref]);
}
