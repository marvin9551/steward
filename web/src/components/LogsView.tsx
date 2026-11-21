import { useCallback, useEffect, useState } from 'react'
import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Filter,
  Loader2,
  RefreshCw,
  ScrollText,
  Search,
  Trash2,
  X,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { api, type LogCategory, type LogLevel, type LogRow } from '@/lib/api'
import { pretty } from '@/lib/format'
import { cn } from '@/lib/utils'

interface Props {
  initialSessionFilter?: string
  onJumpToSession: (sessionId: string) => void
  onNote: (note: string) => void
}

const CATEGORIES: Array<{ id: '' | LogCategory; label: string }> = [
  { id: '', label: '全部分类' },
  { id: 'agent', label: 'Agent' },
  { id: 'tool', label: '工具' },
  { id: 'channel', label: '渠道' },
  { id: 'system', label: '系统' },
]

const LEVELS: Array<{ id: '' | LogLevel; label: string }> = [
  { id: '', label: '全部级别' },
  { id: 'INFO', label: 'INFO' },
  { id: 'WARN', label: 'WARN' },
  { id: 'ERROR', label: 'ERROR' },
]

const CATEGORY_LABELS: Record<string, string> = {
  agent: 'Agent',
  tool: '工具',
  channel: '渠道',
  system: '系统',
}

