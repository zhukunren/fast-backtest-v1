import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'

import type { EnhancedCompareResponse, LibraryCatalogResponse, NetValuePayload } from '../../types/library'
import StrategyComparePage from './StrategyComparePage.vue'

const libraryApi = vi.hoisted(() => ({
  fetchEnhancedCompare: vi.fn(),
  fetchLibraryCatalog: vi.fn(),
  fetchLibraryNetValue: vi.fn(),
}))

vi.mock('../../api/library', () => libraryApi)

const catalog: LibraryCatalogResponse = {
  overview: { total: 2, cached: 2, completed: 2, positive: 2 },
  filters: { families: [], statuses: [] },
  items: [
    {
      name: 'alpha',
      title: 'Alpha 动量',
      cum_return: 0.2,
      max_drawdown: -0.08,
      win_rate: 0.61,
      trade_count: 22,
    },
    {
      name: 'beta',
      title: 'Beta 反转',
      cum_return: 0.12,
      max_drawdown: -0.05,
      win_rate: 0.54,
      trade_count: 18,
    },
  ],
}

const netValues: Record<string, NetValuePayload> = {
  alpha: {
    dates: ['2025-01-02', '2025-01-03', '2025-01-06'],
    net_value: [1, 1.01, 1.03],
    excess_net_value: [1, 1.005, 1.015],
    benchmark_label: '创业板指',
  },
  beta: {
    dates: ['2025-01-02', '2025-01-03', '2025-01-06'],
    net_value: [1, 0.99, 1.02],
    excess_net_value: [1, 0.985, 1.005],
    benchmark_label: '创业板指',
  },
}

const enhanced: EnhancedCompareResponse = {
  names: ['alpha', 'beta'],
  strategies: [
    { name: 'alpha', title: 'Alpha 动量', score: 88.5, grade: 'A', cum_return: 0.2 },
    { name: 'beta', title: 'Beta 反转', score: 79.2, grade: 'B', cum_return: 0.12 },
  ],
}

const CompareChartsPanelStub = defineComponent({
  name: 'CompareChartsPanelStub',
  props: { curves: { type: Object, required: true } },
  template: '<div data-testid="compare-charts"></div>',
})

const CompareReturnHeatmapPanelStub = defineComponent({
  name: 'CompareReturnHeatmapPanelStub',
  props: { strategies: { type: Array, required: true } },
  template: '<div data-testid="compare-heatmap"></div>',
})

const CorrelationMatrixPanelStub = defineComponent({
  name: 'CorrelationMatrixPanelStub',
  props: {
    strategies: { type: Array, required: true },
    dates: { type: Array, required: true },
  },
  template: '<div data-testid="correlation-matrix"></div>',
})

function mountPage(names = ['alpha', 'beta']) {
  return shallowMount(StrategyComparePage, {
    props: { names, returnTo: '/?family=momentum' },
    global: {
      stubs: {
        CompareChartsPanel: CompareChartsPanelStub,
        CompareReturnHeatmapPanel: CompareReturnHeatmapPanelStub,
        CorrelationMatrixPanel: CorrelationMatrixPanelStub,
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState({}, '', '/compare?names=alpha,beta')
  window.sessionStorage.clear()
  libraryApi.fetchLibraryCatalog.mockResolvedValue(catalog)
  libraryApi.fetchLibraryNetValue.mockImplementation((name: string) => Promise.resolve(netValues[name]))
  libraryApi.fetchEnhancedCompare.mockResolvedValue(enhanced)
})

describe('StrategyComparePage', () => {
  it('rejects invalid selection counts before issuing API requests', async () => {
    const wrapper = mountPage(['alpha'])
    await flushPromises()

    expect(wrapper.get('.error-state').text()).toBe('请至少选择 2 个策略后再进入对比页。')
    expect(libraryApi.fetchLibraryCatalog).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('loads two valid curves and exposes summary, chart, heatmap, and score inputs', async () => {
    const wrapper = mountPage([' alpha ', 'beta', 'alpha'])
    await flushPromises()

    expect(wrapper.find('.loading-state').exists()).toBe(false)
    expect(wrapper.get('h1').text()).toBe('净值与超额对比')
    expect(wrapper.findAll('.compare-summary-card')).toHaveLength(2)
    expect(wrapper.text()).toContain('Alpha 动量')
    expect(wrapper.text()).toContain('20.00%')
    const curves = wrapper.findComponent(CompareChartsPanelStub).props('curves') as { dates: string[] }
    expect(curves.dates).toEqual([
      '2025-01-02',
      '2025-01-03',
      '2025-01-06',
    ])
    expect(wrapper.findComponent(CompareReturnHeatmapPanelStub).props('strategies')).toHaveLength(2)
    expect(wrapper.findComponent(CorrelationMatrixPanelStub).props('dates')).toEqual([
      '2025-01-03',
      '2025-01-06',
    ])
    expect(libraryApi.fetchEnhancedCompare.mock.calls[0][0]).toEqual(['alpha', 'beta'])
    wrapper.unmount()
  })

  it('keeps base comparison usable when enhanced scoring fails and retries narrowly', async () => {
    libraryApi.fetchEnhancedCompare
      .mockRejectedValueOnce(new Error('评分服务不可用'))
      .mockResolvedValueOnce(enhanced)
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.findAll('.compare-summary-card')).toHaveLength(2)
    expect(wrapper.get('[role="alert"]').text()).toBe('评分服务不可用')

    await wrapper.findAll('button').find(button => button.text().includes('重试评分'))!.trigger('click')
    await flushPromises()

    expect(libraryApi.fetchEnhancedCompare).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('reports unusable net-value responses without mounting chart panels', async () => {
    libraryApi.fetchLibraryNetValue.mockImplementation((name: string) => Promise.resolve(
      name === 'alpha' ? netValues.alpha : { dates: ['2025-01-02'], net_value: [1] },
    ))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.get('.error-state').text()).toContain('至少需要 2 条有效净值曲线才能对比')
    expect(wrapper.findComponent(CompareChartsPanelStub).exists()).toBe(false)
    expect(libraryApi.fetchEnhancedCompare).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('rejects non-positive net values before building comparison charts', async () => {
    libraryApi.fetchLibraryNetValue.mockImplementation((name: string) => Promise.resolve({
      dates: ['2025-01-02', '2025-01-03'],
      net_value: name === 'alpha' ? [1, 0] : [1, -0.2],
    }))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.get('.error-state').text()).toContain('至少需要 2 条有效净值曲线才能对比')
    expect(wrapper.findComponent(CompareChartsPanelStub).exists()).toBe(false)
    expect(libraryApi.fetchEnhancedCompare).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
