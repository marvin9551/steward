export function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function oneLine(value: unknown, limit = 120): string {
  let text: string
  try {
    text = JSON.stringify(value)
  } catch {
    text = String(value)
  }
  if (!text || text === '{}') return '（无参数）'
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

export function relativeTime(utc: string): string {
  if (!utc) return ''
  const parsed = new Date(`${utc.replace(' ', 'T')}Z`)
  if (Number.isNaN(parsed.getTime())) return utc
  const minutes = Math.round((Date.now() - parsed.getTime()) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.round(hours / 24)} 天前`
}
