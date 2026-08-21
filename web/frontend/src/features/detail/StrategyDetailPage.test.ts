import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  DailyHoldingsPayload,
  DailyHoldingsSummaryPayload,
  LibraryAnalysisResponse,
  LibraryDetailResponse,
  LibraryMetricsResponse,
  NetValuePayload,
  TradesPayload,
  TradeSummaryPayload,
} from '../../types/library'
import StrategyDetailPage from './StrategyDetailPage.vue'

const libraryApi = vi.hoisted(() => ({
  fetchLibraryAnalysis: vi.fn(),
  fetchLibraryDetail: vi.fn(),
  fetchLibraryHoldingsHistory: vi.fn(),
  fetchLibraryHoldingsSummary: vi.fn(),
  fetchLibraryMetrics: vi.fn(),
  fetchLibraryNetValue: vi.fn(),
  fetchLibraryReview: vi.fn(),
  fetchLibraryTradeSummary: vi.fn(),
  fetchLibraryTradeRecords: vi.fn(),
  libraryReportUrl: vi.fn((name: string) => `/api/library/${name}/report`),
}))

const backtestApi = vi.hoisted(() => ({
  fetchBacktestStatus: vi.fn(),
  startBacktestRun: vi.fn(),
}))

vi.mock('../../api/library', () => libraryApi)
vi.mock('../../api/backtest', () => backtestApi)

const detail: LibraryDetailResponse = {
  name: 'alpha',
  title: 'Alpha 动量',
  status: 'completed',
  status_label: '已完成',
  timestamp: '2025-07-01',
  cum_return: 0.2,
  max_drawdown: -0.08,
  win_rate: 0.61,
  trade_count: 22,
  final_net_value: 1.2,
  catalog_entry: {
    name: 'alpha',
    title: 'Alpha 动量',
    summary: '通过质量约束捕捉趋势延续。',
    buy_logic: '趋势确认后买入',
    sell_logic: '趋势破坏后卖出',
    portfolio_label: '双持仓',
    cache_status: 'valid',
    initial_capital: 1_000_000,
    description_status: 'verified',
    strategy_description: {
      schema_version: 2,
      authoring: { method: 'llm_source_review', language: 'zh-CN' },
      source: { file: 'strategy.py', sha256: 'a'.repeat(64) },
      profile: {
        title: 'Alpha 动量',
        summary: 'LLM 摘要',
        buy_logic: 'LLM 买入摘要',
        sell_logic: 'LLM 卖出摘要',
        position_text: '1*1',
        style_label: '动量型',
      },
      runtime: { top_n: 1, portfolio_num_j: 1, portfolio_num_i: 1 },
      buy: ['买入条件'],
      sell: ['卖出条件'],
      indicators: [
        {
          name: '测试动量指标',
          purpose: '识别价格动能',
          formula: 'R_N = 当前收盘价 ÷ N日前收盘价 - 1',
          parameters: ['N=5'],
          usage: ['用于确认趋势'],
        },
      ],
      investor_explanation: {
        buy: {
          summary: 'LLM 买入说明',
          groups: [{ title: '买入组', items: [{ title: '买入条件', description: '只在趋势确认后买入' }] }],
        },
        sell: {
          summary: 'LLM 卖出说明',
          groups: [{ title: '卖出组', items: [{ title: '卖出条件', description: '趋势破坏后卖出' }] }],
        },
      },
    },
  },
}

const netValue: NetValuePayload = {
  dates: ['2025-01-02', '2025-01-03', '2025-01-06'],
  net_value: [1, 1.05, 1.2],
  benchmark_net_value: [1, 1.01, 1.03],
  excess_net_value: [1, 1.04, 1.16],
  benchmark_label: '创业板指',
}

const metrics: LibraryMetricsResponse = {
  cum_return: 0.2,
  ann_return: 0.18,
  max_drawdown: -0.08,
  sharpe: 1.5,
  win_rate: 0.61,
  total_trades: 22,
}

const analysis: LibraryAnalysisResponse = {
  name: 'alpha',
  score: { value: 86, grade: 'A' },
  diagnostics: [],
}

