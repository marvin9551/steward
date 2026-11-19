import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { ApprovalCard, type ApprovalDecision } from '@/components/ApprovalCard'
import { ToolCard } from '@/components/ToolCard'
import type { Block } from '@/lib/blocks'

interface Props {
  block: Block
  showReasoning: boolean
  onDecide: (block: Block, decision: ApprovalDecision) => void
}

/**
 * One rendered block. Memoized because a streaming turn rewrites the block list
 * on every delta; without it a long transcript would re-render from the top.
 */
export const BlockView = memo(function BlockView({ block, showReasoning, onDecide }: Props) {
  switch (block.kind) {
    case 'user':
      return (
        <div className="ml-auto max-w-[88%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-secondary px-5 py-3 text-[20px] leading-relaxed text-foreground">
          {block.text}
        </div>
      )

    case 'assistant':
      return block.open ? (
        <div className="whitespace-pre-wrap break-words text-[20px] leading-relaxed text-foreground">
          {block.text}
        </div>
      ) : (
        <div className="md">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
        </div>
      )

    case 'reasoning':
      if (!showReasoning) return null
      return (
        <div className="max-h-72 overflow-y-auto whitespace-pre-wrap break-words border-l-[3px] border-dashed border-border pl-4 text-[17px] leading-relaxed text-muted-foreground">
          {block.text}
        </div>
      )

    case 'tool':
      return <ToolCard block={block} />

    case 'approval':
      return <ApprovalCard block={block} onDecide={(decision) => onDecide(block, decision)} />

    case 'notice':
      return <div className="text-center text-[17px] text-muted-foreground">{block.text}</div>

    case 'error':
      return (
        <div className="whitespace-pre-wrap break-words rounded-lg border border-border border-l-4 border-l-brand bg-brand-soft px-4 py-3 text-[18px] text-foreground">
          {block.text}
        </div>
      )

    default:
      return null
  }
})
