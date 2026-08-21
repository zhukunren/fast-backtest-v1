import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { NetValuePayload } from '../types/library'
import NetValueChartPanel from './NetValueChartPanel.vue'

const chartApi = vi.hoisted(() => ({
  init: vi.fn(),
}))

vi.mock('../lib/echarts', () => ({ init: chartApi.init }))

type EventHandler = (event: Record<string, unknown>) => void

function createChart(dates: string[]) {
  const chartHandlers = new Map<string, EventHandler>()
  const zrHandlers = new Map<string, EventHandler>()
  const zr = {
    on: vi.fn((name: string, handler: EventHandler) => zrHandlers.set(name, handler)),
    off: vi.fn((name: string) => zrHandlers.delete(name)),
  }
  const chart = {
    setOption: vi.fn(),
    dispatchAction: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
    getZr: vi.fn(() => zr),
    on: vi.fn((name: string, handler: EventHandler) => chartHandlers.set(name, handler)),
    off: vi.fn((name: string) => chartHandlers.delete(name)),
    containPixel: vi.fn(() => true),
    convertFromPixel: vi.fn((finder: Record<string, unknown>, value: number) => {
      if ('xAxisIndex' in finder) return Math.round(value / 100)
      return 1.3 - (value / 1000)
    }),
    convertToPixel: vi.fn((finder: Record<string, unknown>, value: string | number) => {
      if ('xAxisIndex' in finder) return Math.max(0, dates.indexOf(String(value))) * 100
      return (1.3 - Number(value)) * 1000
    }),
  }
  return { chart, chartHandlers, zrHandlers, zr }
}

const dates = Array.from({ length: 70 }, (_, index) => {
  const date = new Date(2025, 0, 1 + index)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
})

const values = dates.map((_, index) => {
  if (index === 25) return 1.01
  if (index === 45) return 1.04
  return 1 + index * 0.002
})

const payload: NetValuePayload = {
  dates,
  net_value: values,
  benchmark_net_value: dates.map((_, index) => 1 + index * 0.001),
  excess_net_value: dates.map((_, index) => 1 + index * 0.0008),
  benchmark_label: '创业板指 399006.SZ',
}

type NetValueOption = {
  legend?: { data: string[] }
  tooltip: {
    triggerOn: string
    formatter?: (params: unknown) => string
    valueFormatter: (value: unknown) => string
    position: (
      point: number[],
      params: unknown,
      dom: unknown,
      rect: unknown,
      size: { contentSize: number[]; viewSize: number[] },
    ) => number[]
  }
  xAxis: Record<string, unknown> | Array<Record<string, unknown>>
  yAxis: Record<string, unknown> | Array<Record<string, unknown>>
  dataZoom?: Array<Record<string, unknown>>
  series: Array<Record<string, unknown>>
}

function fullOption(chart: ReturnType<typeof createChart>['chart']): NetValueOption {
  const call = chart.setOption.mock.calls.find(([option]) => (
    option && typeof option === 'object' && 'animation' in option && Array.isArray(option.series)
  ))
  if (!call) throw new Error('full chart option was not rendered')
  return call[0] as NetValueOption
}

