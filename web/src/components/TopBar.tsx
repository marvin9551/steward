import { MessagesSquare, Settings2, SlidersHorizontal } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import type { PermissionMode, Status } from '@/lib/api'

export type PageTab = 'chat' | 'provider'

interface Props {
  tab: PageTab
  onTab: (tab: PageTab) => void
  status: Status | null
  session: string | null
  showReasoning: boolean
  onToggleReasoning: (value: boolean) => void
  onPermission: (mode: PermissionMode) => void
  onOpenSettings: () => void
}

export function TopBar({
  tab,
  onTab,
  status,
  session,
  showReasoning,
  onToggleReasoning,
  onPermission,
  onOpenSettings,
}: Props) {
  const modes = status?.permissions ?? []

  return (
    <header className="flex items-center justify-between gap-4 border-b border-border px-7 py-3.5">
      <div className="flex min-w-0 items-center gap-4">
        <div className="flex shrink-0 rounded-lg bg-secondary p-0.5">
          <TabButton active={tab === 'chat'} onClick={() => onTab('chat')}>
            <MessagesSquare className="h-4 w-4" />
            对话
          </TabButton>
          <TabButton active={tab === 'provider'} onClick={() => onTab('provider')}>
            <SlidersHorizontal className="h-4 w-4" />
            Provider
          </TabButton>
        </div>

        {tab === 'chat' ? (
          <div className="flex min-w-0 items-baseline gap-2.5">
            <span className="shrink-0 text-[17px] text-muted-foreground">会话</span>
            <span className="truncate font-mono text-[18px] text-foreground">{session ?? '未开始'}</span>
          </div>
        ) : (
          <span className="truncate text-[17px] text-muted-foreground">
            查询与切换已配置的 Provider
          </span>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-6">
        {tab === 'chat' ? (
          <>
            <label className="flex cursor-pointer items-center gap-2.5 text-[17px] text-muted-foreground">
              <Switch checked={showReasoning} onCheckedChange={onToggleReasoning} />
              思考过程
            </label>

            <div className="flex items-center gap-2 text-[17px] text-muted-foreground">
              <span>权限</span>
              <Select
                value={status?.permission}
                onValueChange={(value) => onPermission(value as PermissionMode)}
              >
                <SelectTrigger className="w-[168px]">
                  <SelectValue placeholder="选择模式" />
                </SelectTrigger>
                <SelectContent>
                  {modes.map((mode) => (
                    <SelectItem key={mode} value={mode}>
                      {mode}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </>
        ) : null}

        <Button variant="ghost" size="icon" title="Provider 设置" onClick={onOpenSettings}>
          <Settings2 className="h-5 w-5" />
        </Button>
      </div>
    </header>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-[17px] font-semibold transition-colors',
        active
          ? 'bg-background text-foreground shadow-sm'
          : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}
