import { getJson, postJson } from './http'
import type { WebLogListResponse, WebLogTailResponse } from '../types/library'

export function fetchLogFiles(limit = 50): Promise<WebLogListResponse> {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  return getJson<WebLogListResponse>(`/api/logs?${params.toString()}`, undefined, true)
}

export function fetchLogTail(lines = 120, file?: string): Promise<WebLogTailResponse> {
  const params = new URLSearchParams()
  params.set('lines', String(lines))
  if (file) params.set('file', file)
  return getJson<WebLogTailResponse>(`/api/logs/tail?${params.toString()}`, undefined, true)
}

export function sendClientLog(payload: {
  level?: string
  event?: string
  page?: string
  message: string
  fields?: Record<string, unknown>
}) {
  return postJson<{ ok: boolean }>('/api/logs/client', payload, true)
}
