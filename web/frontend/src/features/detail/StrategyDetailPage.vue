<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, shallowRef, watch, watchEffect } from 'vue'
import {
  Activity,
  ArrowLeft,
  BadgeCheck,
  Bookmark,
  CalendarDays,
  ChartNoAxesCombined,
  ChevronRight,
  CircleCheck,
  CircleDollarSign,
  Info,
  Layers,
  ListChecks,
  Play,
  RefreshCw,
  ReceiptText,
  TableProperties,
  TriangleAlert,
  WalletCards,
  Zap,
} from '@lucide/vue'
import {
  fetchLibraryAnalysis,
  fetchLibraryDetail,
  fetchLibraryHoldingsHistory,
  fetchLibraryHoldingsSummary,
  fetchLibraryMetrics,
  fetchLibraryNetValue,
  fetchLibraryReview,
  fetchLibraryTradeSummary,
  fetchLibraryTradeRecords,
  libraryReportUrl,
} from '../../api/library'
import DetailHoldingsSection from './components/DetailHoldingsSection.vue'
import DetailLedgerSection from './components/DetailLedgerSection.vue'
import DetailOverviewSection from './components/DetailOverviewSection.vue'
import DetailPerformanceSection from './components/DetailPerformanceSection.vue'
import DetailReviewSection from './components/DetailReviewSection.vue'
import DetailRulesSection from './components/DetailRulesSection.vue'
import {
  buildEffectiveDailyHoldings,
  buildHoldingsRowsBySlot,
  buildHoldingsRowsByStock,
  flattenDailyHoldings,
  getDailyHoldingsRowsForDate,
  getEffectiveHoldingsCurrentView,
  historyRowMatchesFilters,
  holdingDirectionLabel,
  holdingSharesScaled,
  normalizeHoldingsCurrentView,
  normalizeHoldingsDirectionFilterValue,
  normalizeHoldingsDisplayMode,
  normalizeHoldingsSlotFilterValue,
  resolveHoldingsSlotOptions,
} from './holdingsAggregation'
import {
  buildDrawdownTone,
  buildHoldingStockText,
  buildReturnTone,
  buildSampleTone,
  buildSharpeTone,
  buildSlotStockListText,
  buildWinRateTone,
  formatCurrencyUnit,
  formatDateForInput,
  formatDateText,
  formatDrawdown,
  formatHoldingRatio,
  formatHoldingShares,
  formatInteger,
  formatMoney,
  formatPercent,
  formatPlainNumber,
  formatSignedMoney,
  inferHoldingDays,
  isRecord,
  metricQualityClass,
  metricTrendClass,
  normalizeDateKey,
  normalizeDrawdownValue,
  parseLooseDate,
  readNumber,
  readRateValue,
  readString,
  scaleMetricProgress,
} from './detailFormatters'
import {
  computeCurveMetrics,
  sliceNetValuePayload,
  trimNetValuePayloadEnd,
  type CurveMetricSnapshot,
} from './detailMetrics'
import { useDetailBacktestPolling } from './useDetailBacktestPolling'
import {
  DETAIL_PAGE_CONTEXT_KEY,
  type DetailPageContext,
  type InvestorConditionGroup,
  type InvestorConditionItem,
  type InvestorConditionSection,
  type InvestorIndicator,
} from './detailPageContext'
import type {
  DailyHoldingRow,
  DailyHoldingHistoryRow,
  DailyHoldingsPayload,
  DailyHoldingsSummaryPayload,
  DailyHoldingsSummary,
  HoldingsCurrentView,
  HoldingsDisplayMode,
  LibraryAnalysisResponse,
  LibraryDetailResponse,
  LibraryMetricsResponse,
  LibraryReviewResponse,
  NetValuePayload,
  SlotAggregatedHoldingRow,
  StockAggregatedHoldingRow,
  TableRow,
  TradesPayload,
  TradeSummaryPayload,
} from '../../types/library'

const NetValueChartPanel = defineAsyncComponent(() => import('../../components/NetValueChartPanel.vue'))

interface TableColumn {
  key: string
  label: string
  align?: 'left' | 'center' | 'right'
}

interface LatestSignalItem {
  code: string
  name: string
}

interface LatestSignalSummary {
  date: string
  items: LatestSignalItem[]
}

interface ResolvedTradeColumns {
  code: string | null
  name: string | null
  buyDate: string | null
  sellDate: string | null
}

interface DetailEntryView {
  name: string
  title: string
  status: string
  status_label: string
  timestamp: string | null
  cum_return: number | null
  max_drawdown: number | null
  win_rate: number | null
  trade_count: number | null
  final_net_value: number | null
}

interface DetailSectionNavItem {
  id: DetailSectionId
  label: string
  note: string
  groupLabel?: string
}

interface DetailSubsectionNavItem {
  id: DetailSubsectionId
  sectionId: DetailSectionId
  label: string
  anchorId: string
  expandPanels?: readonly DetailPanelId[]
}

interface DashboardMetricCard {
  key: string
  label: string
  value: string
  valueClass?: string
  note: string
  accent: 'return' | 'risk' | 'quality' | 'trade'
  tone: string
  progress: number
}

interface DetailRangeOption {
  value: DetailRangeId
  label: string
  days: number | null
}

interface PerformanceMetricCell {
  label: string
  value: string
  note: string
  valueClass?: string
}

interface MetricSnapshot {
  cum_return: number | null
  max_drawdown: number | null
  ann_return: number | null
  sharpe: number | null
  win_rate: number | null
  total_trades: number | null
  avg_holding_days: number | null
}

function readInvestorExplanationItem(value: unknown): InvestorConditionItem | null {
  if (!isRecord(value)) return null
  const description = readString(value.description)
  if (!description) return null
  const title = readString(value.title)
  const example = readString(value.example)
  return { description, title: title || undefined, example: example || undefined }
}

function readInvestorExplanationGroups(value: unknown): InvestorConditionGroup[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(rawGroup => {
    if (!isRecord(rawGroup)) return []
    const title = readString(rawGroup.title)
    const items = Array.isArray(rawGroup.items)
      ? rawGroup.items.map(readInvestorExplanationItem).filter((item): item is InvestorConditionItem => item != null)
      : []
    if (!title || !items.length) return []
    const logic = readString(rawGroup.logic)
    return [{ title, items, logic: logic || undefined }]
  })
}

function readInvestorExplanation(value: unknown): InvestorConditionSection[] | null {
  if (!isRecord(value)) return null
  const buy = isRecord(value.buy) ? value.buy : {}
  const sell = isRecord(value.sell) ? value.sell : {}
  const buyGroups = readInvestorExplanationGroups(buy.groups)
  const sellGroups = readInvestorExplanationGroups(sell.groups)
  if (!buyGroups.length || !sellGroups.length) return null
  return [
    {
      key: 'buy',
      label: '什么情况下买入',
      summary: readString(buy.summary) || '策略描述未提供买入摘要。',
      groups: buyGroups,
    },
    {
      key: 'sell',
      label: '什么情况下卖出',
      summary: readString(sell.summary) || '策略描述未提供卖出摘要。',
      groups: sellGroups,
    },
  ]
}

function readDescriptionList(value: unknown): string[] {
  if (typeof value === 'string') {
    const text = value.trim()
    return text ? [text] : []
  }
  if (!Array.isArray(value)) return []
  return value.map(readString).filter(Boolean)
}

function readInvestorIndicators(value: unknown): InvestorIndicator[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(rawIndicator => {
    if (!isRecord(rawIndicator)) return []
    const name = readString(rawIndicator.name)
    const formula = readDescriptionList(rawIndicator.formula)
    if (!name || !formula.length) return []
    return [{
      name,
      purpose: readString(rawIndicator.purpose) || undefined,
      formula,
      variables: readDescriptionList(rawIndicator.variables),
      parameters: readDescriptionList(rawIndicator.parameters),
      usage: readDescriptionList(rawIndicator.usage),
      notes: readDescriptionList(rawIndicator.notes),
    }]
  })
}

interface SectionNavigationIntent {
  sectionId: DetailSectionId
  subsectionId?: DetailSubsectionId
  source: 'explicit' | 'history' | 'restore'
}

interface DetailNavigationTarget {
  sectionId: DetailSectionId
  subsectionId?: DetailSubsectionId
}

interface ScrollToSectionOptions {
  updateHash?: boolean
  focus?: boolean
  intent?: SectionNavigationIntent['source'] | false
}

type DetailSectionId = 'overview' | 'rules' | 'holdings' | 'details' | 'review' | 'performance'
type DetailSubsectionId =
  | 'rules/conditions'
  | 'performance/distribution'
  | 'performance/risk'
  | 'performance/cycles'
  | 'holdings/current'
  | 'holdings/history'
  | 'holdings/heatmap'
  | 'review/signals'
  | 'review/holdings'
  | 'review/stocks'
  | 'details/summary'
  | 'details/executions'
  | 'details/orders'
type DetailPanelId =
  | 'strategyRules'
  | 'advancedMetrics'
  | 'scoreDetails'
  | 'returnHeatmap'
  | 'marketState'
  | 'yearlyPerformance'
  | 'holdingsHeatmap'
  | 'reviewStocks'
  | 'holdingHistory'
  | 'executionRecords'
  | 'orderRecords'
type DetailViewMode = 'simple' | 'advanced'
type DetailRangeId = '3m' | '6m' | '1y' | 'all'

const DETAIL_SECTIONS: ReadonlyArray<DetailSectionNavItem> = [
  { id: 'overview', label: '执行概览', note: '最新账户与动作', groupLabel: '核心判断' },
  { id: 'rules', label: '策略规则', note: '买入与卖出条件' },
  { id: 'holdings', label: '历史持仓', note: '账户与仓位快照', groupLabel: '执行核对' },
  { id: 'details', label: '成交账单', note: '逐笔成交与订单' },
  { id: 'review', label: '交易复盘', note: '信号与个股贡献' },
  { id: 'performance', label: '研究分析', note: '风险、分布与归因', groupLabel: '研究验证' },
]

const DETAIL_SUBSECTIONS: ReadonlyArray<DetailSubsectionNavItem> = [
  { id: 'rules/conditions', sectionId: 'rules', label: '买卖条件', anchorId: 'rules-conditions', expandPanels: ['strategyRules'] },
  { id: 'performance/distribution', sectionId: 'performance', label: '回撤与分布', anchorId: 'performance-distribution' },
  { id: 'performance/risk', sectionId: 'performance', label: '风险研究', anchorId: 'performance-risk', expandPanels: ['advancedMetrics'] },
  { id: 'performance/cycles', sectionId: 'performance', label: '周期与市场', anchorId: 'performance-cycles', expandPanels: ['returnHeatmap'] },
  { id: 'holdings/current', sectionId: 'holdings', label: '当前持仓', anchorId: 'holdings-current' },
  { id: 'holdings/history', sectionId: 'holdings', label: '历史持仓', anchorId: 'holdings-history', expandPanels: ['holdingHistory'] },
  { id: 'holdings/heatmap', sectionId: 'holdings', label: '持仓热力', anchorId: 'holdings-heatmap', expandPanels: ['holdingsHeatmap'] },
  { id: 'review/signals', sectionId: 'review', label: '最近信号', anchorId: 'review-signals' },
  { id: 'review/holdings', sectionId: 'review', label: '持仓复盘', anchorId: 'review-holdings' },
  { id: 'review/stocks', sectionId: 'review', label: '个股复盘', anchorId: 'review-stocks', expandPanels: ['reviewStocks'] },
  { id: 'details/summary', sectionId: 'details', label: '账单摘要', anchorId: 'details-summary' },
  { id: 'details/executions', sectionId: 'details', label: '逐笔成交', anchorId: 'details-executions', expandPanels: ['executionRecords'] },
  { id: 'details/orders', sectionId: 'details', label: '订单流水', anchorId: 'details-orders', expandPanels: ['orderRecords'] },
]

const DETAIL_RANGE_OPTIONS: ReadonlyArray<DetailRangeOption> = [
  { value: '3m', label: '近3月', days: 90 },
  { value: '6m', label: '近6月', days: 180 },
  { value: '1y', label: '近1年', days: 365 },
  { value: 'all', label: '全部', days: null },
]

const LEDGER_PAGE_SIZE = 50

const DEFAULT_EXPANDED_PANELS: Record<DetailPanelId, boolean> = {
  strategyRules: false,
  advancedMetrics: false,
  scoreDetails: false,
  returnHeatmap: false,
  marketState: false,
  yearlyPerformance: false,
  holdingsHeatmap: false,
  reviewStocks: false,
  holdingHistory: false,
  executionRecords: false,
  orderRecords: false,
}

const CURRENT_HOLDING_DETAIL_COLUMNS: TableColumn[] = [
  { key: 'slot', label: '槽位' },
  { key: 'stock', label: '股票' },
  { key: 'direction', label: '方向', align: 'center' },
  { key: 'shares', label: '股数', align: 'right' },
  { key: 'close', label: '收盘价', align: 'right' },
  { key: 'cost_price', label: '成本价', align: 'right' },
  { key: 'cost_value', label: '成本市值', align: 'right' },
  { key: 'holding_value', label: '持仓市值', align: 'right' },
  { key: 'unrealized_pnl', label: '浮动盈亏', align: 'right' },
  { key: 'unrealized_pnl_pct', label: '盈亏比例', align: 'right' },
  { key: 'nv', label: '净值', align: 'right' },
  { key: 'position_ratio', label: '仓位', align: 'right' },
]

const CURRENT_HOLDING_STOCK_COLUMNS: TableColumn[] = [
  { key: 'stock', label: '股票' },
  { key: 'slots_text', label: '涉及组合' },
  { key: 'close', label: '收盘价', align: 'right' },
  { key: 'cost_price', label: '成本价', align: 'right' },
  { key: 'shares', label: '有效股数', align: 'right' },
  { key: 'original_shares', label: '原始股数', align: 'right' },
  { key: 'cost_value', label: '成本市值', align: 'right' },
  { key: 'holding_value', label: '持仓市值', align: 'right' },
  { key: 'unrealized_pnl', label: '浮动盈亏', align: 'right' },
  { key: 'unrealized_pnl_pct', label: '盈亏比例', align: 'right' },
  { key: 'position_ratio', label: '合计仓位', align: 'right' },
]

const CURRENT_HOLDING_STOCK_SIMPLE_COLUMNS: TableColumn[] = [
  { key: 'stock', label: '股票' },
  { key: 'shares', label: '当前股数', align: 'right' },
  { key: 'holding_value', label: '持仓市值', align: 'right' },
  { key: 'unrealized_pnl', label: '浮动盈亏', align: 'right' },
  { key: 'unrealized_pnl_pct', label: '盈亏比例', align: 'right' },
]

const CURRENT_HOLDING_SLOT_COLUMNS: TableColumn[] = [
  { key: 'slot', label: '槽位' },
  { key: 'stock_count', label: '持仓只数', align: 'right' },
  { key: 'stocks_text', label: '股票列表' },
  { key: 'shares', label: '有效股数', align: 'right' },
  { key: 'original_shares', label: '原始股数', align: 'right' },
  { key: 'cost_value', label: '成本市值', align: 'right' },
  { key: 'holding_value', label: '持仓市值', align: 'right' },
  { key: 'unrealized_pnl', label: '浮动盈亏', align: 'right' },
  { key: 'unrealized_pnl_pct', label: '盈亏比例', align: 'right' },
  { key: 'nv', label: '净值', align: 'right' },
  { key: 'position_ratio', label: '合计仓位', align: 'right' },
]

const HOLDING_HISTORY_COLUMNS: TableColumn[] = [
  { key: 'date', label: '日期' },
  ...CURRENT_HOLDING_DETAIL_COLUMNS,
]

const RESEARCH_YEARLY_COLUMNS: TableColumn[] = [
  { key: 'period', label: '年度' },
  { key: 'return', label: '收益', align: 'right' },
  { key: 'max_drawdown', label: '最大回撤', align: 'right' },
  { key: 'day_count', label: '交易日', align: 'right' },
  { key: 'positive_days', label: '上涨日', align: 'right' },
  { key: 'negative_days', label: '下跌日', align: 'right' },
]

const MARKET_STATE_COLUMNS: TableColumn[] = [
  { key: 'label', label: '市场状态' },
  { key: 'day_count', label: '样本日', align: 'right' },
  { key: 'return', label: '策略收益', align: 'right' },
  { key: 'benchmark_return', label: '基准收益', align: 'right' },
  { key: 'excess_return', label: '超额', align: 'right' },
  { key: 'max_drawdown', label: '最大回撤', align: 'right' },
  { key: 'positive_day_ratio', label: '正收益日', align: 'right' },
]

const SCORE_COMPONENT_COLUMNS: TableColumn[] = [
  { key: 'component', label: '评分维度' },
  { key: 'score', label: '得分', align: 'right' },
  { key: 'weight', label: '权重', align: 'right' },
]

