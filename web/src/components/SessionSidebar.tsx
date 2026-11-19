import { MessageSquare, Plus, Settings2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import type { SessionRow } from '@/lib/api'
import { relativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'

interface Props {
  sessions: SessionRow[]
  session: string | null
  onNew: () => void
  onSelect: (id: string) => void
  onOpenSettings: () => void
}

export function SessionSidebar({ sessions, session, onNew, onSelect, onOpenSettings }: Props) {
  return (
    <aside className="flex w-[300px] shrink-0 flex-col gap-4 overflow-hidden bg-surface px-4 py-5">
      <div className="flex items-center gap-2.5 px-1">
        <span className="h-[18px] w-[18px] rounded-md bg-primary" />
        <h1 className="text-[22px] font-bold tracking-tight text-foreground">steward</h1>
      </div>

      <Button onClick={onNew} className="h-10 w-full rounded-xl">
        <Plus className="h-4 w-4" />
        新对话
      </Button>

      <p className="px-1 text-[15px] font-semibold tracking-[0.08em] text-muted-foreground">会话</p>

      <div className="-mx-1 min-h-0 flex-1 space-y-1 overflow-y-auto px-1">
        {sessions.length === 0 ? (
          <p className="px-2 py-3 text-[16px] text-muted-foreground">还没有会话</p>
        ) : (
          sessions.map((item) => (
            <button
              key={item.session_id}
              type="button"
              onClick={() => onSelect(item.session_id)}
              className={cn(
                'w-full rounded-xl border border-transparent px-3 py-2 text-left transition-colors',
                item.session_id === session
                  ? 'border-border bg-background shadow-sm'
                  : 'hover:bg-secondary/80',
              )}
            >
              <span
                className={cn(
                  'block truncate font-mono text-[16px] text-foreground',
                  item.session_id === session && 'font-semibold',
                )}
              >
                {item.session_id}
              </span>
              <span className="flex items-center gap-1.5 truncate text-[15px] text-muted-foreground">
                <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                {item.messages} 条 · {relativeTime(item.updated_at)}
                {item.preview ? ` · ${item.preview}` : ''}
              </span>
            </button>
          ))
        )}
      </div>

      <Button
        variant="ghost"
        className="w-full justify-start rounded-xl text-muted-foreground hover:text-foreground"
        onClick={onOpenSettings}
      >
        <Settings2 className="h-4 w-4" />
        设置
      </Button>
    </aside>
  )
}
