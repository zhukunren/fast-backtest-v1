import { describe, expect, it } from 'vitest'

import type { DailyHoldingRow, DailyHoldingsPayload } from '../../types/library'
import {
  buildEffectiveDailyHoldings,
  buildHoldingsRowsBySlot,
  buildHoldingsRowsByStock,
  flattenDailyHoldings,
  getEffectiveHoldingsCurrentView,
  getDailyHoldingsRowsForDate,
  historyRowMatchesFilters,
  holdingDirectionLabel,
  holdingSharesScaled,
  normalizeHoldingDirection,
  normalizeHoldingsCurrentView,
  normalizeHoldingsDirectionFilterValue,
  normalizeHoldingsDisplayMode,
  normalizeHoldingsSlotFilterValue,
  resolveHoldingsSlotOptions,
} from './holdingsAggregation'

describe('daily holdings normalization', () => {
  it('applies the requested cutoff, sanitizes rows, and does not reuse mutable records', () => {
    const sourceRow = { code: '000001', slot: '核心' }
    const sourceSummary = { net_value: 1.02 }
    const payload = {
      dates: ['2025-01-02', '2025/01/03', '2025-01-04', 'invalid'],
      holdings_by_date: {
        '2025-01-02': [sourceRow, {}, null],
        '2025/01/03': [{ code: '000002' }],
        '2025-01-04': [{ code: '000003' }],
      },
      summary_by_date: {
        '2025-01-02': sourceSummary,
        '2025/01/03': { net_value: 1.03 },
      },
    } as unknown as DailyHoldingsPayload

    const result = buildEffectiveDailyHoldings(payload, '20250103')

    expect(result?.dates).toEqual(['2025-01-02', '2025/01/03'])
    expect(result?.holdings_by_date).toEqual({
      '2025-01-02': [sourceRow],
      '2025/01/03': [{ code: '000002' }],
    })
    expect(result?.holdings_by_date['2025-01-02'][0]).not.toBe(sourceRow)
    expect(result?.summary_by_date['2025-01-02']).toEqual(sourceSummary)
    expect(result?.summary_by_date['2025-01-02']).not.toBe(sourceSummary)
  })

  it('derives a sorted date list when the payload does not provide one', () => {
    const payload: DailyHoldingsPayload = {
      dates: [],
      holdings_by_date: {
        '2025-01-03': [{ code: '000003' }],
        '2025-01-01': [{ code: '000001' }],
      },
      summary_by_date: {
        '2025-01-02': { net_value: 1.01 },
      },
    }

    expect(buildEffectiveDailyHoldings(payload, '')?.dates).toEqual([
      '2025-01-01',
      '2025-01-02',
      '2025-01-03',
    ])
  })

  it('rejects invalid payloads and normalizes malformed collection fields', () => {
    expect(buildEffectiveDailyHoldings(null, '')).toBeNull()
    expect(buildEffectiveDailyHoldings('invalid' as unknown as DailyHoldingsPayload, '')).toBeNull()

    const malformed = {
      dates: [null, ''],
      holdings_by_date: null,
      summary_by_date: [],
    } as unknown as DailyHoldingsPayload
    expect(buildEffectiveDailyHoldings(malformed, '')).toEqual({
      dates: [],
      holdings_by_date: {},
      summary_by_date: {},
    })
  })
})

