import { MessageSquare, Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import type { SessionRow, Status } from '@/lib/api'
import { relativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'

interface Props {
  status: Status | null
  sessions: SessionRow[]
  session: string | null
  onNew: () => void
  onSelect: (id: string) => void
}

export function SessionSidebar({ status, sessions, session, onNew, onSelect }: Props) {
  return (
    <aside className="flex w-[320px] shrink-0 flex-col gap-4 overflow-hidden border-r border-border bg-background px-4 py-5">
      <div className="flex items-center gap-2.5 px-1">
        <span className="h-[18px] w-[18px] rounded-md bg-brand" />
        <h1 className="text-[24px] font-bold tracking-tight text-foreground">steward</h1>
        <span className="ml-auto font-mono text-[15px] text-muted-foreground">
          {status ? `v${status.version}` : ''}
        </span>
      </div>

      <Button onClick={onNew} className="w-full">
        <Plus className="h-4 w-4" />
        新对话
      </Button>

      <p className="px-1 text-[16px] font-semibold tracking-[0.08em] text-muted-foreground">会话</p>

      <div className="-mx-1 min-h-0 flex-1 space-y-1 overflow-y-auto px-1">
        {sessions.length === 0 ? (
          <p className="px-2 py-3 text-[17px] text-muted-foreground">还没有会话</p>
        ) : (
          sessions.map((item) => (
            <button
              key={item.session_id}
              type="button"
              onClick={() => onSelect(item.session_id)}
              className={cn(
                'w-full rounded-lg border border-transparent px-3 py-2 text-left transition-colors hover:bg-secondary',
                item.session_id === session && 'border-border bg-info-soft hover:bg-info-soft',
              )}
            >
              <span
                className={cn(
                  'block truncate font-mono text-[16px] text-foreground',
                  item.session_id === session && 'text-info',
                )}
              >
                {item.session_id}
              </span>
              <span className="flex items-center gap-1.5 truncate text-[16px] text-muted-foreground">
                <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                {item.messages} 条 · {relativeTime(item.updated_at)}
                {item.preview ? ` · ${item.preview}` : ''}
              </span>
            </button>
          ))
        )}
      </div>

      <Separator />

      <div className="space-y-1 break-words px-1 text-[16px] leading-relaxed text-muted-foreground">
        <p>
          <span className="font-semibold text-foreground">模型</span> {status?.model ?? '—'}
        </p>
        <p>
          <span className="font-semibold text-foreground">工作区</span> {status?.workspace ?? '—'}
        </p>
        <p>
          <span className="font-semibold text-foreground">服务</span> {status?.base_url ?? '—'}
        </p>
        {status && !status.has_api_key ? (
          <p className="text-brand">未配置 api_key（config.toml 或 STEWARD_API_KEY）</p>
        ) : null}
      </div>
    </aside>
  )
}