const REVIEW_STOCK_COLUMNS: TableColumn[] = [
  { key: 'stock', label: '股票' },
  { key: 'holding_days', label: '持仓日', align: 'right' },
  { key: 'date_range', label: '区间' },
  { key: 'latest_holding_value', label: '最新市值', align: 'right' },
  { key: 'max_holding_value', label: '最大市值', align: 'right' },
  { key: 'latest_unrealized_pnl', label: '最新浮盈', align: 'right' },
  { key: 'realized_return_sum', label: '交易收益和', align: 'right' },
  { key: 'avg_position_ratio', label: '平均仓位', align: 'right' },
  { key: 'trade_count', label: '交易数', align: 'right' },
]

const props = defineProps<{
  name: string
}>()

const loading = ref(true)
const error = ref('')
const detail = shallowRef<LibraryDetailResponse | null>(null)
const trades = shallowRef<TradesPayload | null>(null)
const holdings = shallowRef<DailyHoldingsPayload | null>(null)
const tradeSummary = shallowRef<TradeSummaryPayload | null>(null)
const holdingsSummaryPayload = shallowRef<DailyHoldingsSummaryPayload | null>(null)
const metrics = shallowRef<LibraryMetricsResponse | null>(null)
const analysis = shallowRef<LibraryAnalysisResponse | null>(null)
const review = shallowRef<LibraryReviewResponse | null>(null)
const netValue = shallowRef<NetValuePayload | null>(null)
const focusDate = ref('')
const showcaseFocusDate = ref('')
const holdingsDisplayMode = ref<HoldingsDisplayMode>('simple')
const holdingsCurrentView = ref<HoldingsCurrentView>('detail')
const holdingsHistorySearchInput = ref('')
const holdingsHistorySearch = ref('')
const holdingsSlotFilter = ref('all')
const holdingsDirectionFilter = ref<'all' | 'buy' | 'sell' | 'hold'>('all')
const executionSearch = ref('')
const orderSearch = ref('')
const activeSection = ref<DetailSectionId>('overview')
const activeSubsection = ref<DetailSubsectionId | null>(null)
const compactNavigation = ref(false)
const expandedPanels = ref<Record<DetailPanelId, boolean>>({ ...DEFAULT_EXPANDED_PANELS })
const detailViewMode = ref<DetailViewMode>('simple')
const performanceChartsActivated = ref(false)
const activeRange = ref<DetailRangeId>('all')
const supportingDataLoading = ref(false)
const supportingDataError = ref('')
const fullTradesLoading = ref(false)
const fullHoldingsLoading = ref(false)
const fullTradesError = ref('')
const fullHoldingsError = ref('')
const fullTradesLoaded = ref(false)
const fullHoldingsLoaded = ref(false)
const reviewDataLoading = ref(false)
const reviewDataError = ref('')
const coreDataWarning = ref('')
let scrollSpyFrame: number | null = null
let sectionNavigationIntent: SectionNavigationIntent | null = null
let pendingHashTarget: DetailNavigationTarget | null = null
let initialSectionNavigationComplete = false
let stickyLayoutResizeObserver: ResizeObserver | null = null
let observedStickyHeader: HTMLElement | null = null
let pendingSupportReanchor: DetailNavigationTarget | null = null
let pendingReviewReanchor: DetailNavigationTarget | null = null
let holdingHistorySearchTimer: number | null = null
let contextQueryReady = false
let fullTradesRequest: Promise<void> | null = null
let fullHoldingsRequest: Promise<void> | null = null

const dataLoadInProgress = computed(() => (
  supportingDataLoading.value || fullTradesLoading.value || fullHoldingsLoading.value
))
const fullDataError = computed(() => fullTradesError.value || fullHoldingsError.value)

let loadToken = 0

const {
  backtestActionPending,
  backtestActionMessage,
  rerunCurrentStrategy,
  reset: resetBacktestTracking,
} = useDetailBacktestPolling({
  name: () => props.name,
  loadPage,
  hasError: () => Boolean(error.value),
})

const entry = computed<DetailEntryView>(() => mergeDetailEntry(detail.value, props.name))
const cacheActualEndDate = computed(() => {
  const catalogEntry = isRecord(detail.value?.catalog_entry) ? detail.value.catalog_entry : {}
  const cacheConfig = isRecord(detail.value?.cache_config) ? detail.value.cache_config : {}
  const effectiveWindow = isRecord(cacheConfig.effective_window) ? cacheConfig.effective_window : {}
  return normalizeDateKey(
    readString(catalogEntry.cache_actual_end_time)
      || readString(effectiveWindow.end_time),
  )
})
const effectiveHoldings = computed<DailyHoldingsPayload | null>(() => (
  buildEffectiveDailyHoldings(holdings.value, cacheActualEndDate.value)
))
const holdingsDates = computed(() => effectiveHoldings.value?.dates ?? [])
const latestHoldingDate = computed(() => holdingsDates.value.reduce((latest, date) => {
  if (!latest) return date
  return normalizeDateKey(date) > normalizeDateKey(latest) ? date : latest
}, ''))
const effectiveHoldingsView = computed<HoldingsCurrentView>(() => (
  getEffectiveHoldingsCurrentView(holdingsDisplayMode.value, holdingsCurrentView.value)
))
const holdingsStatusText = computed(() => {
  if (loading.value) return '正在加载每日持仓...'
  if (supportingDataLoading.value) return '正在加载持仓摘要...'
  if (fullHoldingsLoading.value) return '正在加载完整持仓历史...'
  if (!effectiveHoldings.value) return '暂无每日持仓缓存，请先重新回测生成新版数据。'
  if (!holdingsDates.value.length) return '当前每日持仓缓存为空。'
  if (!fullHoldingsLoaded.value) return `已加载 ${holdingsDates.value.length} 个交易日摘要，进入本区后读取完整快照。`
  return `已加载 ${holdingsDates.value.length} 个交易日，可按日期查看账户摘要、持仓与成交。`
})
const holdingsSummary = computed<DailyHoldingsSummary | null>(() => {
  if (!effectiveHoldings.value || !focusDate.value) return null
  return effectiveHoldings.value.summary_by_date?.[focusDate.value] ?? null
})
const currentHoldingRawRows = computed<DailyHoldingRow[]>(() => {
  return getDailyHoldingsRowsForDate(effectiveHoldings.value, focusDate.value)
})
const currentHoldingViewRows = computed<Array<DailyHoldingRow | StockAggregatedHoldingRow | SlotAggregatedHoldingRow>>(() => {
  if (!focusDate.value) return []
  if (effectiveHoldingsView.value === 'stock') return buildHoldingsRowsByStock(currentHoldingRawRows.value)
  if (effectiveHoldingsView.value === 'slot') return buildHoldingsRowsBySlot(currentHoldingRawRows.value)
  return currentHoldingRawRows.value
})
const currentHoldingColumns = computed<TableColumn[]>(() => {
  if (effectiveHoldingsView.value === 'stock') {
    return holdingsDisplayMode.value === 'simple' ? CURRENT_HOLDING_STOCK_SIMPLE_COLUMNS : CURRENT_HOLDING_STOCK_COLUMNS
  }
  if (effectiveHoldingsView.value === 'slot') return CURRENT_HOLDING_SLOT_COLUMNS
  return CURRENT_HOLDING_DETAIL_COLUMNS
})
const currentHoldingDisplayRows = computed<TableRow[]>(() => {
  return buildCurrentHoldingDisplayRows(
    currentHoldingViewRows.value,
    effectiveHoldingsView.value,
    holdingsDisplayMode.value,
    focusDate.value,
  )
})
const holdingsSlotOptions = computed(() => resolveHoldingsSlotOptions(effectiveHoldings.value))
const holdingHistoryRows = computed<DailyHoldingHistoryRow[]>(() => flattenDailyHoldings(effectiveHoldings.value))
const holdingHistoryFilteredRows = computed<DailyHoldingHistoryRow[]>(() => {
  const slotFilter = normalizeHoldingsSlotFilterValue(holdingsSlotFilter.value, effectiveHoldings.value)
  const directionFilter = normalizeHoldingsDirectionFilterValue(holdingsDirectionFilter.value)
  return holdingHistoryRows.value.filter(row => historyRowMatchesFilters(row, {
    searchValue: holdingsHistorySearch.value,
    slotFilter,
    directionFilter,
  }))
})
const holdingHistoryDisplayRows = computed<TableRow[]>(() => {
  return holdingHistoryFilteredRows.value.map(row => buildDetailHoldingDisplayRow(row, row.trade_date || ''))
})
const latestHoldingsSummary = computed<DailyHoldingsSummary | null>(() => {
  if (!effectiveHoldings.value || !latestHoldingDate.value) return null
  return effectiveHoldings.value.summary_by_date?.[latestHoldingDate.value] ?? null
})
const overviewHoldingRawRows = computed<DailyHoldingRow[]>(() => {
  return getDailyHoldingsRowsForDate(effectiveHoldings.value, latestHoldingDate.value)
})
const overviewHoldingStockRows = computed<StockAggregatedHoldingRow[]>(() => {
  return buildHoldingsRowsByStock(overviewHoldingRawRows.value)
})
const latestPositionRatio = computed(() => {
  const positionValue = readNumber(latestHoldingsSummary.value?.position_value)
  const asset = readNumber(latestHoldingsSummary.value?.asset)
  if (positionValue != null && asset != null && asset > 0) return positionValue / asset
  const ratios = overviewHoldingStockRows.value
    .map(row => readNumber(row.position_ratio))
    .filter((value): value is number => value != null)
  return ratios.length ? ratios.reduce((sum, value) => sum + value, 0) : null
})
const latestCashRatio = computed(() => {
  const cash = readNumber(latestHoldingsSummary.value?.cash)
  const asset = readNumber(latestHoldingsSummary.value?.asset)
  if (cash != null && asset != null && asset > 0) return cash / asset
  const positionRatio = latestPositionRatio.value
  return positionRatio == null ? null : Math.max(0, 1 - positionRatio)
})
const overviewExposureRows = computed(() => {
  const asset = readNumber(latestHoldingsSummary.value?.asset)
  return overviewHoldingStockRows.value
    .map(row => {
      const holdingValue = readNumber(row.holding_value)
      const fallbackRatio = readNumber(row.position_ratio)
      const weight = holdingValue != null && asset != null && asset > 0
        ? holdingValue / asset
        : fallbackRatio
      const code = readString(row.code)
      const name = readString(row.name)
      return {
        key: `${code}-${name}`,
        label: [code, name].filter(Boolean).join(' ') || '未命名持仓',
        weight: weight == null ? 0 : Math.max(0, weight),
        value: formatMoney(holdingValue),
      }
    })
    .filter(row => row.weight > 0)
    .sort((left, right) => right.weight - left.weight)
    .slice(0, 5)
})
const overviewExecutionFacts = computed(() => [
  {
    label: '总资产',
    value: formatMoney(latestHoldingsSummary.value?.asset),
    valueClass: '',
  },
  {
    label: '最新净值',
    value: formatPlainNumber(latestHoldingsSummary.value?.net_value, 4),
    valueClass: '',
  },
  {
    label: '当前仓位',
    value: formatPercent(latestPositionRatio.value),
    valueClass: '',
  },
  {
    label: '现金比例',
    value: formatPercent(latestCashRatio.value),
    valueClass: '',
  },
  {
    label: '持仓股票',
    value: `${overviewHoldingStockRows.value.length} 只`,
    valueClass: '',
  },
  {
    label: '浮动盈亏',
    value: formatSignedMoney(latestHoldingsSummary.value?.unrealized_pnl),
    valueClass: metricTrendClass(latestHoldingsSummary.value?.unrealized_pnl),
  },
])
const overviewExecutionState = computed(() => {
  if (!latestHoldingDate.value) return { label: '等待持仓数据', tone: 'is-idle' }
  if (!overviewHoldingStockRows.value.length) return { label: '当前空仓', tone: 'is-idle' }
  return { label: '持仓运行中', tone: 'is-active' }
})
const activeRangeOption = computed(() => DETAIL_RANGE_OPTIONS.find(option => option.value === activeRange.value) ?? DETAIL_RANGE_OPTIONS[DETAIL_RANGE_OPTIONS.length - 1])
const effectiveNetValue = computed<NetValuePayload | null>(() => {
  return trimNetValuePayloadEnd(netValue.value, cacheActualEndDate.value)
})
const netValueDates = computed(() => Array.isArray(effectiveNetValue.value?.dates) ? effectiveNetValue.value.dates.map(String) : [])
const rangeStartDate = computed(() => {
  const dates = netValueDates.value
  const days = activeRangeOption.value.days
  if (!dates.length || !days) return ''
  const lastDate = parseLooseDate(dates[dates.length - 1])
  if (!lastDate) return ''
  const startDate = new Date(lastDate)
  startDate.setDate(startDate.getDate() - days)
  return formatDateForInput(startDate)
})
const visibleNetValue = computed<NetValuePayload | null>(() => {
  return sliceNetValuePayload(effectiveNetValue.value, rangeStartDate.value)
})
const visibleNetValueDates = computed(() => Array.isArray(visibleNetValue.value?.dates) ? visibleNetValue.value.dates.map(String) : [])
const visibleDateRangeText = computed(() => {
  const dates = visibleNetValueDates.value.length ? visibleNetValueDates.value : netValueDates.value
  if (!dates.length) return '-'
  return `${dates[0]} ~ ${dates[dates.length - 1]}`
})
const visibleCurveMetrics = computed<CurveMetricSnapshot>(() => computeCurveMetrics(visibleNetValue.value))
const fullCurveMetrics = computed<CurveMetricSnapshot>(() => computeCurveMetrics(effectiveNetValue.value, 1))
const fullMetricSnapshot = computed<MetricSnapshot>(() => ({
  cum_return: fullCurveMetrics.value.cum_return ?? entry.value.cum_return,
  max_drawdown: fullCurveMetrics.value.max_drawdown
    ?? normalizeDrawdownValue(readNumber(metrics.value?.max_drawdown))
    ?? normalizeDrawdownValue(entry.value.max_drawdown),
  ann_return: fullCurveMetrics.value.ann_return ?? readNumber(metrics.value?.ann_return),
  sharpe: fullCurveMetrics.value.sharpe ?? readNumber(metrics.value?.sharpe),
  win_rate: readNumber(metrics.value?.win_rate) ?? entry.value.win_rate,
  total_trades: readNumber(metrics.value?.total_trades) ?? entry.value.trade_count,
  avg_holding_days: readNumber(metrics.value?.avg_holding_days),
}))
const performanceMetricSnapshot = computed<MetricSnapshot>(() => {
  if (activeRange.value === 'all') return fullMetricSnapshot.value
  const curveMetrics = visibleCurveMetrics.value
  return {
    cum_return: curveMetrics.cum_return ?? fullMetricSnapshot.value.cum_return,
    max_drawdown: curveMetrics.max_drawdown ?? fullMetricSnapshot.value.max_drawdown,
    ann_return: curveMetrics.ann_return ?? fullMetricSnapshot.value.ann_return,
    sharpe: curveMetrics.sharpe ?? fullMetricSnapshot.value.sharpe,
    win_rate: readNumber(metrics.value?.win_rate) ?? entry.value.win_rate,
    total_trades: readNumber(metrics.value?.total_trades) ?? entry.value.trade_count,
    avg_holding_days: readNumber(metrics.value?.avg_holding_days),
  }
})
const detailCreatedDate = computed(() => {
  const catalogEntry = isRecord(detail.value?.catalog_entry) ? detail.value.catalog_entry : {}
  return formatDateText(readString(catalogEntry.created_at) || readString(catalogEntry.create_time) || readString(detail.value?.timestamp) || netValueDates.value[0])
})
const initialCapitalText = computed(() => {
  const catalogEntry = isRecord(detail.value?.catalog_entry) ? detail.value.catalog_entry : {}
  const config = isRecord(detail.value?.config) ? detail.value.config : {}
  const portfolio = isRecord(config.portfolio) ? config.portfolio : {}
  const rawValue = readNumber(catalogEntry.initial_cash)
    ?? readNumber(catalogEntry.initial_capital)
    ?? readNumber(detail.value?.initial_cash)
    ?? readNumber(detail.value?.initial_capital)
    ?? readNumber(portfolio.initial_capital)
  return rawValue == null
    ? '-'
    : rawValue.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
})
const benchmarkLabel = computed(() => {
  const catalogEntry = isRecord(detail.value?.catalog_entry) ? detail.value.catalog_entry : {}
  return readString(netValue.value?.benchmark_label)
    || readString(netValue.value?.benchmark_name)
    || readString(catalogEntry.benchmark_label)
    || readString(catalogEntry.benchmark)
    || '基准'
})
const benchmarkDisplayText = computed(() => {
  const config = isRecord(detail.value?.config) ? detail.value.config : {}
  const backtest = isRecord(config.backtest) ? config.backtest : {}
  const code = readString(backtest.benchmark_code)
  const label = benchmarkLabel.value
  return code && !label.includes(code) ? `${label} ${code}` : label
})
const strategyPortfolioText = computed(() => {
  const catalogEntry = isRecord(detail.value?.catalog_entry) ? detail.value.catalog_entry : {}
  return readString(catalogEntry.portfolio_label)
    || readString(catalogEntry.position_text)
    || '-'
})
const strategyDescriptionManifest = computed(() => {
  const direct = detail.value?.strategy_description
  if (isRecord(direct)) return direct
  const catalogEntry = isRecord(detail.value?.catalog_entry) ? detail.value.catalog_entry : {}
  const catalogDescription = catalogEntry.strategy_description
  return isRecord(catalogDescription) ? catalogDescription : null
})
const strategyFrequencyText = computed(() => {
  const description = strategyDescriptionManifest.value
  const semantics = isRecord(description?.semantics) ? description.semantics : {}
  return readString(semantics.timeframe) || '未提供'
})
const strategyDescription = computed(() => {
  const description = strategyDescriptionManifest.value
  const profile = isRecord(description?.profile) ? description.profile : {}
  return readString(profile.summary) || '未提供 LLM 策略描述'
})
const strategyCacheLabel = computed(() => {
  const catalogEntry = isRecord(detail.value?.catalog_entry) ? detail.value.catalog_entry : {}
  const status = readString(catalogEntry.cache_status).toLowerCase()
  if (status === 'valid') return '缓存有效'
  if (status === 'stale') return '缓存待更新'
  return entry.value.status_label || '缓存状态未知'
})
const headlineMetricCards = computed<DashboardMetricCard[]>(() => {
  const snapshot = fullMetricSnapshot.value

  return [
    {
      key: 'cum_return',
      label: '累计收益',
      value: formatPercent(snapshot.cum_return),
      valueClass: metricTrendClass(snapshot.cum_return),
      note: `最终净值 ${formatPlainNumber(entry.value.final_net_value, 4)}`,
      accent: 'return',
      tone: buildReturnTone(snapshot.cum_return),
      progress: scaleMetricProgress(snapshot.cum_return, -0.1, 0.5),
    },
    {
      key: 'max_drawdown',
      label: '最大回撤',
      value: formatDrawdown(snapshot.max_drawdown),
      valueClass: 'metric-down',
      note: buildDrawdownMetricNote(),
      accent: 'risk',
      tone: buildDrawdownTone(snapshot.max_drawdown),
      progress: scaleMetricProgress(Math.abs(readNumber(snapshot.max_drawdown) ?? 0), 0.35, 0.05),
    },
    {
      key: 'ann_return',
      label: '年化收益',
      value: formatPercent(snapshot.ann_return),
      valueClass: metricTrendClass(snapshot.ann_return),
      note: buildReturnMetricNote(),
      accent: 'return',
      tone: buildReturnTone(snapshot.ann_return),
      progress: scaleMetricProgress(snapshot.ann_return, -0.1, 0.5),
    },
    {
      key: 'sharpe',
      label: '夏普比率',
      value: formatPlainNumber(snapshot.sharpe, 2),
      valueClass: metricQualityClass(snapshot.sharpe, 1.5, 0.5),
      note: buildSharpeMetricNote(),
      accent: 'quality',
      tone: buildSharpeTone(snapshot.sharpe),
      progress: scaleMetricProgress(snapshot.sharpe, 0, 2.5),
    },
    {
      key: 'win_rate',
      label: '胜率（全周期）',
      value: formatPercent(snapshot.win_rate),
      valueClass: metricQualityClass(snapshot.win_rate, 0.55, 0.45),
      note: buildWinRateMetricNote(),
      accent: 'quality',
      tone: buildWinRateTone(snapshot.win_rate),
      progress: scaleMetricProgress(snapshot.win_rate, 0.35, 0.7),
    },
    {
      key: 'total_trades',
      label: '交易次数（全周期）',
      value: formatInteger(snapshot.total_trades),
      valueClass: '',
      note: `成交样本 ${formatInteger(snapshot.total_trades)} 笔`,
      accent: 'trade',
      tone: buildSampleTone(snapshot.total_trades),
      progress: scaleMetricProgress(snapshot.total_trades, 0, 300),
    },
  ]
})
const strategyReportUrl = computed(() => libraryReportUrl(props.name))
const investorConditionSections = computed<InvestorConditionSection[]>(() => {
  const description = strategyDescriptionManifest.value
  return readInvestorExplanation(description?.investor_explanation) || []
})
const investorIndicators = computed<InvestorIndicator[]>(() => {
  const description = strategyDescriptionManifest.value
  return readInvestorIndicators(description?.indicators)
})