const trades: TradesPayload = { columns: [], records: [] }
const tradeSummary: TradeSummaryPayload = {
  view: 'summary',
  columns: [],
  records_preview: [],
  execution_columns: [],
  execution_records_preview: [],
  order_columns: [],
  order_records_preview: [],
  latest_signals: {
    missing_code: false,
    buy: { date: '', items: [] },
    sell: { date: '', items: [] },
  },
  summary: {
    record_count: 0,
    execution_record_count: 0,
    order_record_count: 0,
  },
}
const holdings: DailyHoldingsPayload = {
  dates: [],
  holdings_by_date: {},
  summary_by_date: {},
}
const holdingsSummary: DailyHoldingsSummaryPayload = {
  view: 'summary',
  dates: [],
  summary_by_date: {},
  latest_date: '',
  latest_holdings_preview: [],
  summary: {
    date_count: 0,
    populated_date_count: 0,
    holding_row_count: 0,
    latest_holding_count: 0,
  },
}

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

let originalMatchMediaDescriptor: PropertyDescriptor | undefined

function mountPage() {
  return shallowMount(StrategyDetailPage, {
    attachTo: document.body,
    props: { name: 'alpha' },
    global: {
      stubs: {
        NetValueChartPanel: true,
        DrawdownChartPanel: true,
        ReturnHeatmapPanel: true,
        HoldingsHeatmapPanel: true,
        TradeDistributionPanel: true,
        CollapsiblePanel: false,
        DetailSectionHeader: false,
        DetailOverviewSection: false,
        DetailRulesSection: false,
        DetailPerformanceSection: false,
        DetailHoldingsSection: false,
        DetailReviewSection: false,
        DetailLedgerSection: false,
      },
    },
  })
}

async function expandStrategyRules(wrapper: ReturnType<typeof mountPage>) {
  const trigger = wrapper.get('#rules-conditions .collapsible-panel-trigger')
  if (trigger.attributes('aria-expanded') !== 'true') {
    await trigger.trigger('click')
    await flushPromises()
  }
  return wrapper.get('.strategy-rules-panel')
}

beforeEach(() => {
  vi.clearAllMocks()
  window.history.replaceState({}, '', '/strategy/alpha')
  window.localStorage.clear()
  document.body.innerHTML = ''
  originalMatchMediaDescriptor = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(() => ({ matches: false })),
  })
  vi.stubGlobal('ResizeObserver', TestResizeObserver)
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1)
  libraryApi.fetchLibraryDetail.mockResolvedValue(detail)
  libraryApi.fetchLibraryNetValue.mockResolvedValue(netValue)
  libraryApi.fetchLibraryMetrics.mockResolvedValue(metrics)
  libraryApi.fetchLibraryAnalysis.mockResolvedValue(analysis)
  libraryApi.fetchLibraryTradeRecords.mockResolvedValue(trades)
  libraryApi.fetchLibraryHoldingsHistory.mockResolvedValue(holdings)
  libraryApi.fetchLibraryTradeSummary.mockResolvedValue(tradeSummary)
  libraryApi.fetchLibraryHoldingsSummary.mockResolvedValue(holdingsSummary)
  libraryApi.fetchLibraryReview.mockResolvedValue({ name: 'alpha' })
  backtestApi.fetchBacktestStatus.mockResolvedValue({ running: false })
  backtestApi.startBacktestRun.mockResolvedValue({ ok: true, status: 202, data: null })
})

