import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { ProviderDialog } from '@/components/ProviderDialog'
import type {
  ModelSyncResult,
  PermissionMode,
  ProviderProbe,
  ProviderSettings,
  ProviderUpdate,
  Status,
} from '@/lib/api'

interface Props {
  status: Status | null
  session: string | null
  showReasoning: boolean
  onToggleReasoning: (value: boolean) => void
  onPermission: (mode: PermissionMode) => void
  provider: ProviderSettings | null
  onSaveProvider: (update: ProviderUpdate) => Promise<ProviderSettings>
  onTestProvider: () => Promise<ProviderProbe>
  onSyncModels: (baseUrl?: string, apiKey?: string) => Promise<ModelSyncResult>
  onNote: (note: string) => void
}

export function TopBar({
  status,
  session,
  showReasoning,
  onToggleReasoning,
  onPermission,
  provider,
  onSaveProvider,
  onTestProvider,
  onSyncModels,
  onNote,
}: Props) {
  const modes = status?.permissions ?? []

  return (
    <header className="flex items-center justify-between gap-4 border-b border-border px-7 py-3.5">
      <div className="flex min-w-0 items-baseline gap-2.5">
        <span className="shrink-0 text-[17px] text-muted-foreground">会话</span>
        <span className="truncate font-mono text-[18px] text-foreground">{session ?? '未开始'}</span>
      </div>

      <div className="flex shrink-0 items-center gap-6">
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

        <ProviderDialog
          provider={provider}
          onSave={onSaveProvider}
          onTest={onTestProvider}
          onSyncModels={onSyncModels}
          onNote={onNote}
        />
      </div>
    </header>
  )
}