const researchScore = computed(() => analysis.value?.score ?? null)
const researchScoreClass = computed(() => {
  const score = readNumber(researchScore.value?.value)
  if (score == null) return ''
  if (score >= 65) return 'metric-up'
  if (score < 50) return 'metric-down'
  return ''
})
const researchConclusion = computed(() => {
  const cumulativeReturn = readNumber(fullMetricSnapshot.value.cum_return)
  const maxDrawdownValue = readNumber(fullMetricSnapshot.value.max_drawdown)
  if (cumulativeReturn == null) return '数据不足 · 待评估'
  if (maxDrawdownValue == null) {
    return cumulativeReturn > 0 ? '正收益 · 风险待评估' : '收益承压 · 风险待评估'
  }
  const maxDrawdown = Math.abs(maxDrawdownValue)
  const highReturn = cumulativeReturn != null && cumulativeReturn >= 0.5
  const elevatedRisk = maxDrawdown >= 0.25
  if (highReturn && elevatedRisk) return '高收益 · 高波动'
  if (highReturn) return '高收益 · 风险可控'
  if (cumulativeReturn != null && cumulativeReturn > 0 && elevatedRisk) return '正收益 · 回撤偏高'
  if (cumulativeReturn != null && cumulativeReturn > 0) return '正收益 · 表现稳健'
  return '收益承压 · 需要复核'
})
const researchConclusionTone = computed(() => {
  const cumulativeReturn = readNumber(fullMetricSnapshot.value.cum_return)
  const maxDrawdown = Math.abs(readNumber(fullMetricSnapshot.value.max_drawdown) ?? 0)
  if (cumulativeReturn == null) return 'is-neutral'
  if (cumulativeReturn <= 0) return 'is-negative'
  if (maxDrawdown >= 0.25) return 'is-caution'
  return 'is-positive'
})
const researchConclusionNote = computed(() => {
  const grade = readString(researchScore.value?.grade) || '-'
  const score = readNumber(researchScore.value?.value)
  return `评级 ${grade} · 研究评分 ${score == null ? '-' : score.toFixed(1)}`
})
const researchFactCards = computed(() => {
  const quality = analysis.value?.sample_quality ?? {}
  const profile = analysis.value?.return_profile ?? {}
  return [
    { label: '持仓覆盖率', value: formatPercent(quality.holding_coverage) },
    { label: '正收益交易日', value: formatPercent(profile.positive_day_ratio) },
    { label: '交易次数', value: formatInteger(fullMetricSnapshot.value.total_trades) },
    { label: '平均持仓', value: `${formatPlainNumber(fullMetricSnapshot.value.avg_holding_days, 2)} 天` },
  ]
})
const researchDiagnostics = computed(() => analysis.value?.diagnostics ?? [])
const researchWarnings = computed(() => analysis.value?.sample_quality?.warnings ?? [])
const displayResearchDiagnostics = computed(() => {
  const rows = [...researchDiagnostics.value]
  const hasVolatilityWarning = rows.some(item => item.code === 'high_volatility' || String(item.label || '').includes('波动'))
  const annualizedVolatility = fullCurveMetrics.value.ann_volatility
  if (!hasVolatilityWarning && annualizedVolatility != null && annualizedVolatility >= 0.45) {
    rows.push({
      code: 'derived_high_volatility',
      label: '年化波动率偏高',
      detail: `全周期年化波动率为 ${formatPercent(annualizedVolatility)}。`,
      severity: 'warning',
    })
  }
  return rows
})
const performanceMetricCells = computed<PerformanceMetricCell[]>(() => [
  {
    label: '卡玛比率',
    value: formatPlainNumber(metrics.value?.calmar, 2),
    note: '全周期年化收益 / 最大回撤',
    valueClass: metricQualityClass(metrics.value?.calmar, 1.5, 0.5),
  },
  {
    label: '索提诺比率',
    value: formatPlainNumber(readNumber(metrics.value?.sortino) ?? fullCurveMetrics.value.sortino, 2),
    note: '全周期下行风险调整收益',
    valueClass: metricQualityClass(readNumber(metrics.value?.sortino) ?? fullCurveMetrics.value.sortino, 1.5, 0.5),
  },
  {
    label: '信息比率',
    value: formatPlainNumber(readNumber(metrics.value?.information_ratio) ?? fullCurveMetrics.value.information_ratio, 2),
    note: `全周期相对 ${benchmarkLabel.value}`,
    valueClass: metricQualityClass(readNumber(metrics.value?.information_ratio) ?? fullCurveMetrics.value.information_ratio, 0.8, 0),
  },
  {
    label: '回撤修复期',
    value: metrics.value?.max_dd_recovery_days == null ? '-' : `${formatInteger(metrics.value.max_dd_recovery_days)} 天`,
    note: readString(metrics.value?.max_dd_recovery_date) || '未记录恢复日期',
  },
  {
    label: '盈亏比',
    value: formatPlainNumber(metrics.value?.profit_loss_ratio, 2),
    note: '全周期盈利交易 / 亏损交易',
    valueClass: metricQualityClass(metrics.value?.profit_loss_ratio, 1.5, 1),
  },
  {
    label: '平均持仓',
    value: `${formatPlainNumber(fullMetricSnapshot.value.avg_holding_days, 2)} 天`,
    note: `全周期 ${formatInteger(fullMetricSnapshot.value.total_trades)} 笔交易`,
  },
])
const performanceIntervalMetrics = computed<PerformanceMetricCell[]>(() => [
  {
    label: '区间收益',
    value: formatPercent(performanceMetricSnapshot.value.cum_return),
    note: visibleDateRangeText.value,
    valueClass: metricTrendClass(performanceMetricSnapshot.value.cum_return),
  },
  {
    label: '年化收益',
    value: formatPercent(performanceMetricSnapshot.value.ann_return),
    note: activeRangeOption.value.label,
    valueClass: metricTrendClass(performanceMetricSnapshot.value.ann_return),
  },
  {
    label: '最大回撤',
    value: formatDrawdown(performanceMetricSnapshot.value.max_drawdown),
    note: '区间峰谷回撤',
    valueClass: 'metric-down',
  },
  {
    label: '年化波动',
    value: formatPercent(visibleCurveMetrics.value.ann_volatility),
    note: '由区间日收益推导',
  },
  {
    label: '夏普比率',
    value: formatPlainNumber(performanceMetricSnapshot.value.sharpe, 2),
    note: buildSharpeMetricNote(performanceMetricSnapshot.value.sharpe),
    valueClass: metricQualityClass(performanceMetricSnapshot.value.sharpe, 1.5, 0.5),
  },
  {
    label: '净值点',
    value: formatInteger(visibleNetValueDates.value.length),
    note: '当前区间有效样本',
  },
])
const researchYearlyRows = computed<TableRow[]>(() => {
  const dates = Array.isArray(effectiveNetValue.value?.dates) ? effectiveNetValue.value.dates.map(String) : []
  const values = Array.isArray(effectiveNetValue.value?.net_value) ? effectiveNetValue.value.net_value.map(Number) : []
  const usableCount = Math.min(dates.length, values.length)
  if (!usableCount) {
    return (analysis.value?.period_stats?.yearly ?? []).map(row => ({
      period: row.period,
      return: formatPercent(row.return),
      max_drawdown: formatDrawdown(row.max_drawdown),
      day_count: formatInteger(row.day_count),
      positive_days: formatInteger(row.positive_days),
      negative_days: formatInteger(row.negative_days),
    }))
  }

  const yearIndices = new Map<string, number[]>()
  for (let index = 0; index < usableCount; index += 1) {
    if (!Number.isFinite(values[index]) || values[index] <= 0) continue
    const dateKey = normalizeDateKey(dates[index])
    const year = dateKey.slice(0, 4)
    if (!/^\d{4}$/.test(year)) continue
    const indices = yearIndices.get(year) ?? []
    indices.push(index)
    yearIndices.set(year, indices)
  }

  let previousValue = 1
  return Array.from(yearIndices.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([year, indices]) => {
      const baseline = previousValue
      let peak = baseline
      let prior = baseline
      let maxDrawdown = 0
      let positiveDays = 0
      let negativeDays = 0

      for (const index of indices) {
        const value = values[index]
        if (value > prior) positiveDays += 1
        else if (value < prior) negativeDays += 1
        peak = Math.max(peak, value)
        if (peak > 0) maxDrawdown = Math.max(maxDrawdown, 1 - value / peak)
        prior = value
      }

      const lastValue = values[indices[indices.length - 1]]
      const yearlyReturn = baseline > 0 ? lastValue / baseline - 1 : null
      previousValue = lastValue
      return {
        period: year,
        return: formatPercent(yearlyReturn),
        max_drawdown: formatDrawdown(maxDrawdown),
        day_count: formatInteger(indices.length),
        positive_days: formatInteger(positiveDays),
        negative_days: formatInteger(negativeDays),
      }
    })
})
const scoreComponentRows = computed<TableRow[]>(() => {
  const components = analysis.value?.score?.components ?? {}
  const weights = analysis.value?.score?.weights ?? {}
  const labels: Record<string, string> = {
    return_quality: '收益能力',
    risk_control: '回撤控制',
    stability: '稳定性',
    trade_quality: '交易质量',
    sample_quality: '样本质量',
    regime_robustness: '市场状态稳健性',
  }
  return Object.entries(labels).map(([key, label]) => ({
    component: label,
    score: formatPlainNumber(components[key as keyof typeof components], 1),
    weight: formatPercent(weights[key as keyof typeof weights]),
  }))
})
const marketStateRows = computed<TableRow[]>(() => {
  const rows = analysis.value?.market_state?.states?.trend ?? []
  return rows.map(row => ({
    label: row.label,
    day_count: formatInteger(row.day_count),
    return: formatPercent(row.return),
    benchmark_return: formatPercent(row.benchmark_return),
    excess_return: formatPercent(row.excess_return),
    max_drawdown: formatDrawdown(row.max_drawdown),
    positive_day_ratio: formatPercent(row.positive_day_ratio),
  }))
})
const marketStateBars = computed(() => {
  const rows = analysis.value?.market_state?.states?.trend ?? []
  const totalDays = rows.reduce((sum, row) => sum + Math.max(0, readNumber(row.day_count) ?? 0), 0)
  return rows.map((row, index) => {
    const dayCount = Math.max(0, readNumber(row.day_count) ?? 0)
    return {
      label: row.label || `状态 ${index + 1}`,
      percentage: totalDays > 0 ? (dayCount / totalDays) * 100 : 0,
      tone: ['bull', 'range', 'bear'][index] || 'range',
      dayCount,
      returnValue: readNumber(row.return),
      returnText: formatPercent(row.return),
      excessReturnText: formatPercent(row.excess_return),
      maxDrawdownText: formatDrawdown(row.max_drawdown),
    }
  })
})
const showcaseHoldingTargetDate = computed(() => {
  const target = normalizeDateKey(showcaseFocusDate.value)
  if (!target || !holdingsDates.value.length) return ''
  const datedHoldings = holdingsDates.value
    .map(date => ({ date, normalized: normalizeDateKey(date) }))
    .filter(item => item.normalized)
  return [...datedHoldings].reverse().find(item => item.normalized <= target)?.date
    || datedHoldings[0]?.date
    || ''
})
const canOpenShowcaseHolding = computed(() => Boolean(showcaseHoldingTargetDate.value))
const reviewMetricCards = computed(() => {
  const overview = review.value?.overview ?? {}
  return [
    { label: '覆盖交易日', value: formatInteger(overview.holding_date_count), valueClass: '' },
    { label: '涉及股票', value: formatInteger(overview.unique_stock_count), valueClass: '' },
    { label: '平均持仓数', value: formatPlainNumber(overview.avg_holding_count, 2), valueClass: '' },
    { label: '平均仓位', value: formatPercent(overview.avg_position_ratio), valueClass: '' },
    { label: '风控事件', value: formatInteger(overview.risk_event_count), valueClass: readNumber(overview.risk_event_count) ? 'metric-down' : '' },
    { label: '仓位变更', value: formatInteger(overview.position_change_count), valueClass: '' },
  ]
})
const reviewStockRows = computed<TableRow[]>(() => {
  const rows = review.value?.stocks ?? []
  return rows.slice(0, 30).map(row => ({
    stock: buildHoldingStockText(row.code, row.name, row.latest_unrealized_pnl),
    holding_days: formatInteger(row.holding_days),
    date_range: [row.first_date, row.last_date].filter(Boolean).join(' ~ ') || '-',
    latest_holding_value: formatMoney(row.latest_holding_value),
    max_holding_value: formatMoney(row.max_holding_value),
    latest_unrealized_pnl: formatSignedMoney(row.latest_unrealized_pnl),
    realized_return_sum: formatPercent(row.realized_return_sum),
    avg_position_ratio: formatPercent(row.avg_position_ratio),
    trade_count: formatInteger(row.trade_count),
  }))
})
const scoreDetailsSummary = computed(() => {
  const validRows = scoreComponentRows.value.filter(row => row.score !== '-')
  return validRows.length ? `缓存全周期 · ${validRows.length} 个评分维度` : '缓存全周期 · 暂无评分拆解'
})
const marketStateSummary = computed(() => {
  const labels = marketStateRows.value
    .map(row => String(row.label || '').trim())
    .filter(Boolean)
  return labels.length ? `缓存全周期 · ${labels.join('/')} ${labels.length} 类` : '缓存全周期 · 暂无市场状态切片'
})
const yearlyPerformanceSummary = computed(() => {
  return researchYearlyRows.value.length ? `实际数据全周期 · ${researchYearlyRows.value.length} 个年度表现` : '实际数据全周期 · 暂无年度表现'
})
const returnHeatmapSummary = computed(() => {
  const points = visibleNetValueDates.value.length
  const rangeLabel = activeRange.value === 'all' ? '全周期' : activeRangeOption.value.label
  return points ? `${rangeLabel} · 基于 ${points} 个净值点聚合` : `${rangeLabel} · 等待净值数据`
})
const holdingsHeatmapSummary = computed(() => {
  return holdingsDates.value.length ? `${holdingsDates.value.length} 个交易日仓位` : '暂无持仓缓存'
})
const reviewStocksSummary = computed(() => {
  const total = review.value?.stocks?.length ?? 0
  if (!total) return '股票复盘：暂无数据'
  return `股票复盘：展示 Top ${reviewStockRows.value.length} / ${total}`
})
const focusDateIndex = computed(() => holdingsDates.value.indexOf(focusDate.value))
const canShiftBackward = computed(() => focusDateIndex.value > 0)
const canShiftForward = computed(() => focusDateIndex.value >= 0 && focusDateIndex.value < holdingsDates.value.length - 1)
const dailyTradeRows = computed<TableRow[]>(() => {
  const records = Array.isArray(trades.value?.execution_records) ? trades.value.execution_records : []
  if (!focusDate.value) return []
  const targetDate = normalizeDateKey(focusDate.value)
  return records.filter(record => normalizeDateKey(record['日期']) === targetDate)
})
const dailyTradeSummary = computed(() => buildDailyTradeSummary(dailyTradeRows.value))
const dailyTradeColumns = computed<TableColumn[]>(() => {
  const rawColumns = Array.isArray(trades.value?.execution_columns) ? trades.value.execution_columns : []
  if (rawColumns.length) return rawColumns.map(column => ({ key: column, label: column }))
  const sample = dailyTradeRows.value[0]
  return sample ? Object.keys(sample).map(column => ({ key: column, label: column })) : []
})
const dailyTradeCards = computed(() => {
  const summary = dailyTradeSummary.value
  return [
    { label: '当日记录', value: `${summary.recordCount} 条`, valueClass: '' },
    { label: '当日买入', value: `${summary.buyCount} 条`, valueClass: '' },
    { label: '当日卖出', value: `${summary.sellCount} 条`, valueClass: '' },
    { label: '当日风控/调仓', value: `${summary.riskCount} 条`, valueClass: summary.riskCount > 0 ? 'metric-down' : '' },
    { label: '当日净资金变动', value: formatSignedMoney(summary.netCashDelta), valueClass: metricTrendClass(summary.netCashDelta) },
    { label: '当日手续费+印花税', value: formatMoney(summary.totalCost), valueClass: '' },
  ]
})
const latestSignalData = computed(() => {
  const summarySignals = tradeSummary.value?.latest_signals
  if (summarySignals) {
    const normalizeSignal = (signal: typeof summarySignals.buy): LatestSignalSummary => ({
      date: String(signal?.date || ''),
      items: Array.isArray(signal?.items)
        ? signal.items
          .map(item => ({
            code: String(item?.code || '').trim(),
            name: String(item?.name || '').trim(),
          }))
          .filter(item => item.code)
        : [],
    })
    return {
      missingCode: Boolean(summarySignals.missing_code),
      buy: normalizeSignal(summarySignals.buy),
      sell: normalizeSignal(summarySignals.sell),
    }
  }

  const records = Array.isArray(trades.value?.records) ? trades.value.records : []
  const emptySignal: LatestSignalSummary = {
    date: '',
    items: [],
  }
  if (!records.length) {
    return {
      missingCode: false,
      buy: emptySignal,
      sell: emptySignal,
    }
  }

  const columns = resolveTradeColumns(records, Array.isArray(trades.value?.columns) ? trades.value.columns : [])
  if (!columns.code) {
    return {
      missingCode: true,
      buy: emptySignal,
      sell: emptySignal,
    }
  }

  return {
    missingCode: false,
    buy: columns.buyDate ? collectLatestSignal(records, columns.buyDate, columns.code, columns.name) : emptySignal,
    sell: columns.sellDate ? collectLatestSignal(records, columns.sellDate, columns.code, columns.name) : emptySignal,
  }
})
const latestSignalCards = computed(() => {
  return [
    {
      label: '最近买入信号',
      kind: 'buy' as const,
      emptyText: '暂无最近买入信号。',
      signal: latestSignalData.value.buy,
    },
    {
      label: '最近卖出信号',
      kind: 'sell' as const,
      emptyText: '暂无最近卖出信号。',
      signal: latestSignalData.value.sell,
    },
  ]
})
const holdingsSummaryCards = computed(() => {
  if (holdingsDisplayMode.value === 'simple') {
    return [
      { label: '总资产', value: formatMoney(holdingsSummary.value?.asset), valueClass: '' },
      { label: '今日盈亏', value: formatSignedMoney(holdingsSummary.value?.daily_pnl), valueClass: metricTrendClass(holdingsSummary.value?.daily_pnl) },
      { label: '持仓盈亏', value: formatSignedMoney(holdingsSummary.value?.unrealized_pnl), valueClass: metricTrendClass(holdingsSummary.value?.unrealized_pnl) },
      { label: '今日交易', value: `${dailyTradeSummary.value.recordCount} 笔`, valueClass: '' },
    ]
  }
  return [
    { label: '当日净值', value: formatPlainNumber(holdingsSummary.value?.net_value, 4), valueClass: '' },
    { label: '总资产', value: formatMoney(holdingsSummary.value?.asset), valueClass: '' },
    { label: '现金', value: formatMoney(holdingsSummary.value?.cash), valueClass: '' },
    { label: '持仓市值', value: formatMoney(holdingsSummary.value?.position_value), valueClass: '' },
    { label: '累计盈亏', value: formatSignedMoney(holdingsSummary.value?.cum_pnl), valueClass: metricTrendClass(holdingsSummary.value?.cum_pnl) },
    { label: '当日盈亏', value: formatSignedMoney(holdingsSummary.value?.daily_pnl), valueClass: metricTrendClass(holdingsSummary.value?.daily_pnl) },
    { label: '已实现盈亏', value: formatSignedMoney(holdingsSummary.value?.realized_pnl), valueClass: metricTrendClass(holdingsSummary.value?.realized_pnl) },
    { label: '浮动盈亏', value: formatSignedMoney(holdingsSummary.value?.unrealized_pnl), valueClass: metricTrendClass(holdingsSummary.value?.unrealized_pnl) },
    { label: '当日收益率', value: formatPercent(holdingsSummary.value?.daily_return), valueClass: metricTrendClass(holdingsSummary.value?.daily_return) },
    { label: '持仓数量', value: formatInteger(holdingsSummary.value?.holding_count), valueClass: '' },
  ]
})
const detailViewModeOptions: Array<{ value: DetailViewMode, label: string }> = [
  { value: 'simple', label: '展示' },
  { value: 'advanced', label: '研究' },
]
const isResearchMode = computed(() => detailViewMode.value === 'advanced')

