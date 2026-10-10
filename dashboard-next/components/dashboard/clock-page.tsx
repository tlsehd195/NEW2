"use client"

import { FlipDiskMatrix } from "@/components/ui/flip-disk-matrix"

/** Main page: the flip-disk clock in the middle of the screen. Korea time; no trading data is read here. */
export function ClockPage() {
  return (
    <section aria-label="시계" className="dark relative flex min-h-[70dvh] flex-col items-center justify-center gap-8 overflow-hidden rounded-lg border border-border bg-[#050505] p-4 md:p-8">
      <div className="pointer-events-none absolute left-1/2 top-1/2 size-[800px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#E5FD52]/5 blur-[150px]" aria-hidden />
      <div className="relative z-10 flex w-full max-w-5xl flex-col items-center gap-6 md:gap-10">
        <header className="flex flex-col items-center gap-2 text-center">
          <p className="font-mono text-[10px] uppercase tracking-[0.4em] text-[#E5FD52]/70 md:text-xs">한국시간 (KST)</p>
          <h1 className="text-3xl font-light tracking-tight text-neutral-200 md:text-4xl">NEW2 트레이더</h1>
        </header>
        <FlipDiskMatrix />
      </div>
    </section>
  )
}
