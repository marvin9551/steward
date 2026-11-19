import { useEffect, useState } from 'react'
import { Loader2, PlugZap, RefreshCw, Settings2 } from 'lucide-react'

import type { ModelSyncResult, ProviderProbe, ProviderSettings, ProviderUpdate } from '@/lib/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

interface Props {
  provider: ProviderSettings | null
  onSave: (update: ProviderUpdate) => Promise<ProviderSettings>
  onTest: () => Promise<ProviderProbe>
  onSyncModels: (base_url?: string, api_key?: string) => Promise<ModelSyncResult>
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

/**
 * Provider settings dialog: preset or custom endpoint, model sync from the
 * gateway, and a write-only key field. Fields pinned by STEWARD_* env vars are
 * locked; the key never travels back to the page.
 */
export function ProviderDialog({ provider, onSave, onTest, onSyncModels, onNote }: Props) {
  const [open, setOpen] = useState(false)
  const [preset, setPreset] = useState('custom')
  const [form, setForm] = useState<ProviderUpdate>({})
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
      setOpen(false)
      onNote('Provider 设置已保存，下一次模型调用生效')
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
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" title="Provider 设置">
          <Settings2 className="h-5 w-5" />
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Provider 设置</DialogTitle>
          <DialogDescription>
            保存后写入 <code className="font-mono text-[16px]">~/.steward/settings.toml</code>
            （不改动 config.toml），对下一次模型调用生效；STEWARD_* 环境变量始终优先。
          </DialogDescription>
        </DialogHeader>

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
      </DialogContent>
    </Dialog>
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