describe('holdings aggregation', () => {
  const rows: DailyHoldingRow[] = [
    {
      slot: 'A',
      code: '000001',
      name: 'Alpha',
      close: 11,
      shares: 6,
      original_shares: 10,
      cost_value: 60,
      holding_value: 66,
      unrealized_pnl: 6,
      effective_position_ratio: 0.4,
    },
    {
      slot: 'A',
      code: '000002',
      name: 'Beta',
      close: 11,
      shares: 4,
      original_shares: 4,
      cost_value: 40,
      holding_value: 44,
      unrealized_pnl: 4,
      effective_position_ratio: 0.4,
    },
    {
      slot: 'B',
      code: '000001',
      name: 'Alpha',
      close: 11,
      shares: 5,
      original_shares: 5,
      cost_value: 50,
      holding_value: 55,
      unrealized_pnl: -5,
      utilization_rate: 0.2,
      nv: 1.1,
    },
  ]

  it('consolidates stocks across slots without double-counting a slot-level ratio', () => {
    const aggregated = buildHoldingsRowsByStock(rows)

    expect(aggregated).toHaveLength(2)
    expect(aggregated[0]).toMatchObject({
      code: '000001',
      name: 'Alpha',
      slots_text: 'A、B',
      slot_count: 2,
      close: 11,
      cost_price: 10,
      shares: 11,
      original_shares: 15,
      shares_scaled: true,
      cost_value: 110,
      holding_value: 121,
      unrealized_pnl: 1,
      unrealized_pnl_pct: 0.009091,
      position_ratio: 0.44,
    })
    expect(aggregated[1]).toMatchObject({
      code: '000002',
      position_ratio: 0.16,
      unrealized_pnl: 4,
    })
    expect(rows.every(row => !('__agg_position_ratio' in row))).toBe(true)
  })

  it('consolidates slots and retains deterministic per-stock details', () => {
    const aggregated = buildHoldingsRowsBySlot(rows)

    expect(aggregated).toHaveLength(2)
    expect(aggregated[0]).toMatchObject({
      slot: 'A',
      stock_count: 2,
      stocks_text: '000001 Alpha、000002 Beta',
      shares: 10,
      original_shares: 14,
      shares_scaled: true,
      cost_value: 100,
      holding_value: 110,
      unrealized_pnl: 10,
      unrealized_pnl_pct: 0.1,
      position_ratio: 0.4,
    })
    expect(aggregated[0]?.stock_items).toEqual([
      { code: '000001', name: 'Alpha', unrealized_pnl: 6 },
      { code: '000002', name: 'Beta', unrealized_pnl: 4 },
    ])
    expect(aggregated[1]).toMatchObject({ slot: 'B', stock_count: 1, position_ratio: 0.2, nv: 1.1 })
  })

  it('marks explicit and inferred share scaling', () => {
    expect(holdingSharesScaled({ shares_scaled: 'yes' as unknown as boolean })).toBe(true)
    expect(holdingSharesScaled({ shares: 99.6, original_shares: 100.4 })).toBe(false)
    expect(holdingSharesScaled({ shares: 99, original_shares: 100 })).toBe(true)
  })

  it('uses cost and market-value fallbacks without inventing incomplete totals', () => {
    const fallbackRows = [
      {
        slot: '',
        code: 'COST',
        cost_value: '100',
        cost_price: '20',
        holding_value: '110',
      },
      {
        slot: 'Market',
        code: 'MARKET',
        close: '10',
        holding_value: '50',
      },
      {
        slot: 'Empty',
        code: 'EMPTY',
      },
      {
        slot: 'Invalid',
        code: 'INVALID',
        shares: 'bad',
        cost_value: 'bad',
        holding_value: Number.POSITIVE_INFINITY,
      },
      { slot: 'Ignored', code: '', name: 'no code' },
    ] as unknown as DailyHoldingRow[]

    const byCode = new Map(buildHoldingsRowsByStock(fallbackRows).map(row => [row.code, row]))

    expect(byCode.get('COST')).toMatchObject({
      slots_text: '-',
      cost_price: 20,
      shares: null,
      original_shares: null,
      cost_value: 100,
      holding_value: 110,
      unrealized_pnl: 10,
      unrealized_pnl_pct: 0.1,
      position_ratio: null,
    })
    expect(byCode.get('MARKET')).toMatchObject({ cost_price: null, unrealized_pnl: null })
    expect(byCode.get('EMPTY')).toMatchObject({
      close: null,
      cost_value: null,
      holding_value: null,
      unrealized_pnl: null,
    })
    expect(byCode.has('')).toBe(false)
  })

  it('handles incomplete, unequal, zero, and weighted slot-level ratios independently', () => {
    const ratioRows: DailyHoldingRow[] = [
      { slot: 'Incomplete', code: 'I1', holding_value: 10, effective_position_ratio: 0.3 },
      { slot: 'Incomplete', code: 'I2', holding_value: 10 },
      { slot: 'Different', code: 'D1', holding_value: 10, effective_position_ratio: 0.1 },
      { slot: 'Different', code: 'D2', holding_value: 10, effective_position_ratio: 0.2 },
      { slot: 'Even', code: 'E1', effective_position_ratio: 0.2 },
      { slot: 'Even', code: 'E2', effective_position_ratio: 0.2 },
      { slot: 'Weighted', code: 'W0', holding_value: 0, effective_position_ratio: 0.2 },
      { slot: 'Weighted', code: 'W1', holding_value: 10, effective_position_ratio: 0.2 },
    ]

    const byCode = new Map(buildHoldingsRowsByStock(ratioRows).map(row => [row.code, row]))

    expect(byCode.get('I1')?.position_ratio).toBe(0.3)
    expect(byCode.get('I2')?.position_ratio).toBeNull()
    expect(byCode.get('D1')?.position_ratio).toBe(0.1)
    expect(byCode.get('D2')?.position_ratio).toBe(0.2)
    expect(byCode.get('E1')?.position_ratio).toBe(0.1)
    expect(byCode.get('E2')?.position_ratio).toBe(0.1)
    expect(byCode.get('W0')?.position_ratio).toBe(0)
    expect(byCode.get('W1')?.position_ratio).toBe(0.2)
  })

  it('summarizes long slot lists deterministically', () => {
    const repeated = ['D', 'B', 'A', 'C'].map(slot => ({ slot, code: 'MULTI' }))

    expect(buildHoldingsRowsByStock(repeated)[0]?.slots_text).toBe('A、B、C 等4项')
  })

  it('retains slot rows with missing identifiers and repeated stock PnL', () => {
    const sparseRows = [
      { holding_value: 10 },
      { code: '000004' },
      { code: '000001', unrealized_pnl: 2 },
      { code: '000001', unrealized_pnl: 3 },
      { code: '000002', name: 'Beta' },
      { code: '000003', name: 'Gamma', cost_value: 10, holding_value: 12 },
      { name: 'Name only', unrealized_pnl: -1 },
    ] as DailyHoldingRow[]

    const [combined] = buildHoldingsRowsBySlot(sparseRows)

    expect(combined).toMatchObject({
      slot: '组合',
      stock_count: 4,
      stocks_text: '000001、000002 Beta、000003 Gamma 等4项',
      shares: null,
      original_shares: null,
      cost_value: 10,
      holding_value: 22,
      unrealized_pnl: 4,
      position_ratio: null,
    })
    expect(combined?.stock_items).toContainEqual({ code: '000001', name: '', unrealized_pnl: 5 })
    expect(combined?.stock_items).toContainEqual({ code: '000004', name: '', unrealized_pnl: null })
    expect(combined?.stock_items).toContainEqual({ code: '', name: 'Name only', unrealized_pnl: -1 })
  })
})

