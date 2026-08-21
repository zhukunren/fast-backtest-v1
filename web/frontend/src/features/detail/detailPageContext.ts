import { inject, type InjectionKey } from 'vue'
import type {
  HoldingsCurrentView,
  HoldingsDisplayMode,
  LibraryAnalysisResponse,
  LibraryReviewResponse,
  NetValuePayload,
  TableRow,
} from '../../types/library'

export interface DetailTableColumn {
  key: string
  label: string
  align?: 'left' | 'center' | 'right'
}

export interface DetailMetricCell {
  label: string
  value: string
  note?: string
  valueClass?: string
}

export interface InvestorConditionGroup {
  title: string
  logic?: string
  items: InvestorConditionItem[]
}

export interface InvestorConditionItem {
  title?: string
  description: string
  example?: string
}

export interface InvestorConditionSection {
  key: 'buy' | 'sell'
  label: string
  summary: string
  groups: InvestorConditionGroup[]
}

export interface InvestorIndicator {
  name: string
  purpose?: string
  formula: string[]
  variables: string[]
  parameters: string[]
  usage: string[]
  notes: string[]
}

export interface DetailSignalItem {
  code: string
  name: string
}

export interface DetailSignalSummary {
  date: string
  items: DetailSignalItem[]
}

export interface DetailSignalCard {
  label: string
  kind: 'buy' | 'sell'
  emptyText: string
  signal: DetailSignalSummary
}

export interface DetailMarketStateBar {
  label: string
  percentage: number
  tone: string
  dayCount: number
  returnValue: number | null
  returnText: string
  excessReturnText: string
  maxDrawdownText: string
}

export interface DetailExposureRow {
  key: string
  label: string
  weight: number
  value: string
}

export interface DetailTradeDistributionRecord {
  stock_code: string
  stock_name: string
  entry_date: string
  exit_date: string
  holding_days: number
  return_rate: number
}

export interface DetailPageContext {
  analysis: LibraryAnalysisResponse | null
  review: LibraryReviewResponse | null
  reviewDataLoading: boolean
  reviewDataError: string
  supportingDataLoading: boolean
  fullTradesLoading: boolean
  fullHoldingsLoading: boolean
  fullTradesLoaded: boolean
  fullHoldingsLoaded: boolean
  fullDataError: string

  investorConditionSections: InvestorConditionSection[]
  investorIndicators: InvestorIndicator[]
  researchScore: LibraryAnalysisResponse['score'] | null
  researchScoreClass: string
  researchFactCards: DetailMetricCell[]
  displayResearchDiagnostics: Array<{ code: string; label: string; severity?: string }>
  researchWarnings: Array<{ code: string; label: string }>
  marketStateBars: DetailMarketStateBar[]
  overviewExecutionState: { label: string; tone: string }
  overviewExecutionFacts: Array<{ label: string; value: string; valueClass: string }>
  latestSignalData: { missingCode: boolean; buy: DetailSignalSummary; sell: DetailSignalSummary }
  latestSignalCards: DetailSignalCard[]
  overviewExposureRows: DetailExposureRow[]
  latestHoldingDate: string

  activeRange: string
  activeRangeOption: { label: string }
  visibleDateRangeText: string
  visibleNetValue: NetValuePayload | null
  performanceIntervalMetrics: DetailMetricCell[]
  performanceMetricCells: DetailMetricCell[]
  isResearchMode: boolean
  expandedPanels: Record<string, boolean>
  scoreDetailsSummary: string
  scoreComponentRows: TableRow[]
  scoreComponentColumns: DetailTableColumn[]
  returnHeatmapSummary: string
  marketStateSummary: string
  marketStateRows: TableRow[]
  marketStateColumns: DetailTableColumn[]
  yearlyPerformanceSummary: string
  researchYearlyRows: TableRow[]
  researchYearlyColumns: DetailTableColumn[]
  visibleTradeRecordsForDistribution: DetailTradeDistributionRecord[] | null
  performanceChartsActivated: boolean

