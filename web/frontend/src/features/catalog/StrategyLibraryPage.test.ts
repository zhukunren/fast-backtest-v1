import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { BacktestRunResponse, LibraryCatalogResponse } from '../../types/library'
import StrategyLibraryPage from './StrategyLibraryPage.vue'

const libraryApi = vi.hoisted(() => ({
  fetchLibraryCatalog: vi.fn(),
}))

const backtestApi = vi.hoisted(() => ({
  fetchBacktestStatus: vi.fn(),
  startBacktestRun: vi.fn(),
}))

vi.mock('../../api/library', () => libraryApi)
vi.mock('../../api/backtest', () => backtestApi)

const catalog: LibraryCatalogResponse = {
  overview: {
    total: 3,
    cached: 2,
    completed: 2,
    waiting: 1,
    positive: 2,
    completion_rate: 2 / 3,
    avg_return: 0.1,
    latest_timestamp: '2025-07-01',
    best: { name: 'beta', title: 'Beta 反转', cum_return: 0.25 },
  },
  filters: {
    families: [
      { label: '全部来源', value: 'all', count: 3 },
      { label: '动量', value: 'momentum', count: 2 },
      { label: '反转', value: 'reversal', count: 1 },
    ],
    statuses: [
      { label: '全部状态', value: 'all', count: 3 },
      { label: '已完成', value: 'completed', count: 2 },
      { label: '待回测', value: 'waiting', count: 1 },
    ],
  },
  items: [
    {
      name: 'alpha',
      title: 'Alpha 动量',
      summary: '稳健趋势策略',
      family: 'momentum',
      family_label: '动量',
      status: 'completed',
      status_label: '已完成',
      start_time: '20240101',
      end_time: '20241231',
      cum_return: 0.1,
      max_drawdown: -0.15,
      win_rate: 0.6,
      trade_count: 10,
      timestamp: '2025-06-30',
    },
    {
      name: 'beta',
      title: 'Beta 反转',
      summary: '超跌反转策略',
      family: 'reversal',
      family_label: '反转',
      status: 'waiting',
      status_label: '待回测',
      cum_return: 0.25,
      max_drawdown: -0.25,
      win_rate: 0.4,
      trade_count: 30,
      timestamp: '2025-07-01',
    },
    {
      name: 'gamma',
      title: 'Gamma 质量',
      summary: '质量动量策略',
      family: 'momentum',
      family_label: '动量',
      status: 'completed',
      status_label: '已完成',
      cum_return: -0.05,
      max_drawdown: -0.05,
      win_rate: 0.8,
      trade_count: 20,
      timestamp: '2025-06-01',
    },
  ],
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function mountPage() {
  return mount(StrategyLibraryPage, {
    attachTo: document.body,
    global: {
      stubs: { teleport: true },
    },
  })
}

function strategyTitles(wrapper: ReturnType<typeof mountPage>): string[] {
  return wrapper.findAll('.record-title-line > a').map(link => link.text())
}

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState({}, '', '/')
  window.sessionStorage.clear()
  document.body.innerHTML = ''
  libraryApi.fetchLibraryCatalog.mockResolvedValue(catalog)
  backtestApi.fetchBacktestStatus.mockResolvedValue({ running: false })
  backtestApi.startBacktestRun.mockResolvedValue({ ok: true, status: 202, data: null })
})

afterEach(() => {
  document.body.innerHTML = ''
  vi.useRealTimers()
})