// Convert holdings data for HoldingsHeatmapPanel
const flattenedHoldings = computed(() => {
  if (!effectiveHoldings.value) return null
  const result: Array<{
    trade_date: string
    position_ratio: number
  }> = []

  for (const date of effectiveHoldings.value.dates) {
    const summary = effectiveHoldings.value.summary_by_date?.[date]
    const positionValue = readNumber(summary?.position_value) ?? 0
    const asset = readNumber(summary?.asset) ?? 0
    result.push({
      trade_date: date,
      position_ratio: asset > 0 ? Number((positionValue / asset).toFixed(6)) : 0,
    })
  }

  return result.length > 0 ? result : null
})

// Convert trades data for TradeDistributionPanel
const tradeRecordsForDistribution = computed(() => {
  if (!trades.value?.records) return null

  const columns = resolveTradeColumns(
    trades.value.records,
    Array.isArray(trades.value.columns) ? trades.value.columns : []
  )

  if (!columns.code || !columns.buyDate || !columns.sellDate) return null

  const result: Array<{
    stock_code: string
    stock_name: string
    entry_date: string
    exit_date: string
    holding_days: number
    return_rate: number
  }> = []

  for (const row of trades.value.records) {
    const code = String(row[columns.code] || '').trim()
    const name = columns.name ? String(row[columns.name] || '').trim() : ''
    const buyDate = String(row[columns.buyDate] || '').trim()
    const sellDate = String(row[columns.sellDate] || '').trim()

    if (!code || !buyDate || !sellDate) continue

    // Calculate holding days
    const holdingDaysValue = row['持仓天数'] ?? row['持仓日'] ?? row['holding_days'] ?? row['hold_days']
    const holdingDays = readNumber(holdingDaysValue) ?? inferHoldingDays(buyDate, sellDate)

    // Get return rate
    const returnRateValue = row['收益率'] ?? row['return_rate'] ?? row['盈亏比例'] ?? row['盈亏'] ?? row['return']
    const returnRate = readRateValue(returnRateValue) ?? 0

    result.push({
      stock_code: code,
      stock_name: name,
      entry_date: buyDate,
      exit_date: sellDate,
      holding_days: holdingDays,
      return_rate: returnRate,
    })
  }

  return result.length > 0 ? result : null
})
const visibleTradeRecordsForDistribution = computed(() => {
  const records = tradeRecordsForDistribution.value
  const dates = visibleNetValueDates.value
  const startDate = normalizeDateKey(dates[0])
  const endDate = normalizeDateKey(dates[dates.length - 1])
  if (!records || !startDate || !endDate) return records
  const filtered = records.filter(record => {
    const entryDate = normalizeDateKey(record.entry_date)
    const exitDate = normalizeDateKey(record.exit_date)
    if (!entryDate || !exitDate) return false
    return exitDate >= startDate && entryDate <= endDate
  })
  return filtered.length ? filtered : null
})

const holdingsViewOptions = computed(() => ([
  { value: 'detail' as HoldingsCurrentView, label: '原始明细' },
  { value: 'stock' as HoldingsCurrentView, label: '按股票汇总' },
  { value: 'slot' as HoldingsCurrentView, label: '按槽位汇总' },
]))
const showHoldingsViewToggle = computed(() => holdingsDisplayMode.value === 'pro')
const showHoldingsHistory = computed(() => holdingsDisplayMode.value === 'pro')
const showHoldingsExport = computed(() => holdingsDisplayMode.value === 'pro')
const canExportFocusedHoldings = computed(() => Boolean(showHoldingsExport.value && focusDate.value && currentHoldingViewRows.value.length))
const holdingsCurrentViewLabelText = computed(() => holdingsCurrentViewLabel(effectiveHoldingsView.value))
const holdingsCurrentNote = computed(() => {
  if (!focusDate.value) return '当前没有可定位的交易日。'
  if (!currentHoldingRawRows.value.length) return `当前焦点日：${focusDate.value} · 该交易日没有持仓，视为当日空仓。`
  if (holdingsDisplayMode.value === 'simple') {
    return `当前焦点日：${focusDate.value} · 普通模式 · 默认按股票汇总展示`
  }
  return `当前焦点日：${focusDate.value} · ${holdingsCurrentViewLabelText.value}视图 · 导出跟随当前视图`
})
const holdingsHistoryNote = computed(() => {
  if (!effectiveHoldings.value) return '当前策略还没有可展示的按日持仓历史表。'
  if (!fullHoldingsLoaded.value) return '正在准备完整按日持仓历史表...'
  if (!holdingHistoryRows.value.length) return '暂无按日持仓历史记录'
  if (holdingHistoryFilteredRows.value.length === holdingHistoryRows.value.length) {
    return `${holdingHistoryRows.value.length} 条按日持仓记录`
  }
  return `${holdingHistoryFilteredRows.value.length}/${holdingHistoryRows.value.length} 条按日持仓记录`
})
const holdingHistoryEmptyText = computed(() => {
  if (!fullHoldingsLoaded.value) return '正在加载完整按日持仓历史。'
  if (!effectiveHoldings.value || !holdingHistoryRows.value.length) return '当前策略还没有可展示的按日持仓历史表。'
  if (!holdingHistoryFilteredRows.value.length) return '当前筛选条件下没有匹配的持仓记录。'
  return '暂无按日持仓历史。'
})
const executionColumns = computed<TableColumn[]>(() => {
  const rawColumns = Array.isArray(trades.value?.execution_columns) ? trades.value.execution_columns : []
  if (rawColumns.length) return rawColumns.map(column => ({ key: column, label: column }))
  const sample = Array.isArray(trades.value?.execution_records) ? trades.value?.execution_records?.[0] : null
  return sample ? Object.keys(sample).map(column => ({ key: column, label: column })) : []
})
const executionRows = computed<TableRow[]>(() => Array.isArray(trades.value?.execution_records) ? trades.value.execution_records : [])
const latestExecutionRows = computed<TableRow[]>(() => (
  executionRows.value
    .map((row, index) => ({ row, index, date: ledgerRowDate(row) }))
    .sort((left, right) => right.date.localeCompare(left.date) || right.index - left.index)
    .slice(0, 5)
    .map(item => item.row)
))
const filteredExecutionRows = computed<TableRow[]>(() => filterLedgerRows(executionRows.value, executionSearch.value))
const canExportExecutionCsv = computed(() => filteredExecutionRows.value.length > 0)
const orderColumns = computed<TableColumn[]>(() => {
  const rawColumns = Array.isArray(trades.value?.order_columns) ? trades.value.order_columns : []
  if (rawColumns.length) return rawColumns.map(column => ({ key: column, label: column }))
  const sample = Array.isArray(trades.value?.order_records) ? trades.value?.order_records?.[0] : null
  return sample ? Object.keys(sample).map(column => ({ key: column, label: column })) : []
})
const orderRows = computed<TableRow[]>(() => Array.isArray(trades.value?.order_records) ? trades.value.order_records : [])
const filteredOrderRows = computed<TableRow[]>(() => filterLedgerRows(orderRows.value, orderSearch.value))
const canExportOrderCsv = computed(() => filteredOrderRows.value.length > 0)
const totalExecutionCount = computed(() => (
  fullTradesLoaded.value
    ? executionRows.value.length
    : tradeSummary.value?.summary.execution_record_count ?? executionRows.value.length
))
const totalOrderCount = computed(() => (
  fullTradesLoaded.value
    ? orderRows.value.length
    : tradeSummary.value?.summary.order_record_count ?? orderRows.value.length
))
const ledgerDateRangeText = computed(() => {
  if (!fullTradesLoaded.value) {
    const range = tradeSummary.value?.summary.execution_date_range
    if (range?.first && range.last) {
      return range.first === range.last
        ? `成交日期 ${range.first}`
        : `成交区间 ${range.first} 至 ${range.last}`
    }
  }
  const dates = executionRows.value
    .map(ledgerRowDate)
    .filter((value): value is string => Boolean(value))
    .sort()
  if (!dates.length) return '暂无可用成交日期'
  return dates[0] === dates[dates.length - 1]
    ? `成交日期 ${dates[0]}`
    : `成交区间 ${dates[0]} 至 ${dates[dates.length - 1]}`
})
const ledgerSummaryCards = computed(() => {
  const directions = executionRows.value.map(ledgerRowDirection)
  const previewBuyCount = directions.filter(value => value === 'buy').length
  const previewSellCount = directions.filter(value => value === 'sell').length
  const buyCount = fullTradesLoaded.value ? previewBuyCount : null
  const sellCount = fullTradesLoaded.value ? previewSellCount : null
  return [
    { label: '全周期逐笔成交', value: `${totalExecutionCount.value} 条` },
    { label: '全周期买入', value: buyCount == null ? '完整数据后统计' : `${buyCount} 条` },
    { label: '全周期卖出', value: sellCount == null ? '完整数据后统计' : `${sellCount} 条` },
    { label: '全周期订单', value: `${totalOrderCount.value} 条` },
  ]
})
const holdingHistoryPanelSummary = computed(() => {
  if (!fullHoldingsLoaded.value) {
    const count = holdingsSummaryPayload.value?.summary.holding_row_count ?? 0
    return count ? `按日持仓历史：摘要已就绪，完整 ${count} 条` : '按日持仓历史：等待完整数据'
  }
  if (!holdingHistoryRows.value.length) return '按日持仓历史：暂无数据'
  if (holdingHistoryFilteredRows.value.length === holdingHistoryRows.value.length) {
    return `按日持仓历史：${holdingHistoryRows.value.length} 条`
  }
  return `按日持仓历史：${holdingHistoryFilteredRows.value.length}/${holdingHistoryRows.value.length} 条`
})
const executionPanelSummary = computed(() => ledgerPanelSummary('逐笔成交', filteredExecutionRows.value.length, totalExecutionCount.value))
const executionPreviewSummary = computed(() => (
  totalExecutionCount.value
    ? `最近 ${latestExecutionRows.value.length} 条 · 共 ${totalExecutionCount.value} 条成交${fullTradesLoaded.value ? '' : ' · 摘要预览'}`
    : '暂无逐笔成交记录'
))
const orderPanelSummary = computed(() => ledgerPanelSummary('订单流水', filteredOrderRows.value.length, totalOrderCount.value))
const pageTitle = computed(() => entry.value.title || props.name)
const detailStatusClass = computed(() => statusClass(entry.value.status))
const strategyMetaItems = computed(() => {
  return [
    { label: '初始资金', value: initialCapitalText.value },
    { label: '基准', value: benchmarkDisplayText.value },
    { label: '组合', value: strategyPortfolioText.value },
    { label: '频率', value: strategyFrequencyText.value },
  ]
})
const decisionMetricCards = computed(() => {
  const labels: Record<string, string> = {
    cum_return: '累计收益',
    max_drawdown: '最大回撤',
    ann_return: '年化收益',
    sharpe: '夏普比率',
    win_rate: '胜率',
  }
  return headlineMetricCards.value
    .filter(card => card.key in labels)
    .map(card => ({ ...card, label: labels[card.key] }))
})
const rerunButtonText = computed(() => backtestActionPending.value ? '提交中...' : '重新回测')
const backtestActionTone = computed(() => {
  const message = backtestActionMessage.value
  if (/失败|错误|未返回|不包含/.test(message)) return 'is-error'
  if (/取消|重试|运行/.test(message)) return 'is-warning'
  if (/完成|已加载/.test(message)) return 'is-success'
  return 'is-info'
})

