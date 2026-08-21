import type { TableRow, TableValue } from '../../types/library'

interface TradingTableColumn {
  key: string
  label: string
  align?: 'left' | 'center' | 'right'
}

export interface TradingTableCellPresentation {
  badgeClass?: string
  valueClass?: string
}

export function detailTableCellPresentation(
  column: TradingTableColumn,
  value: TableValue,
  _row: TableRow,
): TradingTableCellPresentation | null {
  const label = String(column.label || column.key || '').trim()
  const text = String(value || '').trim()

  const numeric = parseSignedValue(value)
  if (isProfitLossColumn(column) && numeric != null) {
    return {
      valueClass: numeric > 0 ? 'trade-profit' : numeric < 0 ? 'trade-loss' : 'trade-neutral',
    }
  }

  if (!text || text === '-') return null

  if (label === '方向') {
    if (text === '买入') return { badgeClass: 'buy' }
    if (text === '卖出') return { badgeClass: 'sell' }
    return null
  }
  if (label === '来源') {
    return { badgeClass: text === '风控' ? 'risk' : 'strategy' }
  }
  if (label === '状态') {
    if (text === '已成交') return { badgeClass: 'order-filled' }
    if (text === '已拒绝') return { badgeClass: 'order-rejected' }
    if (text === '已跳过') return { badgeClass: 'order-skipped' }
    if (text === '已取消') return { badgeClass: 'order-cancelled' }
    return { badgeClass: 'state-normal' }
  }
  if (label === '风控原因') {
    return { badgeClass: text.includes('恢复') ? 'risk-recover' : 'risk-trigger' }
  }
  if (label === '价格类型') {
    if (text === 'open') return { badgeClass: 'price-open' }
    if (text === 'close') return { badgeClass: 'price-close' }
    return { badgeClass: 'state-normal' }
  }
  if (label === '净值风控' || label === '回撤风控') {
    if (text === 'WARNING') return { badgeClass: 'state-warning' }
    if (text === 'DANGER') return { badgeClass: 'state-danger' }
    if (text === 'LOCK') return { badgeClass: 'state-lock' }
    return { badgeClass: 'state-normal' }
  }
  if (label === '原仓位' || label === '目标仓位' || label === '仓位') {
    return { badgeClass: 'ratio' }
  }

  return null
}

function parseSignedValue(value: TableValue): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  const text = String(value || '').trim()
  if (!text || text === '-') return null
  const normalized = text.replaceAll(',', '').replace('%', '')
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

function isProfitLossColumn(column: TradingTableColumn): boolean {
  const label = String(column.label || column.key || '').trim().toLowerCase()
  return label.includes('盈亏')
    || label.includes('收益率')
    || label.includes('收益')
    || label.includes('return')
    || label.includes('profit')
    || label.includes('pnl')
}
