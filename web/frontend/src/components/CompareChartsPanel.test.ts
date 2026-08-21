import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CompareChartsPanel from './CompareChartsPanel.vue'

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
  if (!call) throw new Error('compare option was not rendered')
  return call[0] as {
    tooltip: { valueFormatter: (value: unknown) => string }
    yAxis: {
      min: (axis: { min: number }) => number
      axisLabel: { formatter: (value: number) => string }
    }
    dataZoom: Array<{ id: string }>
    series: Array<{ name: string; data: Array<number | null> }>
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('CompareChartsPanel', () => {
  it('keeps both charts synchronized across range, resize, update, and disposal', async () => {
    const netChart = createChart()
    const excessChart = createChart()
    chartApi.init.mockReturnValueOnce(netChart).mockReturnValueOnce(excessChart)
    const dates = Array.from({ length: 70 }, (_, index) => `d${index + 1}`)
    const curves = {
      dates,
      netSeries: [{ name: 'alpha', data: dates.map((_, index) => 1 + index / 100), color: '#111111' }],
      excessSeries: [{ name: 'alpha', data: dates.map((_, index) => index / 100), color: '#222222' }],
      benchmarkLabel: '沪深300',
    }
    const wrapper = mount(CompareChartsPanel, { attachTo: document.body, props: { curves } })
    await flushPromises()

    expect(wrapper.get('.panel-note').text()).toContain('沪深300')
    expect(wrapper.findAll('[role="img"]')[0].attributes('aria-label')).toContain('d1至d70')
    const option = renderedOption(netChart)
    expect(option.series[0]).toMatchObject({ name: 'alpha', data: curves.netSeries[0].data })
    expect(option.dataZoom.map(item => item.id)).toEqual(['compare-net-inside', 'compare-net-slider'])
    expect(option.tooltip.valueFormatter(1.23456)).toBe('1.2346')
    expect(option.tooltip.valueFormatter('bad')).toBe('-')
    expect(option.yAxis.min({ min: 0.9 })).toBe(0.8)
    expect(option.yAxis.min({ min: 0.7 })).toBe(0.7)
    expect(option.yAxis.axisLabel.formatter(1.234)).toBe('1.23')

    await wrapper.findAll('button')[0].trigger('click')
    expect(wrapper.findAll('button')[0].classes()).toContain('active')
    expect(netChart.setOption).toHaveBeenLastCalledWith({
      dataZoom: [
        { id: 'compare-net-inside', startValue: 'd61', endValue: 'd70' },
        { id: 'compare-net-slider', startValue: 'd61', endValue: 'd70' },
      ],
    })
    await wrapper.findAll('button')[1].trigger('click')
    await wrapper.findAll('button')[2].trigger('click')
    expect(wrapper.findAll('button')[2].classes()).toContain('active')

    window.dispatchEvent(new Event('resize'))
    expect(netChart.resize).toHaveBeenCalledTimes(1)
    expect(excessChart.resize).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ curves: null })
    await flushPromises()
    expect(netChart.dispose).toHaveBeenCalledTimes(1)
    expect(excessChart.dispose).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.detail-chart-empty').text()).toContain('暂无可对比')
  })
})
