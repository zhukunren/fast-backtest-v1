import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DrawdownChartPanel from './DrawdownChartPanel.vue'

const chartApi = vi.hoisted(() => ({ init: vi.fn() }))

vi.mock('../lib/echarts', () => ({ init: chartApi.init }))

function createChart() {
  return {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }
}

function renderedOption(chart: ReturnType<typeof createChart>) {
  const call = chart.setOption.mock.calls.find(([, replace]) => replace === true)
  if (!call) throw new Error('drawdown option was not rendered')
  return call[0] as {
    tooltip: { valueFormatter: (value: unknown) => string }
    xAxis: { axisLabel: { formatter: (value: string) => string } }
    yAxis: {
      min: (axis: { min: number }) => number
      axisLabel: { formatter: (value: number) => string }
    }
    series: Array<{
      id: string
      data: Array<number | null>
      label?: { position: string; formatter: string }
    }>
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
})

describe('DrawdownChartPanel', () => {
  it('normalizes valid points and renders the deepest drawdown contract', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(DrawdownChartPanel, {
      attachTo: document.body,
      props: {
        data: {
          dates: ['2025-01-01', '', '20250103', '2025/01/04', 'other'],
          net_value: [1, 2, 0.8, Number.NaN, 1.2],
        },
        title: '风险回撤',
        note: '确定性说明',
      },
    })
    await flushPromises()

    expect(wrapper.get('.panel-title').text()).toContain('风险回撤')
    expect(wrapper.get('.panel-note').text()).toBe('确定性说明')
    expect(wrapper.get('.drawdown-summary').text()).toContain('-20.00%')
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('2025-01-01至other')

    const option = renderedOption(chart)
    expect(option.series.map(series => series.id)).toEqual([
      'drawdown-series',
      'deepest-drawdown-series',
    ])
    expect(option.series[0].data[0]).toBe(0)
    expect(option.series[0].data[1]).toBeCloseTo(-20)
    expect(option.series[0].data[2]).toBe(0)
    expect(option.series[1].data[0]).toBeNull()
    expect(option.series[1].data[1]).toBeCloseTo(-20)
    expect(option.series[1].data[2]).toBeNull()
    expect(option.series[1].label).toMatchObject({ position: 'top', formatter: '最深 -20.00%' })
    expect(option.tooltip.valueFormatter(-12.345)).toBe('-12.35%')
    expect(option.xAxis.axisLabel.formatter('2025-01-03')).toBe('2025-01')
    expect(option.xAxis.axisLabel.formatter('20250103')).toBe('2025-01')
    expect(option.xAxis.axisLabel.formatter('other')).toBe('other')
    expect(option.yAxis.min({ min: Number.NaN })).toBe(-1)
    expect(option.yAxis.min({ min: 0 })).toBe(-1)
    expect(option.yAxis.min({ min: -20 })).toBe(-224 / 10)
    expect(option.yAxis.axisLabel.formatter(-12.4)).toBe('-12%')

    window.dispatchEvent(new Event('resize'))
    expect(chart.resize).toHaveBeenCalledTimes(1)
    wrapper.unmount()
    expect(chart.dispose).toHaveBeenCalledTimes(1)
  })

  it('includes the explicit initial capital baseline in the drawdown series', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(DrawdownChartPanel, {
      attachTo: document.body,
      props: {
        data: {
          dates: ['2025-01-02', '2025-01-03'],
          net_value: [0.99, 1.01],
        },
        baselineValue: 1,
      },
    })
    await flushPromises()

    const option = renderedOption(chart)
    expect(option.series[0].data[0]).toBeCloseTo(-1)
    expect(option.series[0].data[1]).toBe(0)
    wrapper.unmount()
  })

  it('fails closed for malformed data and recovers when valid data arrives', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(DrawdownChartPanel, {
      props: {
        data: {
          dates: ['', '2025-01-02'],
          net_value: [1, -1],
        },
      },
    })
    await flushPromises()

    expect(wrapper.get('.detail-chart-empty').text()).toContain('暂无可计算')
    expect(chartApi.init).not.toHaveBeenCalled()

    await wrapper.setProps({
      data: {
        dates: ['2025-01-01', '2025-01-02'],
        net_value: [1, 0.9],
      },
    })
    await flushPromises()
    expect(chartApi.init).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ data: null })
    await flushPromises()
    expect(chart.dispose).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.detail-chart-empty').exists()).toBe(true)
  })
})
