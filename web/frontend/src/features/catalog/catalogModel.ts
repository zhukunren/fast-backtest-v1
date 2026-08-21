import type {
  CatalogEvaluationSummary,
  EvaluationWindow,
  FilterOption,
  StrategyLibraryItem,
} from '../../types/library'

export const CATALOG_SORT_MODES = [
  'risk_adjusted',
  'sharpe_desc',
  'calmar_desc',
  'ann_return_desc',
  'drawdown_asc',
  'return_desc',
  'win_desc',
  'trades_desc',
  'updated_desc',
  'name',
] as const

export type CatalogSortMode = typeof CATALOG_SORT_MODES[number]
export type CatalogItemState = 'ready' | 'failed' | 'stale' | 'idle'

export interface CatalogStatusCounts {
  valid: number
  failed: number
  stale: number
  idle: number
}

export interface EvaluationComparability {
  status: 'comparable' | 'incompatible' | 'unknown'
  label: string
  windowText: string
  detail: string
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function compareFiniteDescending(left: unknown, right: unknown): number {
  const leftValue = finiteNumber(left)
  const rightValue = finiteNumber(right)
  if (leftValue == null && rightValue == null) return 0
  if (leftValue == null) return 1
  if (rightValue == null) return -1
  return rightValue - leftValue
}

function compareDrawdown(left: StrategyLibraryItem, right: StrategyLibraryItem): number {
  const leftValue = finiteNumber(left.max_drawdown)
  const rightValue = finiteNumber(right.max_drawdown)
  if (leftValue == null && rightValue == null) return 0
  if (leftValue == null) return 1
  if (rightValue == null) return -1
  return Math.abs(leftValue) - Math.abs(rightValue)
}

function compareName(left: StrategyLibraryItem, right: StrategyLibraryItem): number {
  return String(left.title || left.name).localeCompare(String(right.title || right.name), 'zh-CN')
}

function fallbackRiskAdjustedScore(item: StrategyLibraryItem): number | null {
  const annualReturn = finiteNumber(item.ann_return)
  const drawdown = finiteNumber(item.max_drawdown)
  if (annualReturn == null || drawdown == null || Math.abs(drawdown) < 1e-9) return null
  return annualReturn / Math.abs(drawdown)
}

function riskAdjustedScore(item: StrategyLibraryItem): number | null {
  return finiteNumber(item.sharpe)
    ?? finiteNumber(item.calmar)
    ?? fallbackRiskAdjustedScore(item)
}

function compareRiskAdjusted(left: StrategyLibraryItem, right: StrategyLibraryItem): number {
  const byScore = compareFiniteDescending(riskAdjustedScore(left), riskAdjustedScore(right))
  if (byScore) return byScore
  const byDrawdown = compareDrawdown(left, right)
  if (byDrawdown) return byDrawdown
  const byAnnualReturn = compareFiniteDescending(left.ann_return, right.ann_return)
  if (byAnnualReturn) return byAnnualReturn
  return compareName(left, right)
}

export function sortCatalogItems(items: StrategyLibraryItem[], mode: CatalogSortMode): StrategyLibraryItem[] {
  const rows = items.slice()
  if (mode === 'risk_adjusted') return rows.sort(compareRiskAdjusted)
  if (mode === 'sharpe_desc') return rows.sort((left, right) => compareFiniteDescending(left.sharpe, right.sharpe) || compareRiskAdjusted(left, right))
  if (mode === 'calmar_desc') return rows.sort((left, right) => compareFiniteDescending(left.calmar, right.calmar) || compareRiskAdjusted(left, right))
  if (mode === 'ann_return_desc') return rows.sort((left, right) => compareFiniteDescending(left.ann_return, right.ann_return) || compareRiskAdjusted(left, right))
  if (mode === 'drawdown_asc') return rows.sort((left, right) => compareDrawdown(left, right) || compareRiskAdjusted(left, right))
  if (mode === 'return_desc') return rows.sort((left, right) => compareFiniteDescending(left.cum_return, right.cum_return) || compareRiskAdjusted(left, right))
  if (mode === 'win_desc') return rows.sort((left, right) => compareFiniteDescending(left.win_rate, right.win_rate) || compareRiskAdjusted(left, right))
  if (mode === 'trades_desc') return rows.sort((left, right) => compareFiniteDescending(left.trade_count, right.trade_count) || compareRiskAdjusted(left, right))
  if (mode === 'updated_desc') {
    return rows.sort((left, right) => String(right.timestamp || '').localeCompare(String(left.timestamp || '')) || compareRiskAdjusted(left, right))
  }
  return rows.sort(compareName)
}

export function rawCatalogItemStatus(item: StrategyLibraryItem): string {
  const cacheStatus = String(item.cache_status || '').trim().toLowerCase()
  const status = String(item.status || '').trim().toLowerCase()
  if (cacheStatus === 'valid') return 'valid'
  if (cacheStatus === 'invalid' || cacheStatus === 'stale') return 'stale'
  if (cacheStatus.includes('failed') || cacheStatus === 'error') return cacheStatus
  if (cacheStatus === 'missing') return status || 'missing'
  return cacheStatus || status
}

export function catalogItemState(item: StrategyLibraryItem): CatalogItemState {
  const status = rawCatalogItemStatus(item)
  if (status === 'stale') return 'stale'
  if (status.includes('failed') || status === 'error' || Boolean(String(item.error || '').trim())) return 'failed'
  if (status === 'completed' || status === 'cached' || status === 'valid' || status === 'ready' || status === 'no_signal') return 'ready'
  return 'idle'
}

export function matchesCatalogStatus(item: StrategyLibraryItem, filter: string): boolean {
  if (!filter || filter === 'all') return true
  const requested = filter.toLowerCase()
  const raw = rawCatalogItemStatus(item)
  const explicitStatus = String(item.status || '').trim().toLowerCase()
  const explicitCacheStatus = String(item.cache_status || '').trim().toLowerCase()
  if (['ready', 'valid', 'completed', 'cached', 'no_signal'].includes(requested)) {
    return catalogItemState(item) === 'ready'
  }
  if (requested === 'failed' || requested === 'error' || requested === 'config_failed') {
    return catalogItemState(item) === 'failed'
  }
  if (requested === 'stale' || requested === 'invalid') return catalogItemState(item) === 'stale'
  if (requested === 'idle' || requested === 'missing' || requested === 'waiting') return catalogItemState(item) === 'idle'
  return requested === raw || requested === explicitStatus || requested === explicitCacheStatus
}

export function deriveCatalogStatusCounts(items: StrategyLibraryItem[]): CatalogStatusCounts {
  return items.reduce<CatalogStatusCounts>((counts, item) => {
    const state = catalogItemState(item)
    if (state === 'ready') counts.valid += 1
    else counts[state] += 1
    return counts
  }, { valid: 0, failed: 0, stale: 0, idle: 0 })
}

export function hasMeaningfulFilter(options?: FilterOption[] | null): boolean {
  const values = new Set((options ?? [])
    .map(option => String(option.value || '').trim())
    .filter(value => value && value !== 'all'))
  return values.size > 1
}

function normalizeDate(value: unknown): string {
  const text = String(value || '').trim()
  if (/^\d{8}$/.test(text)) return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`
  return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : ''
}

function windowBounds(windowValue?: EvaluationWindow | null): { start: string; end: string } {
  return {
    start: normalizeDate(windowValue?.start_time ?? windowValue?.start),
    end: normalizeDate(windowValue?.end_time ?? windowValue?.end),
  }
}

export function itemEvaluationWindow(item: StrategyLibraryItem): { start: string; end: string } {
  const explicit = windowBounds(item.evaluation_identity?.effective_window)
  return {
    start: explicit.start
      || windowBounds(item.evaluation_window).start
      || normalizeDate(item.cache_actual_start_time),
    end: explicit.end
      || windowBounds(item.evaluation_window).end
      || normalizeDate(item.cache_actual_end_time),
  }
}

export function formatEvaluationWindow(item: StrategyLibraryItem): string {
  const windowValue = itemEvaluationWindow(item)
  if (!windowValue.start && !windowValue.end) return ''
  return `${windowValue.start || '未知'} 至 ${windowValue.end || '未知'}`
}

export function hasEvaluationMetadata(item: StrategyLibraryItem): boolean {
  return Boolean(
    item.evaluation_identity
    || item.evaluation_id
    || item.evaluation_context_id
    || item.evaluation_window
    || item.evaluation_window_label
    || item.evaluation_status
    || item.evaluation_comparable != null
    || item.cache_actual_start_time
    || item.cache_actual_end_time,
  )
}

function evaluationIdentityKey(item: StrategyLibraryItem): string {
  const identity = item.evaluation_identity
  const contextId = String(identity?.context_id || '').trim()
  if (contextId) return `context:${contextId}`
  const windowValue = itemEvaluationWindow(item)
  const configHash = String(identity?.execution_config_hash || '').trim()
  const cutoff = normalizeDate(identity?.data_cutoff)
  if (!windowValue.start && !windowValue.end && !configHash && !cutoff) return ''
  return `derived:${windowValue.start}|${windowValue.end}|${cutoff}|${configHash}`
}

function summaryWindow(summary?: CatalogEvaluationSummary | null): string {
  const bounds = windowBounds(summary?.effective_window)
  if (!bounds.start && !bounds.end) return ''
  return `${bounds.start || '未知'} 至 ${bounds.end || '未知'}`
}

export function analyzeEvaluationComparability(
  items: StrategyLibraryItem[],
  summary?: CatalogEvaluationSummary | null,
): EvaluationComparability {
  const selected = items.filter(Boolean)
  const suppliedLabel = String(summary?.status_label || '').trim()
  const suppliedWindow = summaryWindow(summary)
  if (summary?.comparable === false || selected.some(item => item.evaluation_comparable === false)) {
    return {
      status: 'incompatible',
      label: suppliedLabel || '评估条件不一致',
      windowText: suppliedWindow,
      detail: '所选策略的评估窗口或运行配置不同，累计收益和评分不能直接横向比较。',
    }
  }

  const metadataItems = selected.filter(hasEvaluationMetadata)
  if (!selected.length || metadataItems.length !== selected.length) {
    return {
      status: 'unknown',
      label: suppliedLabel || '评估条件待确认',
      windowText: suppliedWindow,
      detail: '部分策略未提供完整评估身份，无法确认结果是否采用相同窗口和运行配置。',
    }
  }

  const keys = new Set(metadataItems.map(evaluationIdentityKey).filter(Boolean))
  const windows = new Set(metadataItems.map(formatEvaluationWindow).filter(Boolean))
  const explicitlyComparable = summary?.comparable === true || metadataItems.every(item => item.evaluation_comparable === true)
  if (keys.size > 1 || (!keys.size && windows.size > 1)) {
    return {
      status: 'incompatible',
      label: suppliedLabel || '评估条件不一致',
      windowText: suppliedWindow,
      detail: '策略使用了不同的评估窗口或运行配置；图表仅展示共同日期，增强评分已停用。',
    }
  }
  if (keys.size === 1 || windows.size === 1 || explicitlyComparable) {
    return {
      status: 'comparable',
      label: suppliedLabel || '评估条件一致',
      windowText: suppliedWindow || Array.from(windows)[0] || '',
      detail: '策略采用相同评估身份，可在统一口径下比较。',
    }
  }
  return {
    status: 'unknown',
    label: suppliedLabel || '评估条件待确认',
    windowText: suppliedWindow,
    detail: '未获得足够的评估身份信息。',
  }
}
