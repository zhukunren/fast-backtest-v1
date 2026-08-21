import type {
  DailyHoldingHistoryRow,
  DailyHoldingRow,
  DailyHoldingsPayload,
  DailyHoldingsSummary,
  HoldingsCurrentView,
  HoldingsDisplayMode,
  SlotAggregatedHoldingRow,
  StockAggregatedHoldingRow,
} from '../../types/library'

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

const HOLDING_ROW_KEYS = [
  'slot',
  'code',
  'name',
  'dir',
  'close',
  'shares',
  'original_shares',
  'holding_value',
  'cost_price',
  'cost_value',
  'unrealized_pnl',
  'unrealized_pnl_pct',
  'nv',
  'utilization_rate',
  'effective_position_ratio',
] as const

function isDailyHoldingRow(value: unknown): value is DailyHoldingRow {
  if (!isRecord(value)) return false
  return HOLDING_ROW_KEYS.some((key) => {
    const field = value[key]
    return field != null && (typeof field !== 'string' || Boolean(field.trim()))
  })
}

function sanitizeHoldingRows(value: unknown): DailyHoldingRow[] {
  if (!Array.isArray(value)) return []
  return value
    .filter(isDailyHoldingRow)
    .map(row => ({ ...row }))
}

function comparableDateKey(value: unknown): string {
  const digits = String(value || '').replace(/\D/g, '')
  return digits.length >= 8 ? digits.slice(0, 8) : ''
}

export function buildEffectiveDailyHoldings(
  payload: DailyHoldingsPayload | null,
  endDate: string,
): DailyHoldingsPayload | null {
  if (!payload || !isRecord(payload)) return null

  const cutoff = comparableDateKey(endDate)
  const sourceHoldings = isRecord(payload.holdings_by_date) ? payload.holdings_by_date : {}
  const sourceSummaries = isRecord(payload.summary_by_date) ? payload.summary_by_date : {}
  const sourceDates = Array.isArray(payload.dates) && payload.dates.length
    ? payload.dates
    : Array.from(new Set([
      ...Object.keys(sourceHoldings),
      ...Object.keys(sourceSummaries),
    ])).sort((left, right) => left.localeCompare(right))
  const dates = Array.from(new Set(
    sourceDates
      .map(value => String(value || '').trim())
      .filter(Boolean)
      .filter((date) => {
        const dateKey = comparableDateKey(date)
        return !cutoff || Boolean(dateKey && dateKey <= cutoff)
      }),
  ))

  const holdingsByDate: Record<string, DailyHoldingRow[]> = {}
  const summaryByDate: Record<string, DailyHoldingsSummary> = {}
  for (const date of dates) {
    holdingsByDate[date] = sanitizeHoldingRows(sourceHoldings[date])
    const summary = sourceSummaries[date]
    if (isRecord(summary)) summaryByDate[date] = { ...summary }
  }

  return {
    ...payload,
    dates,
    holdings_by_date: holdingsByDate,
    summary_by_date: summaryByDate,
  }
}

function asFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function roundHoldingNumber(value: unknown, digits = 2): number | null {
  const numeric = asFiniteNumber(value)
  if (numeric == null) return null
  return Number(numeric.toFixed(digits))
}

function resolveHoldingRatioValue(row: DailyHoldingRow): number | null {
  return asFiniteNumber(row.effective_position_ratio ?? row.utilization_rate)
}

function aggregationPositionRatio(row: DailyHoldingRow): number | null {
  const normalized = asFiniteNumber((row as DailyHoldingRow & { __agg_position_ratio?: number | null }).__agg_position_ratio)
  if (normalized != null) return normalized
  return resolveHoldingRatioValue(row)
}

export function normalizeHoldingsDisplayMode(value: unknown): HoldingsDisplayMode {
  const text = String(value || '').trim().toLowerCase()
  return text === 'pro' ? 'pro' : 'simple'
}

