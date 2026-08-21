import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TradeDistributionPanel from './TradeDistributionPanel.vue'

const chartApi = vi.hoisted(() => ({
  init: vi.fn(),
}))

vi.mock('../lib/echarts', () => ({ init: chartApi.init }))

function createChart() {
  return {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }
}

const trades = [
  { stock_code: '000001.SZ', stock_name: 'A', entry_date: '20250106', exit_date: '20250107', holding_days: 1, return_rate: 0.1 },
  { stock_code: '000002.SZ', stock_name: 'B', entry_date: '2025-01-07', exit_date: '2025-01-09', holding_days: 2, return_rate: -0.05 },
  { stock_code: '000003.SZ', stock_name: 'C', entry_date: '2025/03/09', exit_date: '2025/03/13', holding_days: 4, return_rate: 0.2 },
  { stock_code: '000004.SZ', stock_name: 'D', entry_date: '2025-03-10T09:30:00', exit_date: '2025-03-18', holding_days: 8, return_rate: 0 },
  { stock_code: '000005.SZ', stock_name: 'E', entry_date: '20250311', exit_date: '20250401', holding_days: 16, return_rate: 0.03 },
  { stock_code: '000006.SZ', stock_name: 'F', entry_date: '2025-03-12', exit_date: '2025-05-01', holding_days: 31, return_rate: -0.02 },
  { stock_code: '000007.SZ', stock_name: 'G', entry_date: '2025/03/13', exit_date: '2025/06/01', holding_days: 61, return_rate: 0.07 },
]

type DistributionOption = {
  xAxis: {
    data: string[]
    axisLabel: { formatter: (value: string) => string; interval: number | string }
  }
  series: Array<{ data: number[] }>
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TradeDistributionPanel', () => {
  it('renders an empty state until complete trades arrive, then fills zero-trade months', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(TradeDistributionPanel, {
      attachTo: document.body,
      props: {
        trades: [
          { stock_code: 'x', stock_name: 'x', entry_date: '', exit_date: '20250101', holding_days: 0, return_rate: 0 },
          { stock_code: 'y', stock_name: 'y', entry_date: 'bad-date', exit_date: '', holding_days: 0, return_rate: 0 },
        ],
      },
    })

    expect(wrapper.get('.detail-chart-empty').text()).toBe('暂无交易数据')
    expect(chartApi.init).not.toHaveBeenCalled()

    await wrapper.setProps({ trades })
    await flushPromises()

    expect(chartApi.init).toHaveBeenCalledTimes(1)
    const option = chart.setOption.mock.calls.at(-1)?.[0] as DistributionOption
    expect(option.xAxis.data).toEqual(['2025-01', '2025-02', '2025-03'])
    expect(option.series[0].data).toEqual([2, 0, 5])
    expect(option.series[1].data[0]).toBeCloseTo(2.5)
    expect(option.series[1].data[2]).toBeCloseTo(5.6)
    expect(wrapper.get('.zero-month-note').text()).toContain('2025-02')
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('当前按月份统计7笔交易')
    expect(option.xAxis.axisLabel.formatter('2025-01')).toBe('2025\n01')
    expect(option.xAxis.axisLabel.formatter('2025-04')).toBe('04')
    expect(option.xAxis.axisLabel.formatter('2025-02')).toBe('')
    expect(option.xAxis.axisLabel.formatter('unknown')).toBe('unknown')
  })

  it('switches between weekday and every holding-period bucket with observable aggregates', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(TradeDistributionPanel, {
      attachTo: document.body,
      props: { trades, title: '成交画像', note: '实际成交统计' },
    })
    await flushPromises()

    expect(wrapper.get('.panel-title').text()).toContain('成交画像')
    expect(wrapper.get('.panel-note').text()).toBe('实际成交统计')
    await wrapper.findAll('.toolbar-btn')[1].trigger('click')

    let option = chart.setOption.mock.calls.at(-1)?.[0] as DistributionOption
    expect(option.xAxis.data).toEqual(['周日', '周一', '周二', '周三', '周四', '周五', '周六'])
    expect(option.series[0].data).toEqual([1, 2, 2, 1, 1, 0, 0])
    expect(option.xAxis.axisLabel.interval).toBe('auto')
    expect(option.xAxis.axisLabel.formatter('周一')).toBe('周一')
    expect(wrapper.find('.zero-month-note').exists()).toBe(false)
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('当前按星期统计7笔交易')

    await wrapper.findAll('.toolbar-btn')[2].trigger('click')
    option = chart.setOption.mock.calls.at(-1)?.[0] as DistributionOption
    expect(option.xAxis.data).toEqual(['1天', '2-3天', '4-7天', '8-15天', '16-30天', '31-60天', '60天+'])
    expect(option.series[0].data).toEqual([1, 1, 1, 1, 1, 1, 1])
    expect(option.series[1].data.map(value => Number(value.toFixed(6)))).toEqual([10, -5, 20, 0, 3, -2, 7])
    expect(wrapper.get('[role="img"]').attributes('aria-label')).toContain('当前按持仓天数统计7笔交易')

    await wrapper.findAll('.toolbar-btn')[0].trigger('click')
    expect(wrapper.findAll('.toolbar-btn')[0].attributes('aria-pressed')).toBe('true')
  })

  it('reuses the chart on updates, resizes it, and disposes it for invalid or unmounted data', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const wrapper = mount(TradeDistributionPanel, {
      attachTo: document.body,
      props: { trades, showControls: false, note: '' },
    })
    await flushPromises()

    expect(wrapper.find('.detail-toolbar').exists()).toBe(false)
    expect(wrapper.find('.panel-note').exists()).toBe(false)
    window.dispatchEvent(new Event('resize'))
    expect(chart.resize).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ trades: [...trades, { ...trades[0], stock_code: 'extra' }] })
    await flushPromises()
    expect(chartApi.init).toHaveBeenCalledTimes(1)
    expect(chart.setOption.mock.calls.length).toBeGreaterThan(1)

    await wrapper.setProps({ trades: null })
    await flushPromises()
    expect(chart.dispose).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.detail-chart-empty').exists()).toBe(true)

    await wrapper.setProps({ trades })
    await flushPromises()
    expect(chartApi.init).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    expect(chart.dispose).toHaveBeenCalledTimes(2)
  })

  it('ignores malformed calendar and holding values without corrupting valid groups', async () => {
    const chart = createChart()
    chartApi.init.mockReturnValue(chart)
    const malformed = [
      ...trades.slice(0, 1),
      { stock_code: 'bad-1', stock_name: 'bad', entry_date: '2025-13-10', exit_date: '2025-01-01', holding_days: 0, return_rate: 1 },
      { stock_code: 'bad-2', stock_name: 'bad', entry_date: 'not-a-date', exit_date: '2025-01-01', holding_days: -3, return_rate: 1 },
    ]
    const wrapper = mount(TradeDistributionPanel, { props: { trades: malformed } })
    await flushPromises()

    await wrapper.findAll('.toolbar-btn')[1].trigger('click')
    let option = chart.setOption.mock.calls.at(-1)?.[0] as DistributionOption
    expect(option.series[0].data.reduce((total, count) => total + count, 0)).toBe(1)

    await wrapper.findAll('.toolbar-btn')[2].trigger('click')
    option = chart.setOption.mock.calls.at(-1)?.[0] as DistributionOption
    expect(option.series[0].data.reduce((total, count) => total + count, 0)).toBe(1)
  })
})
