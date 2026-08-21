import { beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchBacktestStatus, startBacktestRun } from './backtest'
import { getJson, postJson } from './http'
import {
  fetchEnhancedCompare,
  fetchLibraryAnalysis,
  fetchLibraryCatalog,
  fetchLibraryDetail,
  fetchLibraryHealth,
  fetchLibraryHoldingsHistory,
  fetchLibraryHoldingsSummary,
  fetchLibraryMarketStates,
  fetchLibraryMetrics,
  fetchLibraryNetValue,
  fetchLibraryReport,
  fetchLibraryReview,
  fetchLibraryTradeRecords,
  fetchLibraryTradeSummary,
  libraryExportUrl,
  libraryReportUrl,
} from './library'
import { fetchLogFiles, fetchLogTail, sendClientLog } from './logs'

vi.mock('./http')

const getJsonMock = vi.mocked(getJson)
const postJsonMock = vi.mocked(postJson)

describe('API endpoint adapters', () => {
  beforeEach(() => {
    getJsonMock.mockReset()
    postJsonMock.mockReset()
  })

  it('forwards cancellation to catalog requests', () => {
    const controller = new AbortController()

    fetchLibraryCatalog(controller.signal)

    expect(getJsonMock).toHaveBeenCalledWith('/api/library', controller.signal)
  })

  it('encodes strategy names as a single path segment', () => {
    fetchLibraryDetail('alpha/beta? 中文')

    expect(getJsonMock).toHaveBeenCalledWith(
      '/api/library/alpha%2Fbeta%3F%20%E4%B8%AD%E6%96%87/detail',
    )
    expect(libraryReportUrl('alpha/beta')).toBe('/api/library/alpha%2Fbeta/report')
  })

  it.each([
    ['health', () => fetchLibraryHealth(), '/api/library/health'],
    ['trade records', () => fetchLibraryTradeRecords('alpha/beta'), '/api/library/alpha%2Fbeta/trades/records'],
    ['trade summary', () => fetchLibraryTradeSummary('alpha/beta'), '/api/library/alpha%2Fbeta/trades/summary'],
    ['holdings history', () => fetchLibraryHoldingsHistory('alpha/beta'), '/api/library/alpha%2Fbeta/holdings/history'],
    ['holdings summary', () => fetchLibraryHoldingsSummary('alpha/beta'), '/api/library/alpha%2Fbeta/holdings/summary'],
    ['metrics', () => fetchLibraryMetrics('alpha/beta'), '/api/library/alpha%2Fbeta/metrics'],
    ['analysis', () => fetchLibraryAnalysis('alpha/beta'), '/api/library/alpha%2Fbeta/analysis'],
    ['market states', () => fetchLibraryMarketStates('alpha/beta'), '/api/library/alpha%2Fbeta/market_states'],
    ['review', () => fetchLibraryReview('alpha/beta'), '/api/library/alpha%2Fbeta/review'],
    ['report JSON', () => fetchLibraryReport('alpha/beta'), '/api/library/alpha%2Fbeta/report?format=json'],
  ])('maps the %s endpoint to its stable route', (_label, request, expectedUrl) => {
    request()

    expect(getJsonMock).toHaveBeenLastCalledWith(expectedUrl)
  })

  it('forwards cancellation to net-value requests', () => {
    const controller = new AbortController()

    fetchLibraryNetValue('alpha/beta', controller.signal)

    expect(getJsonMock).toHaveBeenCalledWith(
      '/api/library/alpha%2Fbeta/net_value',
      controller.signal,
    )
  })

  it('encodes the complete compare-name list and forwards cancellation', () => {
    const controller = new AbortController()

    fetchEnhancedCompare(['alpha', 'beta/三'], controller.signal)

    expect(getJsonMock).toHaveBeenCalledWith(
      '/api/compare/enhanced?names=alpha%2Cbeta%2F%E4%B8%89',
      controller.signal,
    )
  })

  it('builds stable export and log query strings', () => {
    expect(libraryExportUrl('csv')).toBe('/api/library/export?format=csv')

    fetchLogFiles(25)
    expect(getJsonMock).toHaveBeenLastCalledWith('/api/logs?limit=25', undefined, true)

    fetchLogTail(80, 'run 01.jsonl')
    expect(getJsonMock).toHaveBeenLastCalledWith(
      '/api/logs/tail?lines=80&file=run+01.jsonl',
      undefined,
      true,
    )

    fetchLogTail()
    expect(getJsonMock).toHaveBeenLastCalledWith('/api/logs/tail?lines=120', undefined, true)
  })

  it('uses the shared transports for backtest and client-log mutations', () => {
    fetchBacktestStatus()
    expect(getJsonMock).toHaveBeenCalledWith('/api/backtest/status', undefined, true)

    const runPayload = {
      names: ['alpha'],
      date_overrides: { alpha: { start_time: '2025-01-01', end_time: '2025-01-31' } },
    }
    startBacktestRun(runPayload)
    expect(postJsonMock).toHaveBeenCalledWith('/api/backtest/run', runPayload, true)

    const logPayload = { level: 'warn', event: 'render', message: 'slow chart' }
    sendClientLog(logPayload)
    expect(postJsonMock).toHaveBeenCalledWith('/api/logs/client', logPayload, true)
  })
})