export function normalizeHoldingsCurrentView(value: unknown): HoldingsCurrentView {
  const text = String(value || '').trim().toLowerCase()
  if (text === 'stock' || text === 'slot') return text
  return 'detail'
}

export function getEffectiveHoldingsCurrentView(
  mode: HoldingsDisplayMode,
  view: HoldingsCurrentView,
): HoldingsCurrentView {
  return normalizeHoldingsDisplayMode(mode) === 'simple' ? 'stock' : normalizeHoldingsCurrentView(view)
}

export function normalizeHoldingDirection(value: unknown): 'buy' | 'sell' | 'hold' {
  const text = String(value || '').trim().toLowerCase()
  if (text === 'buy' || text === '买入') return 'buy'
  if (text === 'sell' || text === '卖出') return 'sell'
  return 'hold'
}

export function holdingDirectionLabel(value: unknown): string {
  const normalized = normalizeHoldingDirection(value)
  if (normalized === 'buy') return '买入'
  if (normalized === 'sell') return '卖出'
  return '持有中'
}

function normalizeHoldingScaledFlag(value: unknown): boolean {
  if (typeof value === 'boolean') return value
  if (value == null) return false
  const text = String(value).trim().toLowerCase()
  return text === '1' || text === 'true' || text === 'yes'
}

export function holdingSharesScaled(row: Pick<DailyHoldingRow, 'shares_scaled' | 'shares' | 'original_shares'>): boolean {
  if (normalizeHoldingScaledFlag(row.shares_scaled)) return true
  const shares = asFiniteNumber(row.shares)
  const originalShares = asFiniteNumber(row.original_shares)
  return shares != null
    && originalShares != null
    && Math.round(shares) !== Math.round(originalShares)
}

function estimateHoldingQuantity(row: DailyHoldingRow): number | null {
  const shares = asFiniteNumber(row.shares)
  if (shares != null && shares > 0) return shares
  const costValue = asFiniteNumber(row.cost_value)
  const costPrice = asFiniteNumber(row.cost_price)
  if (costValue != null && costValue > 0 && costPrice != null && costPrice > 0) {
    return costValue / costPrice
  }
  const holdingValue = asFiniteNumber(row.holding_value)
  const close = asFiniteNumber(row.close)
  if (holdingValue != null && holdingValue > 0 && close != null && close > 0) {
    return holdingValue / close
  }
  return null
}

function summarizeHoldingText(values: string[], limit = 3): string {
  const safeValues = Array.from(new Set(values.map(item => String(item || '').trim()).filter(Boolean)))
  if (!safeValues.length) return '-'
  if (safeValues.length <= limit) return safeValues.join('、')
  return `${safeValues.slice(0, limit).join('、')} 等${safeValues.length}项`
}

function normalizeHoldingRowsForAggregation(rows: unknown): DailyHoldingRow[] {
  const normalizedRows = sanitizeHoldingRows(rows)
  const slotGroups = new Map<string, DailyHoldingRow[]>()
  for (const row of normalizedRows) {
    const slot = String(row.slot || '').trim() || '组合'
    if (!slotGroups.has(slot)) slotGroups.set(slot, [])
    slotGroups.get(slot)?.push(row)
    const ratio = resolveHoldingRatioValue(row)
    if (ratio != null) {
      ;(row as DailyHoldingRow & { __agg_position_ratio?: number | null }).__agg_position_ratio = ratio
    }
  }
  for (const groupRows of slotGroups.values()) {
    if (groupRows.length <= 1) continue
    const ratios = groupRows
      .map(row => asFiniteNumber((row as DailyHoldingRow & { __agg_position_ratio?: number | null }).__agg_position_ratio))
      .filter((value): value is number => value != null)
    if (ratios.length !== groupRows.length) continue
    const roundedRatios = new Set(ratios.map(value => value.toFixed(10)))
    if (roundedRatios.size !== 1) continue
    const slotRatio = ratios[0] ?? 0
    const holdingValues = groupRows.map(row => asFiniteNumber(row.holding_value))
    const hasHoldingValues = holdingValues.every(value => value != null && value >= 0)
    const numericHoldingValues = hasHoldingValues ? holdingValues as number[] : []
    const totalHoldingValue = hasHoldingValues
      ? numericHoldingValues.reduce((sum, value) => sum + value, 0)
      : 0
    if (totalHoldingValue > 1e-9) {
      groupRows.forEach((row, index) => {
        const holdingValue = numericHoldingValues[index] || 0
        ;(row as DailyHoldingRow & { __agg_position_ratio?: number | null }).__agg_position_ratio = slotRatio * holdingValue / totalHoldingValue
      })
      continue
    }
    const evenRatio = slotRatio / groupRows.length
    groupRows.forEach(row => {
      ;(row as DailyHoldingRow & { __agg_position_ratio?: number | null }).__agg_position_ratio = evenRatio
    })
  }
  return normalizedRows
}

