import type { Snapshot } from "@/lib/types"
import { kst, num, shortStrategy, signedPct, signedUsd, tone } from "@/lib/format"
import { cn } from "@/lib/utils"

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-mono text-sm tabular-nums">{children}</dd>
    </div>
  )
}

export function PositionCard({ snapshot }: { snapshot: Snapshot }) {
  const pos = snapshot.position
  const price = snapshot.last_price ?? snapshot.candles.at(-1)?.close ?? null
  const sign = pos?.direction === "short" ? -1 : 1
  const move = pos?.entry_price && price != null ? ((price - pos.entry_price) / pos.entry_price) * sign : null
  const unrealized = pos?.unrealized_pnl ?? (pos?.entry_price && price != null ? (price - pos.entry_price) * pos.quantity * sign : null)
  const stopDistance = pos?.stop_price && price ? Math.abs(price - pos.stop_price) / price : null

  return (
    <section aria-label="현재 포지션" className="rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <h2 className="text-sm font-semibold">현재 포지션</h2>
        {pos && (
          <span
            className={cn(
              "rounded px-2 py-0.5 text-xs font-semibold",
              pos.direction === "long" ? "bg-up/15 text-up" : "bg-down/15 text-down",
            )}
          >
            {pos.direction === "long" ? "LONG 롱" : "SHORT 숏"}
          </span>
        )}
      </div>
      {pos ? (
        <div className="px-4 py-3">
          <div className="mb-3 flex items-end justify-between">
            <div>
              <p className="text-xs text-muted-foreground">미실현 손익</p>
              <p className={cn("font-mono text-2xl font-semibold tabular-nums", tone(unrealized))}>{signedUsd(unrealized)}</p>
            </div>
            <p className={cn("font-mono text-sm tabular-nums", tone(move))}>{signedPct(move)}</p>
          </div>
          <dl className="divide-y divide-border">
            <Row label="진입가">{num(pos.entry_price)}</Row>
            <Row label="현재가">{num(price)}</Row>
            <Row label="손절가">
              <span className="text-down">{num(pos.stop_price)}</span>
              {stopDistance != null && <span className="ml-1.5 text-xs text-muted-foreground">({(stopDistance * 100).toFixed(2)}%)</span>}
            </Row>
            <Row label="수량">{num(pos.quantity, 4)}</Row>
            <Row label="포지션 금액(명목)">{pos.notional != null ? `${num(pos.notional)} USDT` : "-"}</Row>
            <Row label="실제 레버리지">{pos.effective_leverage != null ? `${pos.effective_leverage.toFixed(2)}배` : "-"}</Row>
            <Row label={pos.leverage ? `사용 증거금 (${pos.leverage}배 추정)` : "사용 증거금"}>
              {pos.margin != null ? `${num(pos.margin)} USDT` : "-"}
            </Row>
            <Row label="진입 시각">{kst(pos.entry_time)}</Row>
            <Row label="전략">
              <span className="text-xs">{shortStrategy(pos.strategy)}</span>
            </Row>
            <Row label="상태">
              <span className="text-xs">{pos.state}</span>
            </Row>
          </dl>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-1 px-4 py-10 text-center">
          <p className="text-sm">포지션 없음</p>
          <p className="text-xs text-muted-foreground">진입 조건을 만족하면 여기에 표시돼요</p>
        </div>
      )}
    </section>
  )
}
