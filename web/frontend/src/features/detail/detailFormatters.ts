export function metricTrendClass(value: unknown): string {
  const num = readNumber(value)
  if (num == null) return ''
  if (num > 0) return 'metric-up'
  if (num < 0) return 'metric-down'
  return ''
}

export function metricQualityClass(value: unknown, goodThreshold: number, weakThreshold: number): string {
  const num = readNumber(value)
  if (num == null) return ''
  if (num >= goodThreshold) return 'metric-up'
  if (num < weakThreshold) return 'metric-down'
  return ''
}

export function scaleMetricProgress(value: unknown, min: number, max: number): number {
  const num = readNumber(value)
  if (num == null || min === max) return 0
  const ratio = (num - min) / (max - min)
  return Math.round(Math.max(0, Math.min(1, ratio)) * 100)
}

export function buildReturnTone(value: unknown): string {
  const num = readNumber(value)
  if (num == null) return '待评估'
  if (num >= 0.3) return '强收益'
  if (num >= 0.1) return '稳健'
  if (num >= 0) return '温和'
  return '承压'
}

export function buildDrawdownTone(value: unknown): string {
  const num = Math.abs(readNumber(value) ?? Number.NaN)
  if (!Number.isFinite(num)) return '待评估'
  if (num < 0.1) return '低风险'
  if (num <= 0.2) return '中风险'
  return '高风险'
}

export function buildSharpeTone(value: unknown): string {
  const num = readNumber(value)
  if (num == null) return '待评估'
  if (num >= 2) return '卓越'
  if (num >= 1.5) return '优秀'
  if (num >= 1) return '良好'
  if (num >= 0.5) return '一般'
  return '偏弱'
}

export function buildWinRateTone(value: unknown): string {
  const num = readNumber(value)
  if (num == null) return '待评估'
  if (num >= 0.6) return '高胜率'
  if (num >= 0.5) return '均衡'
  if (num >= 0.45) return '偏低'
  return '低胜率'
}

export function buildSampleTone(value: unknown): string {
  const num = readNumber(value)
  if (num == null) return '待评估'
  if (num >= 200) return '样本充足'
  if (num >= 50) return '样本可用'
  if (num > 0) return '样本偏少'
  return '无交易'
}

export function formatPercent(value: unknown, digits = 2): string {
  const num = readNumber(value)
  if (num == null) return '-'
  return `${(num * 100).toFixed(digits)}%`
}

export function formatDrawdown(value: unknown): string {
  const num = readNumber(value)
  if (num == null) return '-'
  return `-${(Math.abs(num) * 100).toFixed(2)}%`
}

export function normalizeDrawdownValue(value: unknown): number | null {
  const num = readNumber(value)
  return num == null ? null : Math.abs(num)
}

export function formatMoney(value: unknown): string {
  const num = readNumber(value)
  if (num == null) return '-'
  return num.toFixed(2)
}

export function formatSignedMoney(value: unknown): string {
  const num = readNumber(value)
  if (num == null) return '-'
  return num > 0 ? `+${num.toFixed(2)}` : num.toFixed(2)
}

export function formatCurrencyUnit(value: unknown, digits: number): string {
  const num = readNumber(value)
  if (num == null) return '-'
  return num.toFixed(digits)
}

export function formatInteger(value: unknown): string {
  const num = readNumber(value)
  if (num == null) return '-'
  return `${Math.round(num)}`
}

export function formatPlainNumber(value: unknown, digits: number): string {
  const num = readNumber(value)
  if (num == null) return '-'
  return num.toFixed(digits)
}

export function formatDateText(value: unknown): string {
  const text = String(value || '').trim()
  if (!text) return ''
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  return text.replace('T', ' ').slice(0, 10)
}

export function formatDateForInput(date: Date): string {
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function normalizeDateKey(value: unknown): string {
  const text = String(value || '').trim()
  if (!text) return ''
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  const normalized = text.replace('T', ' ').slice(0, 10)
  if (/^\d{4}-\d{2}-\d{2}$/.test(normalized)) return normalized
  const date = new Date(text)
  if (Number.isNaN(date.getTime())) return ''
  const year = date.getFullYear()
  const month = `${date.getMonth() + 1}`.padStart(2, '0')
  const day = `${date.getDate()}`.padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function inferHoldingDays(entryValue: unknown, exitValue: unknown): number {
  const entry = parseLooseDate(entryValue)
  const exit = parseLooseDate(exitValue)
  if (!(entry && exit)) return 0
  const msPerDay = 24 * 60 * 60 * 1000
  return Math.max(0, Math.round((exit.getTime() - entry.getTime()) / msPerDay))
}

export function parseLooseDate(value: unknown): Date | null {
  const text = String(value || '').trim()
  if (!text) return null
  const normalized = /^\d{8}$/.test(text)
    ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
    : text.replace('T', ' ').slice(0, 10)
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function readBoolean(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value
  if (typeof value === 'number' && Number.isFinite(value)) return value !== 0
  const text = readString(value).toLowerCase()
  if (!text) return null
  if (['true', '1', 'yes', 'on', 'enabled'].includes(text)) return true
  if (['false', '0', 'no', 'off', 'disabled'].includes(text)) return false
  return null
}

export function readNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export function readRateValue(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const text = String(value || '').trim()
  if (!text) return null
  if (text.endsWith('%')) {
    const parsedPercent = Number(text.slice(0, -1))
    return Number.isFinite(parsedPercent) ? parsedPercent / 100 : null
  }
  const parsed = Number(text)
  return Number.isFinite(parsed) ? parsed : null
}

export function formatHoldingRatio(value: unknown): string {
  return formatPercent(value)
}

export function formatHoldingShares(value: unknown, scaled = false): string {
  const base = formatInteger(value)
  if (!scaled || base === '-') return base
  return `${base}（风控缩放）`
}

export function buildHoldingStockText(codeValue: unknown, nameValue: unknown, pnlValue: unknown): string {
  const code = String(codeValue || '').trim()
  const name = String(nameValue || '').trim()
  const base = [code, name].filter(Boolean).join(' ') || '-'
  const pnl = readNumber(pnlValue)
  if (pnl == null) return base
  if (pnl > 0) return `${base} · 盈利 ${formatSignedMoney(pnl)}`
  if (pnl < 0) return `${base} · 亏损 ${formatSignedMoney(pnl)}`
  return `${base} · 持平 0.00`
}

export function buildSlotStockListText(items: unknown, fallbackText: unknown): string {
  if (Array.isArray(items) && items.length) {
    return items.map(item => buildHoldingStockText(
      isRecord(item) ? item.code : '',
      isRecord(item) ? item.name : '',
      isRecord(item) ? item.unrealized_pnl : null,
    )).join('；')
  }
  const fallback = String(fallbackText || '').trim()
  return fallback || '-'
}
