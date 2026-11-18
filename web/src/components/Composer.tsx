import { useEffect, useRef } from 'react'
import { SendHorizontal } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

interface Props {
  value: string
  busy: boolean
  connected: boolean
  note: string
  onChange: (value: string) => void
  onSend: (text: string) => void
}

export function Composer({ value, busy, connected, note, onChange, onSend }: Props) {
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

  return (
    <div className="border-t border-border px-6 pb-4 pt-3">
      <form
        className="mx-auto w-full max-w-3xl"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <div className="flex items-end gap-3 rounded-xl border border-border bg-background px-4 py-2.5 shadow-sm transition-colors focus-within:border-info">
          <Textarea
            ref={inputRef}
            value={value}
            rows={1}
            disabled={busy}
            placeholder={busy ? '模型正在处理，完成后再继续…' : '说点什么…（Enter 发送，Shift + Enter 换行）'}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                submit()
              }
            }}
            className="max-h-[220px]"
          />
          <Button type="submit" size="lg" disabled={busy || !value.trim()} className="shrink-0">
            <SendHorizontal className="h-4 w-4" />
            发送
          </Button>
        </div>

        <div className="mt-2 flex min-h-[22px] items-center gap-2 text-[16px] text-muted-foreground">
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