export function getDailyHoldingsRowsForDate(payload: DailyHoldingsPayload | null, dateKey: string): DailyHoldingRow[] {
  const safeDate = String(dateKey || '').trim()
  if (!safeDate) return []
  const grouped = payload?.holdings_by_date
  return sanitizeHoldingRows(grouped?.[safeDate])
}

export function buildHoldingsRowsByStock(rows: DailyHoldingRow[]): StockAggregatedHoldingRow[] {
  const normalizedRows = normalizeHoldingRowsForAggregation(rows)
  const groups = new Map<string, {
    code: string
    name: string
    slots: Set<string>
    close: number | null
    shares: number
    sharesComplete: boolean
    originalShares: number
    originalSharesComplete: boolean
    sharesScaled: boolean
    costValue: number
    hasCostValue: boolean
    holdingValue: number
    hasHoldingValue: boolean
    unrealizedPnl: number
    hasUnrealizedPnl: boolean
    positionRatio: number
    hasPositionRatio: boolean
    costQty: number
  }>()
  for (const row of normalizedRows) {
    const code = String(row.code || '').trim()
    if (!code) continue
    if (!groups.has(code)) {
      groups.set(code, {
        code,
        name: '',
        slots: new Set<string>(),
        close: null,
        shares: 0,
        sharesComplete: true,
        originalShares: 0,
        originalSharesComplete: true,
        sharesScaled: false,
        costValue: 0,
        hasCostValue: false,
        holdingValue: 0,
        hasHoldingValue: false,
        unrealizedPnl: 0,
        hasUnrealizedPnl: false,
        positionRatio: 0,
        hasPositionRatio: false,
        costQty: 0,
      })
    }
    const group = groups.get(code)
    if (!group) continue
    const slot = String(row.slot || '').trim()
    const name = String(row.name || '').trim()
    const close = asFiniteNumber(row.close)
    const shares = asFiniteNumber(row.shares)
    const originalShares = asFiniteNumber(row.original_shares)
    const costValue = asFiniteNumber(row.cost_value)
    const holdingValue = asFiniteNumber(row.holding_value)
    const unrealizedPnl = asFiniteNumber(row.unrealized_pnl)
    const positionRatio = aggregationPositionRatio(row)
    const qtyForCost = estimateHoldingQuantity(row)

    if (slot) group.slots.add(slot)
    if (!group.name && name) group.name = name
    if (group.close == null && close != null) group.close = close
    if (shares != null) group.shares += shares
    else group.sharesComplete = false
    if (originalShares != null) group.originalShares += originalShares
    else group.originalSharesComplete = false
    if (holdingSharesScaled(row)) group.sharesScaled = true
    if (costValue != null) {
      group.costValue += costValue
      group.hasCostValue = true
    }
    if (holdingValue != null) {
      group.holdingValue += holdingValue
      group.hasHoldingValue = true
    }
    if (unrealizedPnl != null) {
      group.unrealizedPnl += unrealizedPnl
      group.hasUnrealizedPnl = true
    }
    if (positionRatio != null) {
      group.positionRatio += positionRatio
      group.hasPositionRatio = true
    }
    if (qtyForCost != null && qtyForCost > 0) group.costQty += qtyForCost
  }

  return Array.from(groups.values()).map(group => {
    const costValue = group.hasCostValue ? roundHoldingNumber(group.costValue, 2) : null
    const holdingValue = group.hasHoldingValue ? roundHoldingNumber(group.holdingValue, 2) : null
    const unrealizedPnl = group.hasUnrealizedPnl
      ? roundHoldingNumber(group.unrealizedPnl, 2)
      : (group.hasHoldingValue && group.hasCostValue ? roundHoldingNumber(group.holdingValue - group.costValue, 2) : null)
    const unrealizedPnlPct = costValue != null && costValue !== 0 && unrealizedPnl != null
      ? roundHoldingNumber(unrealizedPnl / costValue, 6)
      : null
    return {
      code: group.code,
      name: group.name,
      slots_text: summarizeHoldingText(Array.from(group.slots).sort((left, right) => left.localeCompare(right, 'zh-CN'))),
      slot_count: group.slots.size,
      close: group.close != null ? roundHoldingNumber(group.close, 2) : null,
      cost_price: group.hasCostValue && group.costQty > 1e-9 ? roundHoldingNumber(group.costValue / group.costQty, 4) : null,
      shares: group.sharesComplete ? Math.round(group.shares) : null,
      original_shares: group.originalSharesComplete ? Math.round(group.originalShares) : null,
      shares_scaled: group.sharesScaled,
      cost_value: costValue,
      holding_value: holdingValue,
      unrealized_pnl: unrealizedPnl,
      unrealized_pnl_pct: unrealizedPnlPct,
      position_ratio: group.hasPositionRatio ? roundHoldingNumber(group.positionRatio, 4) : null,
    }
  }).sort((left, right) => {
    const leftPnl = asFiniteNumber(left.unrealized_pnl)
    const rightPnl = asFiniteNumber(right.unrealized_pnl)
    if (leftPnl != null && rightPnl != null && leftPnl !== rightPnl) return leftPnl - rightPnl
    return String(left.code || '').localeCompare(String(right.code || ''), 'zh-CN')
  })
}

