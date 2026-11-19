const JSON_HEADERS = { 'Content-Type': 'application/json' }

export type PermissionMode = 'read-only' | 'workspace-write' | 'full'

export interface Status {
  version: string
  model: string
  base_url: string
  permission: PermissionMode
  permissions: PermissionMode[]
  workspace: string
  show_reasoning: boolean
  has_api_key: boolean
  busy: boolean
  busy_session: string | null
  latest_session: string | null
}

export interface SessionRow {
  session_id: string
  messages: number
  updated_at: string
  last_role: string | null
  preview: string
}

/** Wire contract from `web/stream.py`. Keep the two lists in sync. */
export type StreamEvent =
  | { type: 'turn_start'; session: string; text: string }
  | { type: 'text'; text: string }
  | { type: 'reasoning'; text: string }
  | { type: 'tool_call'; name: string; args: Record<string, unknown> }
  | { type: 'tool_result'; name: string; output: string; ok: boolean }
  | { type: 'notice'; text: string }
  | { type: 'turn_done'; text: string; steps: number; usage: Record<string, number> }
  | { type: 'error'; text: string }
  | { type: 'idle' }
  | { type: 'approval_request'; id: string; name: string; args: Record<string, unknown> }
  | { type: 'approval_resolved'; id: string; decision: string }
  | { type: 'config'; permission: PermissionMode }
  | ({ type: 'provider' } & ProviderSettings)
  | { type: 'resync' }

/** What `GET/POST /api/provider` accepts and returns. The key never returns. */
export interface ProviderSettings {
  base_url: string
  model: string
  temperature: number
  stream_usage: boolean
  api_key_set: boolean
  /** Fields pinned by STEWARD_* environment variables; the page cannot win. */
  env_overrides: string[]
}

export interface ProviderUpdate {
  base_url?: string
  model?: string
  api_key?: string
  temperature?: number
  stream_usage?: boolean
}

export interface ProviderProbe {
  ok: boolean
  reply?: string
  error?: string
}

export interface ModelSyncResult {
  models: string[]
  error?: string
}

/** One saved provider profile; the key itself never leaves the server. */
export interface ProfileRow {
  name: string
  base_url: string
  model: string
  api_key_set: boolean
  temperature: number | null
  stream_usage: boolean | null
}

export interface Profiles {
  active: string | null
  profiles: ProfileRow[]
}

export interface StoredBlock {
  kind: 'user' | 'assistant' | 'reasoning' | 'notice' | 'error' | 'tool'
  text?: string
  call_id?: string
  name?: string
  args?: Record<string, unknown>
  output?: string
  ok?: boolean
}

export interface Transcript {
  session: string
  blocks: StoredBlock[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: JSON_HEADERS,
    ...init,
  })
  const raw = await response.text()
  let data: unknown = {}
  try {
    data = raw ? JSON.parse(raw) : {}
  } catch {
    data = { error: raw }
  }
  if (!response.ok) {
    const message =
      typeof data === 'object' && data && 'error' in data
        ? String((data as { error: unknown }).error)
        : `HTTP ${response.status}`
    throw new Error(message)
  }
  return data as T
}

export const api = {
  status: () => request<Status>('/api/status'),

  sessions: () => request<{ sessions: SessionRow[] }>('/api/sessions'),

  transcript: (session: string) =>
    request<Transcript>(`/api/sessions/${encodeURIComponent(session)}`),

  newSession: () => request<{ session_id: string }>('/api/sessions', { method: 'POST', body: '{}' }),

  turn: (session: string | null, text: string) =>
    request<{ session_id: string }>('/api/turn', {
      method: 'POST',
      body: JSON.stringify({ session, text }),
    }),

  approval: (id: string, decision: 'once' | 'always' | 'deny') =>
    request<{ resolved: boolean }>('/api/approval', {
      method: 'POST',
      body: JSON.stringify({ id, decision }),
    }),

  permission: (mode: PermissionMode) =>
    request<{ permission: PermissionMode }>('/api/permission', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),

  provider: () => request<ProviderSettings>('/api/provider'),

  updateProvider: (update: ProviderUpdate) =>
    request<ProviderSettings>('/api/provider', {
      method: 'POST',
      body: JSON.stringify(update),
    }),

  testProvider: () =>
    request<ProviderProbe>('/api/provider/test', { method: 'POST', body: '{}' }),

  syncModels: (base_url?: string, api_key?: string) =>
    request<ModelSyncResult>('/api/models', {
      method: 'POST',
      body: JSON.stringify({ base_url, api_key }),
    }),

  profiles: () => request<Profiles>('/api/profiles'),

  saveProfile: (name: string, update: ProviderUpdate) =>
    request<Profiles>('/api/profiles', {
      method: 'POST',
      body: JSON.stringify({ name, ...update, mark_active: true }),
    }),

  applyProfile: (name: string) =>
    request<ProviderSettings>('/api/profiles/apply', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),

  deleteProfile: (name: string) =>
    request<Profiles>(`/api/profiles/${encodeURIComponent(name)}`, { method: 'DELETE' }),
}
