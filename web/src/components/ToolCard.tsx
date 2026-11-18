import { useEffect, useState } from 'react'
import { ChevronRight, Terminal } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import type { ToolBlock } from '@/lib/blocks'
import { oneLine, pretty } from '@/lib/format'
import { cn } from '@/lib/utils'

type BadgeVariant = 'success' | 'warning' | 'info'

export function ToolCard({ block }: { block: ToolBlock }) {
  const [open, setOpen] = useState(block.pending)

  useEffect(() => {
    if (block.pending || !block.ok) setOpen(true)
  }, [block.pending, block.ok])

  const status: { label: string; variant: BadgeVariant } = block.pending
    ? { label: '运行中', variant: 'info' }
    : block.ok
      ? { label: 'ok', variant: 'success' }
      : { label: 'error', variant: 'warning' }

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <Card className={cn('overflow-hidden shadow-none', block.pending && 'border-info/50')}>
        <CollapsibleTrigger className="flex w-full items-center gap-3 bg-secondary/60 px-4 py-2.5 text-left transition-colors hover:bg-secondary">
          <ChevronRight
            className={cn(
              'h-4 w-4 shrink-0 text-muted-foreground transition-transform',
              open && 'rotate-90',
            )}
          />
          <Terminal className="h-4 w-4 shrink-0 text-info" />
          <span className="shrink-0 font-mono text-[17px] font-semibold text-foreground">
            {block.name}
          </span>
          <span className="min-w-0 flex-1 truncate font-mono text-[15px] text-muted-foreground">
            {oneLine(block.args)}
          </span>
          <Badge variant={status.variant} className="shrink-0">
            {status.label}
          </Badge>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="space-y-2 border-t border-border px-4 py-3">
            <p className="text-[16px] text-muted-foreground">参数</p>
            <Pre text={pretty(block.args)} />
            <p className="text-[16px] text-muted-foreground">结果</p>
            <Pre text={block.output || '（等待结果）'} />
          </div>
        </CollapsibleContent>
      </Card>
    </Collapsible>
  )
}

export function Pre({ text }: { text: string }) {
  return (
    <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-secondary px-3.5 py-3 font-mono text-[15px] leading-relaxed text-foreground">
      {text}
    </pre>
  )
}