export function buildHoldingsRowsBySlot(rows: DailyHoldingRow[]): SlotAggregatedHoldingRow[] {
  const normalizedRows = normalizeHoldingRowsForAggregation(rows)
  const groups = new Map<string, {
    slot: string
    stocks: Set<string>
    stockItems: Map<string, { code: string, name: string, unrealized_pnl: number | null, has_unrealized_pnl: boolean }>
    shares: number
    sharesComplete: boolean
    originalShares: number
    originalSharesComplete: boolean
    sharesScaled: boolean
    costValue: number
    hasCostValue: boolean
    holdingValue: number
    hasHoldingValue: boolean
    unrealizedPnl: number
    hasUnrealizedPnl: boolean
    positionRatio: number
    hasPositionRatio: boolean
    netValue: number | null
  }>()
  for (const row of normalizedRows) {
    const slot = String(row.slot || '').trim() || '组合'
    if (!groups.has(slot)) {
      groups.set(slot, {
        slot,
        stocks: new Set<string>(),
        stockItems: new Map<string, { code: string, name: string, unrealized_pnl: number | null, has_unrealized_pnl: boolean }>(),
        shares: 0,
        sharesComplete: true,
        originalShares: 0,
        originalSharesComplete: true,
        sharesScaled: false,
        costValue: 0,
        hasCostValue: false,
        holdingValue: 0,
        hasHoldingValue: false,
        unrealizedPnl: 0,
        hasUnrealizedPnl: false,
        positionRatio: 0,
        hasPositionRatio: false,
        netValue: null,
      })
    }
    const group = groups.get(slot)
    if (!group) continue
    const codeText = String(row.code || '').trim()
    const nameText = String(row.name || '').trim()
    const shares = asFiniteNumber(row.shares)
    const originalShares = asFiniteNumber(row.original_shares)
    const costValue = asFiniteNumber(row.cost_value)
    const holdingValue = asFiniteNumber(row.holding_value)
    const unrealizedPnl = asFiniteNumber(row.unrealized_pnl)
    const positionRatio = aggregationPositionRatio(row)
    const netValue = asFiniteNumber(row.nv)

    if (codeText) group.stocks.add(nameText ? `${codeText} ${nameText}` : codeText)
    const stockKey = codeText || nameText
    if (stockKey) {
      if (!group.stockItems.has(stockKey)) {
        group.stockItems.set(stockKey, {
          code: codeText,
          name: nameText,
          unrealized_pnl: null,
          has_unrealized_pnl: false,
        })
      }
      const stockItem = group.stockItems.get(stockKey)
      if (stockItem && unrealizedPnl != null) {
        const base = stockItem.has_unrealized_pnl ? Number(stockItem.unrealized_pnl) : 0
        stockItem.unrealized_pnl = roundHoldingNumber(base + unrealizedPnl, 2)
        stockItem.has_unrealized_pnl = true
      }
    }
    if (shares != null) group.shares += shares
    else group.sharesComplete = false
    if (originalShares != null) group.originalShares += originalShares
    else group.originalSharesComplete = false
    if (holdingSharesScaled(row)) group.sharesScaled = true
    if (costValue != null) {
      group.costValue += costValue
      group.hasCostValue = true
    }
    if (holdingValue != null) {
      group.holdingValue += holdingValue
      group.hasHoldingValue = true
    }
    if (unrealizedPnl != null) {
      group.unrealizedPnl += unrealizedPnl
      group.hasUnrealizedPnl = true
    }
    if (positionRatio != null) {
      group.positionRatio += positionRatio
      group.hasPositionRatio = true
    }
    if (group.netValue == null && netValue != null) group.netValue = netValue
  }

  return Array.from(groups.values()).map(group => {
    const costValue = group.hasCostValue ? roundHoldingNumber(group.costValue, 2) : null
    const holdingValue = group.hasHoldingValue ? roundHoldingNumber(group.holdingValue, 2) : null
    const unrealizedPnl = group.hasUnrealizedPnl
      ? roundHoldingNumber(group.unrealizedPnl, 2)
      : (group.hasHoldingValue && group.hasCostValue ? roundHoldingNumber(group.holdingValue - group.costValue, 2) : null)
    const unrealizedPnlPct = costValue != null && costValue !== 0 && unrealizedPnl != null
      ? roundHoldingNumber(unrealizedPnl / costValue, 6)
      : null
    return {
      slot: group.slot,
      stock_count: group.stocks.size,
      stocks_text: summarizeHoldingText(Array.from(group.stocks).sort((left, right) => left.localeCompare(right, 'zh-CN'))),
      stock_items: Array.from(group.stockItems.values())
        .map(item => ({
          code: item.code,
          name: item.name,
          unrealized_pnl: item.has_unrealized_pnl ? roundHoldingNumber(item.unrealized_pnl, 2) : null,
        }))
        .sort((left, right) => `${String(left.code || '')} ${String(left.name || '')}`.localeCompare(`${String(right.code || '')} ${String(right.name || '')}`, 'zh-CN')),
      shares: group.sharesComplete ? Math.round(group.shares) : null,
      original_shares: group.originalSharesComplete ? Math.round(group.originalShares) : null,
      shares_scaled: group.sharesScaled,
      cost_value: costValue,
      holding_value: holdingValue,
      unrealized_pnl: unrealizedPnl,
      unrealized_pnl_pct: unrealizedPnlPct,
      nv: group.netValue != null ? roundHoldingNumber(group.netValue, 4) : null,
      position_ratio: group.hasPositionRatio ? roundHoldingNumber(group.positionRatio, 4) : null,
    }
  }).sort((left, right) => String(left.slot || '').localeCompare(String(right.slot || ''), 'zh-CN'))
}

