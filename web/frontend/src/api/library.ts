import { getJson } from './http'
import type {
  DailyHoldingsPayload,
  DailyHoldingsSummaryPayload,
  EnhancedCompareResponse,
  LibraryAnalysisResponse,
  LibraryCatalogResponse,
  LibraryDetailResponse,
  LibraryHealthResponse,
  LibraryMarketStateResponse,
  LibraryMetricsResponse,
  LibraryReportResponse,
  LibraryReviewResponse,
  NetValuePayload,
  TradesPayload,
  TradeSummaryPayload,
} from '../types/library'

export function fetchLibraryCatalog(signal?: AbortSignal): Promise<LibraryCatalogResponse> {
  return getJson<LibraryCatalogResponse>('/api/library', signal)
}

export function fetchLibraryHealth(): Promise<LibraryHealthResponse> {
  return getJson<LibraryHealthResponse>('/api/library/health')
}

export function fetchLibraryDetail(name: string): Promise<LibraryDetailResponse> {
  return getJson<LibraryDetailResponse>(`/api/library/${encodeURIComponent(name)}/detail`)
}

export function fetchLibraryNetValue(name: string, signal?: AbortSignal): Promise<NetValuePayload> {
  return getJson<NetValuePayload>(`/api/library/${encodeURIComponent(name)}/net_value`, signal)
}

export function fetchLibraryTradeRecords(name: string): Promise<TradesPayload> {
  return getJson<TradesPayload>(`/api/library/${encodeURIComponent(name)}/trades/records`)
}

export function fetchLibraryTradeSummary(name: string): Promise<TradeSummaryPayload> {
  return getJson<TradeSummaryPayload>(`/api/library/${encodeURIComponent(name)}/trades/summary`)
}

export function fetchLibraryHoldingsHistory(name: string): Promise<DailyHoldingsPayload> {
  return getJson<DailyHoldingsPayload>(`/api/library/${encodeURIComponent(name)}/holdings/history`)
}

export function fetchLibraryHoldingsSummary(name: string): Promise<DailyHoldingsSummaryPayload> {
  return getJson<DailyHoldingsSummaryPayload>(`/api/library/${encodeURIComponent(name)}/holdings/summary`)
}

export function fetchLibraryMetrics(name: string): Promise<LibraryMetricsResponse> {
  return getJson<LibraryMetricsResponse>(`/api/library/${encodeURIComponent(name)}/metrics`)
}

export function fetchLibraryAnalysis(name: string): Promise<LibraryAnalysisResponse> {
  return getJson<LibraryAnalysisResponse>(`/api/library/${encodeURIComponent(name)}/analysis`)
}

export function fetchLibraryMarketStates(name: string): Promise<LibraryMarketStateResponse> {
  return getJson<LibraryMarketStateResponse>(`/api/library/${encodeURIComponent(name)}/market_states`)
}

export function fetchLibraryReview(name: string): Promise<LibraryReviewResponse> {
  return getJson<LibraryReviewResponse>(`/api/library/${encodeURIComponent(name)}/review`)
}

export function fetchEnhancedCompare(names: string[], signal?: AbortSignal): Promise<EnhancedCompareResponse> {
  return getJson<EnhancedCompareResponse>(`/api/compare/enhanced?names=${encodeURIComponent(names.join(','))}`, signal)
}

export function fetchLibraryReport(name: string): Promise<LibraryReportResponse> {
  return getJson<LibraryReportResponse>(`/api/library/${encodeURIComponent(name)}/report?format=json`)
}

export function libraryExportUrl(format: 'csv' | 'json'): string {
  return `/api/library/export?format=${encodeURIComponent(format)}`
}

export function libraryReportUrl(name: string): string {
  return `/api/library/${encodeURIComponent(name)}/report`
}
