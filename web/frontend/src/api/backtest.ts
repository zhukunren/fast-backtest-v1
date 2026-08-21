import { getJson, postJson } from './http'
import type { BacktestProgress, BacktestRunResponse } from '../types/library'

export interface BacktestRunPayload {
  names: string[]
  date_overrides?: Record<string, { start_time?: string; end_time?: string }>
}

export function fetchBacktestStatus(): Promise<BacktestProgress> {
  return getJson<BacktestProgress>('/api/backtest/status', undefined, true)
}

export function startBacktestRun(payload: BacktestRunPayload) {
  return postJson<BacktestRunResponse>('/api/backtest/run', payload, true)
}
