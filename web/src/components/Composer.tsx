import { useEffect, useRef } from 'react'
import { ArrowUp, ShieldCheck } from 'lucide-react'

import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import type { PermissionMode, Status } from '@/lib/api'
import { cn } from '@/lib/utils'

interface Props {
  value: string
  busy: boolean
  connected: boolean
  note: string
  status: Status | null
  showReasoning: boolean
  onToggleReasoning: (value: boolean) => void
  onPermission: (mode: PermissionMode) => void
  onChange: (value: string) => void
  onSend: (text: string) => void
}

/**
 * The composer, styled after the Codex desktop app: one floating rounded
 * card with the prompt on top and the controls inline at the bottom —
 * permission mode on the left, reasoning toggle next to it, a round
 * near-black send button on the right.
 */
export function Composer({
  value,
  busy,
  connected,
  note,
  status,
  showReasoning,
  onToggleReasoning,
  onPermission,
  onChange,
  onSend,
}: Props) {
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const element = inputRef.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 220)}px`
  }, [value])

  const submit = () => {
    if (busy || !value.trim()) return
    onSend(value)
  }

  const statusText = !connected ? '连接已断开，正在重试…' : busy ? note || '模型正在处理…' : note || '就绪'
  const modes = status?.permissions ?? []

  return (
    <div className="px-6 pb-4 pt-2">
      <form
        className="mx-auto w-full max-w-3xl"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <div className="rounded-3xl bg-background px-5 py-3.5 shadow-[0_2px_20px_rgba(0,0,0,0.08)]">
          <Textarea
            ref={inputRef}
            value={value}
            rows={1}
            disabled={busy}
            placeholder={busy ? '模型正在处理，完成后再继续…' : '让 steward 做点什么…（Enter 发送，Shift + Enter 换行）'}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                submit()
              }
            }}
            className="max-h-[220px] px-0 py-1 text-[19px]"
          />

          <div className="mt-1.5 flex items-center gap-1.5">
            <Select value={status?.permission} onValueChange={(value) => onPermission(value as PermissionMode)}>
              <SelectTrigger className="h-9 w-fit gap-1.5 rounded-full border-0 bg-secondary/70 px-3 text-[16px] font-medium text-muted-foreground hover:bg-secondary focus:ring-0">
                <ShieldCheck className="h-4 w-4" />
                <SelectValue placeholder="权限" />
              </SelectTrigger>
              <SelectContent>
                {modes.map((mode) => (
                  <SelectItem key={mode} value={mode}>
                    {mode}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <button
              type="button"
              onClick={() => onToggleReasoning(!showReasoning)}
              className={cn(
                'flex h-9 items-center gap-1.5 rounded-full px-3 text-[16px] font-medium transition-colors',
                showReasoning
                  ? 'bg-secondary text-foreground'
                  : 'text-muted-foreground hover:bg-secondary/70',
              )}
            >
              思考过程
            </button>

            <button
              type="submit"
              aria-label="发送"
              disabled={busy || !value.trim()}
              className="ml-auto flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground transition-colors hover:bg-primary/90 disabled:bg-secondary disabled:text-muted-foreground"
            >
              <ArrowUp className="h-5 w-5" />
            </button>
          </div>
        </div>

        <div className="mt-2 flex min-h-[22px] items-center gap-2 px-2 text-[16px] text-muted-foreground">
          <span
            className={cn(
              'h-2 w-2 shrink-0 rounded-full',
              !connected ? 'bg-brand' : busy ? 'animate-pulse bg-brand' : 'bg-success',
            )}
          />
          {statusText}
        </div>
      </form>
    </div>
  )
}
