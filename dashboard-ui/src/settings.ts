// Display settings the user can change in the page. They only change how the chart is drawn; the strategy's own
// indicator periods and all trading settings live on the server and are not touched. Saved in this browser.
import { useCallback, useEffect, useState } from "react";

export interface LineSetting { on: boolean; period: number; color: string; width: number }
export type PanelId = "chart" | "price" | "position" | "account" | "votes" | "ivals" | "decisions" | "trades";
export type PanelSize = "third" | "half" | "twothirds" | "full";
export interface PanelSetting { id: PanelId; show: boolean; size: PanelSize }
export const PANEL_NAMES: Record<PanelId, string> = {
  chart: "차트", price: "현재가", position: "포지션", account: "계좌", votes: "지표별 판단",
  ivals: "지표 현재값", decisions: "최근 판단", trades: "청산된 거래",
};
export const PANEL_SIZES: [PanelSize, string][] = [["third", "1/3"], ["half", "1/2"], ["twothirds", "2/3"], ["full", "전체"]];
export const TRADE_COLUMNS = { dir: "방향", times: "진입·청산 시각(한국시간)", prices: "진입 → 청산가", priceRet: "가격 수익률 %", equityRet: "계좌 수익률 %", pnl: "손익 USDT", reason: "청산 사유" };

export interface Settings {
  theme: "auto" | "dark" | "light";
  fontScale: number;
  chartHeight: "low" | "normal" | "high";
  layout: PanelSetting[];
  tradeColumns: Record<keyof typeof TRADE_COLUMNS, boolean>;
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
  theme: "auto", fontScale: 1, chartHeight: "normal",
  layout: [
    { id: "chart", show: true, size: "full" }, { id: "price", show: true, size: "third" },
    { id: "position", show: true, size: "third" }, { id: "account", show: true, size: "third" },
    { id: "votes", show: true, size: "full" }, { id: "ivals", show: true, size: "third" },
    { id: "decisions", show: true, size: "third" }, { id: "trades", show: true, size: "full" },
  ],
  tradeColumns: { dir: true, times: true, prices: true, priceRet: true, equityRet: true, pnl: true, reason: true },
  refreshSec: 5, historyBars: 1000, startBars: 200,
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
  if (Array.isArray(def)) return def as T; // lists are checked by sanitizeLayout
  if (def !== null && typeof def === "object") {
    const s = saved !== null && typeof saved === "object" ? (saved as Record<string, unknown>) : {};
    return Object.fromEntries(Object.entries(def).map(([k, v]) => [k, merge(v, s[k])])) as T;
  }
  return typeof saved === typeof def && (typeof def !== "number" || Number.isFinite(saved)) ? (saved as T) : def;
}

/** The saved panel list, kept only where it makes sense: known panels once each, valid sizes; panels it lacks are added at the end. */
function sanitizeLayout(saved: unknown): PanelSetting[] {
  const out: PanelSetting[] = [];
  const sizes = PANEL_SIZES.map(([k]) => k);
  for (const item of Array.isArray(saved) ? saved : []) {
    const def = DEFAULTS.layout.find((p) => p.id === item?.id);
    if (!def || out.some((p) => p.id === def.id)) continue;
    out.push({ id: def.id, show: typeof item.show === "boolean" ? item.show : def.show, size: sizes.includes(item.size) ? item.size : def.size });
  }
  return [...out, ...DEFAULTS.layout.filter((p) => !out.some((o) => o.id === p.id))];
}

function load(): Settings {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "null");
    return { ...merge(DEFAULTS, saved), layout: sanitizeLayout(saved?.layout) };
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
  useEffect(() => {
    document.documentElement.style.fontSize = `${16 * Math.min(Math.max(settings.fontScale, 0.8), 1.5)}px`;
  }, [settings.fontScale]);
  const update = useCallback((change: (s: Settings) => Settings) => setSettings(change), []);
  const reset = useCallback(() => setSettings(DEFAULTS), []);
  return [settings, update, reset];
}
