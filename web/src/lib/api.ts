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
  | { type: 'resync' }

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
}
