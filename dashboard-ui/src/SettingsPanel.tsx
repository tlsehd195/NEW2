import { useEffect, type ReactNode } from "react";
import { DEFAULTS, type LineSetting, type Settings } from "./settings";

type Update = (change: (s: Settings) => Settings) => void;
type LineKey = "ema" | "bb" | "don" | "rsi" | "roc" | "obv";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="set-section">
      <h3 className="mono">{title}</h3>
      {children}
    </section>
  );
}

function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="set-row">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

function Num({ label, value, min, max, step, onChange }: {
  label: string; value: number; min: number; max: number; step?: number; onChange: (v: number) => void;
}) {
  return (
    <label className="set-row">
      <span>{label}</span>
      <input type="number" value={value} min={min} max={max} step={step ?? 1}
        onChange={(e) => { const v = e.target.valueAsNumber; if (Number.isFinite(v)) onChange(v); }} />
    </label>
  );
}

export function SettingsPanel({ s, update, reset, close }: { s: Settings; update: Update; reset: () => void; close: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => update((p) => ({ ...p, [key]: value }));
  const line = (key: LineKey, patch: Partial<LineSetting> & { k?: number; fill?: boolean }) =>
    update((p) => ({ ...p, [key]: { ...p[key], ...patch } }));

  // one block per indicator: on/off, period (not OBV), colour and thickness
  const block = (key: LineKey, name: string, extra?: ReactNode) => {
    const v = s[key];
    return (
      <div className="set-block">
        <Check label={name} checked={v.on} onChange={(on) => line(key, { on })} />
        {v.on && (
          <div className="set-sub">
            {key !== "obv" && <Num label="기간" value={v.period} min={2} max={200} onChange={(period) => line(key, { period })} />}
            {extra}
            <label className="set-row"><span>색</span><input type="color" value={v.color} onChange={(e) => line(key, { color: e.target.value })} /></label>
            <Num label="굵기" value={v.width} min={0.5} max={4} step={0.1} onChange={(width) => line(key, { width })} />
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      <div className="set-backdrop" onClick={close} />
      <aside className="set-drawer" role="dialog" aria-label="화면 설정">
        <header>
          <strong>화면 설정</strong>
          <button onClick={close} aria-label="닫기">✕</button>
        </header>
        <p className="set-note">
          이 설정은 <b>화면에 그리는 방식만</b> 바꿔요. 전략이 실제로 쓰는 지표 기간과 매매 설정은 바뀌지 않아요. 이 브라우저에 저장돼요.
        </p>

        <Section title="화면">
          <label className="set-row">
            <span>테마</span>
            <select value={s.theme} onChange={(e) => set("theme", e.target.value as Settings["theme"])}>
              <option value="auto">자동 (컴퓨터 설정)</option>
              <option value="dark">어둡게</option>
              <option value="light">밝게</option>
            </select>
          </label>
          <label className="set-row">
            <span>새로고침 간격</span>
            <select value={s.refreshSec} onChange={(e) => set("refreshSec", Number(e.target.value))}>
              {[2, 5, 10, 30, 60].map((n) => <option key={n} value={n}>{n}초</option>)}
            </select>
          </label>
          <Num label="처음 보일 봉 수" value={s.startBars} min={20} max={1000} onChange={(v) => set("startBars", v)} />
          <label className="set-row">
            <span>불러올 과거 봉 수</span>
            <select value={s.historyBars} onChange={(e) => set("historyBars", Number(e.target.value))}>
              {[500, 1000, 2000].map((n) => <option key={n} value={n}>{n}봉 (약 {Math.round(n / 96)}일)</option>)}
            </select>
          </label>
        </Section>

        <Section title="가격 차트 위 지표">
          {block("ema", "EMA")}
          {block("bb", "볼린저 밴드", (
            <>
              <Num label="폭(표준편차 배수)" value={s.bb.k} min={0.5} max={5} step={0.5} onChange={(k) => line("bb", { k })} />
              <Check label="밴드 사이 색칠" checked={s.bb.fill} onChange={(fill) => line("bb", { fill })} />
            </>
          ))}
          {block("don", "돈치안 채널")}
        </Section>

        <Section title="아래 패널">
          {block("rsi", "RSI")}
          {block("roc", "ROC")}
          {block("obv", "OBV")}
        </Section>

        <Section title="표시">
          <Check label="체결 마커 (L진입·L청산·S진입·S청산)" checked={s.fills} onChange={(v) => set("fills", v)} />
          {s.fills && <Check label="마커 글자" checked={s.fillLabels} onChange={(v) => set("fillLabels", v)} />}
          <Check label="진입·손절·현재가 선" checked={s.levels} onChange={(v) => set("levels", v)} />
        </Section>

        <footer>
          <button onClick={reset} disabled={JSON.stringify(s) === JSON.stringify(DEFAULTS)}>기본값으로</button>
          <button className="primary" onClick={close}>닫기</button>
        </footer>
      </aside>
    </>
  );
}
