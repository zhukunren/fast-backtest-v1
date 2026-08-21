import { describe, expect, it } from 'vitest'

import {
  clamp,
  computeSelectionValueRange,
  computeSliceMetrics,
  formatDrawdown,
  formatPercent,
  normalizeRangeValue,
  resolveFixedAxisRange,
  resolveRangeIndices,
  usesFixedAxisRange,
} from './chart'

describe('chart range conversion', () => {
  it('normalizes arbitrary UI values into the supported range choices', () => {
    expect(normalizeRangeValue(null)).toBe('all')
    expect(normalizeRangeValue('all')).toBe('all')
    expect(normalizeRangeValue('invalid')).toBe('all')
    expect(normalizeRangeValue(5)).toBe(10)
    expect(normalizeRangeValue('10')).toBe(10)
    expect(normalizeRangeValue(11)).toBe(60)
    expect(usesFixedAxisRange(60)).toBe(true)
    expect(usesFixedAxisRange(0)).toBe(false)
  })

  it('selects the trailing fixed-size window without producing negative indices', () => {
    const dates = Array.from({ length: 75 }, (_, index) => `day-${index + 1}`)

    expect(resolveRangeIndices(dates, 10)).toEqual({ startIndex: 65, endIndex: 74 })
    expect(resolveRangeIndices(dates.slice(0, 4), 10)).toEqual({ startIndex: 0, endIndex: 3 })
    expect(resolveRangeIndices(dates, 'all')).toEqual({ startIndex: 0, endIndex: 74 })
    expect(resolveRangeIndices([], 10)).toBeNull()
  })

  it('derives a padded axis from only the selected trailing values', () => {
    const values = [
      ...new Array(60).fill(100),
      1, 2, Number.NaN, 4, 5, 2, 3, 4, 5, 1,
    ]

    expect(resolveFixedAxisRange(values, 10)).toEqual({ min: 0.9, max: 5.1 })
    expect(resolveFixedAxisRange(values, 'all')).toBeNull()
    expect(resolveFixedAxisRange([Number.NaN], 10)).toBeNull()
  })
})

describe('chart selection metrics', () => {
  it('adds proportional padding for varying data and stable padding for a flat line', () => {
    expect(computeSelectionValueRange([1, 2, 3], 0, 2)).toEqual({ yMin: 0.84, yMax: 3.16 })
    expect(computeSelectionValueRange([2, 2, 2], 0, 2)).toEqual({ yMin: 1.96, yMax: 2.04 })
    expect(computeSelectionValueRange([0, 0], 0, 1)).toEqual({ yMin: -0.02, yMax: 0.02 })
    expect(computeSelectionValueRange([Number.NaN], 0, 0)).toBeNull()
  })

  it('computes return and peak-to-trough drawdown for a selected slice', () => {
    const metrics = computeSliceMetrics([9, 1, 1.2, 0.9, 1.1, 9], 1, 4)
    expect(metrics?.returnRate).toBeCloseTo(0.1)
    expect(metrics?.maxDrawdown).toBeCloseTo(-0.25)
    expect(computeSliceMetrics([-2, -1], 0, 1)).toEqual({ returnRate: -0.5, maxDrawdown: 0 })
    expect(computeSliceMetrics([0, 1], 0, 1)).toBeNull()
    expect(computeSliceMetrics([1], 0, 0)).toBeNull()
  })

  it('formats percentages and clamps interaction indices', () => {
    expect(clamp(12, 0, 10)).toBe(10)
    expect(formatPercent(0.12345, 1)).toBe('12.3%')
    expect(formatPercent(Number.NaN)).toBe('-')
    expect(formatDrawdown(-0.125)).toBe('-12.50%')
    expect(formatDrawdown(null)).toBe('-')
  })
})
