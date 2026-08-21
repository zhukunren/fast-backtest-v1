import { afterEach, describe, expect, it, vi } from 'vitest'

import type { NetValuePayload } from '../types/library'
import {
  buildReturnHeatmapData,
  buildReturnHeatmapPanelNote,
  formatReturnHeatmapPercent,
  hasReturnHeatmapExcessSeries,
  resolveIsoWeek,
  resolveXAxisLabels,
} from './returnHeatmap'

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('return heatmap data', () => {
  it('sorts points and computes period returns across month and year boundaries', () => {
    const payload: NetValuePayload = {
      dates: ['2024-02-29', '2024-01-02', 'invalid', '2024-01-31', '2025-01-31'],
      net_value: [1.21, 1, 99, 1.1, 1.331],
    }

    const result = buildReturnHeatmapData(payload, 'month', 'return')

    expect(result?.yearLabels).toEqual(['2025', '2024'])
    expect(result?.xAxisLabels).toEqual([
      '01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '12',
    ])
    expect(result?.seriesData).toEqual([
      [0, 1, 10, '2024-01', '2024-01-02', '2024-01-31', '01', '2024'],
      [1, 1, 10, '2024-02', '2024-01-31', '2024-02-29', '02', '2024'],
      [0, 0, 10, '2025-01', '2024-02-29', '2025-01-31', '01', '2025'],
    ])
    expect(result?.maxAbs).toBe(10)
  })

  it('uses an explicit initial net-value baseline for the first period', () => {
    const payload: NetValuePayload = {
      dates: ['2025-01-02', '2025-01-31'],
      net_value: [0.99, 1.01],
    }

    expect(buildReturnHeatmapData(payload, 'month', 'return')?.seriesData[0]?.[2]).toBe(2.02)
    expect(buildReturnHeatmapData(payload, 'month', 'return', 1)?.seriesData[0]?.[2]).toBe(1)
  })

  it('uses the excess series independently and rejects payloads without finite values', () => {
    const payload: NetValuePayload = {
      dates: ['2025-01-02', '2025-01-31'],
      net_value: [1, 1.5],
      excess_net_value: [1, 0.9],
    }

    expect(hasReturnHeatmapExcessSeries(payload)).toBe(true)
    expect(buildReturnHeatmapData(payload, 'month', 'excess')?.seriesData[0]?.[2]).toBe(-10)
    expect(hasReturnHeatmapExcessSeries({ dates: payload.dates, net_value: payload.net_value })).toBe(false)
    expect(buildReturnHeatmapData({ dates: ['bad'], net_value: [1] }, 'month', 'return')).toBeNull()
  })

  it('supports compact dates, quarters, weeks, and zero-valued bases', () => {
    const quarterly = buildReturnHeatmapData({
      dates: ['20250101', '2025-03-31T15:00:00', '2025-04-30'],
      net_value: [0, 1, 1.1],
    }, 'quarter', 'return')
    expect(quarterly?.seriesData).toEqual([
      [0, 0, 0, '2025 Q1', '2025-01-01', '2025-03-31', 'Q1', '2025'],
      [1, 0, 10, '2025 Q2', '2025-03-31', '2025-04-30', 'Q2', '2025'],
    ])
    expect(quarterly?.maxAbs).toBe(10)

    const weekly = buildReturnHeatmapData({
      dates: ['2021-01-03', '2021-01-04'],
      net_value: [1, 1.1],
    }, 'week', 'return')
    expect(weekly?.yearLabels).toEqual(['2021', '2020'])
    expect(weekly?.seriesData.map(cell => [cell[3], cell[2]])).toEqual([
      ['2020 W53', 0],
      ['2021 W01', 10],
    ])
  })

  it.each(['America/Los_Angeles', 'Pacific/Honolulu'])(
    'keeps calendar boundaries stable in %s',
    (timezone) => {
      vi.stubEnv('TZ', timezone)
      const result = buildReturnHeatmapData({
        dates: ['2025-01-01', '2025-03-31', '2025-04-01'],
        net_value: [1, 1.1, 1.21],
      }, 'quarter', 'return')

      expect(result?.yearLabels).toEqual(['2025'])
      expect(result?.seriesData).toEqual([
        [0, 0, 10, '2025 Q1', '2025-01-01', '2025-03-31', 'Q1', '2025'],
        [1, 0, 10, '2025 Q2', '2025-03-31', '2025-04-01', 'Q2', '2025'],
      ])
    },
  )

  it('ignores blank dates and non-finite values while accepting numeric API strings', () => {
    const malformed = {
      dates: ['', '2025-01-01', '2025-01-02', '2025-01-03', '2025-01-04'],
      net_value: [1, null, '', Number.POSITIVE_INFINITY, '1.2'],
      excess_net_value: 'not-an-array',
    } as unknown as NetValuePayload

    const result = buildReturnHeatmapData(malformed, 'month', 'return')

    expect(result?.seriesData[0]?.[2]).toBe(0)
    expect(result?.maxAbs).toBe(1)
    expect(hasReturnHeatmapExcessSeries({ dates: 'bad' } as unknown as NetValuePayload)).toBe(false)
    expect(hasReturnHeatmapExcessSeries(null)).toBe(false)
  })

  it('maps ISO week boundaries to the ISO week-year', () => {
    expect(resolveIsoWeek(new Date(2021, 0, 1))).toEqual({ year: 2020, week: 53 })
    expect(resolveIsoWeek(new Date(2021, 0, 4))).toEqual({ year: 2021, week: 1 })
    expect(resolveIsoWeek(new Date(2021, 0, 3))).toEqual({ year: 2020, week: 53 })
    expect(resolveXAxisLabels('week')).toHaveLength(53)
    expect(resolveXAxisLabels('quarter')).toEqual(['Q1', 'Q2', 'Q3', 'Q4'])
  })

  it('formats values and explains excess-series fallback without chart dependencies', () => {
    expect(formatReturnHeatmapPercent(2.345)).toBe('+2.35%')
    expect(formatReturnHeatmapPercent(-0.5)).toBe('-0.50%')
    expect(buildReturnHeatmapPanelNote({
      scope: 'compare',
      granularity: 'quarter',
      metricMode: 'excess',
      hasExcessSeries: false,
    })).toContain('当前对比策略未提供超额曲线，已自动回退到收益分布。')
    expect(buildReturnHeatmapPanelNote({
      scope: 'detail',
      granularity: 'week',
      metricMode: 'excess',
      hasExcessSeries: false,
    })).toContain('当前净值数据未提供超额曲线，已自动回退到收益分布。')
    expect(buildReturnHeatmapPanelNote({
      scope: 'detail',
      granularity: 'quarter',
      metricMode: 'return',
      hasExcessSeries: true,
    })).toContain('当前为按季查看收益分布；单元格收益按分段累计净值变化计算。')
  })
})
