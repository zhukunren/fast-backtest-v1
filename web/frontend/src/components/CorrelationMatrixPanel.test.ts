import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CorrelationMatrixPanel from './CorrelationMatrixPanel.vue'

const chartApi = vi.hoisted(() => ({ init: vi.fn() }))

vi.mock('../lib/echarts', () => ({ init: chartApi.init }))

function createChart() {
  return {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }
}

function lastFullOption(chart: ReturnType<typeof createChart>) {
  const calls = chart.setOption.mock.calls.filter(([, replace]) => replace === true)
  if (!calls.length) throw new Error('correlation option was not rendered')
  return calls.at(-1)?.[0] as Record<string, unknown>
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('CorrelationMatrixPanel', () => {
  it('renders a finite correlation matrix and exposes stable tooltip values', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(CorrelationMatrixPanel, {
      attachTo: document.body,
      props: {
        dates: ['d1', 'd2', 'd3', 'd4'],
        strategies: [
          { name: 'alpha', values: [1, 2, 3, 4] },
          { name: 'beta', values: [2, 4, 6, 8] },
          { name: 'constant', values: [1, 1, Number.NaN, 1] },
        ],
      },
    })
    await flushPromises()

    expect(wrapper.get('.panel-note').text()).toContain('相关系数矩阵')
    const option = lastFullOption(chart) as {
      tooltip: { formatter: (params: { data: [number, number, number] }) => string }
      series: Array<{
        data: Array<[number, number, number]>
        label: { formatter: (params: { data: [number, number, number] }) => string }
      }>
    }
    expect(option.series[0].data).toHaveLength(9)
    expect(option.series[0].data[1][2]).toBeCloseTo(1)
    expect(option.series[0].data[2][2]).toBe(0)
    expect(option.tooltip.formatter({ data: [1, 0, 1] })).toContain('alpha vs beta')
    expect(option.series[0].label.formatter({ data: [1, 0, 1] })).toBe('1.00')

    window.dispatchEvent(new Event('resize'))
    expect(chart.resize).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    expect(chart.dispose).toHaveBeenCalledTimes(1)
  })

  it('switches rolling windows and disposes the chart when common data becomes insufficient', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const dates = Array.from({ length: 25 }, (_, index) => `2025-01-${String(index + 1).padStart(2, '0')}`)
    const wrapper = mount(CorrelationMatrixPanel, {
      props: {
        dates,
        strategies: [
          { name: 'alpha', values: dates.map((_, index) => index) },
          { name: 'beta', values: dates.map((_, index) => index * -1) },
        ],
      },
    })
    await flushPromises()

    expect(wrapper.get('.panel-note').text()).toContain('滚动相关性')
    let option = lastFullOption(chart) as {
      title: { text: string; subtext: string }
      tooltip: { formatter: (params: Array<{ axisValue: string; value: number }>) => string }
      series: Array<{ data: number[] }>
    }
    expect(option.title).toMatchObject({ text: '滚动相关性 (20日窗口)', subtext: 'alpha vs beta' })
    expect(option.series[0].data).toHaveLength(6)
    expect(option.tooltip.formatter([{ axisValue: dates[19], value: -1 }])).toContain('-1.000')

    await wrapper.get('select').setValue('10')
    await flushPromises()
    option = lastFullOption(chart) as typeof option
    expect(option.title.text).toBe('滚动相关性 (10日窗口)')
    expect(option.series[0].data).toHaveLength(16)

    await wrapper.setProps({ dates: ['d1'], strategies: [{ name: 'alpha', values: [1] }] })
    await flushPromises()
    expect(chart.dispose).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.detail-chart-empty').exists()).toBe(true)
  })
})
