import type { Snapshot } from "@/lib/types"
import { kst } from "@/lib/format"
import { cn } from "@/lib/utils"

function Badge({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span className={cn("rounded px-2 py-0.5 text-xs font-semibold", ok ? "bg-up/15 text-up" : "bg-down/15 text-down")}>{children}</span>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1.5">
      <dt className="shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd className="text-right text-sm">{children}</dd>
    </div>
  )
}

/** Read-only view: engaging or releasing the kill switch is never possible from here. */
export function SafetyCard({ snapshot }: { snapshot: Snapshot }) {
  const ks = snapshot.kill_switch
  const rec = snapshot.reconciliation
  return (
    <section aria-label="안전 상태" className="rounded-lg border border-border bg-card">
      <div className="border-b border-border px-4 py-2.5">
        <h2 className="text-sm font-semibold">안전 상태</h2>
      </div>
      <dl className="divide-y divide-border px-4 py-1.5">
        <Row label="킬 스위치">
          {ks ? (
            <div className="flex flex-col items-end gap-1">
              <Badge ok={!ks.engaged}>{ks.engaged ? "작동 중 (거래 중지)" : "해제됨"}</Badge>
              <span className="text-xs text-muted-foreground">
                {ks.reason ?? "-"}
                {ks.triggered_by ? ` · ${ks.triggered_by}` : ""}
                {ks.at ? ` · ${kst(ks.at)}` : ""}
              </span>
            </div>
          ) : (
            <span className="text-xs text-muted-foreground">알 수 없음</span>
          )}
        </Row>
        <Row label="재조정(내부 vs 거래소)">
          {rec ? (
            <div className="flex flex-col items-end gap-1">
              <Badge ok={rec.ok}>{rec.ok ? "일치" : "불일치 (신규 진입 차단)"}</Badge>
              <span className="text-xs text-muted-foreground">
                {rec.ok ? "" : `${rec.detail ?? ""} ${rec.mismatches.join(", ")} · `}
                {kst(rec.at)}
              </span>
            </div>
          ) : (
            <span className="text-xs text-muted-foreground">최근 기록 없음</span>
          )}
        </Row>
      </dl>
    </section>
  )
}
