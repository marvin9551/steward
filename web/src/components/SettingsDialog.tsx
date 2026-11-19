import { useEffect, useState } from 'react'
import {
  FileClock,
  KeyRound,
  Loader2,
  Plug,
  PlugZap,
  RefreshCw,
  Settings2,
  Trash2,
} from 'lucide-react'

import type {
  ModelSyncResult,
  ProfileRow,
  Profiles,
  ProviderProbe,
  ProviderSettings,
  ProviderUpdate,
} from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

interface Props {
  open: boolean
  onOpenChange: (open: boolean) => void
  provider: ProviderSettings | null
  profiles: Profiles
  onSave: (update: ProviderUpdate) => Promise<ProviderSettings>
  onTest: () => Promise<ProviderProbe>
  onSyncModels: (base_url?: string, api_key?: string) => Promise<ModelSyncResult>
  onSaveProfile: (name: string, update: ProviderUpdate) => Promise<unknown>
  onApplyProfile: (name: string) => Promise<unknown>
  onDeleteProfile: (name: string) => Promise<unknown>
  onNote: (note: string) => void
}

/** base_url presets; `opencode-go` is an internal gateway, so it only sets hints. */
const PRESETS: Array<{
  id: string
  label: string
  base_url?: string
  model?: string
  hint?: string
}> = [
  {
    id: 'deepseek',
    label: 'DeepSeek',
    base_url: 'https://api.deepseek.com/v1',
    model: 'deepseek-chat',
    hint: '官方端点，模型名以 deepseek- 开头',
  },
  {
    id: 'openai',
    label: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o',
    hint: '需要支持工具调用的模型',
  },
  {
    id: 'glm',
    label: '智谱 GLM',
    base_url: 'https://open.bigmodel.cn/api/paas/v4',
    model: 'glm-4-plus',
    hint: 'OpenAI 兼容端点，支持 glm-4 系列',
  },
  {
    id: 'moonshot',
    label: 'Moonshot',
    base_url: 'https://api.moonshot.cn/v1',
    model: 'moonshot-v1-8k',
    hint: 'OpenAI 兼容端点',
  },
  {
    id: 'ollama',
    label: 'Ollama（本地）',
    base_url: 'http://127.0.0.1:11434/v1',
    model: 'qwen2.5',
    hint: '本地推理，key 可填任意值（如 ollama）',
  },
  {
    id: 'opencode-go',
    label: 'opencode go（内部网关）',
    hint: '内部 OpenAI 兼容网关：填入地址（通常以 /v1 结尾）和 key，然后点「同步模型」拉取模型列表',
  },
  { id: 'custom', label: '自定义', hint: '任意 OpenAI 兼容端点' },
]

const EMPTY_PROBE: ProviderProbe = { ok: false, error: '' }

type Section = 'provider' | 'profiles'

/**
 * The settings dialog, opened from the sidebar's bottom-left button. Two
 * sections: the provider form (preset or custom endpoint, model sync, a
 * write-only key) and the saved profiles you can switch between. Fields
 * pinned by STEWARD_* env vars are locked; the key never travels back to
 * the page.
 */
