import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ReturnHeatmapData } from '../utils/returnHeatmap'
import ReturnHeatmapChart from './ReturnHeatmapChart.vue'

const chartApi = vi.hoisted(() => ({ init: vi.fn() }))

vi.mock('../lib/echarts', () => ({ init: chartApi.init }))

function createChart() {
  return {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }
}

const data: ReturnHeatmapData = {
  xAxisLabels: ['1月', '2月'],
  yearLabels: ['2024', '2025'],
  seriesData: [
    [0, 0, 0.1, '2024年1月', '2024-01-01', '2024-01-31', '1月', '2024'],
    [1, 1, -0.2, '', '2025-02-01', '2025-02-28', '2月', '2025'],
  ],
  maxAbs: 0.2,
}

function renderedOption(chart: ReturnType<typeof createChart>) {
  const calls = chart.setOption.mock.calls.filter(([, replace]) => replace === true)
  if (!calls.length) throw new Error('return heatmap option was not rendered')
  return calls.at(-1)?.[0] as {
    tooltip: { formatter: (params: { data?: unknown }) => string }
    visualMap: { min: number; max: number }
    xAxis: { axisLabel: { interval: number; fontSize: number } }
    series: Array<{ label: { show: boolean; formatter: (params: { data?: unknown }) => string } }>
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
})

describe('ReturnHeatmapChart', () => {
  it('renders accessible return and excess options and redraws responsively', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(ReturnHeatmapChart, {
      attachTo: document.body,
      props: { data, metricMode: 'return', height: 340 },
    })
    await flushPromises()

    expect(wrapper.attributes('style')).toContain('height: 340px')
    expect(wrapper.attributes('aria-label')).toContain('策略收益热力图，共2个时间区间')
    let option = renderedOption(chart)
    expect(option.visualMap).toMatchObject({ min: -1, max: 1 })
    expect(option.xAxis.axisLabel).toMatchObject({ interval: 0, fontSize: 12 })
    expect(option.series[0].label.show).toBe(true)
    expect(option.series[0].label.formatter({ data: data.seriesData[0] })).toBe('+0.10%')
    expect(option.series[0].label.formatter({ data: [0, 0, Number.NaN] })).toBe('+0.00%')
    expect(option.tooltip.formatter({ data: data.seriesData[0] })).toContain('2024年1月')
    expect(option.tooltip.formatter({ data: data.seriesData[1] })).toContain('2025 2月')
    expect(option.tooltip.formatter({ data: [0, 0, Number.NaN] })).toContain('收益: +0.00%')

    await wrapper.setProps({ metricMode: 'excess', maxAbs: 3 })
    await flushPromises()
    expect(wrapper.attributes('aria-label')).toContain('超额收益')
    option = renderedOption(chart)
    expect(option.visualMap).toMatchObject({ min: -3, max: 3 })

    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(300)
    window.dispatchEvent(new Event('resize'))
    option = renderedOption(chart)
    expect(option.xAxis.axisLabel.fontSize).toBe(10)
    expect(option.series[0].label.show).toBe(false)
    expect(chart.resize).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ data: null })
    await flushPromises()
    expect(chart.dispose).toHaveBeenCalledTimes(1)
  })
})