export function flattenDailyHoldings(payload: DailyHoldingsPayload | null): DailyHoldingHistoryRow[] {
  const grouped = payload?.holdings_by_date
  if (!isRecord(grouped)) return []
  const rows: DailyHoldingHistoryRow[] = []
  const dates = Object.keys(grouped).sort((left, right) => right.localeCompare(left))
  for (const tradeDate of dates) {
    const dayRows = sanitizeHoldingRows(grouped[tradeDate])
    for (const row of dayRows) {
      rows.push({
        trade_date: tradeDate,
        slot: String(row?.slot || '').trim(),
        code: String(row?.code || '').trim(),
        name: String(row?.name || '').trim(),
        dir: String(row?.dir || '').trim(),
        close: row?.close,
        cost_price: row?.cost_price,
        shares: row?.shares,
        original_shares: row?.original_shares,
        shares_scaled: row?.shares_scaled,
        cost_value: row?.cost_value,
        holding_value: row?.holding_value,
        unrealized_pnl: row?.unrealized_pnl,
        unrealized_pnl_pct: row?.unrealized_pnl_pct,
        nv: row?.nv,
        utilization_rate: row?.utilization_rate,
        effective_position_ratio: row?.effective_position_ratio,
      })
    }
  }
  return rows.sort((left, right) => {
    const dateCmp = String(right.trade_date || '').localeCompare(String(left.trade_date || ''))
    if (dateCmp !== 0) return dateCmp
    const slotCmp = String(left.slot || '').localeCompare(String(right.slot || ''), 'zh-CN')
    if (slotCmp !== 0) return slotCmp
    return String(left.code || '').localeCompare(String(right.code || ''), 'zh-CN')
  })
}

