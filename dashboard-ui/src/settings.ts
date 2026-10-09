// Display settings the user can change in the page. They only change how the chart is drawn; the strategy's own
// indicator periods and all trading settings live on the server and are not touched. Saved in this browser.
import { useCallback, useEffect, useState } from "react";

export interface LineSetting { on: boolean; period: number; color: string; width: number }
export interface Settings {
  theme: "auto" | "dark" | "light";
  refreshSec: number;
  historyBars: number;
  startBars: number;
  ema: LineSetting;
  bb: LineSetting & { k: number; fill: boolean };
  don: LineSetting;
  rsi: LineSetting;
  roc: LineSetting;
  obv: LineSetting;
  fills: boolean;
  fillLabels: boolean;
  levels: boolean;
}

export const DEFAULTS: Settings = {
  theme: "auto", refreshSec: 5, historyBars: 1000, startBars: 200,
  ema: { on: true, period: 20, color: "#e0a030", width: 1.4 },
  bb: { on: true, period: 20, k: 2, fill: true, color: "#9b7fe8", width: 1.4 },
  don: { on: true, period: 20, color: "#3aa0c0", width: 1.4 },
  rsi: { on: true, period: 14, color: "#e0a030", width: 1.6 },
  roc: { on: true, period: 14, color: "#d06090", width: 1.6 },
  obv: { on: true, period: 0, color: "#60b060", width: 1.6 },
  fills: true, fillLabels: true, levels: true,
};

const KEY = "paper-dashboard-settings-v1";

/** Saved values laid over the defaults; anything missing or of the wrong type falls back to the default. */
function merge<T>(def: T, saved: unknown): T {
  if (def !== null && typeof def === "object") {
    const s = saved !== null && typeof saved === "object" ? (saved as Record<string, unknown>) : {};
    return Object.fromEntries(Object.entries(def).map(([k, v]) => [k, merge(v, s[k])])) as T;
  }
  return typeof saved === typeof def && (typeof def !== "number" || Number.isFinite(saved)) ? (saved as T) : def;
}

function load(): Settings {
  try {
    return merge(DEFAULTS, JSON.parse(localStorage.getItem(KEY) ?? "null"));
  } catch {
    return DEFAULTS; // storage blocked or the saved text is damaged: start from the defaults
  }
}

export const clampInt = (v: number, lo: number, hi: number) => Math.min(Math.max(Math.round(v), lo), hi);

export function useSettings(): [Settings, (change: (s: Settings) => Settings) => void, () => void] {
  const [settings, setSettings] = useState<Settings>(load);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(settings));
    } catch {
      /* private window or blocked storage: the settings just last until the page closes */
    }
  }, [settings]);
  useEffect(() => {
    if (settings.theme === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = settings.theme;
  }, [settings.theme]);
  const update = useCallback((change: (s: Settings) => Settings) => setSettings(change), []);
  const reset = useCallback(() => setSettings(DEFAULTS), []);
  return [settings, update, reset];
}
