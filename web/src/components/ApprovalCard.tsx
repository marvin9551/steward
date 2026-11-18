import { ShieldAlert } from 'lucide-react'

import { Pre } from '@/components/ToolCard'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { ApprovalBlock } from '@/lib/blocks'
import { pretty } from '@/lib/format'

const LABELS: Record<string, string> = {
  once: '已允许一次',
  always: '已允许，本次运行不再询问',
  deny: '已拒绝',
  '': '已超时，按拒绝处理',
}

export type ApprovalDecision = 'once' | 'always' | 'deny'

export function ApprovalCard({
  block,
  onDecide,
}: {
  block: ApprovalBlock
  onDecide: (decision: ApprovalDecision) => void
}) {
  const decided = block.decision !== null

  return (
    <Card className="border-brand/50 bg-brand-soft shadow-none">
      <CardHeader className="pb-0">
        <CardTitle className="flex flex-wrap items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-brand" />
          需要确认
          <code className="rounded bg-white/70 px-1.5 py-0.5 font-mono text-[17px] text-brand">
            {block.name}
          </code>
        </CardTitle>
        <CardDescription className="text-[17px]">
          这次调用会在你的工作区里执行，确认之后才会运行。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3 pt-3">
        <Pre text={pretty(block.args)} />
        {decided ? (
          <p className="text-[17px] text-muted-foreground">{LABELS[block.decision ?? '']}</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="success" onClick={() => onDecide('once')}>
              允许一次
            </Button>
            <Button size="sm" onClick={() => onDecide('always')}>
              始终允许
            </Button>
            <Button size="sm" variant="outline" onClick={() => onDecide('deny')}>
              拒绝
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