function isDetailSectionId(value: string): value is DetailSectionId {
  return DETAIL_SECTIONS.some(section => section.id === value)
}

function isDetailSubsectionId(value: string): value is DetailSubsectionId {
  return DETAIL_SUBSECTIONS.some(subsection => subsection.id === value)
}

function detailSubsectionsForSection(sectionId: DetailSectionId): ReadonlyArray<DetailSubsectionNavItem> {
  return DETAIL_SUBSECTIONS.filter(subsection => subsection.sectionId === sectionId)
}

function detailSubsectionById(subsectionId: DetailSubsectionId): DetailSubsectionNavItem | undefined {
  return DETAIL_SUBSECTIONS.find(subsection => subsection.id === subsectionId)
}

function setPanelExpanded(panelId: DetailPanelId, isExpanded: boolean) {
  expandedPanels.value = {
    ...expandedPanels.value,
    [panelId]: isExpanded,
  }
  if (isExpanded) {
    if (panelId === 'holdingHistory' || panelId === 'holdingsHeatmap') {
      void ensureFullDataForSection('holdings', loadToken)
    } else if (panelId === 'executionRecords' || panelId === 'orderRecords') {
      void ensureFullDataForSection('details', loadToken)
    }
  }
  void nextTick().then(scheduleScrollSpy)
}

function expandDetailPanels(panelIds: readonly DetailPanelId[] = []) {
  if (!panelIds.length) return
  expandedPanels.value = panelIds.reduce<Record<DetailPanelId, boolean>>(
    (nextPanels, panelId) => ({ ...nextPanels, [panelId]: true }),
    { ...expandedPanels.value },
  )
  void nextTick().then(scheduleScrollSpy)
}

function applyDetailViewMode(mode: DetailViewMode) {
  detailViewMode.value = mode
  holdingsDisplayMode.value = normalizeHoldingsDisplayMode(mode === 'advanced' ? 'pro' : 'simple')
}

function setDetailViewMode(mode: DetailViewMode) {
  const sectionId = activeSection.value
  const shouldReanchorSection = mode === 'simple' && Boolean(
    activeSubsection.value || readDetailHashTarget()?.subsectionId,
  )
  applyDetailViewMode(mode)
  window.localStorage.setItem('strategy-detail-view-mode', mode)
  if (mode === 'simple') activeSubsection.value = null
  if (shouldReanchorSection) {
    void scrollToSection(sectionId, { focus: false, intent: 'restore' })
  } else if (mode === 'advanced') {
    void nextTick().then(scheduleScrollSpy)
  }
}

function setActiveRange(range: DetailRangeId) {
  activeRange.value = range
}

function readContextRange(): DetailRangeId {
  const value = new URL(window.location.href).searchParams.get('range') || ''
  return DETAIL_RANGE_OPTIONS.some(option => option.value === value)
    ? value as DetailRangeId
    : 'all'
}

function readContextFocusDate(): string {
  return normalizeDateKey(new URL(window.location.href).searchParams.get('date') || '')
}

function readContextViewMode(): DetailViewMode {
  const queryMode = new URL(window.location.href).searchParams.get('mode')
  if (queryMode === 'research' || queryMode === 'advanced') return 'advanced'
  if (queryMode === 'display' || queryMode === 'simple') return 'simple'
  return window.localStorage.getItem('strategy-detail-view-mode') === 'advanced' ? 'advanced' : 'simple'
}

function replaceContextQuery() {
  if (!contextQueryReady) return
  const url = new URL(window.location.href)
  const requestedMode = url.searchParams.get('mode')
  const preserveDisplayOverride = requestedMode === 'display' || requestedMode === 'simple'
  if (activeRange.value === 'all') url.searchParams.delete('range')
  else url.searchParams.set('range', activeRange.value)
  if (focusDate.value) url.searchParams.set('date', focusDate.value)
  else url.searchParams.delete('date')
  if (isResearchMode.value) url.searchParams.set('mode', 'research')
  else if (preserveDisplayOverride) url.searchParams.set('mode', 'display')
  else url.searchParams.delete('mode')
  window.history.replaceState(
    window.history.state,
    '',
    `${url.pathname}${url.search}${url.hash}`,
  )
}

async function openShowcaseHolding() {
  if (!showcaseHoldingTargetDate.value) return
  focusDate.value = showcaseHoldingTargetDate.value
  await scrollToSection('holdings')
}

async function openStrategyRules() {
  setPanelExpanded('strategyRules', true)
  await scrollToSection('rules')
}

async function openAllExecutionRecords() {
  setDetailViewMode('advanced')
  executionSearch.value = ''
  void ensureFullDataForSection('details', loadToken)
  await scrollToSubsection('details/executions', { focus: false, intent: 'explicit' })
}

function retrySupportingData() {
  void loadSupportingData(loadToken)
  if (activeSection.value === 'review') void loadReviewData(loadToken)
}

function handleHoldingHistoryPanelToggle(isExpanded: boolean) {
  if (isExpanded) setDetailViewMode('advanced')
  if (isExpanded) void ensureFullDataForSection('holdings', loadToken)
  setPanelExpanded('holdingHistory', isExpanded)
}

