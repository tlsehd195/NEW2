import type { Snapshot } from "@/lib/types"
import { kst, num, pct, signedPct, signedUsd, tone, usd } from "@/lib/format"
import { cn } from "@/lib/utils"

function Stat({ label, value, sub, valueClass }: { label: string; value: string; sub?: React.ReactNode; valueClass?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-card px-4 py-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={cn("truncate font-mono text-lg font-semibold tabular-nums", valueClass)}>{value}</span>
      {sub && <span className="truncate text-xs text-muted-foreground">{sub}</span>}
    </div>
  )
}

export function StatStrip({ snapshot }: { snapshot: Snapshot }) {
  const { candles, last_price, position, closed_trades, account } = snapshot
  const price = last_price ?? candles.at(-1)?.close ?? null
  const dayAgo = candles.at(-97)?.close ?? candles[0]?.close
  const change = price != null && dayAgo ? (price - dayAgo) / dayAgo : null

  const unrealized =
    position?.unrealized_pnl ??
    (position?.entry_price && price != null
      ? (price - position.entry_price) * position.quantity * (position.direction === "long" ? 1 : -1)
      : null)

  const realized = closed_trades.reduce((a, t) => a + t.net_pnl, 0)
  const wins = closed_trades.filter((t) => t.net_pnl > 0).length
  const winRate = closed_trades.length ? wins / closed_trades.length : null

  return (
    <section aria-label="요약" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      <Stat
        label="현재가"
        value={num(price, price != null && price < 10 ? 4 : 2)}
        valueClass={tone(change)}
        sub={
          <>
            <span className={tone(change)}>{signedPct(change)}</span> 24시간 · {kst(snapshot.last_price_time)}
          </>
        }
      />
      <Stat
        label="계좌 잔고"
        value={account ? usd(account.balance) : "-"}
        sub={
          account
            ? account.balance_krw != null
              ? `≈ ${Math.round(account.balance_krw).toLocaleString("ko-KR")}원 (${num(account.rate_krw, 0)}원/USDT)`
              : `저장 ${kst(account.saved_at)} · 원화 환산 없음`
            : "트레이더 미실행"
        }
      />
      <Stat
        label="미실현 손익"
        value={signedUsd(unrealized)}
        valueClass={tone(unrealized)}
        sub={position ? `${position.direction === "long" ? "롱" : "숏"} ${num(position.quantity, 4)}` : "포지션 없음"}
      />
      <Stat
        label="최근 실현 손익"
        value={signedUsd(closed_trades.length ? realized : null)}
        valueClass={tone(realized)}
        sub={`최근 ${closed_trades.length}건 합계`}
      />
      <Stat
        label="승률"
        value={pct(winRate)}
        valueClass={winRate == null ? undefined : winRate >= 0.5 ? "text-up" : "text-down"}
        sub={`${wins}승 ${closed_trades.length - wins}패`}
      />
    </section>
  )
}