export function LogsView({ initialSessionFilter = '', onJumpToSession, onNote }: Props) {
  const [category, setCategory] = useState<'' | LogCategory>('')
  const [level, setLevel] = useState<'' | LogLevel>('')
  const [sessionFilter, setSessionFilter] = useState(initialSessionFilter)
  const [searchInput, setSearchInput] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(false)

  const [logs, setLogs] = useState<LogRow[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())
  const [copiedId, setCopiedId] = useState<number | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearing, setClearing] = useState(false)

  useEffect(() => {
    if (initialSessionFilter !== undefined) {
      setSessionFilter(initialSessionFilter)
    }
  }, [initialSessionFilter])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedQuery(searchInput.trim())
    }, 250)
    return () => window.clearTimeout(timer)
  }, [searchInput])

  const fetchLogs = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true)
      setError('')
      try {
        const res = await api.logs({
          category: category || undefined,
          level: level || undefined,
          session_id: sessionFilter.trim() || undefined,
          q: debouncedQuery || undefined,
          limit: 100,
        })
        setLogs(res.logs)
        setHasMore(res.has_more)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause))
      } finally {
        if (!silent) setLoading(false)
      }
    },
    [category, level, sessionFilter, debouncedQuery],
  )

  useEffect(() => {
    void fetchLogs(false)
  }, [fetchLogs])

  useEffect(() => {
    if (!autoRefresh) return
    const timer = window.setInterval(() => {
      void fetchLogs(true)
    }, 3000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, fetchLogs])

  const loadMore = async () => {
    if (loadingMore || !hasMore || logs.length === 0) return
    const lastId = logs[logs.length - 1].id
    setLoadingMore(true)
    try {
      const res = await api.logs({
        category: category || undefined,
        level: level || undefined,
        session_id: sessionFilter.trim() || undefined,
        q: debouncedQuery || undefined,
        before_id: lastId,
        limit: 100,
      })
      setLogs((prev) => [...prev, ...res.logs])
      setHasMore(res.has_more)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoadingMore(false)
    }
  }

  const handleClear = async () => {
    if (!confirmClear) {
      setConfirmClear(true)
      return
    }
    setClearing(true)
    try {
      const res = await api.clearLogs()
      setLogs([])
      setHasMore(false)
      setExpandedIds(new Set())
      setConfirmClear(false)
      onNote(`已清空 ${res.cleared} 条日志`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setClearing(false)
    }
  }

  const toggleExpand = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const copyDetail = async (row: LogRow, event: React.MouseEvent) => {
    event.stopPropagation()
    try {
      await navigator.clipboard.writeText(pretty(row))
      setCopiedId(row.id)
      window.setTimeout(() => {
        setCopiedId((current) => (current === row.id ? null : current))
      }, 1500)
    } catch {
      onNote('复制失败')
    }
  }

  const resetFilters = () => {
    setCategory('')
    setLevel('')
    setSessionFilter('')
    setSearchInput('')
    setDebouncedQuery('')
  }

  const hasFilters = Boolean(category || level || sessionFilter.trim() || searchInput.trim())

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
      {/* Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border/70 px-7 py-3.5">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-secondary">
            <ScrollText className="h-4 w-4 text-foreground" />
          </div>
          <div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-[19px] font-semibold text-foreground">运行日志</h2>
              <Badge variant="secondary" className="font-mono text-[14px]">
                {logs.length}
                {hasMore ? '+' : ''} 条
              </Badge>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-[15px] text-muted-foreground hover:text-foreground">
            <span>自动刷新</span>
            <Switch checked={autoRefresh} onCheckedChange={setAutoRefresh} />
          </label>

          <Button
            variant="outline"
            size="sm"
            onClick={() => void fetchLogs(false)}
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            刷新
          </Button>

          <Button
            variant={confirmClear ? 'default' : 'outline'}
            size="sm"
            disabled={clearing || logs.length === 0}
            className={cn(confirmClear && 'bg-brand text-white hover:bg-brand/90')}
            onClick={() => void handleClear()}
            onBlur={() => setConfirmClear(false)}
          >
            {clearing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Trash2 className="h-4 w-4" />
            )}
            {confirmClear ? '确认清空' : '清空日志'}
          </Button>
        </div>
      </div>

      {/* Filter Bar */}
      <div className="flex flex-col gap-3 border-b border-border/70 bg-surface/50 px-7 py-3.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* Category pills */}
          <div className="flex flex-wrap items-center gap-1.5">
            {CATEGORIES.map((item) => (
              <button
                key={item.id || 'all'}
                type="button"
                onClick={() => setCategory(item.id)}
                className={cn(
                  'rounded-lg px-3 py-1.5 text-[15px] font-medium transition-colors',
                  category === item.id
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-secondary/80 text-muted-foreground hover:bg-secondary hover:text-foreground',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>

          {/* Level pills */}
          <div className="flex flex-wrap items-center gap-1.5">
            {LEVELS.map((item) => (
              <button
                key={item.id || 'all-level'}
                type="button"
                onClick={() => setLevel(item.id)}
                className={cn(
                  'rounded-lg px-2.5 py-1 text-[14px] font-semibold transition-colors',
                  level === item.id
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-secondary/80 text-muted-foreground hover:bg-secondary hover:text-foreground',
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {/* Search & Session filter inputs */}
        <div className="grid gap-3 sm:grid-cols-[1fr_260px_auto]">
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="搜索日志内容、事件名（如 tool.result）、工具名或错误信息…"
              className="pl-9 pr-8"
            />
            {searchInput ? (
              <button
                type="button"
                onClick={() => setSearchInput('')}
                className="absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            ) : null}
          </div>

          <div className="relative">
            <Filter className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={sessionFilter}
              onChange={(event) => setSessionFilter(event.target.value)}
              placeholder="按会话 ID 过滤…"
              className="pl-9 pr-8 font-mono text-[15px]"
            />
            {sessionFilter ? (
              <button
                type="button"
                onClick={() => setSessionFilter('')}
                className="absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            ) : null}
          </div>

          {hasFilters ? (
            <Button variant="ghost" size="sm" onClick={resetFilters} className="h-10">
              重置筛选
            </Button>
          ) : null}
        </div>
      </div>

      {/* Error banner */}
      {error ? (
        <div className="mx-7 mt-4 rounded-md border border-border border-l-4 border-l-brand bg-brand-soft px-3.5 py-2.5 text-[16px] text-foreground">
          {error}
        </div>
      ) : null}

      {/* Log List */}
      <div className="min-h-0 flex-1 overflow-y-auto px-7 py-4">
        {loading && logs.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-16 text-[16px] text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            正在加载日志…
          </div>
        ) : logs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-border bg-secondary/30 px-6 py-12 text-center">
            <p className="text-[18px] font-medium text-foreground">暂无匹配的日志记录</p>
            <p className="mt-1 text-[15px] text-muted-foreground">
              {hasFilters
                ? '可以尝试调整分类、级别或清空搜索条件'
                : '当发起对话、调用工具、接收飞书消息或修改设置时，日志会自动记录在这里'}
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {logs.map((row) => {
              const expanded = expandedIds.has(row.id)
              const detailKeys = Object.keys(row.detail || {})
              return (
                <div
                  key={row.id}
                  className={cn(
                    'rounded-xl border border-border/80 bg-card transition-colors',
                    row.level === 'ERROR' && 'border-l-4 border-l-brand',
                    row.level === 'WARN' && 'border-l-4 border-l-brand/60',
                  )}
                >
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => toggleExpand(row.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        toggleExpand(row.id)
                      }
                    }}
                    className="flex cursor-pointer flex-wrap items-center gap-2.5 px-4 py-2.5 hover:bg-secondary/40"
                  >
                    {expanded ? (
                      <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                    )}

                    <span className="shrink-0 font-mono text-[14px] text-muted-foreground">
                      {formatLogTime(row.created_at)}
                    </span>

                    <LevelBadge level={row.level} />

                    <Badge variant="outline" className="shrink-0 font-mono text-[14px]">
                      {CATEGORY_LABELS[row.category] ?? row.category} · {row.event}
                    </Badge>

                    {row.session_id ? (
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation()
                          setSessionFilter(row.session_id)
                        }}
                        title="点击仅筛选此会话的日志"
                        className="shrink-0 rounded-md bg-secondary px-2 py-0.5 font-mono text-[14px] text-foreground transition-colors hover:bg-primary hover:text-primary-foreground"
                      >
                        {row.session_id}
                      </button>
                    ) : null}

                    <span className="min-w-0 flex-1 truncate text-[16px] text-foreground">
                      {row.message}
                    </span>

                    {detailKeys.length > 0 ? (
                      <span className="shrink-0 font-mono text-[13px] text-muted-foreground">
                        {detailKeys.length} 项详情
                      </span>
                    ) : null}
                  </div>

                  {expanded ? (
                    <div className="border-t border-border/70 bg-secondary/30 px-4 py-3">
                      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                        <div className="flex flex-wrap items-center gap-3 text-[14px] text-muted-foreground">
                          <span>
                            ID: <code className="font-mono text-foreground">#{row.id}</code>
                          </span>
                          <span>
                            时间 (UTC):{' '}
                            <code className="font-mono text-foreground">{row.created_at}</code>
                          </span>
                          {row.session_id ? (
                            <button
                              type="button"
                              onClick={() => onJumpToSession(row.session_id)}
                              className="inline-flex items-center gap-1 font-medium text-info hover:underline"
                            >
                              跳转到会话 {row.session_id}
                              <ExternalLink className="h-3.5 w-3.5" />
                            </button>
                          ) : null}
                        </div>

                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => void copyDetail(row, e)}
                          className="h-7 px-2 text-[14px]"
                        >
                          {copiedId === row.id ? (
                            <>
                              <Check className="h-3.5 w-3.5 text-success" />
                              已复制
                            </>
                          ) : (
                            <>
                              <Copy className="h-3.5 w-3.5" />
                              复制 JSON
                            </>
                          )}
                        </Button>
                      </div>

                      <p className="mb-2 whitespace-pre-wrap break-words text-[16px] font-medium text-foreground">
                        {row.message}
                      </p>

                      {detailKeys.length > 0 ? (
                        <pre className="max-h-80 overflow-auto rounded-lg border border-border bg-background p-3 font-mono text-[14px] leading-relaxed text-foreground">
                          {pretty(row.detail)}
                        </pre>
                      ) : (
                        <p className="text-[14px] text-muted-foreground">（无额外结构化字段）</p>
                      )}
                    </div>
                  ) : null}
                </div>
              )
            })}

            {hasMore ? (
              <div className="pt-3 text-center">
                <Button variant="outline" onClick={() => void loadMore()} disabled={loadingMore}>
                  {loadingMore ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                  加载更多历史日志
                </Button>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}

function LevelBadge({ level }: { level: LogLevel }) {
  if (level === 'ERROR') {
    return (
      <Badge variant="warning" className="shrink-0 font-mono text-[13px]">
        ERROR
      </Badge>
    )
  }
  if (level === 'WARN') {
    return (
      <Badge variant="warning" className="shrink-0 font-mono text-[13px] opacity-85">
        WARN
      </Badge>
    )
  }
  return (
    <Badge variant="info" className="shrink-0 font-mono text-[13px]">
      INFO
    </Badge>
  )
}

function formatLogTime(utc: string): string {
  if (!utc) return ''
  const parsed = new Date(`${utc.replace(' ', 'T')}Z`)
  if (Number.isNaN(parsed.getTime())) return utc
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`
}
