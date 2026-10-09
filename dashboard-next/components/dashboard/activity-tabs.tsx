"use client"

import { useState } from "react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import type { Snapshot } from "@/lib/types"
import { ACTIONS, exitReason, FILL_KINDS, kst, num, signedPct, signedUsd, tone } from "@/lib/format"
import { cn } from "@/lib/utils"

function Empty({ text }: { text: string }) {
  return <p className="py-10 text-center text-xs text-muted-foreground">{text}</p>
}

function Direction({ long }: { long: boolean }) {
  return <span className={cn("text-xs font-semibold", long ? "text-up" : "text-down")}>{long ? "롱" : "숏"}</span>
}

type Side = "all" | "long" | "short"
const SIDES: { value: Side; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "long", label: "롱" },
  { value: "short", label: "숏" },
]

export function ActivityTabs({ snapshot }: { snapshot: Snapshot }) {
  const [side, setSide] = useState<Side>("all")
  const trades = [...snapshot.closed_trades]
    .reverse()
    .filter((t) => side === "all" || t.direction === (side === "long" ? 1 : -1))
  const decisions = [...snapshot.decisions].reverse()
  const fills = snapshot.fills
    .filter((f) => f.kind && (side === "all" || f.kind.startsWith(side)))
    .slice(-30)
    .reverse()

  return (
    <section aria-label="거래 기록" className="rounded-lg border border-border bg-card">
      <Tabs defaultValue="trades" className="gap-0">
        <div className="flex items-end border-b border-border px-3 pt-2">
          <TabsList variant="line" className="h-9">
            <TabsTrigger value="trades" className="px-2 text-xs">
              청산 거래 <span className="text-muted-foreground">{trades.length}</span>
            </TabsTrigger>
            <TabsTrigger value="decisions" className="px-2 text-xs">
              판단 기록 <span className="text-muted-foreground">{decisions.length}</span>
            </TabsTrigger>
            <TabsTrigger value="fills" className="px-2 text-xs">
              체결 <span className="text-muted-foreground">{fills.length}</span>
            </TabsTrigger>
          </TabsList>
          <div role="group" aria-label="롱·숏 보기" className="ml-auto flex items-center gap-1 pb-1.5 text-xs">
            {SIDES.map((s) => (
              <button
                key={s.value}
                type="button"
                aria-pressed={side === s.value}
                onClick={() => setSide(s.value)}
                className={cn(
                  "rounded-md px-2 py-1 font-medium transition-colors",
                  side === s.value
                    ? s.value === "long" ? "bg-up/15 text-up" : s.value === "short" ? "bg-down/15 text-down" : "bg-secondary text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <TabsContent value="trades" className="overflow-x-auto">
          {trades.length ? (
            <Table className="font-mono text-xs tabular-nums">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-4 font-sans">진입</TableHead>
                  <TableHead className="font-sans">청산</TableHead>
                  <TableHead className="font-sans">방향</TableHead>
                  <TableHead className="text-right font-sans">진입가</TableHead>
                  <TableHead className="text-right font-sans">청산가</TableHead>
                  <TableHead className="text-right font-sans">수익률</TableHead>
                  <TableHead className="text-right font-sans">순손익</TableHead>
                  <TableHead className="pr-4 font-sans">사유</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {trades.map((t, i) => (
                  <TableRow key={`${t.entry_time}-${i}`}>
                    <TableCell className="pl-4 text-muted-foreground">{kst(t.entry_time)}</TableCell>
                    <TableCell className="text-muted-foreground">{kst(t.exit_time)}</TableCell>
                    <TableCell>{t.direction == null ? "-" : <Direction long={t.direction > 0} />}</TableCell>
                    <TableCell className="text-right">{num(t.entry_price)}</TableCell>
                    <TableCell className="text-right">{num(t.exit_price)}</TableCell>
                    <TableCell className={cn("text-right", tone(t.price_return))}>{signedPct(t.price_return)}</TableCell>
                    <TableCell className={cn("text-right font-semibold", tone(t.net_pnl))}>{signedUsd(t.net_pnl)}</TableCell>
                    <TableCell className="pr-4 font-sans text-muted-foreground">{exitReason(t.exit_reason)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Empty text={side === "all" ? "아직 청산된 거래가 없어요" : `${side === "long" ? "롱" : "숏"} 청산 거래가 없어요`} />
          )}
        </TabsContent>

        <TabsContent value="decisions" className="overflow-x-auto">
          {decisions.length ? (
            <Table className="text-xs">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-4">봉 시각</TableHead>
                  <TableHead>판단</TableHead>
                  <TableHead className="pr-4">사유</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {decisions.map((d, i) => (
                  <TableRow key={`${d.time}-${i}`}>
                    <TableCell className="pl-4 font-mono text-muted-foreground tabular-nums">{kst(d.time)}</TableCell>
                    <TableCell
                      className={cn(
                        "font-medium",
                        d.action === "enter_long" && "text-up",
                        d.action === "enter_short" && "text-down",
                        d.action === "hold" && "text-muted-foreground",
                      )}
                    >
                      {ACTIONS[d.action] ?? d.action}
                    </TableCell>
                    <TableCell className="pr-4 font-mono text-muted-foreground">{d.reason}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Empty text="판단 기록이 없어요" />
          )}
        </TabsContent>

        <TabsContent value="fills" className="overflow-x-auto">
          {fills.length ? (
            <Table className="font-mono text-xs tabular-nums">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="pl-4 font-sans">시각</TableHead>
                  <TableHead className="font-sans">구분</TableHead>
                  <TableHead className="text-right font-sans">가격</TableHead>
                  <TableHead className="pr-4 text-right font-sans">수량</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {fills.map((f, i) => (
                  <TableRow key={`${f.time}-${i}`}>
                    <TableCell className="pl-4 text-muted-foreground">{kst(f.time)}</TableCell>
                    <TableCell className={cn("font-sans", f.kind?.endsWith("entry") && (f.kind.startsWith("long") ? "text-up" : "text-down"))}>
                      {f.kind ? FILL_KINDS[f.kind] : f.side}
                    </TableCell>
                    <TableCell className="text-right">{num(f.price)}</TableCell>
                    <TableCell className="pr-4 text-right">{num(f.quantity, 4)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <Empty text={side === "all" ? "체결 내역이 없어요" : `${side === "long" ? "롱" : "숏"} 체결이 없어요`} />
          )}
        </TabsContent>
      </Tabs>
    </section>
  )
}
