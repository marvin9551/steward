import { useState } from 'react'
import { Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface Props {
  onLogin: (username: string, password: string) => Promise<void>
}

/** The login screen: a single centered card, Codex style. The default
 * account comes from the CLI banner on first start. */
export function LoginView({ onLogin }: Props) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (busy || !username.trim() || !password) return
    setBusy(true)
    setError('')
    try {
      await onLogin(username.trim(), password)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex h-full items-center justify-center bg-surface px-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl bg-background p-7 shadow-[0_8px_40px_rgba(0,0,0,0.1)]"
      >
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-secondary">
            <span className="text-[18px] font-bold text-foreground">s</span>
          </div>
          <div>
            <h1 className="text-[22px] font-bold leading-tight text-foreground">steward</h1>
            <p className="text-[16px] text-muted-foreground">登录你的个人助理</p>
          </div>
        </div>

        <div className="grid gap-4">
          <div className="grid gap-1.5">
            <Label htmlFor="login-username">用户名</Label>
            <Input
              id="login-username"
              value={username}
              autoComplete="username"
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="login-password">密码</Label>
            <Input
              id="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              placeholder="首次启动的默认密码在启动日志里"
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>

          {error ? (
            <p className="rounded-md border border-border border-l-4 border-l-brand bg-brand-soft px-3 py-2 text-[16px] text-foreground">
              {error}
            </p>
          ) : null}

          <Button type="submit" disabled={busy || !username.trim() || !password}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            登录
          </Button>
        </div>
      </form>
    </div>
  )
}
