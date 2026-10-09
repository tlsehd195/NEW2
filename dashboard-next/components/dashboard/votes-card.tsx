import type { Snapshot, Vote } from "@/lib/types"
import { ACTIONS, INDICATOR_NAMES, kst, num, shortStrategy } from "@/lib/format"
import { cn } from "@/lib/utils"

function ConfidenceBar({ p, enter }: { p: number; enter: number }) {
  const short = 1 - enter
  return (
    <div className="relative h-2 w-full overflow-hidden rounded-full bg-secondary">
      <div className="absolute inset-y-0 left-0 bg-down/25" style={{ width: `${short * 100}%` }} />
      <div className="absolute inset-y-0 right-0 bg-up/25" style={{ width: `${(1 - enter) * 100}%` }} />
      <div
        className={cn("absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background", p >= enter ? "bg-up" : p <= short ? "bg-down" : "bg-foreground")}
        style={{ left: `${p * 100}%` }}
      />
    </div>
  )
}

function IndicatorRow({ name, value }: { name: string; value: number }) {
  const lean = value - 0.5
  return (
    <li className="grid grid-cols-[88px_1fr_40px] items-center gap-2">
      <span className="truncate text-xs text-muted-foreground">{INDICATOR_NAMES[name] ?? name}</span>
      <div className="relative h-1.5 rounded-full bg-secondary">
        <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
        <div
          className={cn("absolute inset-y-0 rounded-full", lean >= 0 ? "bg-up" : "bg-down")}
          style={lean >= 0 ? { left: "50%", width: `${lean * 100}%` } : { right: "50%", width: `${-lean * 100}%` }}
        />
      </div>
      <span className={cn("text-right font-mono text-xs tabular-nums", lean >= 0 ? "text-up" : "text-down")}>
        {(value * 100).toFixed(0)}
      </span>
    </li>
  )
}

function StrategyVote({ vote, enter, volLo, volHi }: { vote: Vote; enter: number; volLo?: number; volHi?: number }) {
  const volOk = vote.vol_ratio == null || volLo == null || volHi == null || (vote.vol_ratio >= volLo && vote.vol_ratio <= volHi)
  return (
    <div className="flex flex-col gap-3 px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs font-medium">{shortStrategy(vote.strategy)}</span>
        <span className="rounded bg-secondary px-1.5 py-0.5 text-xs">{ACTIONS[vote.action] ?? vote.action}</span>
      </div>
      {vote.p_long != null && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between text-xs">
            <span className="text-down">숏</span>
            <span className="font-mono tabular-nums">
              롱 확률 <span className="font-semibold text-foreground">{(vote.p_long * 100).toFixed(1)}%</span>
            </span>
            <span className="text-up">롱</span>
          </div>
          <ConfidenceBar p={vote.p_long} enter={enter} />
        </div>
      )}
      <ul className="flex flex-col gap-1.5">
        {Object.entries(vote.per_indicator).map(([k, v]) => (
          <IndicatorRow key={k} name={k} value={v} />
        ))}
      </ul>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>
          찬성 <span className="text-up">{vote.agree_long ?? "-"}</span> / <span className="text-down">{vote.agree_short ?? "-"}</span>
        </span>
        <span className={volOk ? undefined : "text-down"}>변동성 {num(vote.vol_ratio)}x</span>
        <span className="truncate">사유: {vote.reason}</span>
      </div>
    </div>
  )
}

export function VotesCard({ snapshot }: { snapshot: Snapshot }) {
  const enter = snapshot.rules.enter_confidence ?? 0.6
  const lastBar = snapshot.votes[0]?.bar_time
  return (
    <section aria-label="지표 투표" className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <h2 className="text-sm font-semibold">지표 투표</h2>
        <span className="text-xs text-muted-foreground">{lastBar ? kst(lastBar) : "-"} 봉</span>
      </div>
      {snapshot.votes.length ? (
        <div className="divide-y divide-border">
          {snapshot.votes.map((v) => (
            <StrategyVote key={v.strategy} vote={v} enter={enter} volLo={snapshot.rules.vol_gate_lo} volHi={snapshot.rules.vol_gate_hi} />
          ))}
        </div>
      ) : (
        <p className="px-4 py-8 text-center text-xs text-muted-foreground">아직 판단 기록이 없어요</p>
      )}
      <div className="border-t border-border px-4 py-2 text-xs leading-relaxed text-muted-foreground">
        진입 {Math.round(enter * 100)}% · 청산 {Math.round((snapshot.rules.exit_confidence ?? 0.5) * 100)}% · 최소 찬성{" "}
        {Math.round((snapshot.rules.min_agree ?? 0.6) * 100)}% · 변동성 {snapshot.rules.vol_gate_lo ?? "-"}–{snapshot.rules.vol_gate_hi ?? "-"}x
      </div>
    </section>
  )
}
