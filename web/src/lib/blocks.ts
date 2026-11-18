import type { StreamEvent, StoredBlock } from './api'

/* The conversation is a list of blocks, not a list of messages.
 *
 * A "turn" can contain many model steps and tool calls, and the page wants
 * them interleaved in the order they happened. The reducer below is the only
 * place that interprets the event stream; components stay dumb.
 *
 * `open` marks an assistant block that is still receiving deltas. While open we
 * render plain text; once closed it is re-rendered as markdown, so a half
 * streamed code fence never flickers into a broken layout.
 */

export type UserBlock = { key: string; kind: 'user'; text: string }

export type AssistantBlock = { key: string; kind: 'assistant'; text: string; open: boolean }

export type ReasoningBlock = { key: string; kind: 'reasoning'; text: string }

export type ToolBlock = {
  key: string
  kind: 'tool'
  callId?: string
  name: string
  args: Record<string, unknown>
  output: string
  ok: boolean
  pending: boolean
}

export type ApprovalBlock = {
  key: string
  kind: 'approval'
  approvalId: string
  name: string
  args: Record<string, unknown>
  decision: string | null
}

export type NoticeBlock = { key: string; kind: 'notice'; text: string }

export type ErrorBlock = { key: string; kind: 'error'; text: string }

export type Block =
  | UserBlock
  | AssistantBlock
  | ReasoningBlock
  | ToolBlock
  | ApprovalBlock
  | NoticeBlock
  | ErrorBlock

let counter = 0
function nextKey(prefix = 'b'): string {
  counter += 1
  return `${prefix}-${counter}`
}

export type Action =
  | { type: 'reset'; blocks: Block[] }
  | { type: 'event'; event: StreamEvent }
  | { type: 'decision'; key: string; decision: string }
  | { type: 'answered'; approvalId: string; decision: string }

function closeAssistant(blocks: Block[]): Block[] {
  const last = blocks[blocks.length - 1]
  if (last && last.kind === 'assistant' && last.open) {
    return [...blocks.slice(0, -1), { ...last, open: false }]
  }
  return blocks
}

export function reducer(blocks: Block[], action: Action): Block[] {
  if (action.type === 'reset') return action.blocks

  if (action.type === 'decision') {
    return blocks.map((block) =>
      block.key === action.key && block.kind === 'approval'
        ? { ...block, decision: action.decision }
        : block,
    )
  }

  if (action.type === 'answered') {
    return blocks.map((block) =>
      block.kind === 'approval' && block.approvalId === action.approvalId && !block.decision
        ? { ...block, decision: action.decision }
        : block,
    )
  }

  const event = action.event

  switch (event.type) {
    case 'turn_start': {
      const base = closeAssistant(blocks)
      return [...base, { key: nextKey('user'), kind: 'user', text: event.text }]
    }

    case 'text': {
      const last = blocks[blocks.length - 1]
      if (last && last.kind === 'assistant' && last.open) {
        return [...blocks.slice(0, -1), { ...last, text: last.text + event.text }]
      }
      return [...blocks, { key: nextKey('assistant'), kind: 'assistant', text: event.text, open: true }]
    }

    case 'reasoning': {
      const base = closeAssistant(blocks)
      const last = base[base.length - 1]
      if (last && last.kind === 'reasoning') {
        return [...base.slice(0, -1), { ...last, text: last.text + event.text }]
      }
      return [...base, { key: nextKey('reasoning'), kind: 'reasoning', text: event.text }]
    }

    case 'tool_call': {
      const base = closeAssistant(blocks)
      return [
        ...base,
        {
          key: nextKey('tool'),
          kind: 'tool',
          name: event.name,
          args: event.args || {},
          output: '',
          ok: true,
          pending: true,
        },
      ]
    }

    case 'tool_result': {
      for (let index = blocks.length - 1; index >= 0; index -= 1) {
        const block = blocks[index]
        if (block.kind === 'tool' && block.pending) {
          const updated: Block = {
            ...block,
            output: event.output || '',
            ok: Boolean(event.ok),
            pending: false,
          }
          return [...blocks.slice(0, index), updated, ...blocks.slice(index + 1)]
        }
      }
      return blocks
    }

    case 'notice': {
      const base = closeAssistant(blocks)
      return [...base, { key: nextKey('notice'), kind: 'notice', text: event.text }]
    }

    case 'error': {
      const base = closeAssistant(blocks)
      return [...base, { key: nextKey('error'), kind: 'error', text: event.text }]
    }

    case 'turn_done': {
      const base = closeAssistant(blocks)
      const last = base[base.length - 1]
      if (last && last.kind === 'assistant') return base
      if (!event.text) return base
      return [...base, { key: nextKey('assistant'), kind: 'assistant', text: event.text, open: false }]
    }

    case 'approval_request': {
      const base = closeAssistant(blocks)
      return [
        ...base,
        {
          key: nextKey('approval'),
          kind: 'approval',
          approvalId: event.id,
          name: event.name,
          args: event.args || {},
          decision: null,
        },
      ]
    }

    default:
      return blocks
  }
}

export function blocksFromTranscript(stored: StoredBlock[]): Block[] {
  const blocks: Block[] = []
  for (const item of stored) {
    if (item.kind === 'tool') {
      blocks.push({
        key: nextKey('tool'),
        kind: 'tool',
        callId: item.call_id,
        name: item.name || 'tool',
        args: item.args || {},
        output: item.output || '',
        ok: item.ok !== false,
        pending: false,
      })
      continue
    }
    if (item.kind === 'assistant') {
      blocks.push({
        key: nextKey('assistant'),
        kind: 'assistant',
        text: item.text || '',
        open: false,
      })
      continue
    }
    if (item.kind === 'user') {
      blocks.push({ key: nextKey('user'), kind: 'user', text: item.text || '' })
      continue
    }
    if (item.kind === 'reasoning') {
      blocks.push({ key: nextKey('reasoning'), kind: 'reasoning', text: item.text || '' })
      continue
    }
    if (item.kind === 'error') {
      blocks.push({ key: nextKey('error'), kind: 'error', text: item.text || '' })
      continue
    }
    blocks.push({ key: nextKey('notice'), kind: 'notice', text: item.text || '' })
  }
  return blocks
}