function readDetailHashTarget(): DetailNavigationTarget | null {
  const rawHash = window.location.hash.replace(/^#/, '')
  if (!rawHash) return null
  let decodedHash = rawHash
  try {
    decodedHash = decodeURIComponent(rawHash)
  } catch {
    decodedHash = rawHash
  }
  if (isDetailSubsectionId(decodedHash)) {
    const subsection = detailSubsectionById(decodedHash)
    return subsection
      ? { sectionId: subsection.sectionId, subsectionId: subsection.id }
      : null
  }
  return isDetailSectionId(decodedHash) ? { sectionId: decodedHash } : null
}

function replaceNavigationHash(target: DetailNavigationTarget) {
  const nextHash = `#${target.subsectionId ?? target.sectionId}`
  if (window.location.hash === nextHash) return
  const nextUrl = `${window.location.pathname}${window.location.search}${nextHash}`
  window.history.replaceState(window.history.state, '', nextUrl)
}

function replaceSectionHash(sectionId: DetailSectionId) {
  replaceNavigationHash({ sectionId })
}

function replaceSubsectionHash(subsectionId: DetailSubsectionId) {
  const subsection = detailSubsectionById(subsectionId)
  if (!subsection) return
  replaceNavigationHash({ sectionId: subsection.sectionId, subsectionId })
}

function getSectionActivationTop(targetElement?: HTMLElement): number {
  const header = document.querySelector<HTMLElement>('.strategy-workbench-v2 .result-detail-header')
  const mobileNavigation = window.matchMedia('(max-width: 1040px)').matches
    ? document.querySelector<HTMLElement>('.strategy-workbench-v2 .workbench-sidebar')
    : null
  let occupiedTop = 0
  for (const element of [header, mobileNavigation]) {
    if (!element) continue
    const position = window.getComputedStyle(element).position
    if (position !== 'fixed' && position !== 'sticky') continue
    const rect = element.getBoundingClientRect()

    if (targetElement && element === mobileNavigation) {
      const navigationPrecedesTarget = Boolean(
        element.compareDocumentPosition(targetElement) & Node.DOCUMENT_POSITION_FOLLOWING,
      )
      if (!navigationPrecedesTarget) continue
      occupiedTop += rect.height
      continue
    }

    if (rect.bottom <= occupiedTop || rect.top > occupiedTop + 2) continue
    occupiedTop += rect.height
  }
  return Math.round(occupiedTop + 16)
}

function syncStickyLayoutMetrics() {
  const pageRoot = document.querySelector<HTMLElement>('.strategy-workbench-v2')
  const header = document.querySelector<HTMLElement>('.strategy-workbench-v2 .result-detail-header')
  if (!pageRoot || !header) return
  pageRoot.style.setProperty('--detail-topbar-height', `${Math.ceil(header.getBoundingClientRect().height)}px`)
  scheduleScrollSpy()
}

function observeStickyLayout() {
  const header = document.querySelector<HTMLElement>('.strategy-workbench-v2 .result-detail-header')
  if (!header || observedStickyHeader === header) {
    syncStickyLayoutMetrics()
    return
  }

  stickyLayoutResizeObserver?.disconnect()
  stickyLayoutResizeObserver = new ResizeObserver(syncStickyLayoutMetrics)
  stickyLayoutResizeObserver.observe(header)
  observedStickyHeader = header
  syncStickyLayoutMetrics()
}

function setActiveNavigationFromScroll(
  sectionId: DetailSectionId,
  subsectionId: DetailSubsectionId | null = null,
) {
  activeSection.value = sectionId
  activeSubsection.value = subsectionId
  if (subsectionId) replaceSubsectionHash(subsectionId)
  else replaceSectionHash(sectionId)
}

function resolveActiveSubsectionFromScroll(
  sectionId: DetailSectionId,
  activationTop: number,
): DetailSubsectionId | null {
  if (!isResearchMode.value) return null
  const subsections = detailSubsectionsForSection(sectionId)
    .map(item => ({ item, element: document.getElementById(item.anchorId) }))
    .filter((entry): entry is { item: DetailSubsectionNavItem, element: HTMLElement } => (
      entry.element instanceof HTMLElement
    ))
  if (!subsections.length) return null

  let nextSubsection: DetailSubsectionId | null = null
  for (const subsection of subsections) {
    if (subsection.element.getBoundingClientRect().top > activationTop + 2) break
    nextSubsection = subsection.item.id
  }
  return nextSubsection
}

function updateActiveSectionFromScroll() {
  scrollSpyFrame = null
  if (loading.value || error.value) return

  if (sectionNavigationIntent) {
    activeSection.value = sectionNavigationIntent.sectionId
    activeSubsection.value = sectionNavigationIntent.subsectionId ?? null
    replaceNavigationHash(sectionNavigationIntent)
    return
  }

  const sections = DETAIL_SECTIONS
    .map(item => document.getElementById(item.id))
    .filter((section): section is HTMLElement => section instanceof HTMLElement)
  if (!sections.length) return

  const scrollingElement = document.scrollingElement ?? document.documentElement
  const maxScrollTop = Math.max(0, scrollingElement.scrollHeight - scrollingElement.clientHeight)
  if (maxScrollTop > 2 && scrollingElement.scrollTop >= maxScrollTop - 2) {
    const detailsSubsections = detailSubsectionsForSection('details')
      .filter(item => document.getElementById(item.anchorId) instanceof HTMLElement)
    const finalSubsection = isResearchMode.value && detailsSubsections.length
      ? detailsSubsections[detailsSubsections.length - 1].id
      : null
    setActiveNavigationFromScroll('details', finalSubsection)
    return
  }

  const activationTop = getSectionActivationTop()
  let nextSection = sections[0].id as DetailSectionId
  for (const section of sections) {
    if (section.getBoundingClientRect().top > activationTop + 2) break
    nextSection = section.id as DetailSectionId
  }
  setActiveNavigationFromScroll(
    nextSection,
    resolveActiveSubsectionFromScroll(nextSection, activationTop),
  )
}

function scheduleScrollSpy() {
  if (scrollSpyFrame !== null) return
  scrollSpyFrame = window.requestAnimationFrame(updateActiveSectionFromScroll)
}

function keepActiveSidebarLinkVisible() {
  const navigation = document.querySelector<HTMLElement>('.strategy-workbench-v2 .research-main-tabs')
  if (!navigation) return
  if (compactNavigation.value) {
    navigation.scrollTop = 0
    return
  }

  const activeLink = navigation.querySelector<HTMLElement>('.research-subnav-link[aria-current="location"]')
    ?? navigation.querySelector<HTMLElement>('.research-main-tab.active')
  if (!activeLink || navigation.scrollHeight <= navigation.clientHeight) return

  const navigationRect = navigation.getBoundingClientRect()
  const linkRect = activeLink.getBoundingClientRect()
  const edgeGap = 8
  if (linkRect.top < navigationRect.top + edgeGap) {
    navigation.scrollTop -= navigationRect.top + edgeGap - linkRect.top
  } else if (linkRect.bottom > navigationRect.bottom - edgeGap) {
    navigation.scrollTop += linkRect.bottom - navigationRect.bottom + edgeGap
  }
}

function handleViewportResize() {
  compactNavigation.value = window.matchMedia('(max-width: 1040px)').matches
  scheduleScrollSpy()
  void nextTick().then(keepActiveSidebarLinkVisible)
}

async function syncSectionFromHash() {
  const hashTarget = readDetailHashTarget()
  if (!hashTarget) {
    sectionNavigationIntent = null
    activeSubsection.value = null
    scheduleScrollSpy()
    return
  }
  sectionNavigationIntent = { ...hashTarget, source: 'history' }
  activeSection.value = hashTarget.sectionId
  activeSubsection.value = hashTarget.subsectionId ?? null
  pendingSupportReanchor = null
  pendingReviewReanchor = null
  if (loading.value || error.value) {
    pendingHashTarget = hashTarget
    return
  }
  await scrollToNavigationTarget(hashTarget, { updateHash: false, focus: false, intent: 'history' })
}

async function scrollToSection(
  sectionId: string,
  options: ScrollToSectionOptions = {},
) {
  if (!isDetailSectionId(sectionId)) return
  const { updateHash = true, focus = true, intent = 'explicit' } = options
  if (intent) sectionNavigationIntent = { sectionId, source: intent }
  activeSection.value = sectionId
  activeSubsection.value = null
  if (updateHash) replaceSectionHash(sectionId)
  await nextTick()
  const section = document.getElementById(sectionId)
  if (!section) return
  pendingSupportReanchor = dataLoadInProgress.value ? { sectionId } : null
  pendingReviewReanchor = null
  const targetTop = window.scrollY + section.getBoundingClientRect().top - getSectionActivationTop(section)
  window.scrollTo({ top: Math.max(0, targetTop), behavior: 'auto' })
  if (focus) section.focus({ preventScroll: true })
}

async function scrollToSubsection(
  subsectionId: string,
  options: ScrollToSectionOptions = {},
) {
  if (!isDetailSubsectionId(subsectionId)) return
  const subsection = detailSubsectionById(subsectionId)
  if (!subsection) return
  const { updateHash = true, focus = true, intent = 'explicit' } = options

  if (!isResearchMode.value) applyDetailViewMode('advanced')
  expandDetailPanels(subsection.expandPanels)
  if (intent) {
    sectionNavigationIntent = {
      sectionId: subsection.sectionId,
      subsectionId,
      source: intent,
    }
  }
  activeSection.value = subsection.sectionId
  activeSubsection.value = subsectionId
  if (updateHash) replaceSubsectionHash(subsectionId)

  await nextTick()
  const target = document.getElementById(subsection.anchorId)
  if (!target) return
  const navigationTarget = { sectionId: subsection.sectionId, subsectionId }
  pendingSupportReanchor = dataLoadInProgress.value ? navigationTarget : null
  pendingReviewReanchor = subsectionId === 'review/stocks' && !review.value
    ? navigationTarget
    : null
  const targetTop = window.scrollY + target.getBoundingClientRect().top - getSectionActivationTop(target)
  window.scrollTo({ top: Math.max(0, targetTop), behavior: 'auto' })
  if (focus) target.focus({ preventScroll: true })
}

async function scrollToNavigationTarget(
  target: DetailNavigationTarget,
  options: ScrollToSectionOptions = {},
) {
  if (target.subsectionId) {
    await scrollToSubsection(target.subsectionId, options)
    return
  }
  await scrollToSection(target.sectionId, options)
}

function handleHashChange() {
  void syncSectionFromHash()
}

function cancelDeferredSectionNavigation() {
  pendingHashTarget = null
  pendingSupportReanchor = null
  pendingReviewReanchor = null
}

function handleNavigationInterrupt(event: KeyboardEvent | Event) {
  if (event instanceof KeyboardEvent) {
    const scrollingKeys = new Set(['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' '])
    if (!scrollingKeys.has(event.key)) return
  }
  sectionNavigationIntent = null
  cancelDeferredSectionNavigation()
  scheduleScrollSpy()
}

watchEffect(() => {
  document.title = `${pageTitle.value} - 策略详情`
})

onMounted(() => {
  activeRange.value = readContextRange()
  applyDetailViewMode(readContextViewMode())
  compactNavigation.value = window.matchMedia('(max-width: 1040px)').matches
  contextQueryReady = true
  window.addEventListener('hashchange', handleHashChange)
  window.addEventListener('popstate', handleHashChange)
  window.addEventListener('scroll', scheduleScrollSpy, { passive: true })
  window.addEventListener('resize', handleViewportResize)
  window.addEventListener('wheel', handleNavigationInterrupt, { passive: true })
  window.addEventListener('touchstart', handleNavigationInterrupt, { passive: true })
  window.addEventListener('pointerdown', handleNavigationInterrupt, { passive: true })
  window.addEventListener('keydown', handleNavigationInterrupt)
  void syncSectionFromHash()
})

onBeforeUnmount(() => {
  window.removeEventListener('hashchange', handleHashChange)
  window.removeEventListener('popstate', handleHashChange)
  window.removeEventListener('scroll', scheduleScrollSpy)
  window.removeEventListener('resize', handleViewportResize)
  window.removeEventListener('wheel', handleNavigationInterrupt)
  window.removeEventListener('touchstart', handleNavigationInterrupt)
  window.removeEventListener('pointerdown', handleNavigationInterrupt)
  window.removeEventListener('keydown', handleNavigationInterrupt)
  if (scrollSpyFrame !== null) window.cancelAnimationFrame(scrollSpyFrame)
  if (holdingHistorySearchTimer !== null) window.clearTimeout(holdingHistorySearchTimer)
  stickyLayoutResizeObserver?.disconnect()
  stickyLayoutResizeObserver = null
  observedStickyHeader = null
})

watch(
  () => props.name,
  () => {
    sectionNavigationIntent = null
    resetBacktestTracking()
    executionSearch.value = ''
    orderSearch.value = ''
    pendingHashTarget = readDetailHashTarget()
    performanceChartsActivated.value = pendingHashTarget?.sectionId === 'performance'
    initialSectionNavigationComplete = false
    void loadPage()
  },
  { immediate: true },
)

watch(holdingsDates, nextDates => {
  if (!nextDates.length) {
    focusDate.value = ''
    return
  }
  if (focusDate.value && nextDates.includes(focusDate.value)) return
  const requestedFocusDate = readContextFocusDate()
  if (requestedFocusDate && nextDates.includes(requestedFocusDate)) {
    focusDate.value = requestedFocusDate
    return
  }
  focusDate.value = resolveInitialFocusDate(effectiveHoldings.value)
})

watch(visibleNetValueDates, nextDates => {
  if (!nextDates.length) {
    showcaseFocusDate.value = ''
    return
  }
  if (showcaseFocusDate.value && nextDates.includes(showcaseFocusDate.value)) return
  showcaseFocusDate.value = nextDates[nextDates.length - 1]
})

watch(holdingsSlotOptions, () => {
  holdingsSlotFilter.value = normalizeHoldingsSlotFilterValue(holdingsSlotFilter.value, effectiveHoldings.value)
})

watch(holdingsHistorySearchInput, value => {
  if (holdingHistorySearchTimer !== null) window.clearTimeout(holdingHistorySearchTimer)
  holdingHistorySearchTimer = window.setTimeout(() => {
    holdingsHistorySearch.value = value
    holdingHistorySearchTimer = null
  }, 180)
})

watch([activeRange, focusDate, detailViewMode], replaceContextQuery)

watch(
  [activeSection, activeSubsection, compactNavigation, detailViewMode, loading],
  async () => {
    await nextTick()
    keepActiveSidebarLinkVisible()
  },
  { flush: 'post' },
)

watch([activeSection, loading], ([sectionId, isLoading]) => {
  if (sectionId === 'performance') performanceChartsActivated.value = true
  if (isLoading || error.value || !detail.value) return
  void ensureFullDataForSection(sectionId, loadToken)
  if (sectionId === 'review') void loadReviewData(loadToken)
})

watch([loading, error], async ([isLoading, currentError]) => {
  if (isLoading || currentError) return
  await nextTick()
  observeStickyLayout()

  const target = pendingHashTarget
    ?? (!initialSectionNavigationComplete ? readDetailHashTarget() : null)
  pendingHashTarget = null
  initialSectionNavigationComplete = true

  if (target) {
    await scrollToNavigationTarget(target, { updateHash: false, focus: false, intent: 'restore' })
    if (dataLoadInProgress.value) pendingSupportReanchor = target
  }
  scheduleScrollSpy()
}, { flush: 'post' })

watch(dataLoadInProgress, async (isLoading, wasLoading) => {
  if (isLoading || !wasLoading || !pendingSupportReanchor) return
  const target = pendingSupportReanchor
  pendingSupportReanchor = null
  await nextTick()
  await scrollToNavigationTarget(target, { updateHash: false, focus: false, intent: 'restore' })
  scheduleScrollSpy()
})

watch(reviewDataLoading, async (isLoading, wasLoading) => {
  if (isLoading || !wasLoading || !pendingReviewReanchor) return
  const target = pendingReviewReanchor
  pendingReviewReanchor = null
  await nextTick()
  await scrollToNavigationTarget(target, { updateHash: false, focus: false, intent: 'restore' })
  scheduleScrollSpy()
})

async function loadPage() {
  if (initialSectionNavigationComplete) {
    pendingHashTarget = readDetailHashTarget() ?? {
      sectionId: activeSection.value,
      subsectionId: activeSubsection.value ?? undefined,
    }
    initialSectionNavigationComplete = false
  }
  loadToken += 1
  const currentToken = loadToken
  loading.value = true
  error.value = ''
  coreDataWarning.value = ''
  supportingDataError.value = ''
  supportingDataLoading.value = false
  reviewDataError.value = ''
  reviewDataLoading.value = false
  detail.value = null
  trades.value = null
  holdings.value = null
  tradeSummary.value = null
  holdingsSummaryPayload.value = null
  metrics.value = null
  analysis.value = null
  review.value = null
  netValue.value = null
  fullTradesLoaded.value = false
  fullHoldingsLoaded.value = false
  fullTradesLoading.value = false
  fullHoldingsLoading.value = false
  fullTradesError.value = ''
  fullHoldingsError.value = ''
  fullTradesRequest = null
  fullHoldingsRequest = null
  showcaseFocusDate.value = ''

  const detailResult = await Promise.allSettled([
    fetchLibraryDetail(props.name),
  ]).then(results => results[0])

  if (currentToken !== loadToken) return

  if (detailResult.status === 'fulfilled') {
    detail.value = detailResult.value
  }

  if (!detail.value) {
    error.value = '未找到该策略的缓存详情，请先确认策略名称有效并已完成回测。'
  }

  loading.value = false
  if (!detail.value) return

  void loadCoreData(currentToken)
  void loadSupportingSummary(currentToken)
}

async function loadCoreData(currentToken: number) {
  const [netValueResult, metricsResult, analysisResult] = await Promise.allSettled([
    fetchLibraryNetValue(props.name),
    fetchLibraryMetrics(props.name),
    fetchLibraryAnalysis(props.name),
  ])

  if (currentToken !== loadToken) return

  if (netValueResult.status === 'fulfilled') netValue.value = netValueResult.value
  if (metricsResult.status === 'fulfilled') metrics.value = metricsResult.value
  if (analysisResult.status === 'fulfilled') analysis.value = analysisResult.value

  const failedCoreSources = [
    netValueResult.status === 'rejected' ? '净值' : '',
    metricsResult.status === 'rejected' ? '指标' : '',
    analysisResult.status === 'rejected' ? '研究分析' : '',
  ].filter(Boolean)
  if (failedCoreSources.length) {
    coreDataWarning.value = `部分核心数据加载失败：${failedCoreSources.join('、')}`
  }
}

function projectTradeSummary(summary: TradeSummaryPayload): TradesPayload {
  const executionCount = summary.summary.execution_record_count
  const orderCount = summary.summary.order_record_count
  return {
    columns: summary.columns,
    records: summary.records_preview,
    execution_columns: summary.execution_columns,
    execution_records: summary.execution_records_preview,
    execution_meta: {
      ...(summary.execution_meta || {}),
      record_count: executionCount,
    },
    order_columns: summary.order_columns,
    order_records: summary.order_records_preview,
    order_meta: {
      ...(summary.order_meta || {}),
      record_count: orderCount,
    },
  }
}

function projectHoldingsSummary(summary: DailyHoldingsSummaryPayload): DailyHoldingsPayload {
  const latestDate = summary.latest_date || summary.dates[summary.dates.length - 1] || ''
  return {
    dates: summary.dates,
    holdings_by_date: latestDate
      ? { [latestDate]: summary.latest_holdings_preview }
      : {},
    summary_by_date: summary.summary_by_date,
  }
}

async function loadSupportingSummary(currentToken: number) {
  supportingDataLoading.value = true
  supportingDataError.value = ''

  const [tradeSummaryResult, holdingsSummaryResult] = await Promise.allSettled([
    fetchLibraryTradeSummary(props.name),
    fetchLibraryHoldingsSummary(props.name),
  ])

  if (currentToken !== loadToken) return

  if (tradeSummaryResult.status === 'fulfilled') {
    tradeSummary.value = tradeSummaryResult.value
    trades.value = projectTradeSummary(tradeSummaryResult.value)
  }
  if (holdingsSummaryResult.status === 'fulfilled') {
    holdingsSummaryPayload.value = holdingsSummaryResult.value
    holdings.value = projectHoldingsSummary(holdingsSummaryResult.value)
  }

  const failedSources = [
    tradeSummaryResult.status === 'rejected' ? '交易摘要' : '',
    holdingsSummaryResult.status === 'rejected' ? '持仓摘要' : '',
  ].filter(Boolean)
  if (failedSources.length) {
    supportingDataError.value = `部分摘要数据加载失败：${failedSources.join('、')}`
  }

  const requestedFocusDate = readContextFocusDate()
  focusDate.value = requestedFocusDate && holdingsDates.value.includes(requestedFocusDate)
    ? requestedFocusDate
    : resolveInitialFocusDate(effectiveHoldings.value)
  supportingDataLoading.value = false
}

async function loadFullTrades(currentToken: number): Promise<void> {
  if (fullTradesLoaded.value || fullTradesRequest) return fullTradesRequest || Promise.resolve()
  fullTradesLoading.value = true
  fullTradesError.value = ''
  let request!: Promise<void>
  request = (async () => {
    try {
      const result = await fetchLibraryTradeRecords(props.name)
      if (currentToken !== loadToken) return
      trades.value = result
      fullTradesLoaded.value = true
    } catch {
      if (currentToken === loadToken) fullTradesError.value = '完整交易账单加载失败，请稍后重试。'
    } finally {
      if (fullTradesRequest !== request) return
      fullTradesRequest = null
      if (currentToken === loadToken) fullTradesLoading.value = false
    }
  })()
  fullTradesRequest = request
  return request
}

async function loadFullHoldings(currentToken: number): Promise<void> {
  if (fullHoldingsLoaded.value || fullHoldingsRequest) return fullHoldingsRequest || Promise.resolve()
  fullHoldingsLoading.value = true
  fullHoldingsError.value = ''
  let request!: Promise<void>
  request = (async () => {
    try {
      const result = await fetchLibraryHoldingsHistory(props.name)
      if (currentToken !== loadToken) return
      holdings.value = result
      fullHoldingsLoaded.value = true
    } catch {
      if (currentToken === loadToken) fullHoldingsError.value = '完整持仓历史加载失败，请稍后重试。'
    } finally {
      if (fullHoldingsRequest !== request) return
      fullHoldingsRequest = null
      if (currentToken === loadToken) fullHoldingsLoading.value = false
    }
  })()
  fullHoldingsRequest = request
  return request
}

async function ensureFullDataForSection(sectionId: DetailSectionId, currentToken = loadToken) {
  if (currentToken !== loadToken) return
  if (sectionId === 'holdings') {
    await Promise.all([loadFullHoldings(currentToken), loadFullTrades(currentToken)])
  } else if (sectionId === 'details' || sectionId === 'performance') {
    await loadFullTrades(currentToken)
  }
}

async function loadSupportingData(currentToken: number) {
  await loadSupportingSummary(currentToken)
  await ensureFullDataForSection(activeSection.value, currentToken)
}

async function loadReviewData(currentToken: number) {
  if (reviewDataLoading.value || review.value) return
  reviewDataLoading.value = true
  reviewDataError.value = ''

  try {
    const result = await fetchLibraryReview(props.name)
    if (currentToken !== loadToken) return
    review.value = result
  } catch {
    if (currentToken !== loadToken) return
    reviewDataError.value = '持仓交易复盘加载失败，请稍后重试。'
  } finally {
    if (currentToken === loadToken) reviewDataLoading.value = false
  }
}

function shiftFocus(step: number) {
  const currentIndex = focusDateIndex.value
  if (currentIndex < 0) return
  const nextIndex = currentIndex + step
  if (nextIndex < 0 || nextIndex >= holdingsDates.value.length) return
  focusDate.value = holdingsDates.value[nextIndex]
}

function statusClass(status: string): string {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'completed' || normalized === 'cached') return 'status-completed'
  if (normalized === 'failed' || normalized === 'config_failed') return 'status-failed'
  if (normalized === 'no_signal') return 'status-no-signal'
  return 'status-idle'
}

function mergeDetailEntry(payload: LibraryDetailResponse | null, fallbackName: string): DetailEntryView {
  const data = payload && typeof payload === 'object' ? payload : {}
  const catalogEntry = isRecord(data.catalog_entry) ? data.catalog_entry : {}
  const result = isRecord(data.result) ? data.result : {}
  const extra = isRecord(data.extra) ? data.extra : {}
  return {
    name: readString(data.name) || readString(catalogEntry.name) || fallbackName,
    title: readString(catalogEntry.title) || readString(data.title) || fallbackName,
    status: readString(data.status) || readString(extra.status) || readString(catalogEntry.status) || 'idle',
    status_label: readString(data.status_label) || readString(catalogEntry.status_label) || '策略详情',
    timestamp: readString(data.timestamp) || readString(catalogEntry.timestamp) || null,
    cum_return: readNumber(data.cum_return) ?? readNumber(catalogEntry.cum_return) ?? readNumber(result.cum_return),
    max_drawdown: readNumber(data.max_drawdown) ?? readNumber(catalogEntry.max_drawdown) ?? readNumber(result.max_drawdown),
    win_rate: readNumber(data.win_rate) ?? readNumber(catalogEntry.win_rate) ?? readNumber(result.actual_segment_win_rate),
    trade_count: readNumber(data.trade_count) ?? readNumber(catalogEntry.trade_count) ?? readNumber(result.actual_segment_trade_count),
    final_net_value: readNumber(data.final_net_value) ?? readNumber(catalogEntry.final_net_value) ?? readNumber(result.final_net_value),
  }
}

function normalizeKey(value: unknown): string {
  return String(value || '').trim().toLowerCase().replace(/[\s_-]+/g, '')
}

function findColumn(columns: string[], matchers: Array<(normalized: string, raw: string) => boolean>): string | null {
  for (const column of columns) {
    const raw = String(column || '')
    const normalized = normalizeKey(raw)
    if (matchers.some(matcher => matcher(normalized, raw))) {
      return raw
    }
  }
  return null
}

function resolveTradeColumns(records: TableRow[], columns: string[]): ResolvedTradeColumns {
  const tradeColumns = columns.length ? columns : (records[0] ? Object.keys(records[0]) : [])
  return {
    code: findColumn(tradeColumns, [
      key => ['stockcode', 'securitycode', 'code', 'tscode', 'symbol', 'ticker'].includes(key),
      (_, raw) => raw.includes('股票代码') || raw.includes('证券代码'),
    ]),
    name: findColumn(tradeColumns, [
      key => ['stockname', 'securityname', 'name'].includes(key),
      (_, raw) => raw.includes('股票名称') || raw === '名称',
    ]),
    buyDate: findColumn(tradeColumns, [
      key => key === 'buydate' || key === 'entrydate' || key === 'buyday' || key === 'entryday' || key.includes('buy'),
      (_, raw) => raw.includes('买入') || raw.includes('入场'),
    ]),
    sellDate: findColumn(tradeColumns, [
      key => key === 'selldate' || key === 'exitdate' || key === 'sellday' || key === 'exitday' || key.includes('sell') || key.includes('exit'),
      (_, raw) => raw.includes('卖出') || raw.includes('出场'),
    ]),
  }
}

function signalDateKey(value: unknown): string {
  const text = String(value || '').trim()
  if (!text || text === '-' || text.toLowerCase() === 'nan' || text.toLowerCase() === 'none') {
    return ''
  }
  const digits = text.replace(/\D/g, '')
  if (digits.length >= 8) {
    return digits.slice(0, 8)
  }
  return text
}

function dateText(value: string): string {
  return /^\d{8}$/.test(value)
    ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`
    : value
}

function collectLatestSignal(
  records: TableRow[],
  dateColumn: string,
  codeColumn: string,
  nameColumn: string | null,
): LatestSignalSummary {
  let latestKey = ''
  for (const row of records) {
    const dateKey = signalDateKey(row?.[dateColumn])
    if (dateKey && dateKey > latestKey) {
      latestKey = dateKey
    }
  }

  if (!latestKey) {
    return {
      date: '',
      items: [],
    }
  }

  const itemsByCode = new Map<string, LatestSignalItem>()
  for (const row of records) {
    if (signalDateKey(row?.[dateColumn]) !== latestKey) continue
    const code = String(row?.[codeColumn] || '').trim()
    if (!code) continue
    const name = nameColumn ? String(row?.[nameColumn] || '').trim() : ''
    itemsByCode.set(code, { code, name })
  }

  return {
    date: dateText(latestKey),
    items: Array.from(itemsByCode.values()).sort((left, right) => left.code.localeCompare(right.code, 'zh-CN')),
  }
}

function formatSignalItem(item: LatestSignalItem): string {
  return item.name ? `${item.code} ${item.name}` : item.code
}

function resolveInitialFocusDate(payload: DailyHoldingsPayload | null): string {
  const dates = payload?.dates ?? []
  if (!dates.length) return ''
  for (let index = dates.length - 1; index >= 0; index -= 1) {
    const dateKey = dates[index]
    const rows = getDailyHoldingsRowsForDate(payload, dateKey)
    if (rows.length) return dateKey
  }
  return dates[dates.length - 1]
}

function holdingsCurrentViewLabel(value: HoldingsCurrentView): string {
  if (value === 'stock') return '按股票汇总'
  if (value === 'slot') return '按槽位汇总'
  return '原始明细'
}

function buildDetailHoldingDisplayRow(row: DailyHoldingRow, dateKey: string): TableRow {
  const code = String(row.code || '').trim()
  const name = String(row.name || '').trim()
  const scaled = holdingSharesScaled(row)
  return {
    date: dateKey,
    slot: row.slot ? String(row.slot) : '-',
    stock: buildHoldingStockText(code, name, row.unrealized_pnl),
    direction: holdingDirectionLabel(row.dir),
    shares: formatHoldingShares(row.shares, scaled),
    close: formatCurrencyUnit(row.close, 2),
    cost_price: formatCurrencyUnit(row.cost_price, 4),
    original_shares: formatInteger(row.original_shares),
    cost_value: formatMoney(row.cost_value),
    holding_value: formatMoney(row.holding_value),
    unrealized_pnl: formatSignedMoney(row.unrealized_pnl),
    unrealized_pnl_pct: formatPercent(row.unrealized_pnl_pct),
    nv: formatPlainNumber(row.nv, 4),
    position_ratio: formatHoldingRatio(row.effective_position_ratio ?? row.utilization_rate),
  }
}

function buildStockHoldingDisplayRow(row: StockAggregatedHoldingRow, simple: boolean): TableRow {
  const scaled = holdingSharesScaled({
    shares_scaled: row.shares_scaled,
    shares: row.shares,
    original_shares: row.original_shares,
  })
  return {
    stock: buildHoldingStockText(row.code, row.name, row.unrealized_pnl),
    slots_text: String(row.slots_text || '-'),
    close: formatCurrencyUnit(row.close, 2),
    cost_price: formatCurrencyUnit(row.cost_price, 4),
    shares: formatHoldingShares(row.shares, scaled),
    original_shares: formatInteger(row.original_shares),
    cost_value: formatMoney(row.cost_value),
    holding_value: formatMoney(row.holding_value),
    unrealized_pnl: formatSignedMoney(row.unrealized_pnl),
    unrealized_pnl_pct: formatPercent(row.unrealized_pnl_pct),
    position_ratio: simple ? undefined : formatHoldingRatio(row.position_ratio),
  }
}

function buildSlotHoldingDisplayRow(row: SlotAggregatedHoldingRow): TableRow {
  const scaled = holdingSharesScaled({
    shares_scaled: row.shares_scaled,
    shares: row.shares,
    original_shares: row.original_shares,
  })
  return {
    slot: String(row.slot || '-'),
    stock_count: formatInteger(row.stock_count),
    stocks_text: buildSlotStockListText(row.stock_items, row.stocks_text),
    shares: formatHoldingShares(row.shares, scaled),
    original_shares: formatInteger(row.original_shares),
    cost_value: formatMoney(row.cost_value),
    holding_value: formatMoney(row.holding_value),
    unrealized_pnl: formatSignedMoney(row.unrealized_pnl),
    unrealized_pnl_pct: formatPercent(row.unrealized_pnl_pct),
    nv: formatPlainNumber(row.nv, 4),
    position_ratio: formatHoldingRatio(row.position_ratio),
  }
}

function buildCurrentHoldingDisplayRows(
  rows: Array<DailyHoldingRow | StockAggregatedHoldingRow | SlotAggregatedHoldingRow>,
  view: HoldingsCurrentView,
  mode: HoldingsDisplayMode,
  dateKey: string,
): TableRow[] {
  if (view === 'stock') {
    return rows.map(row => buildStockHoldingDisplayRow(row as StockAggregatedHoldingRow, mode === 'simple'))
  }
  if (view === 'slot') {
    return rows.map(row => buildSlotHoldingDisplayRow(row as SlotAggregatedHoldingRow))
  }
  return rows.map(row => buildDetailHoldingDisplayRow(row as DailyHoldingRow, dateKey))
}

function normalizeHoldingsTradeDirection(direction: unknown, source: unknown): '' | 'buy' | 'sell' {
  const rawDirection = String(direction || '').trim()
  const rawSource = String(source || '').trim()
  if (rawDirection === '买入' || rawDirection === '加仓' || rawDirection === '恢复') return 'buy'
  if (rawDirection === '卖出' || rawDirection === '减仓') return 'sell'
  if (rawSource === '风控') {
    const lowered = rawDirection.toLowerCase()
    if (lowered === 'buy') return 'buy'
    if (lowered === 'sell') return 'sell'
  }
  return ''
}

function getExecutionTaxCost(row: TableRow): number | null {
  return readNumber(row['手续费+印花税']) ?? readNumber(row['总成本'])
}

function buildDailyTradeSummary(rows: TableRow[]) {
  let buyCount = 0
  let sellCount = 0
  let riskCount = 0
  let totalCost = 0
  let netCashDelta = 0
  for (const row of rows) {
    const direction = String(row['方向'] || '').trim()
    const source = String(row['来源'] || '').trim()
    const normalizedDirection = normalizeHoldingsTradeDirection(direction, source)
    const totalCostValue = getExecutionTaxCost(row)
    const cashDeltaValue = readNumber(row['资金变动'])
    if (normalizedDirection === 'buy') buyCount += 1
    if (normalizedDirection === 'sell') sellCount += 1
    if (source === '风控' || direction === '减仓' || direction === '加仓' || direction === '恢复') riskCount += 1
    if (totalCostValue != null) totalCost += totalCostValue
    if (cashDeltaValue != null) netCashDelta += cashDeltaValue
  }
  return {
    recordCount: rows.length,
    buyCount,
    sellCount,
    riskCount,
    totalCost: Number(totalCost.toFixed(2)),
    netCashDelta: Number(netCashDelta.toFixed(2)),
  }
}

function buildFocusedHoldingsExportRows(dateKey: string): Array<Record<string, string | number | null>> {
  const safeView = effectiveHoldingsView.value
  if (safeView === 'stock') {
    return (currentHoldingViewRows.value as StockAggregatedHoldingRow[]).map(row => ({
      日期: dateKey,
      视图: holdingsCurrentViewLabel(safeView),
      股票代码: String(row.code || ''),
      股票名称: String(row.name || ''),
      涉及组合: String(row.slots_text || ''),
      收盘价: row.close ?? null,
      成本价: row.cost_price ?? null,
      有效股数: row.shares ?? null,
      原始股数: row.original_shares ?? null,
      股数说明: holdingSharesScaled({
        shares_scaled: row.shares_scaled,
        shares: row.shares,
        original_shares: row.original_shares,
      }) ? '风控缩放' : '',
      成本市值: row.cost_value ?? null,
      持仓市值: row.holding_value ?? null,
      浮动盈亏: row.unrealized_pnl ?? null,
      浮动盈亏比: row.unrealized_pnl_pct ?? null,
      合计仓位: row.position_ratio ?? null,
    }))
  }
  if (safeView === 'slot') {
    return (currentHoldingViewRows.value as SlotAggregatedHoldingRow[]).map(row => ({
      日期: dateKey,
      视图: holdingsCurrentViewLabel(safeView),
      槽位: String(row.slot || ''),
      持仓只数: row.stock_count ?? null,
      股票列表: buildSlotStockListText(row.stock_items, row.stocks_text),
      有效股数: row.shares ?? null,
      原始股数: row.original_shares ?? null,
      股数说明: holdingSharesScaled({
        shares_scaled: row.shares_scaled,
        shares: row.shares,
        original_shares: row.original_shares,
      }) ? '风控缩放' : '',
      成本市值: row.cost_value ?? null,
      持仓市值: row.holding_value ?? null,
      浮动盈亏: row.unrealized_pnl ?? null,
      浮动盈亏比: row.unrealized_pnl_pct ?? null,
      净值: row.nv ?? null,
      合计仓位: row.position_ratio ?? null,
    }))
  }
  return (currentHoldingViewRows.value as DailyHoldingRow[]).map(row => ({
    日期: dateKey,
    视图: holdingsCurrentViewLabel(safeView),
    槽位: String(row.slot || ''),
    股票代码: String(row.code || ''),
    股票名称: String(row.name || ''),
    方向: holdingDirectionLabel(row.dir),
    收盘价: row.close ?? null,
    成本价: row.cost_price ?? null,
    有效股数: row.shares ?? null,
    原始股数: row.original_shares ?? null,
    股数说明: holdingSharesScaled(row) ? '风控缩放' : '',
    成本市值: row.cost_value ?? null,
    持仓市值: row.holding_value ?? null,
    浮动盈亏: row.unrealized_pnl ?? null,
    浮动盈亏比: row.unrealized_pnl_pct ?? null,
    净值: row.nv ?? null,
    仓位: row.effective_position_ratio ?? row.utilization_rate ?? null,
  }))
}

function csvEscapeCell(value: unknown): string {
  const text = String(value ?? '')
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

const HTML_ESCAPE_MAP: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

function htmlEscapeCell(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, char => HTML_ESCAPE_MAP[char] || char)
}

function downloadTextFile(filename: string, content: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => window.URL.revokeObjectURL(url), 0)
}

function safeDownloadName(value: string): string {
  return (value || 'strategy').replace(/[<>:"/\\|?*\x00-\x1F]+/g, '_')
}

function filterLedgerRows(rows: TableRow[], queryValue: string): TableRow[] {
  const query = String(queryValue || '').trim().toLowerCase()
  if (!query) return rows
  return rows.filter(row => Object.values(row).some(value => String(value ?? '').toLowerCase().includes(query)))
}

function ledgerRowValue(row: TableRow, keys: string[]): unknown {
  for (const key of keys) {
    const value = row[key]
    if (value !== null && value !== undefined && String(value).trim()) return value
  }
  return null
}

function ledgerRowDate(row: TableRow): string {
  return normalizeDateKey(ledgerRowValue(row, ['日期', '成交日期', 'trade_date', 'date', 'timestamp', 'datetime']))
}

function ledgerRowDirection(row: TableRow): 'buy' | 'sell' | 'other' {
  const value = String(ledgerRowValue(row, ['方向', '成交方向', 'direction', 'side', 'action']) ?? '').trim().toLowerCase()
  if (value.includes('买') || value === 'buy') return 'buy'
  if (value.includes('卖') || value === 'sell') return 'sell'
  return 'other'
}

function ledgerPanelSummary(label: string, visible: number, total: number): string {
  return visible === total ? `${label}：${total} 条` : `${label}：${visible}/${total} 条`
}

function buildTableCsv(rows: ReadonlyArray<Record<string, unknown>>, columnKeys?: string[]): string {
  if (!rows.length) return ''
  const columns = columnKeys?.length ? columnKeys : Object.keys(rows[0])
  return `\uFEFF${[
    columns.map(csvEscapeCell).join(','),
    ...rows.map(row => columns.map(column => csvEscapeCell(row[column])).join(',')),
  ].join('\r\n')}`
}

function buildHoldingsCsv(rows: Array<Record<string, string | number | null>>): string {
  return buildTableCsv(rows)
}

function buildHoldingsExcelHtml(rows: Array<Record<string, string | number | null>>): string {
  if (!rows.length) return ''
  const columns = Object.keys(rows[0])
  return `
    <html xmlns:o="urn:schemas-microsoft-com:office:office"
          xmlns:x="urn:schemas-microsoft-com:office:excel"
          xmlns="http://www.w3.org/TR/REC-html40">
      <head>
        <meta charset="UTF-8">
      </head>
      <body>
        <table>
          <thead>
            <tr>${columns.map(column => `<th>${htmlEscapeCell(column)}</th>`).join('')}</tr>
          </thead>
          <tbody>
            ${rows.map(row => `
              <tr>${columns.map(column => `<td>${htmlEscapeCell(row[column])}</td>`).join('')}</tr>
            `).join('')}
          </tbody>
        </table>
      </body>
    </html>`.trim()
}

async function exportFocusedHoldings(format: 'csv' | 'xls') {
  await ensureFullDataForSection('holdings', loadToken)
  const dateKey = String(focusDate.value || '').trim()
  const rows = buildFocusedHoldingsExportRows(dateKey)
  if (!dateKey || !rows.length) return
  const strategyName = safeDownloadName(props.name || 'strategy')
  const viewSuffix = effectiveHoldingsView.value
  if (format === 'xls') {
    downloadTextFile(
      `${strategyName}_daily_holdings_${dateKey}_${viewSuffix}.xls`,
      buildHoldingsExcelHtml(rows),
      'application/vnd.ms-excel;charset=utf-8;',
    )
    return
  }
  downloadTextFile(
    `${strategyName}_daily_holdings_${dateKey}_${viewSuffix}.csv`,
    buildHoldingsCsv(rows),
    'text/csv;charset=utf-8',
  )
}

async function exportExecutionRecordsCsv() {
  await ensureFullDataForSection('details', loadToken)
  const rows = filteredExecutionRows.value
  if (!rows.length) return
  const strategyName = safeDownloadName(props.name || 'strategy')
  const columns = executionColumns.value.map(column => column.key)
  downloadTextFile(
    `${strategyName}_executions.csv`,
    buildTableCsv(rows, columns),
    'text/csv;charset=utf-8',
  )
}

async function exportOrderRecordsCsv() {
  await ensureFullDataForSection('details', loadToken)
  const rows = filteredOrderRows.value
  if (!rows.length) return
  const strategyName = safeDownloadName(props.name || 'strategy')
  const columns = orderColumns.value.map(column => column.key)
  downloadTextFile(
    `${strategyName}_orders.csv`,
    buildTableCsv(rows, columns),
    'text/csv;charset=utf-8',
  )
}

function resetHoldingsHistoryFilters() {
  holdingsHistorySearchInput.value = ''
  holdingsHistorySearch.value = ''
  holdingsSlotFilter.value = 'all'
  holdingsDirectionFilter.value = 'all'
}

function holdingHistoryRowKey(row: TableRow, index: number): string {
  return [row.date, row.slot, row.stock, row.direction, index].map(value => String(value ?? '')).join('-')
}

function buildReturnMetricNote(): string {
  const monthlyTrend = metrics.value?.monthly_trend
  if (Array.isArray(monthlyTrend) && monthlyTrend.length) {
    const latest = monthlyTrend[monthlyTrend.length - 1]
    const period = String(latest?.period || '').trim()
    return `${period || '最近月'} ${formatPercent(latest?.return)}`
  }
  return `累计 ${formatPercent(fullMetricSnapshot.value.cum_return)}`
}

function buildDrawdownMetricNote(): string {
  const endDate = readString(metrics.value?.max_drawdown_end_date)
  const recoveryDays = readNumber(metrics.value?.max_dd_recovery_days)
  const recoveryText = recoveryDays == null ? '未恢复' : `${formatInteger(recoveryDays)} 天恢复`
  return endDate ? `${endDate} · ${recoveryText}` : recoveryText
}

function buildSharpeMetricNote(value: unknown = fullMetricSnapshot.value.sharpe): string {
  const sharpe = readNumber(value)
  if (sharpe == null) return '等待净值波动率样本'
  if (sharpe >= 2) return '评级：卓越'
  if (sharpe >= 1.5) return '评级：优秀'
  if (sharpe >= 1) return '评级：良好'
  if (sharpe >= 0.5) return '评级：一般'
  return '评级：偏弱'
}

function buildWinRateMetricNote(): string {
  const winRate = readNumber(metrics.value?.win_rate) ?? entry.value.win_rate
  const totalTrades = readNumber(metrics.value?.total_trades) ?? entry.value.trade_count
  if (winRate == null || totalTrades == null) return '等待交易样本'
  const wins = Math.round(winRate * totalTrades)
  return `${wins}/${Math.round(totalTrades)} 笔盈利`
}

const detailPageContext = reactive({
  analysis,
  review,
  reviewDataLoading,
  reviewDataError,
  supportingDataLoading,
  fullTradesLoading,
  fullHoldingsLoading,
  fullTradesLoaded,
  fullHoldingsLoaded,
  fullDataError,
  investorConditionSections,
  investorIndicators,
  researchScore,
  researchScoreClass,
  researchFactCards,
  displayResearchDiagnostics,
  researchWarnings,
  marketStateBars,
  overviewExecutionState,
  overviewExecutionFacts,
  latestSignalData,
  latestSignalCards,
  overviewExposureRows,
  latestHoldingDate,
  activeRange,
  activeRangeOption,
  visibleDateRangeText,
  visibleNetValue,
  performanceIntervalMetrics,
  performanceMetricCells,
  isResearchMode,
  expandedPanels,
  scoreDetailsSummary,
  scoreComponentRows,
  scoreComponentColumns: SCORE_COMPONENT_COLUMNS,
  returnHeatmapSummary,
  marketStateSummary,
  marketStateRows,
  marketStateColumns: MARKET_STATE_COLUMNS,
  yearlyPerformanceSummary,
  researchYearlyRows,
  researchYearlyColumns: RESEARCH_YEARLY_COLUMNS,
  visibleTradeRecordsForDistribution,
  performanceChartsActivated,
  holdingsStatusText,
  holdingsDisplayMode,
  holdingsCurrentView,
  effectiveHoldingsView,
  holdingsViewOptions,
  showHoldingsViewToggle,
  showHoldingsHistory,
  showHoldingsExport,
  focusDate,
  holdingsDates,
  canShiftBackward,
  canShiftForward,
  holdingsSummaryCards,
  holdingsCurrentNote,
  canExportFocusedHoldings,
  currentHoldingColumns,
  currentHoldingDisplayRows,
  dailyTradeCards,
  dailyTradeColumns,
  dailyTradeRows,
  holdingsHistoryNote,
  holdingsHistorySearchInput,
  holdingsSlotFilter,
  holdingsSlotOptions,
  holdingsDirectionFilter,
  holdingHistoryDisplayRows,
  holdingHistoryEmptyText,
  holdingHistoryPanelSummary,
  holdingsHeatmapSummary,
  flattenedHoldings,
  holdingHistoryColumns: HOLDING_HISTORY_COLUMNS,
  reviewMetricCards,
  reviewStockRows,
  reviewStocksSummary,
  reviewStockColumns: REVIEW_STOCK_COLUMNS,
  ledgerDateRangeText,
  ledgerSummaryCards,
  executionPreviewSummary,
  executionPanelSummary,
  executionColumns,
  latestExecutionRows,
  executionRows,
  filteredExecutionRows,
  executionSearch,
  canExportExecutionCsv,
  orderPanelSummary,
  orderColumns,
  orderRows,
  filteredOrderRows,
  orderSearch,
  canExportOrderCsv,
  ledgerPageSize: LEDGER_PAGE_SIZE,
  getLoadToken: () => loadToken,
  setPanelExpanded: (panelId: string, expanded: boolean) => {
    setPanelExpanded(panelId as DetailPanelId, expanded)
  },
  handleHoldingHistoryPanelToggle,
  shiftFocus,
  normalizeHoldingsCurrentView,
  scrollToSection,
  openAllExecutionRecords,
  setDetailViewMode,
  loadReviewData,
  ensureFullDataForSection: (sectionId: string) => ensureFullDataForSection(sectionId as DetailSectionId, loadToken),
  exportFocusedHoldings,
  exportExecutionRecordsCsv,
  exportOrderRecordsCsv,
  resetHoldingsHistoryFilters,
  holdingHistoryRowKey,
  formatSignalItem,
  formatPlainNumber,
  formatPercent,
  metricTrendClass,
})

provide(
  DETAIL_PAGE_CONTEXT_KEY,
  detailPageContext as unknown as DetailPageContext,
)

</script>

<template>
  <section
    class="detail-layout research-detail-page strategy-demo-page result-detail strategy-workbench-v2"
    :class="{
      'has-backtest-note': Boolean(backtestActionMessage),
      'is-research-mode': isResearchMode,
    }"
  >
    <section v-if="loading" class="strategy-detail-state" aria-live="polite" aria-busy="true">
      <div class="strategy-loading-mark" aria-hidden="true"></div>
      <div>
        <strong>正在载入回测结果</strong>
        <span>正在汇总净值、指标与研究数据...</span>
      </div>
      <div class="strategy-loading-grid" aria-hidden="true">
        <i v-for="index in 6" :key="index"></i>
      </div>
    </section>

    <section v-else-if="error" class="strategy-detail-state strategy-detail-error" role="alert">
      <strong>策略详情加载失败</strong>
      <span>{{ error }}</span>
      <div class="strategy-state-actions">
        <a class="secondary-btn" href="/"><ArrowLeft :size="16" aria-hidden="true" />返回策略库</a>
        <button class="primary-btn" type="button" @click="loadPage"><RefreshCw :size="16" aria-hidden="true" />重新加载</button>
      </div>
    </section>

    <template v-else>
      <header class="research-topbar result-detail-header">
        <div class="detail-breadcrumb" aria-label="面包屑">
          <a class="breadcrumb-home" href="/">
            <Zap class="breadcrumb-brand-icon" :size="16" fill="currentColor" aria-hidden="true" />
            <span class="breadcrumb-label-desktop">策略库</span>
            <span class="breadcrumb-label-mobile">急速回测</span>
          </a>
          <span class="breadcrumb-separator">/</span>
          <strong>{{ pageTitle }}</strong>
        </div>

        <div class="research-topbar-actions">
          <a class="strategy-switcher" href="/" title="返回策略库切换策略" aria-label="切换策略">
            <Layers :size="16" aria-hidden="true" />
            <span>切换策略</span>
            <ChevronRight :size="16" aria-hidden="true" />
          </a>
          <div class="detail-view-mode-control topbar-view-mode-control" role="group" aria-label="详情视图模式">
            <button
              v-for="option in detailViewModeOptions"
              :key="option.value"
              class="detail-view-mode-button"
              type="button"
              :class="{ active: detailViewMode === option.value }"
              :aria-pressed="detailViewMode === option.value"
              :aria-label="`${option.label}模式`"
              :title="`${option.label}模式`"
              @click="setDetailViewMode(option.value)"
            >
              <Activity v-if="option.value === 'simple'" :size="14" aria-hidden="true" />
              <TableProperties v-else :size="14" aria-hidden="true" />
              {{ option.label }}
            </button>
          </div>
          <label class="research-date-range" title="选择收益观察区间">
            <CalendarDays :size="16" aria-hidden="true" />
            <span class="research-date-range-copy">
              <strong>收益观察区间 · {{ activeRangeOption.label }}</strong>
              <small>{{ visibleDateRangeText }}</small>
            </span>
            <select
              :value="activeRange"
              aria-label="收益观察区间"
              @change="setActiveRange(($event.target as HTMLSelectElement).value as DetailRangeId)"
            >
              <option v-for="option in DETAIL_RANGE_OPTIONS" :key="option.value" :value="option.value">
                {{ option.label }}
              </option>
            </select>
          </label>
          <button class="secondary-btn icon-btn command-icon command-refresh" type="button" title="刷新数据" aria-label="刷新数据" @click="loadPage">
            <RefreshCw :size="18" aria-hidden="true" />
          </button>
          <a class="secondary-btn icon-btn command-icon command-download" :href="strategyReportUrl" target="_blank" rel="noreferrer" title="查看报告（新窗口）" aria-label="查看报告（新窗口）">
            <ReceiptText :size="18" aria-hidden="true" />
          </a>
          <button class="primary-btn rerun-command" type="button" title="按策略默认完整区间重新回测" aria-label="按策略默认完整区间重新回测" :disabled="backtestActionPending" @click="rerunCurrentStrategy">
            <RefreshCw v-if="backtestActionPending" :size="16" class="is-spinning" aria-hidden="true" />
            <Play v-else :size="16" fill="currentColor" aria-hidden="true" />
            {{ rerunButtonText }}
          </button>
        </div>
        <p v-if="backtestActionMessage" class="backtest-action-note" :class="backtestActionTone" aria-live="polite">{{ backtestActionMessage }}</p>
      </header>

      <aside class="workbench-sidebar" aria-label="策略详情导航">
        <a class="workbench-brand" href="/" aria-label="返回急速回测策略库">
          <span><Zap :size="20" fill="currentColor" aria-hidden="true" /></span>
          <strong>急速回测</strong>
        </a>

        <a class="workbench-library-link" href="/">
          <ArrowLeft :size="18" aria-hidden="true" />
          <span>返回策略库</span>
        </a>

        <div class="workbench-nav-intro">
          <span>页面目录</span>
          <strong :title="pageTitle">{{ pageTitle }}</strong>
        </div>
        <nav class="research-main-tabs" aria-label="详情分区">
          <div
            v-for="section in DETAIL_SECTIONS"
            :key="section.id"
            class="research-nav-group"
          >
            <span v-if="section.groupLabel" class="research-nav-divider">{{ section.groupLabel }}</span>
            <a
              class="research-main-tab"
              :href="`#${section.id}`"
              :class="{ active: activeSection === section.id }"
              :aria-current="activeSection === section.id && (!activeSubsection || compactNavigation)
                ? 'location'
                : undefined"
              :aria-expanded="isResearchMode
                && !compactNavigation
                && detailSubsectionsForSection(section.id).length
                ? activeSection === section.id
                : undefined"
              :aria-controls="isResearchMode
                && !compactNavigation
                && detailSubsectionsForSection(section.id).length
                ? `detail-subnav-${section.id}`
                : undefined"
              @click.prevent="scrollToSection(section.id)"
            >
              <Activity v-if="section.id === 'overview'" :size="18" aria-hidden="true" />
              <ListChecks v-else-if="section.id === 'rules'" :size="18" aria-hidden="true" />
              <ChartNoAxesCombined v-else-if="section.id === 'performance'" :size="18" aria-hidden="true" />
              <WalletCards v-else-if="section.id === 'holdings'" :size="18" aria-hidden="true" />
              <ListChecks v-else-if="section.id === 'review'" :size="18" aria-hidden="true" />
              <ReceiptText v-else :size="18" aria-hidden="true" />
              <span>
                <strong>{{ section.label }}</strong>
                <small>{{ section.note }}</small>
              </span>
            </a>
            <nav
              v-if="isResearchMode
                && !compactNavigation
                && detailSubsectionsForSection(section.id).length"
              v-show="activeSection === section.id"
              :id="`detail-subnav-${section.id}`"
              class="research-subnav"
              :aria-label="`${section.label}二级导航`"
            >
              <a
                v-for="subsection in detailSubsectionsForSection(section.id)"
                :key="subsection.id"
                class="research-subnav-link"
                :class="{ active: activeSubsection === subsection.id }"
                :href="`#${subsection.id}`"
                :aria-current="activeSubsection === subsection.id ? 'location' : undefined"
                @click.prevent="scrollToSubsection(subsection.id)"
              >
                {{ subsection.label }}
              </a>
            </nav>
          </div>
        </nav>

        <footer class="workbench-sidebar-footer">
          <span><i></i>{{ strategyCacheLabel }}</span>
          <small>更新于 {{ detailCreatedDate || '-' }}</small>
        </footer>
      </aside>

      <div class="result-report">
        <div class="research-main-column main-content result-report-main">
          <section id="strategy-summary" class="strategy-identity-band" tabindex="-1" aria-labelledby="strategy-detail-title">
            <div class="strategy-identity-summary-row">
              <div class="strategy-identity-copy">
                <div class="strategy-identity-overline">
                  <span>策略详情</span>
                  <code :title="entry.name">{{ entry.name }}</code>
                </div>
                <div class="strategy-identity-title-row">
                  <h1 id="strategy-detail-title">{{ pageTitle }}</h1>
                  <span class="status-pill" :class="detailStatusClass">
                    <TriangleAlert v-if="detailStatusClass === 'status-failed'" :size="14" aria-hidden="true" />
                    <Info v-else-if="detailStatusClass === 'status-no-signal' || detailStatusClass === 'status-idle'" :size="14" aria-hidden="true" />
                    <CircleCheck v-else :size="14" aria-hidden="true" />
                    {{ entry.status_label }}
                  </span>
                </div>
                <p class="strategy-identity-description">{{ strategyDescription }}</p>
              </div>
              <button class="secondary-btn strategy-rule-jump" type="button" @click="openStrategyRules">
                <ListChecks :size="15" aria-hidden="true" />查看完整规则
              </button>
            </div>
            <div class="strategy-identity-meta">
              <span v-for="(item, index) in strategyMetaItems" :key="item.label">
                <CircleDollarSign v-if="index === 0" :size="17" aria-hidden="true" />
                <Bookmark v-else-if="index === 1" :size="17" aria-hidden="true" />
                <Layers v-else-if="index === 2" :size="17" aria-hidden="true" />
                <CalendarDays v-else :size="17" aria-hidden="true" />
                <em>{{ item.label }}</em><strong :title="item.value">{{ item.value }}</strong>
              </span>
            </div>
          </section>

          <section class="strategy-showcase-grid" aria-label="全周期净值与核心表现">
            <NetValueChartPanel
              v-if="netValue"
              class="showcase-chart-panel"
              :data="visibleNetValue"
              :baseline-value="activeRange === 'all' ? 1 : null"
              :focus-date="showcaseFocusDate"
              title="策略净值走势"
              :note="`${activeRangeOption.label} · ${visibleDateRangeText} · 策略、基准、超额与回撤。`"
              :show-controls="false"
              :show-data-zoom="true"
              variant="workbench"
              @focus-date="showcaseFocusDate = $event"
            />
            <section v-else class="panel showcase-chart-panel overview-holdings-loading" aria-live="polite">
              <span class="strategy-data-spinner" aria-hidden="true"></span>正在载入净值曲线...
            </section>

            <section class="panel showcase-summary-panel" :class="researchConclusionTone">
              <header class="panel-head showcase-summary-head">
                <div>
                  <span class="showcase-eyebrow">全周期表现</span>
                  <h2>{{ researchConclusion }}</h2>
                  <p class="panel-note">{{ researchConclusionNote }}</p>
                </div>
                <BadgeCheck v-if="researchConclusionTone === 'is-positive'" :size="20" aria-hidden="true" />
                <TriangleAlert v-else-if="researchConclusionTone === 'is-caution' || researchConclusionTone === 'is-negative'" :size="20" aria-hidden="true" />
                <Info v-else :size="20" aria-hidden="true" />
              </header>
              <div class="showcase-metric-grid">
                <article v-for="card in decisionMetricCards" :key="card.key">
                  <span>{{ card.label }}</span>
                  <strong :class="card.valueClass">{{ card.value }}</strong>
                  <small>全周期</small>
                </article>
              </div>
              <div class="showcase-actions">
                <button
                  class="secondary-btn showcase-holding-action"
                  type="button"
                  :disabled="!canOpenShowcaseHolding"
                  @click="openShowcaseHolding"
                >
                  <WalletCards :size="16" aria-hidden="true" />
                  查看 {{ showcaseHoldingTargetDate || '该日' }} 持仓
                </button>
                <button class="primary-btn showcase-analysis-action" type="button" @click="scrollToSection('performance')">
                  <ChartNoAxesCombined :size="16" aria-hidden="true" />
                  深入分析
                </button>
              </div>
            </section>
          </section>

          <div id="detail-workspace" class="detail-content-shell research-content-shell">
            <div v-if="coreDataWarning" class="strategy-data-notice warning" role="alert">
              <span>{{ coreDataWarning }}</span>
              <button class="secondary-btn" type="button" @click="loadPage">
                <RefreshCw :size="14" aria-hidden="true" />重试核心数据
              </button>
            </div>
            <div
              v-if="supportingDataLoading"
              class="strategy-data-notice"
              aria-live="polite"
            >
              <span class="strategy-data-spinner" aria-hidden="true"></span>
              正在载入持仓与交易摘要...
            </div>
            <div v-else-if="supportingDataError" class="strategy-data-notice danger" role="alert">
              <span>{{ supportingDataError }}</span>
              <button class="secondary-btn" type="button" @click="retrySupportingData">
                <RefreshCw :size="14" aria-hidden="true" />重试明细
              </button>
            </div>
            <div v-else-if="fullDataError" class="strategy-data-notice danger" role="alert">
              <span>{{ fullDataError }}</span>
              <button class="secondary-btn" type="button" @click="ensureFullDataForSection(activeSection)">
                <RefreshCw :size="14" aria-hidden="true" />重试完整数据
              </button>
            </div>
            <div class="detail-section-stack">
          <DetailOverviewSection />

          <DetailRulesSection />

          <DetailHoldingsSection />

          <DetailLedgerSection />

          <DetailReviewSection />

          <DetailPerformanceSection />
            </div>
          </div>
        </div>

      </div>
    </template>
  </section>
</template>

<style src="./strategy-detail-workbench.css"></style>