  holdingsStatusText: string
  holdingsDisplayMode: HoldingsDisplayMode
  holdingsCurrentView: HoldingsCurrentView
  effectiveHoldingsView: HoldingsCurrentView
  holdingsViewOptions: Array<{ value: HoldingsCurrentView; label: string }>
  showHoldingsViewToggle: boolean
  showHoldingsHistory: boolean
  showHoldingsExport: boolean
  focusDate: string
  holdingsDates: string[]
  canShiftBackward: boolean
  canShiftForward: boolean
  holdingsSummaryCards: DetailMetricCell[]
  holdingsCurrentNote: string
  canExportFocusedHoldings: boolean
  currentHoldingColumns: DetailTableColumn[]
  currentHoldingDisplayRows: TableRow[]
  dailyTradeCards: DetailMetricCell[]
  dailyTradeColumns: DetailTableColumn[]
  dailyTradeRows: TableRow[]
  holdingsHistoryNote: string
  holdingsHistorySearchInput: string
  holdingsSlotFilter: string
  holdingsSlotOptions: string[]
  holdingsDirectionFilter: 'all' | 'buy' | 'sell' | 'hold'
  holdingHistoryDisplayRows: TableRow[]
  holdingHistoryEmptyText: string
  holdingHistoryPanelSummary: string
  holdingsHeatmapSummary: string
  flattenedHoldings: Array<{ trade_date: string; position_ratio: number }> | null
  holdingHistoryColumns: DetailTableColumn[]

  reviewMetricCards: DetailMetricCell[]
  reviewStockRows: TableRow[]
  reviewStocksSummary: string
  reviewStockColumns: DetailTableColumn[]

  ledgerDateRangeText: string
  ledgerSummaryCards: DetailMetricCell[]
  executionPreviewSummary: string
  executionPanelSummary: string
  executionColumns: DetailTableColumn[]
  latestExecutionRows: TableRow[]
  executionRows: TableRow[]
  filteredExecutionRows: TableRow[]
  executionSearch: string
  canExportExecutionCsv: boolean
  orderPanelSummary: string
  orderColumns: DetailTableColumn[]
  orderRows: TableRow[]
  filteredOrderRows: TableRow[]
  orderSearch: string
  canExportOrderCsv: boolean
  ledgerPageSize: number
  getLoadToken: () => number

  setPanelExpanded: (panelId: string, expanded: boolean) => void
  handleHoldingHistoryPanelToggle: (expanded: boolean) => void
  shiftFocus: (step: number) => void
  normalizeHoldingsCurrentView: (value: HoldingsCurrentView) => HoldingsCurrentView
  scrollToSection: (sectionId: string) => Promise<void>
  openAllExecutionRecords: () => Promise<void>
  setDetailViewMode: (mode: 'simple' | 'advanced') => void
  loadReviewData: (token: number) => Promise<void>
  ensureFullDataForSection: (sectionId: string) => Promise<void>
  exportFocusedHoldings: (format: 'csv' | 'xls') => Promise<void>
  exportExecutionRecordsCsv: () => Promise<void>
  exportOrderRecordsCsv: () => Promise<void>
  resetHoldingsHistoryFilters: () => void
  holdingHistoryRowKey: (row: TableRow, index: number) => string
  formatSignalItem: (item: DetailSignalItem) => string
  formatPlainNumber: (value: unknown, digits: number) => string
  formatPercent: (value: unknown, digits?: number) => string
  metricTrendClass: (value: unknown) => string
}

export const DETAIL_PAGE_CONTEXT_KEY: InjectionKey<DetailPageContext> = Symbol('detail-page-context')

export function useDetailPageContext(): DetailPageContext {
  const context = inject(DETAIL_PAGE_CONTEXT_KEY)
  if (!context) throw new Error('Detail page context is not available')
  return context
}