afterEach(() => {
  document.body.innerHTML = ''
  if (originalMatchMediaDescriptor) {
    Object.defineProperty(window, 'matchMedia', originalMatchMediaDescriptor)
  } else {
    Reflect.deleteProperty(window, 'matchMedia')
  }
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('StrategyDetailPage', () => {
  it('shows strategy identity before slower research requests finish', async () => {
    let resolveNetValue!: (value: NetValuePayload) => void
    let resolveMetrics!: (value: LibraryMetricsResponse) => void
    let resolveAnalysis!: (value: LibraryAnalysisResponse) => void
    libraryApi.fetchLibraryNetValue.mockReturnValueOnce(new Promise(resolve => { resolveNetValue = resolve }))
    libraryApi.fetchLibraryMetrics.mockReturnValueOnce(new Promise(resolve => { resolveMetrics = resolve }))
    libraryApi.fetchLibraryAnalysis.mockReturnValueOnce(new Promise(resolve => { resolveAnalysis = resolve }))

    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.find('.strategy-detail-state').exists()).toBe(false)
    expect(wrapper.get('#strategy-detail-title').text()).toBe('Alpha 动量')
    expect(libraryApi.fetchLibraryNetValue).toHaveBeenCalledWith('alpha')
    expect(wrapper.find('net-value-chart-panel-stub').exists()).toBe(false)

    resolveNetValue(netValue)
    resolveMetrics(metrics)
    resolveAnalysis(analysis)
    await flushPromises()
    expect(wrapper.find('net-value-chart-panel-stub').exists()).toBe(true)
    wrapper.unmount()
  })

  it('loads bounded supporting summaries before deferring full records', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.get('#strategy-detail-title').text()).toBe('Alpha 动量')
    expect(libraryApi.fetchLibraryTradeSummary).toHaveBeenCalledWith('alpha')
    expect(libraryApi.fetchLibraryHoldingsSummary).toHaveBeenCalledWith('alpha')
    expect(libraryApi.fetchLibraryTradeRecords).not.toHaveBeenCalled()
    expect(libraryApi.fetchLibraryHoldingsHistory).not.toHaveBeenCalled()

    await wrapper.get('.research-main-tab[href="#details"]').trigger('click')
    await flushPromises()

    expect(libraryApi.fetchLibraryTradeRecords).toHaveBeenCalledWith('alpha')
    wrapper.unmount()
  })

  it('keeps the current full-record request when a stale strategy request settles', async () => {
    let resolveAlpha!: (value: TradesPayload) => void
    let resolveBeta!: (value: TradesPayload) => void
    libraryApi.fetchLibraryTradeRecords.mockImplementation((name: string) => new Promise(resolve => {
      if (name === 'alpha') resolveAlpha = resolve
      else resolveBeta = resolve
    }))

    const wrapper = mountPage()
    await flushPromises()

    await wrapper.get('.research-main-tab[href="#details"]').trigger('click')
    await flushPromises()
    expect(libraryApi.fetchLibraryTradeRecords).toHaveBeenCalledTimes(1)
    expect(libraryApi.fetchLibraryTradeRecords).toHaveBeenLastCalledWith('alpha')

    await wrapper.setProps({ name: 'beta' })
    await flushPromises()
    expect(libraryApi.fetchLibraryTradeRecords).toHaveBeenCalledTimes(2)
    expect(libraryApi.fetchLibraryTradeRecords).toHaveBeenLastCalledWith('beta')

    resolveAlpha({ columns: [], records: [{ stock_code: 'STALE' }] })
    await flushPromises()
    await wrapper.get('.research-main-tab[href="#overview"]').trigger('click')
    await wrapper.get('.research-main-tab[href="#details"]').trigger('click')
    await flushPromises()

    expect(libraryApi.fetchLibraryTradeRecords).toHaveBeenCalledTimes(2)

    resolveBeta({ columns: [], records: [{ stock_code: 'CURRENT' }] })
    await flushPromises()
    wrapper.unmount()
  })

  it('renders core detail data and loads supporting data independently', async () => {
    const wrapper = mountPage()
    expect(wrapper.text()).toContain('正在载入回测结果')

    await flushPromises()

    expect(wrapper.find('.strategy-detail-error').exists()).toBe(false)
    expect(wrapper.get('#strategy-detail-title').text()).toBe('Alpha 动量')
    expect(wrapper.get('.status-pill').text()).toContain('已完成')
    expect(wrapper.get('.rerun-command').attributes('disabled')).toBeUndefined()
    expect(libraryApi.fetchLibraryDetail).toHaveBeenCalledWith('alpha')
    expect(libraryApi.fetchLibraryTradeSummary).toHaveBeenCalledWith('alpha')
    expect(libraryApi.fetchLibraryHoldingsSummary).toHaveBeenCalledWith('alpha')
    expect(libraryApi.fetchLibraryTradeRecords).not.toHaveBeenCalled()
    expect(libraryApi.fetchLibraryHoldingsHistory).not.toHaveBeenCalled()
    expect(document.title).toBe('Alpha 动量 - 策略详情')
    wrapper.unmount()
  })

  it('keeps one current-state overview and defers repeated detail', async () => {
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.findAll('#overview')).toHaveLength(1)
    expect(wrapper.find('.overview-current-panel').exists()).toBe(true)
    expect(wrapper.find('.overview-holdings-panel').exists()).toBe(false)
    expect(wrapper.find('.performance-interval-panel').exists()).toBe(false)
    expect(wrapper.get('#rules-conditions .collapsible-panel-trigger').attributes('aria-expanded')).toBe('false')
    expect(wrapper.findAll('.detail-view-mode-control')).toHaveLength(1)
    expect(wrapper.findAll('.rule-overview-card')).toHaveLength(2)

    const navigationGroups = wrapper.findAll('.research-nav-divider').map(item => item.text())
    expect(navigationGroups).toEqual(['核心判断', '执行核对', '研究验证'])
    expect(wrapper.findAll('.detail-section-index').map(item => item.text())).toEqual(['01', '02', '03', '04', '05', '06'])

    const navigationLabels = wrapper.findAll('.research-main-tab > span > strong').map(item => item.text())
    expect(navigationLabels).toEqual([
      '执行概览',
      '策略规则',
      '历史持仓',
      '成交账单',
      '交易复盘',
      '研究分析',
    ])

    await wrapper.get('.strategy-rule-jump').trigger('click')
    await flushPromises()
    expect(wrapper.get('#rules-conditions .collapsible-panel-trigger').attributes('aria-expanded')).toBe('true')

    await wrapper.get('select[aria-label="收益观察区间"]').setValue('3m')
    await flushPromises()
    expect(wrapper.find('.performance-interval-panel').exists()).toBe(true)
    wrapper.unmount()
  })

  it('renders the LLM-authored strategy summary without interpreting source text', async () => {
    libraryApi.fetchLibraryDetail.mockResolvedValueOnce({
      ...detail,
      catalog_entry: {
        ...detail.catalog_entry,
        buy_logic: '源码条件：不应被前端解析',
        sell_logic: '源码条件：不应被前端解析',
      },
    })

    const wrapper = mountPage()
    await flushPromises()

    const summary = wrapper.get('.strategy-identity-description').text()
    expect(summary).toBe('LLM 摘要')
    expect(summary).not.toContain('源码条件')
    wrapper.unmount()
  })

  it('labels historical holding dates explicitly and compresses empty ledgers', async () => {
    libraryApi.fetchLibraryHoldingsSummary.mockResolvedValueOnce({
      view: 'summary',
      dates: ['2025-01-02', '2025-01-03'],
      summary_by_date: {
        '2025-01-02': { asset: 1_010_000, cash: 1_010_000, position_value: 0, holding_count: 0 },
        '2025-01-03': { asset: 1_020_000, cash: 1_020_000, position_value: 0, holding_count: 0 },
      },
      latest_date: '2025-01-03',
      latest_holdings_preview: [],
      summary: {
        date_count: 2,
        populated_date_count: 0,
        holding_row_count: 0,
        latest_holding_count: 0,
      },
    })
    libraryApi.fetchLibraryHoldingsHistory.mockResolvedValueOnce({
      dates: ['2025-01-02', '2025-01-03'],
      holdings_by_date: {
        '2025-01-02': [],
        '2025-01-03': [],
      },
      summary_by_date: {
        '2025-01-02': { asset: 1_010_000, cash: 1_010_000, position_value: 0, holding_count: 0 },
        '2025-01-03': { asset: 1_020_000, cash: 1_020_000, position_value: 0, holding_count: 0 },
      },
    })

    const wrapper = mountPage()
    await flushPromises()

    await wrapper.get('.research-main-tab[href="#holdings"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.detail-focus-date').text()).toBe('最新持仓日 · 2025-01-03')
    await wrapper.get('select[aria-label="历史查看日期"]').setValue('2025-01-02')
    await flushPromises()
    expect(wrapper.get('.detail-focus-date').text()).toBe('历史快照 · 2025-01-02')
    expect(wrapper.get('.holdings-latest-button').text()).toContain('回到最新')

    expect(wrapper.find('.ledger-summary-panel').exists()).toBe(false)
    expect(wrapper.get('#details-summary').text()).toContain('没有可用的逐笔成交或订单记录')
    wrapper.unmount()
  })

  it('shows only investor-facing buy and sell conditions', async () => {
    libraryApi.fetchLibraryDetail.mockResolvedValueOnce({
      ...detail,
      catalog_entry: {
        ...detail.catalog_entry,
        use_index_timing: true,
        risk_control: {
          enable_nv: true,
          enable_dd: true,
          nv_warn: 0.9,
          nv_danger: 0.8,
          nv_clear: 0.5,
          warn_dd: -0.05,
          danger_dd: -0.08,
          lock_dd: -0.12,
        },
      },
    })
    const wrapper = mountPage()
    await flushPromises()

    const panel = await expandStrategyRules(wrapper)
    const sections = panel.findAll('.investor-condition-section')
    expect(sections).toHaveLength(2)
    expect(sections.map(section => section.get('h3').text())).toEqual([
      '什么情况下买入',
      '什么情况下卖出',
    ])
    expect(panel.get('.investor-condition-section.is-buy').text()).toContain('只在趋势确认后买入')
    expect(panel.get('.investor-condition-section.is-sell').text()).toContain('趋势破坏后卖出')
    expect(panel.get('.strategy-indicator-section').text()).toContain('测试动量指标')
    expect(panel.get('.strategy-indicator-section').text()).toContain('R_N = 当前收盘价')
    expect(panel.get('.strategy-indicator-count').text()).toContain('1 项')
    expect(wrapper.get('#rules').text()).toContain('买入与卖出条件')
    expect(panel.text()).not.toContain('组合净值降至')
    wrapper.unmount()
  })

  it('uses initial capital as the full-period baseline for return and drawdown cards', async () => {
    libraryApi.fetchLibraryNetValue.mockResolvedValueOnce({
      dates: ['2025-01-02', '2025-01-03', '2025-01-06'],
      net_value: [0.99, 1.01, 0.98],
    })
    libraryApi.fetchLibraryMetrics.mockResolvedValueOnce({})

    const wrapper = mountPage()
    await flushPromises()

    const summaryText = wrapper.get('.showcase-metric-grid').text()
    expect(summaryText).toContain('-2.00%')
    expect(summaryText).toContain('-2.97%')
    expect(wrapper.find('net-value-chart-panel-stub').attributes('baseline-value')).toBe('1')
    expect(wrapper.find('drawdown-chart-panel-stub').exists()).toBe(false)

    await wrapper.get('.research-main-tab[href="#performance"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('drawdown-chart-panel-stub').attributes('baseline-value')).toBe('1')
    wrapper.unmount()
  })

  it('shows a stable not-found state and recovers through reload', async () => {
    libraryApi.fetchLibraryDetail.mockRejectedValueOnce(new Error('detail unavailable'))
    const wrapper = mountPage()
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('未找到该策略的缓存详情')
    expect(libraryApi.fetchLibraryTradeRecords).not.toHaveBeenCalled()

    await wrapper.findAll('button').find(button => button.text().includes('重新加载'))!.trigger('click')
    await flushPromises()

    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.get('#strategy-detail-title').text()).toBe('Alpha 动量')
    expect(libraryApi.fetchLibraryDetail).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('restores the rerun command after failure and refreshes after a successful retry', async () => {
    backtestApi.startBacktestRun
      .mockResolvedValueOnce({ ok: false, status: 422, data: { error: '策略配置无效' } })
      .mockResolvedValueOnce({
        ok: true,
        status: 202,
        data: { progress: { running: true, total: 1, completed: 0, current: 'alpha' } },
      })
    backtestApi.fetchBacktestStatus.mockResolvedValueOnce({
      running: false,
      total: 1,
      completed: 1,
      results: [{ name: 'alpha', status: 'completed' }],
    })
    const wrapper = mountPage()
    await flushPromises()

    await wrapper.get('.rerun-command').trigger('click')
    await flushPromises()
    expect(wrapper.get('.backtest-action-note').text()).toBe('启动失败：策略配置无效')
    expect(wrapper.get('.rerun-command').attributes('disabled')).toBeUndefined()

    await wrapper.get('.rerun-command').trigger('click')
    await flushPromises()

    expect(backtestApi.startBacktestRun).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.backtest-action-note').text()).toBe('已加载最新回测结果。')
    expect(wrapper.get('.rerun-command').attributes('disabled')).toBeUndefined()
    expect(libraryApi.fetchLibraryDetail).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('refreshes the current detail as soon as its result arrives in a running batch', async () => {
    vi.useFakeTimers()
    backtestApi.startBacktestRun.mockResolvedValueOnce({
      ok: true,
      status: 202,
      data: {
        progress: { running: true, task_id: 'task-1', total: 2, completed: 0, current: 'alpha' },
      },
    })
    backtestApi.fetchBacktestStatus
      .mockResolvedValueOnce({
        running: true,
        task_id: 'task-1',
        total: 2,
        completed: 1,
        current: 'beta',
        results: [{ name: 'alpha', status: 'completed' }],
      })
      .mockResolvedValueOnce({
        running: false,
        task_id: 'task-1',
        total: 2,
        completed: 2,
        results: [
          { name: 'alpha', status: 'completed' },
          { name: 'beta', status: 'completed' },
        ],
      })

    const wrapper = mountPage()
    await flushPromises()
    const initialDetailCalls = libraryApi.fetchLibraryDetail.mock.calls.length

    await wrapper.get('.rerun-command').trigger('click')
    await flushPromises()
    expect(libraryApi.fetchLibraryDetail).toHaveBeenCalledTimes(initialDetailCalls + 1)
    expect(wrapper.get('.rerun-command').attributes('disabled')).toBeDefined()

    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(libraryApi.fetchLibraryDetail).toHaveBeenCalledTimes(initialDetailCalls + 1)
    expect(wrapper.get('.backtest-action-note').text()).toBe('已加载最新回测结果。')
    wrapper.unmount()
    vi.useRealTimers()
  })
})
