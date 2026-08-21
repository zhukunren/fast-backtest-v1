import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import HoldingsHeatmapPanel from './HoldingsHeatmapPanel.vue'

const chartApi = vi.hoisted(() => ({ init: vi.fn() }))

vi.mock('../lib/echarts', () => ({ init: chartApi.init }))

function createChart() {
  return {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }
}

function lastOption(chart: ReturnType<typeof createChart>) {
  const calls = chart.setOption.mock.calls.filter(([, replace]) => replace === true)
  if (!calls.length) throw new Error('holdings option was not rendered')
  return calls.at(-1)?.[0] as Record<string, unknown>
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
})

describe('HoldingsHeatmapPanel', () => {
  it('normalizes trading days and switches between calendar and timeline contracts', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(HoldingsHeatmapPanel, {
      attachTo: document.body,
      props: {
        embedded: true,
        holdings: [
          { trade_date: '2024-12-30', position_ratio: 0.25 },
          { trade_date: '2024-12-31', position_ratio: 0.5 },
          { trade_date: '20250101', position_ratio: 1.2 },
          { trade_date: '2025-01-06', position_ratio: 0.8 },
          { trade_date: '2025-01-04', position_ratio: 0.9 },
          { trade_date: '', position_ratio: 0.2 },
          { trade_date: '2025-01-02', position_ratio: Number.NaN },
        ],
      },
    })
    await flushPromises()

    expect(wrapper.attributes('aria-label')).toBe('持仓热力图')
    expect(wrapper.find('.panel-title').exists()).toBe(false)
    let option = lastOption(chart) as {
      tooltip: { formatter: (params: { data: [number, number, number, string] }) => string }
      visualMap: { max: number }
      xAxis: Array<{ data: string[]; axisLabel: { formatter: (value: string) => string } }>
      series: Array<{ data: Array<[number, number, number, string]>; markLine?: { data: unknown[] } }>
    }
    expect(option.visualMap.max).toBe(120)
    expect(option.series[0].data).toHaveLength(4)
    expect(option.tooltip.formatter({ data: [0, 0, 25, '2024-12-30'] })).toContain('25.00%')
    expect(option.xAxis[0].axisLabel.formatter(option.xAxis[0].data[0])).toBe('12月')
    expect(option.series[0].markLine?.data).toHaveLength(1)

    await wrapper.findAll('button')[1].trigger('click')
    await flushPromises()
    expect(wrapper.get('.panel-note').text()).toContain('时间线')
    option = lastOption(chart) as unknown as typeof option
    const timeline = option as unknown as {
      tooltip: { formatter: (params: Array<{ axisValue: string; value: number }>) => string }
      xAxis: { axisLabel: { formatter: (value: string, index: number) => string } }
      series: Array<{ data: number[] }>
    }
    expect(timeline.series[0].data).toEqual([25, 50, 120, 80])
    expect(timeline.tooltip.formatter([{ axisValue: '2025-01-01', value: 120 }])).toContain('仓位: 120%')
    expect(timeline.xAxis.axisLabel.formatter('2024-12-30', 0)).toBe('2024\n12-30')
    expect(timeline.xAxis.axisLabel.formatter('bad-date', 1)).toBe('bad-date')

    window.dispatchEvent(new Event('resize'))
    expect(chart.resize).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    expect(chart.dispose).toHaveBeenCalledTimes(1)
  })

  it('rejects empty and weekend-only data, then recovers from a valid update', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(HoldingsHeatmapPanel, {
      props: { holdings: [{ trade_date: '2025-01-04', position_ratio: 0.5 }] },
    })
    await flushPromises()
    expect(wrapper.get('.detail-chart-empty').text()).toContain('暂无持仓数据')
    expect(chartApi.init).not.toHaveBeenCalled()

    await wrapper.setProps({ holdings: [{ trade_date: '2025-01-06', position_ratio: 0.5 }] })
    await flushPromises()
    expect(chartApi.init).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ holdings: null })
    await flushPromises()
    expect(chart.dispose).toHaveBeenCalledTimes(1)
  })
})
