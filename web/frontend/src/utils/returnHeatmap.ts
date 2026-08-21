import type { NetValuePayload } from '../types/library'

export type ReturnHeatmapGranularity = 'week' | 'month' | 'quarter'
export type ReturnHeatmapMetricMode = 'return' | 'excess'
export type ReturnHeatmapScope = 'detail' | 'compare'

export interface ReturnHeatmapOption<T extends string> {
  label: string
  value: T
}

export const DEFAULT_RETURN_HEATMAP_GRANULARITY: ReturnHeatmapGranularity = 'month'
export const DEFAULT_RETURN_HEATMAP_METRIC_MODE: ReturnHeatmapMetricMode = 'return'

export const RETURN_HEATMAP_GRANULARITY_OPTIONS: ReadonlyArray<ReturnHeatmapOption<ReturnHeatmapGranularity>> = [
  { label: '按周', value: 'week' },
  { label: '按月', value: 'month' },
  { label: '按季', value: 'quarter' },
]

export const RETURN_HEATMAP_METRIC_OPTIONS: ReadonlyArray<ReturnHeatmapOption<ReturnHeatmapMetricMode>> = [
  { label: '收益分布', value: 'return' },
  { label: '超额分布', value: 'excess' },
]

interface NormalizedPoint {
  date: Date
  dateText: string
  strategyValue: number | null
  excessValue: number | null
}

interface PeriodMeta {
  year: number
  label: string
  order: number
  displayText: string
}

export interface ReturnHeatmapSeriesPoint {
  yearLabel: string
  periodLabel: string
  displayText: string
  startDate: string
  endDate: string
  value: number
}

export interface ReturnHeatmapData {
  xAxisLabels: string[]
  yearLabels: string[]
  seriesData: Array<[number, number, number, string, string, string, string, string]>
  maxAbs: number
}

export function buildReturnHeatmapData(
  payload: NetValuePayload | null | undefined,
  granularity: ReturnHeatmapGranularity,
  metricMode: ReturnHeatmapMetricMode,
  initialValue: number | null = null,
): ReturnHeatmapData | null {
  const sourcePoints = normalizePoints(payload)
  const valueField = metricMode === 'excess' ? 'excessValue' : 'strategyValue'
  const filteredPoints = sourcePoints.filter(point => point[valueField] != null)
  if (!filteredPoints.length) return null

  const groups = new Map<string, {
    yearLabel: string
    periodLabel: string
    displayText: string
    startValue: number
    endValue: number
    startDate: string
    endDate: string
  }>()

  const normalizedInitialValue = readFiniteNumber(initialValue)
  let previousPointValue: number | null = null
  let previousPointDateText = ''
  for (const point of filteredPoints) {
    const pointValue = point[valueField]
    if (pointValue == null) continue
    const meta = resolvePeriodMeta(point.date, granularity)
    const key = `${meta.year}-${meta.order}`
    const current = groups.get(key)
    if (!current) {
      const periodStartValue = previousPointValue ?? pointValue
      const startValue = previousPointValue == null
        ? normalizedInitialValue ?? periodStartValue
        : periodStartValue
      const startDate = previousPointValue != null ? previousPointDateText : point.dateText
      groups.set(key, {
        yearLabel: String(meta.year),
        periodLabel: meta.label,
        displayText: meta.displayText,
        startValue,
        endValue: pointValue,
        startDate,
        endDate: point.dateText,
      })
      previousPointValue = pointValue
      previousPointDateText = point.dateText
      continue
    }
    current.endValue = pointValue
    current.endDate = point.dateText
    previousPointValue = pointValue
    previousPointDateText = point.dateText
  }

  const xAxisLabels = resolveXAxisLabels(granularity)
  const yearLabels = Array.from(new Set(Array.from(groups.values()).map(item => item.yearLabel)))
    .sort((left, right) => Number(right) - Number(left))

  const cells: ReturnHeatmapSeriesPoint[] = Array.from(groups.values())
    .map(item => {
      const base = item.startValue
      const end = item.endValue
      const periodReturn = base !== 0 ? (end / base) - 1 : 0
      return {
        yearLabel: item.yearLabel,
        periodLabel: item.periodLabel,
        displayText: item.displayText,
        startDate: item.startDate,
        endDate: item.endDate,
        value: Number((periodReturn * 100).toFixed(2)),
      }
    })
    .filter(item => xAxisLabels.includes(item.periodLabel) && yearLabels.includes(item.yearLabel))

  if (!cells.length) return null

  return {
    xAxisLabels,
    yearLabels,
    seriesData: cells.map(item => {
      const xIndex = xAxisLabels.indexOf(item.periodLabel)
      const yIndex = yearLabels.indexOf(item.yearLabel)
      return [
        xIndex,
        yIndex,
        item.value,
        item.displayText,
        item.startDate,
        item.endDate,
        item.periodLabel,
        item.yearLabel,
      ]
    }),
    maxAbs: Math.max(1, ...cells.map(item => Math.abs(item.value))),
  }
}

export function hasReturnHeatmapExcessSeries(payload: NetValuePayload | null | undefined): boolean {
  return normalizePoints(payload).some(point => point.excessValue != null)
}

