import { useEffect, useState } from 'react'
import { Loader2, PlugZap, Settings2 } from 'lucide-react'

import type { ProviderProbe, ProviderSettings, ProviderUpdate } from '@/lib/api'
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
import { Switch } from '@/components/ui/switch'

interface Props {
  provider: ProviderSettings | null
  onSave: (update: ProviderUpdate) => Promise<ProviderSettings>
  onTest: () => Promise<ProviderProbe>
  onNote: (note: string) => void
}

const EMPTY_PROBE: ProviderProbe = { ok: false, error: '' }

/**
 * Provider settings dialog: base_url / model / api_key / temperature.
 * The key field is write-only — the API never returns it, and leaving the field
 * empty keeps the current one. Fields pinned by STEWARD_* env vars are locked.
 */
export function ProviderDialog({ provider, onSave, onTest, onNote }: Props) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<ProviderUpdate>({})
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [probe, setProbe] = useState<ProviderProbe>(EMPTY_PROBE)
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
    setProbe(EMPTY_PROBE)
    setError('')
  }, [open, provider])

  const locked = (field: string) => Boolean(provider?.env_overrides?.includes(field))

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

        <div className="grid gap-4">
          <Field label="Base URL" locked={locked('base_url')} hint="OpenAI 兼容端点，通常以 /v1 结尾">
            <Input
              value={form.base_url ?? ''}
              disabled={locked('base_url')}
              placeholder="https://api.deepseek.com/v1"
              onChange={(event) => setForm({ ...form, base_url: event.target.value })}
            />
          </Field>

          <Field label="Model" locked={locked('model')} hint="需要支持工具调用">
            <Input
              value={form.model ?? ''}
              disabled={locked('model')}
              placeholder="deepseek-chat"
              onChange={(event) => setForm({ ...form, model: event.target.value })}
            />
          </Field>

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
          <Button variant="outline" onClick={test} disabled={testing}>
            {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <PlugZap className="h-4 w-4" />}
            测试连接
          </Button>
          <Button onClick={save} disabled={saving}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
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
