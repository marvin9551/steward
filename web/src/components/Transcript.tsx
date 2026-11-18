import { useEffect, useRef } from 'react'
import { MessageSquarePlus } from 'lucide-react'

import type { ApprovalDecision } from '@/components/ApprovalCard'
import { BlockView } from '@/components/ConversationBlocks'
import { Button } from '@/components/ui/button'
import type { Block } from '@/lib/blocks'

const SUGGESTIONS = [
  '列出工作区里有哪些文件',
  '读一下 README 并总结要点',
  '看看有哪些 Python 模块，画出调用关系',
]

interface Props {
  blocks: Block[]
  showReasoning: boolean
  busy: boolean
  onDecide: (block: Block, decision: ApprovalDecision) => void
  onPick: (text: string) => void
}

export function Transcript({ blocks, showReasoning, busy, onDecide, onPick }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const stickRef = useRef(true)

  const handleScroll = () => {
    const element = containerRef.current
    if (!element) return
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight
    stickRef.current = distance < 160
  }

  useEffect(() => {
    const element = containerRef.current
    if (element && stickRef.current) {
      element.scrollTop = element.scrollHeight
    }
  }, [blocks])

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto px-6 py-6"
      aria-live="polite"
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
        {blocks.length === 0 ? (
          <EmptyHint onPick={onPick} />
        ) : (
          blocks.map((block) => (
            <BlockView
              key={block.key}
              block={block}
              showReasoning={showReasoning}
              onDecide={onDecide}
            />
          ))
        )}
        {busy ? (
          <div className="flex items-center gap-2 text-[17px] text-muted-foreground">
            <span className="flex gap-1">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand" />
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand [animation-delay:150ms]" />
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand [animation-delay:300ms]" />
            </span>
            模型正在处理…
          </div>
        ) : null}
      </div>
    </div>
  )
}

function EmptyHint({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="rounded-xl border border-dashed border-border bg-secondary/40 px-7 py-6">
      <h2 className="flex items-center gap-2 text-[22px] font-semibold text-foreground">
        <MessageSquarePlus className="h-5 w-5 text-info" />
        开始一段对话
      </h2>
      <p className="mt-2 text-[18px] leading-relaxed text-muted-foreground">
        模型会在你的工作区里读写文件、执行命令，每一步都会显示在这里；执行命令前会先弹出确认卡片。
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        {SUGGESTIONS.map((text) => (
          <Button key={text} variant="outline" size="sm" onClick={() => onPick(text)}>
            {text}
          </Button>
        ))}
      </div>
    </div>
  )
}
