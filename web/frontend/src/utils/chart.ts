export type ChartRangeValue = 'all' | 10 | 60

export interface RangeIndices {
  startIndex: number
  endIndex: number
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

export function normalizeRangeValue(value: unknown): ChartRangeValue {
  if (value === 'all' || value == null || value === '' || value === 0) return 'all'
  const num = Number(value)
  if (!Number.isFinite(num) || num <= 0) return 'all'
  return num <= 10 ? 10 : 60
}

export function resolveRangeIndices(dates: string[], value: unknown): RangeIndices | null {
  if (!Array.isArray(dates) || !dates.length) return null
  const normalized = normalizeRangeValue(value)
  const endIndex = dates.length - 1
  const startIndex = normalized === 'all'
    ? 0
    : Math.max(0, endIndex - normalized + 1)
  return { startIndex, endIndex }
}

export function usesFixedAxisRange(value: unknown): boolean {
  const normalized = normalizeRangeValue(value)
  return normalized === 10 || normalized === 60
}

export function resolveFixedAxisRange(values: number[], range: unknown): { min: number; max: number } | null {
  if (!values.length || !usesFixedAxisRange(range)) return null
  const indices = resolveRangeIndices(new Array(values.length).fill(''), range)
  if (!indices) return null
  const slice = values
    .slice(indices.startIndex, indices.endIndex + 1)
    .map(Number)
    .filter(Number.isFinite)
  if (!slice.length) return null
  return {
    min: Math.min(...slice) - 0.1,
    max: Math.max(...slice) + 0.1,
  }
}

export function computeSelectionValueRange(values: number[], startIndex: number, endIndex: number): { yMin: number; yMax: number } | null {
  const slice = values
    .slice(startIndex, endIndex + 1)
    .map(Number)
    .filter(Number.isFinite)
  if (!slice.length) return null

  let yMin = Math.min(...slice)
  let yMax = Math.max(...slice)
  const spread = yMax - yMin
  const padding = spread > 0
    ? spread * 0.08
    : Math.max(Math.abs(yMax || 1) * 0.02, 0.01)
  yMin -= padding
  yMax += padding
  return { yMin, yMax }
}

export function computeSliceMetrics(values: number[], startIndex: number, endIndex: number): { returnRate: number; maxDrawdown: number } | null {
  const slice = values
    .slice(startIndex, endIndex + 1)
    .map(Number)
    .filter(Number.isFinite)
  if (slice.length < 2 || slice[0] === 0) return null

  let peak = slice[0]
  let maxDrawdown = 0
  for (const value of slice) {
    if (value > peak) peak = value
    const drawdown = peak > 0 ? (value - peak) / peak : 0
    if (drawdown < maxDrawdown) maxDrawdown = drawdown
  }

  return {
    returnRate: slice[slice.length - 1] / slice[0] - 1,
    maxDrawdown,
  }
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-'
  return `${(value * 100).toFixed(digits)}%`
}

export function formatDrawdown(value: number | null | undefined): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-'
  return `-${formatPercent(Math.abs(value))}`
}
