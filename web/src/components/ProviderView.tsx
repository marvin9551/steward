import { useState } from 'react'
import { Check, FileClock, KeyRound, Pencil, Plug, Trash2 } from 'lucide-react'

import type { ProfileRow, Profiles, ProviderSettings } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'

interface Props {
  provider: ProviderSettings | null
  profiles: Profiles
  note: string
  onEdit: () => void
  onApply: (name: string) => Promise<unknown>
  onDelete: (name: string) => Promise<unknown>
}

/**
 * The provider page: what is in effect right now, and the saved profiles you
 * can switch between. This is the "query" surface — editing happens in the
 * settings dialog, which can save its form as a new profile.
 */
export function ProviderView({ provider, profiles, note, onEdit, onApply, onDelete }: Props) {
  return (
    <div className="flex-1 overflow-y-auto px-8 py-7">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-8">
        <section className="space-y-3">
          <header className="flex items-center justify-between">
            <h2 className="text-[24px] font-semibold text-foreground">当前生效</h2>
            <Button variant="outline" size="sm" onClick={onEdit}>
              <Pencil className="h-4 w-4" />
              编辑设置
            </Button>
          </header>
          <ActiveCard provider={provider} />
        </section>

        <section className="space-y-3">
          <header className="flex items-baseline justify-between">
            <h2 className="text-[24px] font-semibold text-foreground">已配置的档案</h2>
            <span className="text-[17px] text-muted-foreground">
              {profiles.profiles.length} 个
            </span>
          </header>

          {profiles.profiles.length === 0 ? (
            <div className="rounded-xl border border-dashed border-border bg-secondary/40 px-6 py-5 text-[18px] leading-relaxed text-muted-foreground">
              还没有档案。打开「编辑设置」，填好 base_url / key / 模型后，在底部
              「另存为档案」里填个名字（如 <code className="font-mono text-[16px] text-foreground">opencode-go</code>）
              再保存，这里就会出现一张可一键切换的卡片。
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {profiles.profiles.map((row) => (
                <ProfileCard
                  key={row.name}
                  row={row}
                  active={profiles.active === row.name}
                  onApply={() => onApply(row.name)}
                  onDelete={() => onDelete(row.name)}
                />
              ))}
            </div>
          )}
        </section>

        {note ? (
          <p className="text-[16px] text-muted-foreground" aria-live="polite">
            {note}
          </p>
        ) : null}
      </div>
    </div>
  )
}

function ActiveCard({ provider }: { provider: ProviderSettings | null }) {
  if (!provider) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-secondary/40 px-6 py-5 text-[18px] text-muted-foreground">
        还没有加载到 Provider 信息。正在运行的 steward web 可能是旧版本，重启后再试。
      </div>
    )
  }

  return (
    <Card className="shadow-none">
      <CardContent className="grid gap-x-8 gap-y-4 p-5 sm:grid-cols-2">
        <Row label="模型">
          <span className="font-mono text-[18px] text-foreground">{provider.model || '—'}</span>
        </Row>
        <Row label="Base URL">
          <span className="break-all font-mono text-[17px] text-foreground">
            {provider.base_url || '—'}
          </span>
        </Row>
        <Row label="API Key">
          <div className="flex items-center gap-2">
            {provider.api_key_set ? (
              <>
                <Badge variant="success">
                  <Check className="h-3.5 w-3.5" />
                  已配置
                </Badge>
                <span className="text-[16px] text-muted-foreground">只写不读</span>
              </>
            ) : (
              <Badge variant="warning">未配置</Badge>
            )}
          </div>
        </Row>
        <Row label="Temperature">
          <span className="font-mono text-[18px] text-foreground">{provider.temperature}</span>
        </Row>
        <Row label="报告 token 用量">
          <span className="text-[18px] text-foreground">{provider.stream_usage ? '开启' : '关闭'}</span>
        </Row>
        <Row label="环境锁定">
          {provider.env_overrides.length ? (
            <div className="flex flex-wrap gap-1.5">
              {provider.env_overrides.map((name) => (
                <Badge key={name} variant="info">
                  {name}
                </Badge>
              ))}
            </div>
          ) : (
            <span className="text-[18px] text-muted-foreground">无（页面可改全部字段）</span>
          )}
        </Row>
      </CardContent>
    </Card>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[16px] text-muted-foreground">{label}</span>
      {children}
    </div>
  )
}

function ProfileCard({
  row,
  active,
  onApply,
  onDelete,
}: {
  row: ProfileRow
  active: boolean
  onApply: () => Promise<unknown>
  onDelete: () => Promise<unknown>
}) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await action()
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  return (
    <Card className={cn('shadow-none', active && 'border-info/50 bg-info-soft/40')}>
      <CardHeader className="grid gap-1.5 pb-0 sm:grid-cols-[1fr_auto] sm:items-center">
        <CardTitle className="flex flex-wrap items-center gap-2.5">
          <Plug className={cn('h-4 w-4', active ? 'text-info' : 'text-muted-foreground')} />
          <span className="font-mono text-[19px]">{row.name}</span>
          {active ? <Badge variant="info">当前</Badge> : null}
        </CardTitle>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant={active ? 'outline' : 'default'}
            disabled={active || busy}
            onClick={() => run(onApply)}
          >
            {active ? '已启用' : '启用'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            className={cn(confirming && 'text-brand')}
            onClick={() => (confirming ? run(onDelete) : setConfirming(true))}
          >
            {confirming ? (
              '确认删除'
            ) : (
              <>
                <Trash2 className="h-4 w-4" />
                删除
              </>
            )}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="grid gap-x-8 gap-y-3 pt-3 sm:grid-cols-3">
        <Row label="Base URL">
          <span className="break-all font-mono text-[16px] text-foreground">{row.base_url || '—'}</span>
        </Row>
        <Row label="模型">
          <span className="font-mono text-[17px] text-foreground">{row.model || '—'}</span>
        </Row>
        <Row label="API Key">
          {row.api_key_set ? (
            <Badge variant="success">
              <KeyRound className="h-3.5 w-3.5" />
              已配置
            </Badge>
          ) : (
            <Badge variant="warning">
              <FileClock className="h-3.5 w-3.5" />
              复用当前 key
            </Badge>
          )}
        </Row>
      </CardContent>
    </Card>
  )
}
