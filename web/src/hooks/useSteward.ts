import { useCallback, useEffect, useReducer, useRef, useState } from 'react'

import {
  api,
  type PermissionMode,
  type ProviderSettings,
  type ProviderUpdate,
  type SessionRow,
  type Status,
  type StreamEvent,
} from '@/lib/api'
import { blocksFromTranscript, reducer, type Block } from '@/lib/blocks'

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/**
 * All client state in one place: configuration, the session list, the current
 * transcript, and the SSE connection.
 *
 * The transcript is only ever changed in two ways — a full reset from
 * `GET /api/sessions/:id`, or one event through `reduce` — which is what keeps
 * history and live streaming from drifting apart.
 */
export function useSteward() {
  const [status, setStatus] = useState<Status | null>(null)
  const [provider, setProvider] = useState<ProviderSettings | null>(null)
  const [sessions, setSessions] = useState<SessionRow[]>([])
  const [session, setSessionState] = useState<string | null>(null)
  const [blocks, dispatch] = useReducer(reducer, [] as Block[])
  const [busy, setBusyState] = useState(false)
  const [connected, setConnected] = useState(false)
  const [note, setNote] = useState('')

  const sessionRef = useRef<string | null>(null)
  const busyRef = useRef(false)

  const setSession = useCallback((value: string | null) => {
    sessionRef.current = value
    setSessionState(value)
  }, [])

  const setBusy = useCallback((value: boolean) => {
    busyRef.current = value
    setBusyState(value)
  }, [])

  const refreshSessions = useCallback(async () => {
    try {
      const data = await api.sessions()
      setSessions(data.sessions)
    } catch (error) {
      setNote(`会话列表加载失败：${describeError(error)}`)
    }
  }, [])

  const loadTranscript = useCallback(
    async (id: string) => {
      const data = await api.transcript(id)
      setSession(data.session)
      dispatch({ type: 'reset', blocks: blocksFromTranscript(data.blocks) })
    },
    [setSession],
  )

  const reloadCurrent = useCallback(async () => {
    const current = sessionRef.current
    if (!current) return
    try {
      await loadTranscript(current)
    } catch (error) {
      setNote(`刷新记录失败：${describeError(error)}`)
    }
  }, [loadTranscript])

  const handleEvent = useCallback(
    (event: StreamEvent) => {
      switch (event.type) {
        case 'turn_start':
          setSession(event.session)
          setBusy(true)
          setNote('模型正在处理…')
          break
        case 'turn_done':
          setNote(turnNote(event))
          break
        case 'idle':
          setBusy(false)
          void refreshSessions()
          break
        case 'error':
          setNote('出错了')
          break
        case 'approval_request':
          setNote(`等待确认 ${event.name}…`)
          break
        case 'approval_resolved':
          dispatch({ type: 'answered', approvalId: event.id, decision: event.decision })
          break
        case 'config':
          setStatus((current) => (current ? { ...current, permission: event.permission } : current))
          break
        case 'provider': {
          const { type: _type, ...settings } = event
          setProvider(settings)
          break
        }
        case 'resync':
          setNote('连接中断过久，已重新载入会话记录')
          void reloadCurrent()
          void refreshSessions()
          break
        default:
          break
      }
      dispatch({ type: 'event', event })
    },
    [refreshSessions, reloadCurrent, setBusy, setSession],
  )

  useEffect(() => {
    let cancelled = false
    void (async () => {
      // The page is served from disk on every request, so it can be newer than
      // the process behind it. Fetching these independently keeps a missing
      // /api/provider from taking down the rest of the page.
      const [statusResult, providerResult] = await Promise.allSettled([
        api.status(),
        api.provider(),
      ])
      if (cancelled) return

      if (statusResult.status === 'fulfilled') {
        setStatus(statusResult.value)
        setBusy(statusResult.value.busy)
        if (statusResult.value.latest_session) {
          await loadTranscript(statusResult.value.latest_session)
        }
      } else {
        setNote(`无法连接后端：${describeError(statusResult.reason)}`)
      }

      if (providerResult.status === 'fulfilled') {
        setProvider(providerResult.value)
      } else if (statusResult.status === 'fulfilled') {
        setNote('后端还没有 /api/provider（进程是旧版本），重启 steward web 后即可配置')
      }

      if (!cancelled) await refreshSessions()
    })()
    return () => {
      cancelled = true
    }
  }, [loadTranscript, refreshSessions, setBusy])

  useEffect(() => {
    const source = new EventSource('/api/stream')
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.onmessage = (raw) => {
      let event: StreamEvent
      try {
        event = JSON.parse(raw.data) as StreamEvent
      } catch {
        return
      }
      handleEvent(event)
    }
    return () => source.close()
  }, [handleEvent])

  const send = useCallback(
    async (text: string): Promise<boolean> => {
      const trimmed = text.trim()
      if (!trimmed || busyRef.current) return false
      setBusy(true)
      setNote('模型正在处理…')
      try {
        const data = await api.turn(sessionRef.current, trimmed)
        setSession(data.session_id)
        void refreshSessions()
        return true
      } catch (error) {
        setBusy(false)
        setNote(describeError(error))
        dispatch({ type: 'event', event: { type: 'error', text: describeError(error) } })
        return false
      }
    },
    [refreshSessions, setBusy, setSession],
  )

  const newSession = useCallback(async () => {
    if (busyRef.current) {
      setNote('当前对话还在进行，完成后再新建会话')
      return
    }
    try {
      const data = await api.newSession()
      setSession(data.session_id)
      dispatch({ type: 'reset', blocks: [] })
      setNote('')
    } catch (error) {
      setNote(describeError(error))
    }
  }, [setSession])

  const selectSession = useCallback(
    async (id: string) => {
      if (busyRef.current) {
        setNote('当前对话还在进行，完成后再切换会话')
        return
      }
      if (id === sessionRef.current) return
      try {
        await loadTranscript(id)
        setNote('')
      } catch (error) {
        setNote(describeError(error))
      }
    },
    [loadTranscript],
  )

  const decide = useCallback(async (approvalId: string, key: string, decision: 'once' | 'always' | 'deny') => {
    dispatch({ type: 'decision', key, decision })
    try {
      await api.approval(approvalId, decision)
    } catch (error) {
      setNote(`提交批准失败：${describeError(error)}`)
    }
  }, [])

  const setPermission = useCallback(async (mode: PermissionMode) => {
    try {
      const data = await api.permission(mode)
      setStatus((current) => (current ? { ...current, permission: data.permission } : current))
      setNote(`权限模式：${data.permission}`)
    } catch (error) {
      setNote(describeError(error))
    }
  }, [])

  const saveProvider = useCallback(async (update: ProviderUpdate) => {
    const result = await api.updateProvider(update)
    setProvider(result)
    return result
  }, [])

  const testProvider = useCallback(async () => api.testProvider(), [])

  return {
    status,
    provider,
    sessions,
    session,
    blocks,
    busy,
    connected,
    note,
    setNote,
    send,
    newSession,
    selectSession,
    decide,
    setPermission,
    saveProvider,
    testProvider,
  }
}

function turnNote(event: { steps: number; usage: Record<string, number> }): string {
  const bits = ['完成']
  if (event.steps) bits.push(`${event.steps} 步`)
  const input = event.usage?.prompt_tokens ?? event.usage?.input_tokens
  const output = event.usage?.completion_tokens ?? event.usage?.output_tokens
  if (input || output) bits.push(`tokens ${input ?? 0}↑ ${output ?? 0}↓`)
  return bits.join(' · ')
}
