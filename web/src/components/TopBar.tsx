import type { Status } from '@/lib/api'

interface Props {
  status: Status | null
  session: string | null
}

/** Slim task header, Codex style: the session on the left, version on the
 * right. The model and permission controls live in the composer. */
export function TopBar({ status, session }: Props) {
  return (
    <header className="flex items-center justify-between gap-4 border-b border-border/70 px-7 py-3">
      <div className="flex min-w-0 items-baseline gap-2.5">
        <span className="shrink-0 text-[16px] text-muted-foreground">会话</span>
        <span className="truncate font-mono text-[17px] text-foreground">{session ?? '未开始'}</span>
      </div>

      <span className="shrink-0 font-mono text-[14px] text-muted-foreground">
        {status ? `v${status.version}` : ''}
      </span>
    </header>
  )
}