export function SettingsDialog({
  open,
  onOpenChange,
  provider,
  profiles,
  onSave,
  onTest,
  onSyncModels,
  onSaveProfile,
  onApplyProfile,
  onDeleteProfile,
  onNote,
}: Props) {
  const [section, setSection] = useState<Section>('provider')
  const [preset, setPreset] = useState('custom')
  const [form, setForm] = useState<ProviderUpdate>({})
  const [profileName, setProfileName] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [probe, setProbe] = useState<ProviderProbe>(EMPTY_PROBE)
  const [syncing, setSyncing] = useState(false)
  const [synced, setSynced] = useState<string[]>([])
  const [syncError, setSyncError] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open || !provider) return
    setForm({
      base_url: provider.base_url,
      model: provider.model,
      temperature: provider.temperature,
      stream_usage: provider.stream_usage,
      api_key: '',
    })
    setPreset(matchPreset(provider.base_url))
    setProbe(EMPTY_PROBE)
    setSynced([])
    setSyncError('')
    setProfileName('')
    setError('')
  }, [open, provider])

  const locked = (field: string) => Boolean(provider?.env_overrides?.includes(field))

  const applyPreset = (id: string) => {
    setPreset(id)
    const found = PRESETS.find((item) => item.id === id)
    if (!found || locked('base_url')) return
    setForm((current) => ({
      ...current,
      base_url: found.base_url ?? current.base_url ?? '',
      model: found.model ?? current.model ?? '',
    }))
  }

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      // Locked fields are disabled in the form; leaving them out of the
      // request entirely keeps the server from rejecting the whole save.
      const payload: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(form)) {
        if (!locked(key)) payload[key] = value
      }
      await onSave(payload as ProviderUpdate)

      // Optionally keep this exact set of values as a switchable profile.
      const name = profileName.trim()
      if (name) {
        await onSaveProfile(name, payload as ProviderUpdate)
      }

      onOpenChange(false)
      onNote(
        name
          ? `Provider 设置与档案 ${name} 已保存，下一次模型调用生效`
          : 'Provider 设置已保存，下一次模型调用生效',
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setError('')
    try {
      const result = await onTest()
      setProbe(result)
    } catch (cause) {
      setProbe({ ok: false, error: cause instanceof Error ? cause.message : String(cause) })
    } finally {
      setTesting(false)
    }
  }

  const sync = async () => {
    setSyncing(true)
    setSyncError('')
    try {
      // Pass the unsaved form values so "paste URL + key → sync → pick" is
      // one visit; empty key means "use the saved one" server-side.
      const result = await onSyncModels(
        locked('base_url') ? undefined : form.base_url,
        locked('api_key') ? undefined : form.api_key || undefined,
      )
      if (result.error) {
        setSynced([])
        setSyncError(result.error)
        return
      }
      setSynced(result.models)
      setForm((current) => ({
        ...current,
        model: current.model || result.models[0] || current.model,
      }))
    } catch (cause) {
      setSyncError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSyncing(false)
    }
  }

  const presetHint = PRESETS.find((item) => item.id === preset)?.hint

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <div className="flex items-start gap-3">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-secondary">
              <Settings2 className="h-5 w-5 text-foreground" />
            </div>
            <div className="min-w-0">
              <DialogTitle>设置</DialogTitle>
              <DialogDescription>
                保存后写入 <code className="font-mono text-[16px]">~/.steward/settings.toml</code>
                （不改动 config.toml），对下一次模型调用生效；STEWARD_* 环境变量始终优先。
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="flex w-fit rounded-lg bg-secondary p-0.5">
          <SectionButton active={section === 'provider'} onClick={() => setSection('provider')}>
            模型与供应商
          </SectionButton>
          <SectionButton active={section === 'profiles'} onClick={() => setSection('profiles')}>
            档案 {profiles.profiles.length > 0 ? profiles.profiles.length : ''}
          </SectionButton>
        </div>

        {section === 'provider' ? (
          <>
            {provider === null ? (
              <p className="rounded-md border border-border border-l-4 border-l-brand bg-brand-soft px-3 py-2 text-[17px] text-foreground">
                后端还没有 /api/provider——正在运行的 <code className="font-mono text-[16px]">steward web</code>{' '}
                是旧版本。重启进程（终端 Ctrl-C 后重新执行，VS Code 按 ⇧⌘F5）后再来配置。
              </p>
            ) : null}

            <div className="grid gap-4">
              <div className="grid gap-1.5">
                <Label>预设</Label>
                <Select value={preset} onValueChange={applyPreset}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PRESETS.map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {presetHint ? <span className="text-[15px] text-muted-foreground">{presetHint}</span> : null}
              </div>

              <Field label="Base URL" locked={locked('base_url')} hint="OpenAI 兼容端点，通常以 /v1 结尾">
                <Input
                  value={form.base_url ?? ''}
                  disabled={locked('base_url')}
                  placeholder="https://api.deepseek.com/v1"
                  onChange={(event) => setForm({ ...form, base_url: event.target.value })}
                />
              </Field>

              <div className="grid gap-1.5">
                <div className="flex items-baseline justify-between">
                  <Label>Model</Label>
                  <button
                    type="button"
                    onClick={sync}
                    disabled={syncing || !provider}
                    className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[15px] font-semibold text-info transition-colors hover:bg-info-soft disabled:opacity-50"
                  >
                    {syncing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                    同步模型
                  </button>
                </div>
                <Input
                  value={form.model ?? ''}
                  disabled={locked('model')}
                  placeholder="deepseek-chat"
                  onChange={(event) => setForm({ ...form, model: event.target.value })}
                />
                {syncError ? (
                  <p className="whitespace-pre-wrap break-words font-mono text-[15px] text-brand">{syncError}</p>
                ) : null}
                {synced.length ? (
                  <div className="flex max-h-36 flex-wrap gap-2 overflow-y-auto rounded-md bg-secondary/60 p-2">
                    {synced.map((id) => (
                      <button
                        key={id}
                        type="button"
                        onClick={() => setForm((current) => ({ ...current, model: id }))}
                        className={cn(
                          'rounded-full border px-3 py-1 font-mono text-[15px] transition-colors',
                          form.model === id
                            ? 'border-info bg-info-soft text-info'
                            : 'border-border bg-background text-foreground hover:bg-accent',
                        )}
                      >
                        {id}
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>

              <Field
                label="API Key"
                locked={locked('api_key')}
                hint={
                  locked('api_key')
                    ? '已由 STEWARD_API_KEY 环境变量设置'
                    : provider?.api_key_set
                      ? '已配置，留空表示保持不变'
                      : '尚未配置'
                }
              >
                <Input
                  type="password"
                  autoComplete="off"
                  value={form.api_key ?? ''}
                  disabled={locked('api_key')}
                  placeholder={provider?.api_key_set ? '••••••••' : 'sk-...'}
                  onChange={(event) => setForm({ ...form, api_key: event.target.value })}
                />
              </Field>

              <div className="grid grid-cols-2 gap-4">
                <Field label="Temperature" locked={locked('temperature')} hint="0 – 2">
                  <Input
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={form.temperature ?? 0.3}
                    disabled={locked('temperature')}
                    onChange={(event) =>
                      setForm({ ...form, temperature: Number(event.target.value) })
                    }
                  />
                </Field>

                <div className="flex items-center justify-between rounded-md border border-border px-4 py-2">
                  <div>
                    <p className="text-[17px] font-medium text-foreground">报告 token 用量</p>
                    <p className="text-[15px] text-muted-foreground">个别网关会拒绝该字段</p>
                  </div>
                  <Switch
                    checked={Boolean(form.stream_usage)}
                    onCheckedChange={(value) => setForm({ ...form, stream_usage: value })}
                  />
                </div>
              </div>

              <div className="grid gap-1.5">
                <div className="flex items-baseline justify-between">
                  <Label>另存为档案（可选）</Label>
                  <span className="text-[15px] text-muted-foreground">
                    保存后可在「档案」里一键切换
                  </span>
                </div>
                <Input
                  value={profileName}
                  placeholder="如 opencode-go、deepseek（字母 / 数字 / - / _）"
                  onChange={(event) => setProfileName(event.target.value)}
                />
              </div>

              {error ? (
                <p className="rounded-md border border-border border-l-4 border-l-brand bg-brand-soft px-3 py-2 text-[17px] text-foreground">
                  {error}
                </p>
              ) : null}

              {probe.error !== undefined && probe.error !== '' ? (
                <p className="whitespace-pre-wrap break-words rounded-md border border-border border-l-4 border-l-brand bg-brand-soft px-3 py-2 font-mono text-[15px] text-foreground">
                  {probe.error}
                </p>
              ) : probe.ok ? (
                <p className="rounded-md border border-border border-l-4 border-l-success bg-success-soft px-3 py-2 text-[17px] text-success">
                  连接正常{probe.reply ? `：${probe.reply}` : ''}
                </p>
              ) : null}
            </div>

            <DialogFooter>
              <Button variant="outline" onClick={test} disabled={testing || !provider}>
                {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlugZap className="h-4 w-4" />}
                测试连接
              </Button>
              <Button onClick={save} disabled={saving || !provider}>
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                保存
              </Button>
            </DialogFooter>
          </>
        ) : (
          <div className="grid gap-3">
            {profiles.profiles.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border bg-secondary/40 px-6 py-5 text-[18px] leading-relaxed text-muted-foreground">
                还没有档案。在「模型与供应商」里填好 base_url / key / 模型后，在底部
                「另存为档案」里填个名字（如 <code className="font-mono text-[16px] text-foreground">opencode-go</code>）
                再保存，这里就会出现一张可一键切换的卡片。
              </div>
            ) : (
              profiles.profiles.map((row) => (
                <ProfileCard
                  key={row.name}
                  row={row}
                  active={profiles.active === row.name}
                  onApply={() => onApplyProfile(row.name)}
                  onDelete={() => onDeleteProfile(row.name)}
                />
              ))
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function SectionButton({
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
        'flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-[16px] font-semibold transition-colors',
        active ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
      )}
    >
      {children}
    </button>
  )
}

function matchPreset(base_url: string): string {
  const found = PRESETS.find((item) => item.base_url === base_url)
  return found?.id ?? 'custom'
}

function Field({
  label,
  hint,
  locked,
  children,
}: {
  label: string
  hint?: string
  locked?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="grid gap-1.5">
      <div className="flex items-baseline justify-between">
        <Label>{label}</Label>
        {hint ? <span className="text-[15px] text-muted-foreground">{hint}</span> : null}
      </div>
      {children}
      {locked ? (
        <span className="text-[15px] text-brand">该字段由环境变量锁定，此处修改不会生效</span>
      ) : null}
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
        <div className="flex flex-col gap-1">
          <span className="text-[16px] text-muted-foreground">Base URL</span>
          <span className="break-all font-mono text-[16px] text-foreground">{row.base_url || '—'}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[16px] text-muted-foreground">模型</span>
          <span className="font-mono text-[17px] text-foreground">{row.model || '—'}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[16px] text-muted-foreground">API Key</span>
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
        </div>
      </CardContent>
    </Card>
  )
}
