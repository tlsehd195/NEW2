"use client"

import { Plug } from "lucide-react"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover"

const STEPS = [
  { label: "1. 트레이더 PC에서 대시보드 서버 실행", code: "python3 scripts/run_dashboard.py" },
  { label: "2. 이 UI 프로젝트에 환경변수 추가", code: "TRADER_API_URL=http://127.0.0.1:8765" },
  { label: "3. 같은 PC에서 UI 실행", code: "pnpm install && pnpm dev" },
]

export function ConnectionHelp({ live, error }: { live: boolean; error?: string }) {
  return (
    <Popover>
      <PopoverTrigger className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border px-2 text-xs text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground">
        <Plug className="size-3.5" aria-hidden />
        연결 방법
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[340px] text-sm">
        <div className="flex flex-col gap-3">
          <div>
            <p className="font-medium">내 트레이더에 연결하기</p>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
              {live
                ? "지금 실제 트레이더 데이터를 보고 있어요."
                : "지금은 데모 데이터예요. 아래 순서대로 하면 NEW2의 실제 데이터가 표시돼요."}
            </p>
            {error && <p className="mt-1 text-xs text-down">{error}</p>}
          </div>
          <ol className="flex flex-col gap-2.5">
            {STEPS.map((s) => (
              <li key={s.code} className="flex flex-col gap-1">
                <span className="text-xs text-muted-foreground">{s.label}</span>
                <code className="rounded bg-secondary px-2 py-1.5 font-mono text-xs">{s.code}</code>
              </li>
            ))}
          </ol>
          <p className="text-xs leading-relaxed text-muted-foreground">
            이 화면은 조회만 해요. 주문 버튼이 없어서 화면을 통해 실제 주문이 나갈 수 없어요.
          </p>
        </div>
      </PopoverContent>
    </Popover>
  )
}