describe('holdings UI value normalization', () => {
  it('normalizes display modes and effective views', () => {
    expect(normalizeHoldingsDisplayMode(' PRO ')).toBe('pro')
    expect(normalizeHoldingsDisplayMode(undefined)).toBe('simple')
    expect(normalizeHoldingsCurrentView('stock')).toBe('stock')
    expect(normalizeHoldingsCurrentView('SLOT')).toBe('slot')
    expect(normalizeHoldingsCurrentView('unknown')).toBe('detail')
    expect(getEffectiveHoldingsCurrentView('simple', 'detail')).toBe('stock')
    expect(getEffectiveHoldingsCurrentView('pro', 'slot')).toBe('slot')
  })

  it('normalizes directions and localized labels', () => {
    expect(normalizeHoldingDirection('BUY')).toBe('buy')
    expect(normalizeHoldingDirection('买入')).toBe('buy')
    expect(normalizeHoldingDirection('sell')).toBe('sell')
    expect(normalizeHoldingDirection('卖出')).toBe('sell')
    expect(normalizeHoldingDirection(undefined)).toBe('hold')
    expect(holdingDirectionLabel('buy')).toBe('买入')
    expect(holdingDirectionLabel('sell')).toBe('卖出')
    expect(holdingDirectionLabel('other')).toBe('持有中')
    expect(normalizeHoldingsDirectionFilterValue('HOLD')).toBe('hold')
    expect(normalizeHoldingsDirectionFilterValue('unknown')).toBe('all')
  })
})

describe('holdings history', () => {
  const payload: DailyHoldingsPayload = {
    dates: ['2025-01-02', '2025-01-03'],
    holdings_by_date: {
      '2025-01-02': [
        { slot: 'B', code: '000003', name: 'Gamma', dir: 'sell' },
      ],
      '2025-01-03': [
        { slot: 'B', code: '000002', name: 'Beta', dir: '持有' },
        { slot: 'A', code: '000001', name: 'Alpha', dir: '买入' },
      ],
    },
    summary_by_date: {},
  }

  it('flattens newest-first history with stable slot and code ordering', () => {
    const history = flattenDailyHoldings(payload)

    expect(history.map(row => `${row.trade_date}:${row.slot}:${row.code}`)).toEqual([
      '2025-01-03:A:000001',
      '2025-01-03:B:000002',
      '2025-01-02:B:000003',
    ])
    expect(resolveHoldingsSlotOptions(payload)).toEqual(['A', 'B'])
    expect(getDailyHoldingsRowsForDate(payload, '2025-01-03')).not.toBe(payload.holdings_by_date['2025-01-03'])
    expect(getDailyHoldingsRowsForDate(payload, '')).toEqual([])
    expect(flattenDailyHoldings(null)).toEqual([])
  })

  it('matches normalized direction, slot, and localized search filters', () => {
    const [buyRow] = flattenDailyHoldings(payload)

    expect(historyRowMatchesFilters(buyRow, {
      searchValue: '买入',
      slotFilter: 'A',
      directionFilter: 'buy',
    })).toBe(true)
    expect(historyRowMatchesFilters(buyRow, {
      searchValue: 'alpha',
      slotFilter: 'B',
      directionFilter: 'all',
    })).toBe(false)
    expect(historyRowMatchesFilters(buyRow, {
      searchValue: '',
      slotFilter: 'all',
      directionFilter: 'sell',
    })).toBe(false)
    expect(historyRowMatchesFilters(buyRow, {
      searchValue: '',
      slotFilter: 'all',
      directionFilter: 'all',
    })).toBe(true)
  })

  it('normalizes valid and stale slot filters', () => {
    expect(normalizeHoldingsSlotFilterValue('', payload)).toBe('all')
    expect(normalizeHoldingsSlotFilterValue('all', payload)).toBe('all')
    expect(normalizeHoldingsSlotFilterValue('A', payload)).toBe('A')
    expect(normalizeHoldingsSlotFilterValue('missing', payload)).toBe('all')
  })
})