export function metricModeLabel(value: ReturnHeatmapMetricMode): string {
  return value === 'excess' ? '超额分布' : '收益分布'
}

export function granularityLabel(value: ReturnHeatmapGranularity): string {
  if (value === 'week') return '周'
  if (value === 'quarter') return '季'
  return '月'
}

export function buildReturnHeatmapPanelNote(options: {
  scope: ReturnHeatmapScope
  granularity: ReturnHeatmapGranularity
  metricMode: ReturnHeatmapMetricMode
  hasExcessSeries: boolean
}): string {
  const defaultText = `默认按${granularityLabel(DEFAULT_RETURN_HEATMAP_GRANULARITY)}查看${metricModeLabel(DEFAULT_RETURN_HEATMAP_METRIC_MODE)}`
  const currentText = `当前为按${granularityLabel(options.granularity)}查看${metricModeLabel(options.metricMode)}`
  const scopeText = options.scope === 'compare'
    ? '单元格收益按分段累计净值变化计算，所有卡片共享颜色范围，便于横向比较。'
    : '单元格收益按分段累计净值变化计算。'

  if (options.metricMode === 'excess' && !options.hasExcessSeries) {
    const fallbackScopeText = options.scope === 'compare'
      ? '当前对比策略未提供超额曲线，已自动回退到收益分布。'
      : '当前净值数据未提供超额曲线，已自动回退到收益分布。'
    return `${defaultText}；${fallbackScopeText}`
  }

  return `${defaultText}；${currentText}；${scopeText}`
}

export function formatReturnHeatmapPercent(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
}

export function resolveXAxisLabels(value: ReturnHeatmapGranularity): string[] {
  if (value === 'week') {
    return Array.from({ length: 53 }, (_, index) => `W${String(index + 1).padStart(2, '0')}`)
  }
  if (value === 'quarter') {
    return ['Q1', 'Q2', 'Q3', 'Q4']
  }
  return Array.from({ length: 12 }, (_, index) => String(index + 1).padStart(2, '0'))
}

export function resolveIsoWeek(date: Date): { year: number; week: number } {
  const utcDate = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()))
  const day = utcDate.getUTCDay() || 7
  utcDate.setUTCDate(utcDate.getUTCDate() + 4 - day)
  const yearStart = new Date(Date.UTC(utcDate.getUTCFullYear(), 0, 1))
  const week = Math.ceil((((utcDate.getTime() - yearStart.getTime()) / 86400000) + 1) / 7)
  return {
    year: utcDate.getUTCFullYear(),
    week,
  }
}

function normalizePoints(payload: NetValuePayload | null | undefined): NormalizedPoint[] {
  const dates = Array.isArray(payload?.dates) ? payload.dates : []
  const strategyValues = Array.isArray(payload?.net_value) ? payload.net_value : []
  const excessValues = Array.isArray(payload?.excess_net_value) ? payload.excess_net_value : []

  const byDate = new Map<string, NormalizedPoint>()
  for (let index = 0; index < dates.length; index += 1) {
    const dateText = normalizeDateText(dates[index])
    const pointDate = parseTradeDate(dateText)
    if (!pointDate) continue
    byDate.set(dateText, {
      date: pointDate,
      dateText,
      strategyValue: readFiniteNumber(strategyValues[index]),
      excessValue: readFiniteNumber(excessValues[index]),
    })
  }
  return Array.from(byDate.values()).sort((left, right) => (
    left.date.getTime() - right.date.getTime()
  ))
}

function readFiniteNumber(value: unknown): number | null {
  if (value == null || value === '') return null
  const num = Number(value)
  return Number.isFinite(num) ? num : null
}

function parseTradeDate(value: unknown): Date | null {
  const text = normalizeDateText(value)
  if (!text) return null

  // Date-only strings parse as UTC; local noon keeps calendar getters on the API date.
  const parseText = /^\d{4}-\d{2}-\d{2}$/.test(text) ? `${text}T12:00:00` : text
  const date = new Date(parseText)
  return Number.isNaN(date.getTime()) ? null : date
}

function normalizeDateText(value: unknown): string {
  const text = String(value || '').trim()
  if (!text) return ''
  if (/^\d{8}$/.test(text)) {
    return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  }
  return text.replace('T', ' ').slice(0, 10)
}

function resolvePeriodMeta(date: Date, value: ReturnHeatmapGranularity): PeriodMeta {
  if (value === 'week') {
    const info = resolveIsoWeek(date)
    const label = `W${String(info.week).padStart(2, '0')}`
    return {
      year: info.year,
      label,
      order: info.week,
      displayText: `${info.year} ${label}`,
    }
  }

  const month = date.getMonth() + 1
  if (value === 'quarter') {
    const quarter = Math.floor((month - 1) / 3) + 1
    return {
      year: date.getFullYear(),
      label: `Q${quarter}`,
      order: quarter,
      displayText: `${date.getFullYear()} Q${quarter}`,
    }
  }

  const label = String(month).padStart(2, '0')
  return {
    year: date.getFullYear(),
    label,
    order: month,
    displayText: `${date.getFullYear()}-${label}`,
  }
}
