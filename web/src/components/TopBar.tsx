import { LogOut } from 'lucide-react'

interface Props {
  session: string | null
  onLogout: () => void
}

/** Slim task header, Codex style: the session on the left, a logout button
 * on the right. The model and permission controls live in the composer. */
export function TopBar({ session, onLogout }: Props) {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-border/70 px-7 py-3">
      <div className="flex min-w-0 items-baseline gap-2.5">
        <span className="shrink-0 text-[16px] text-muted-foreground">会话</span>
        <span className="truncate font-mono text-[17px] text-foreground">{session ?? '未开始'}</span>
      </div>

      <button
        type="button"
        onClick={onLogout}
        title="退出登录"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
      >
        <LogOut className="h-4 w-4" />
      </button>
    </header>
  )
}