function setChartDimensions(wrapper: ReturnType<typeof mount>) {
  const wrap = wrapper.get('.detail-chart-wrap').element
  Object.defineProperty(wrap, 'clientWidth', { configurable: true, value: 800 })
  Object.defineProperty(wrap, 'clientHeight', { configurable: true, value: 500 })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('NetValueChartPanel', () => {
  it('builds benchmark, excess, accessibility, and range options from valid data', async () => {
    const mock = createChart(dates)
    chartApi.init.mockReturnValue(mock.chart)
    const wrapper = mount(NetValueChartPanel, {
      attachTo: document.body,
      props: { data: payload, focusDate: dates[5] },
    })
    await flushPromises()

    const option = fullOption(mock.chart)
    expect(option.legend?.data).toEqual(['策略净值', '创业板指 399006.SZ', '超额曲线'])
    expect(option.series.map(series => series.id)).toEqual([
      'net-value-series',
      'benchmark-series',
      'excess-series',
    ])
    expect(wrapper.get('.panel-note').text()).toContain('基准：创业板指 399006.SZ')
    expect(wrapper.get('.panel-note').text()).toContain('包含超额曲线')
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain(`共${dates.length}个净值点`)
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('最大回撤')
    expect(mock.chart.dispatchAction).toHaveBeenCalledWith({ type: 'showTip', seriesIndex: 0, dataIndex: 5 })

    expect(option.tooltip.triggerOn).toBe('mousemove|click|mousewheel')
    expect(option.tooltip.valueFormatter(1.23456)).toBe('1.2346')
    expect(option.tooltip.valueFormatter('invalid')).toBe('-')
    expect(option.tooltip.position([790, 10], null, null, null, { contentSize: [200, 100], viewSize: [800, 500] })).toEqual([588, 12])
    const defaultYAxis = option.yAxis as { min: (axis: { min: number }) => number; axisLabel: { formatter: (value: number) => string } }
    expect(defaultYAxis.min({ min: 0.95 })).toBe(0.8)
    expect(defaultYAxis.axisLabel.formatter(1.234)).toBe('1.23')

    await wrapper.findAll('.toolbar-btn')[0].trigger('click')
    expect(wrapper.findAll('.toolbar-btn')[0].classes()).toContain('active')
    expect(mock.chart.setOption).toHaveBeenCalledWith(expect.objectContaining({
      dataZoom: [
        expect.objectContaining({ startValue: dates[60], endValue: dates[69] }),
        expect.objectContaining({ startValue: dates[60], endValue: dates[69] }),
      ],
    }))
    await wrapper.findAll('.toolbar-btn')[1].trigger('click')
    expect(mock.chart.setOption).toHaveBeenCalledWith(expect.objectContaining({
      dataZoom: [
        expect.objectContaining({ startValue: dates[10], endValue: dates[69] }),
        expect.objectContaining({ startValue: dates[10], endValue: dates[69] }),
      ],
    }))
    await wrapper.findAll('.toolbar-btn')[2].trigger('click')
    expect(wrapper.findAll('.toolbar-btn')[2].classes()).toContain('active')
  })

  it('turns a real pointer drag into a selection summary and synchronized focus date', async () => {
    const mock = createChart(dates)
    chartApi.init.mockReturnValue(mock.chart)
    const wrapper = mount(NetValueChartPanel, {
      attachTo: document.body,
      props: { data: payload, focusDate: '' },
    })
    await flushPromises()
    setChartDimensions(wrapper)

    const down = mock.zrHandlers.get('mousedown')!
    const move = mock.zrHandlers.get('mousemove')!
    const up = mock.zrHandlers.get('mouseup')!
    down({ offsetX: 100, offsetY: 100, event: { button: 0 } })
    move({ offsetX: 400, offsetY: 300 })
    up({})
    await wrapper.vm.$nextTick()

    expect(wrapper.get('.detail-selection-band').attributes('style')).toContain('width: 400px')
    expect(wrapper.get('.detail-selection-box').text()).toContain(`${dates[1]} ~ ${dates[4]}`)
    expect(wrapper.get('.detail-selection-box').text()).toContain('1.0000 ~ 1.2000')
    expect(wrapper.get('.detail-selection-box').text()).toContain('区间收益')
    expect(wrapper.emitted('focusDate')?.at(-1)).toEqual([dates[4]])
    expect(mock.chart.setOption).toHaveBeenCalledWith(expect.objectContaining({
      series: [expect.objectContaining({ id: 'net-value-series', markArea: expect.any(Object) })],
    }))

    window.dispatchEvent(new Event('resize'))
    expect(mock.chart.resize).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.secondary-btn').attributes('disabled')).toBeUndefined()
    await wrapper.get('.secondary-btn').trigger('click')
    expect(wrapper.find('.detail-selection-box').exists()).toBe(false)
    expect(wrapper.get('.secondary-btn').attributes('disabled')).toBeDefined()

    mock.chart.containPixel.mockReturnValueOnce(false)
    down({ offsetX: 20, offsetY: 20, event: { button: 0 } })
    expect(wrapper.find('.detail-selection-band').exists()).toBe(false)
    down({ offsetX: 20, offsetY: 20, event: { button: 2 } })
    expect(wrapper.find('.detail-selection-band').exists()).toBe(false)

    down({ offsetX: 100, offsetY: 100, event: { button: 0 } })
    move({ offsetX: 103, offsetY: 102 })
    up({})
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.detail-selection-band').exists()).toBe(false)

    mock.zrHandlers.get('globalout')!({})
    expect(mock.chart.dispatchAction).toHaveBeenLastCalledWith({ type: 'showTip', seriesIndex: 0, dataIndex: expect.any(Number) })
  })

  it('renders workbench axes and safely reacts to normalized datazoom events', async () => {
    const specialDates = ['2025-01-01', '2025-01-02', '2025-01-03', '2025-01-04']
    const mock = createChart(specialDates)
    chartApi.init.mockReturnValue(mock.chart)
    const wrapper = mount(NetValueChartPanel, {
      attachTo: document.body,
      props: {
        data: {
          dates: specialDates,
          net_value: [1, 1.2, 1.05, 1.3],
          benchmark_net_value: [1, 1.1, 1.08, 1.2],
          excess_net_value: [1, 1.1, 0.97, 1.1],
          benchmark_label: '基准 <script> 399006.SZ',
        },
        focusDate: specialDates[1],
        variant: 'workbench',
        showDataZoom: false,
        showControls: false,
      },
    })
    await flushPromises()

    const option = fullOption(mock.chart)
    expect(wrapper.find('.detail-toolbar').exists()).toBe(false)
    expect(Array.isArray(option.xAxis)).toBe(true)
    expect(Array.isArray(option.yAxis)).toBe(true)
    expect(option.dataZoom).toBeUndefined()
    expect(option.series.map(series => series.id)).toContain('drawdown-risk-series')
    expect(option.legend?.data).toContain('基准 <script>')
    expect(mock.chart.dispatchAction).toHaveBeenCalledWith({ type: 'hideTip' })

    const tooltip = option.tooltip.formatter!([{ dataIndex: 2 }])
    expect(tooltip).toContain('2025-01-03')
    expect(tooltip).toContain('基准 &lt;script&gt;')
    expect(tooltip).not.toContain('基准 <script>')
    expect(option.tooltip.formatter!({ dataIndex: 999 })).toContain('2025-01-04')
    expect(option.tooltip.formatter!(null)).toContain('2025-01-02')
    expect(option.tooltip.valueFormatter(1.2)).toBe('1.2000')
    const axes = option.yAxis as Array<{ axisLabel: { formatter: (value: number) => string }; min: number; max: number }>
    expect(axes[0].axisLabel.formatter(1.2)).toBe('1.20')
    expect(axes[1].axisLabel.formatter(-0.125)).toBe('-12%')
    expect(axes[0].min).toBeLessThan(1)
    expect(axes[0].max).toBeGreaterThan(1.2)

    const zoom = mock.chartHandlers.get('datazoom')!
    const before = mock.chart.setOption.mock.calls.length
    zoom({ start: 25, end: 75 })
    expect(mock.chart.setOption.mock.calls.length).toBe(before + 1)
    zoom({ start: 25, end: 75 })
    expect(mock.chart.setOption.mock.calls.length).toBe(before + 1)
    zoom({ batch: [{ startValue: specialDates[3], endValue: specialDates[1] }] })
    expect(mock.chart.setOption.mock.calls.length).toBe(before + 2)
    zoom({ startValue: 0, endValue: 2 })
    expect(mock.chart.setOption.mock.calls.length).toBe(before + 3)
    zoom({ start: 'invalid', end: 'invalid' })
    expect(mock.chart.setOption.mock.calls.length).toBe(before + 3)
    zoom(null as unknown as Record<string, unknown>)
    expect(mock.chart.setOption.mock.calls.length).toBe(before + 3)

    mock.zrHandlers.get('globalout')!({})
    expect(mock.chart.dispatchAction).toHaveBeenLastCalledWith({ type: 'hideTip' })
  })

  it('uses the explicit initial capital baseline for full-period drawdown and aria return', async () => {
    const shortDates = ['2025-01-02', '2025-01-03']
    const mock = createChart(shortDates)
    chartApi.init.mockReturnValue(mock.chart)
    const wrapper = mount(NetValueChartPanel, {
      attachTo: document.body,
      props: {
        data: { dates: shortDates, net_value: [0.99, 1.01] },
        focusDate: '',
        variant: 'workbench',
        baselineValue: 1,
        showDataZoom: false,
        showControls: false,
      },
    })
    await flushPromises()

    const option = fullOption(mock.chart)
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('区间收益1.00%')
    const drawdownSeries = option.series.find(series => series.id === 'drawdown-risk-series')
    const drawdownData = (drawdownSeries?.data as number[] | undefined) ?? []
    expect(drawdownData[0]).toBeCloseTo(-0.01)
    expect(drawdownData[1]).toBe(0)
  })

  it('updates focus and variant without recreating the chart, then disposes all handlers', async () => {
    const mock = createChart(dates)
    chartApi.init.mockReturnValue(mock.chart)
    const removeEventListener = vi.spyOn(window, 'removeEventListener')
    const wrapper = mount(NetValueChartPanel, {
      attachTo: document.body,
      props: { data: payload, focusDate: dates[2], note: '自定义说明' },
    })
    await flushPromises()
    expect(wrapper.get('.panel-note').text()).toBe('自定义说明')

    await wrapper.setProps({ focusDate: dates[12] })
    expect(mock.chart.dispatchAction).toHaveBeenLastCalledWith({ type: 'showTip', seriesIndex: 0, dataIndex: 12 })
    await wrapper.setProps({ focusDate: 'missing' })
    await wrapper.setProps({ variant: 'workbench' })
    await flushPromises()
    expect(chartApi.init).toHaveBeenCalledTimes(1)
    expect(mock.chart.on).toHaveBeenCalledWith('datazoom', expect.any(Function))

    wrapper.unmount()
    expect(mock.chart.off).toHaveBeenCalledWith('datazoom')
    expect(mock.zr.off).toHaveBeenCalledWith('mousedown')
    expect(mock.zr.off).toHaveBeenCalledWith('globalout')
    expect(mock.chart.dispose).toHaveBeenCalledTimes(1)
    expect(removeEventListener).toHaveBeenCalledWith('resize', expect.any(Function))
  })

  it('fails closed for malformed series lengths and can recover when valid data is supplied', async () => {
    const mock = createChart(dates)
    chartApi.init.mockReturnValue(mock.chart)
    const wrapper = mount(NetValueChartPanel, {
      attachTo: document.body,
      props: {
        data: { dates: dates.slice(0, 2), net_value: [1, Number.NaN] },
        focusDate: '',
        showDataZoom: false,
      },
    })
    await flushPromises()

    expect(wrapper.get('.detail-chart-empty').text()).toBe('暂无回测数据')
    expect(wrapper.get('.panel-note').text()).toContain('还没有可展示')
    expect(chartApi.init).not.toHaveBeenCalled()

    await wrapper.setProps({
      data: {
        dates: dates.slice(0, 3),
        net_value: [0, 0, 0],
        benchmark_net_value: [1],
        excess_net_value: [1, 1],
        benchmark_name: '无代码基准',
      },
    })
    await flushPromises()

    expect(chartApi.init).toHaveBeenCalledTimes(1)
    const option = fullOption(mock.chart)
    expect(option.legend).toBeUndefined()
    expect(option.series).toHaveLength(1)
    expect(option.dataZoom).toBeUndefined()
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('区间收益-')

    await wrapper.setProps({ data: null })
    await flushPromises()
    expect(mock.chart.dispose).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.detail-chart-empty').exists()).toBe(true)
  })
})