describe('StrategyLibraryPage catalog workflow', () => {
  it('keeps the loading state observable, then renders the risk-adjusted catalog', async () => {
    const request = deferred<LibraryCatalogResponse>()
    libraryApi.fetchLibraryCatalog.mockReturnValueOnce(request.promise)
    const wrapper = mountPage()

    expect(wrapper.find('[aria-label="正在加载策略"]').exists()).toBe(true)
    expect(wrapper.get('.catalog-run-all').attributes('disabled')).toBeDefined()

    request.resolve(catalog)
    await flushPromises()

    expect(wrapper.text()).toContain('3 个策略')
    expect(strategyTitles(wrapper)).toEqual(['Gamma 质量', 'Alpha 动量', 'Beta 反转'])
    wrapper.unmount()
  })

  it('supports search, family/status filters, sorting, and filter reset', async () => {
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.get('input[type="search"]').setValue('gamma')
    expect(strategyTitles(wrapper)).toEqual(['Gamma 质量'])
    expect(window.location.search).toContain('q=gamma')

    await wrapper.findAll('.catalog-filter-grid select')[0].setValue('reversal')
    expect(wrapper.text()).toContain('没有匹配的策略')

    await wrapper.findAll('button').find(button => button.text() === '清除筛选')!.trigger('click')
    expect(strategyTitles(wrapper)).toEqual(['Gamma 质量', 'Alpha 动量', 'Beta 反转'])

    await wrapper.findAll('.catalog-filter-grid select')[1].setValue('completed')
    expect(strategyTitles(wrapper)).toEqual(['Gamma 质量', 'Alpha 动量'])

    await wrapper.findAll('.catalog-filter-grid select')[2].setValue('drawdown_asc')
    expect(strategyTitles(wrapper)).toEqual(['Gamma 质量', 'Alpha 动量'])
    expect(window.location.search).toContain('sort=drawdown_asc')
    wrapper.unmount()
  })

  it('recovers from a catalog request failure through the visible retry command', async () => {
    libraryApi.fetchLibraryCatalog
      .mockRejectedValueOnce(new Error('目录服务不可用'))
      .mockResolvedValueOnce(catalog)
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('目录服务不可用')
    await wrapper.findAll('button').find(button => button.text() === '重新加载')!.trigger('click')
    await flushPromises()

    expect(strategyTitles(wrapper)).toHaveLength(3)
    expect(libraryApi.fetchLibraryCatalog).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('disables every run command while a server-side task is active', async () => {
    backtestApi.fetchBacktestStatus.mockResolvedValue({
      running: true,
      total: 3,
      completed: 1,
      current: 'alpha',
      elapsed_sec: 5,
    })
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.get('.catalog-run-all').text()).toContain('回测运行中')
    expect(wrapper.get('.catalog-run-all').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[role="progressbar"]').attributes('aria-valuenow')).toBe('33')
    expect(wrapper.findAll('.library-run-button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.text()).toContain('1/3 · Alpha 动量 · 5 秒')
    wrapper.unmount()
  })

  it('submits the selected strategy and refreshes the catalog after success', async () => {
    const submitRequest = deferred<{ ok: boolean; status: number; data: BacktestRunResponse | null }>()
    backtestApi.startBacktestRun.mockReturnValueOnce(submitRequest.promise)
    backtestApi.fetchBacktestStatus
      .mockResolvedValueOnce({ running: false })
      .mockResolvedValueOnce({
        running: false,
        total: 1,
        completed: 1,
        results: [{ name: 'alpha', status: 'completed' }],
      })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.get('button[aria-label="回测 Alpha 动量"]').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('Alpha 动量')
    expect((wrapper.findAll('input[type="date"]')[0].element as HTMLInputElement).value).toBe('2024-01-01')
    await wrapper.findAll('button').find(button => button.text() === '开始回测')!.trigger('click')

    expect(wrapper.findAll('button').find(button => button.text() === '提交中...')?.attributes('disabled')).toBeDefined()
    submitRequest.resolve({
      ok: true,
      status: 202,
      data: { progress: { running: true, total: 1, completed: 0, current: 'alpha' } },
    })
    await flushPromises()

    expect(backtestApi.startBacktestRun).toHaveBeenCalledWith({
      names: ['alpha'],
      date_overrides: {
        alpha: { start_time: '20240101', end_time: '20241231' },
      },
    })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.get('.catalog-run-status').text()).toContain('回测完成，结果已更新。')
    expect(libraryApi.fetchLibraryCatalog).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('uses the configured window when the previous cache was an ad-hoc run', async () => {
    const original = { ...catalog.items[0] }
    Object.assign(catalog.items[0], {
      cache_date_source: 'override',
      cache_actual_start_time: '20240101',
      cache_actual_end_time: '20240630',
    })

    try {
      const wrapper = mountPage()
      await flushPromises()

      await wrapper.get('button[aria-label="回测 Alpha 动量"]').trigger('click')
      const dateInputs = wrapper.findAll('input[type="date"]')
      expect((dateInputs[0].element as HTMLInputElement).value).toBe('2024-01-01')
      expect((dateInputs[1].element as HTMLInputElement).value).toBe('2024-12-31')

      await wrapper.findAll('button').find(button => button.text() === '开始回测')!.trigger('click')
      await flushPromises()

      expect(backtestApi.startBacktestRun).toHaveBeenCalledWith({
        names: ['alpha'],
        date_overrides: {
          alpha: { start_time: '20240101', end_time: '20241231' },
        },
      })
      wrapper.unmount()
    } finally {
      Object.assign(catalog.items[0], original)
    }
  })

  it('keeps the dialog actionable after a failed submission and accepts a retry', async () => {
    backtestApi.startBacktestRun
      .mockResolvedValueOnce({ ok: false, status: 422, data: { error: '配置无效' } })
      .mockResolvedValueOnce({
        ok: true,
        status: 202,
        data: { progress: { running: true, total: 1, completed: 0, current: 'beta' } },
      })
    backtestApi.fetchBacktestStatus
      .mockResolvedValueOnce({ running: false })
      .mockResolvedValueOnce({
        running: false,
        total: 1,
        completed: 1,
        results: [{ name: 'beta', status: 'completed' }],
      })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.get('button[aria-label="回测 Beta 反转"]').trigger('click')
    await wrapper.findAll('button').find(button => button.text() === '开始回测')!.trigger('click')
    await flushPromises()

    expect(wrapper.get('.backtest-dialog-error').text()).toBe('配置无效')
    const retryButton = wrapper.findAll('button').find(button => button.text() === '开始回测')!
    expect(retryButton.attributes('disabled')).toBeUndefined()

    await retryButton.trigger('click')
    await flushPromises()

    expect(backtestApi.startBacktestRun).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('回测完成，结果已更新。')
    wrapper.unmount()
  })

  it('refreshes the catalog once after a batch settles', async () => {
    vi.useFakeTimers()
    backtestApi.startBacktestRun.mockResolvedValueOnce({
      ok: true,
      status: 202,
      data: {
        progress: { running: true, task_id: 'task-1', total: 3, completed: 0, current: 'alpha' },
      },
    })
    backtestApi.fetchBacktestStatus
      .mockResolvedValueOnce({ running: false })
      .mockResolvedValueOnce({
        running: true,
        task_id: 'task-1',
        total: 3,
        completed: 1,
        current: 'beta',
        results: [{ name: 'alpha', status: 'completed' }],
      })
      .mockResolvedValueOnce({
        running: true,
        task_id: 'task-1',
        total: 3,
        completed: 2,
        current: 'gamma',
        results: [
          { name: 'alpha', status: 'completed' },
          { name: 'beta', status: 'no_signal' },
        ],
      })
      .mockResolvedValueOnce({
        running: false,
        task_id: 'task-1',
        total: 3,
        completed: 3,
        results: [
          { name: 'alpha', status: 'completed' },
          { name: 'beta', status: 'no_signal' },
          { name: 'gamma', status: 'completed' },
        ],
      })

    const wrapper = mountPage()
    await flushPromises()
    await wrapper.get('.catalog-run-all').trigger('click')
    await wrapper.findAll('button').find(button => button.text() === '开始回测')!.trigger('click')
    await flushPromises()

    expect(libraryApi.fetchLibraryCatalog).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(libraryApi.fetchLibraryCatalog).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(libraryApi.fetchLibraryCatalog).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(libraryApi.fetchLibraryCatalog).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    vi.useRealTimers()
  })
})
