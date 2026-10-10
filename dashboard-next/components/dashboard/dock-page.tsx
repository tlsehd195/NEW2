"use client"

import type { LucideIcon } from "lucide-react"
import { Calendar, LayoutDashboard } from "lucide-react"

type Target = 2 | 3

/** Main page: a dock whose icons jump to the other pages. Reads no trading data. */
export function DockPage({ onGo }: { onGo: (page: Target) => void }) {
  return (
    <section
      aria-label="메인"
      className="dark relative flex min-h-[70dvh] flex-col items-center justify-center overflow-hidden rounded-lg border border-border px-4 text-white"
      style={{ background: "radial-gradient(ellipse 80% 60% at 50% 0%, rgba(120,180,255,0.25), transparent 70%), #000" }}
    >
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.05] [background-image:radial-gradient(rgba(255,255,255,0.2)_1px,transparent_1px)] [background-size:12px_12px]"
        aria-hidden
      />
      <div className="relative z-10 flex flex-col items-center gap-3 text-center">
        <h1 className="text-balance font-semibold tracking-tight text-white/90 [font-size:clamp(20px,4.5vw,38px)]">NEW2 모의투자</h1>
        <p className="text-xs text-white/70 sm:text-sm">조회 전용 · 실제 돈 아님</p>
        <div className="mt-8 flex items-center gap-3 rounded-[28px] bg-neutral-900/80 px-3 py-2 shadow-2xl ring-1 ring-white/10 backdrop-blur-lg sm:gap-5 sm:rounded-[48px] sm:px-6 sm:py-3">
          <DockIcon icon={Calendar} label="캘린더" onClick={() => onGo(2)} />
          <DockIcon icon={LayoutDashboard} label="대시보드" onClick={() => onGo(3)} />
        </div>
      </div>
    </section>
  )
}

function DockIcon({ icon: Icon, label, onClick }: { icon: LucideIcon; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="group relative grid size-12 place-items-center rounded-xl bg-gradient-to-b from-neutral-800/60 to-neutral-900/70 shadow-lg ring-1 ring-white/10 backdrop-blur-xl transition-transform duration-200 hover:-translate-y-1 hover:scale-105 sm:size-14"
    >
      <Icon className="size-5 text-white/85 transition-transform duration-200 group-hover:scale-110" strokeWidth={2.1} aria-hidden />
      <span className="pointer-events-none absolute -bottom-6 text-[10px] tracking-wide text-white/70 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
        {label}
      </span>
    </button>
  )
}