export function resolveHoldingsSlotOptions(payload: DailyHoldingsPayload | null): string[] {
  const slots = new Set<string>()
  for (const row of flattenDailyHoldings(payload)) {
    const slot = String(row.slot || '').trim()
    if (slot) slots.add(slot)
  }
  return Array.from(slots).sort((left, right) => left.localeCompare(right, 'zh-CN'))
}

export function normalizeHoldingsSlotFilterValue(value: unknown, payload: DailyHoldingsPayload | null): string {
  const text = String(value || '').trim()
  if (!text || text === 'all') return 'all'
  return resolveHoldingsSlotOptions(payload).includes(text) ? text : 'all'
}

export function normalizeHoldingsDirectionFilterValue(value: unknown): 'all' | 'buy' | 'sell' | 'hold' {
  const text = String(value || '').trim().toLowerCase()
  if (text === 'buy' || text === 'sell' || text === 'hold') return text
  return 'all'
}

export function historyRowMatchesFilters(
  row: DailyHoldingHistoryRow,
  options: {
    searchValue: string
    slotFilter: string
    directionFilter: 'all' | 'buy' | 'sell' | 'hold'
  },
): boolean {
  const searchValue = String(options.searchValue || '').trim().toLowerCase()
  const slotFilter = String(options.slotFilter || 'all').trim()
  const directionFilter = normalizeHoldingsDirectionFilterValue(options.directionFilter)
  if (slotFilter !== 'all' && String(row.slot || '') !== slotFilter) return false
  if (directionFilter !== 'all' && normalizeHoldingDirection(row.dir) !== directionFilter) return false
  if (!searchValue) return true
  const haystack = [
    row.trade_date,
    row.slot,
    row.code,
    row.name,
    holdingDirectionLabel(row.dir),
  ].join(' ').toLowerCase()
  return haystack.includes(searchValue)
}
