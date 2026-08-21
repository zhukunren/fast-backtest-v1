import { withAdminToken } from './admin-auth'

export interface MaintenanceTaskResult {
  task?: string
  label?: string
  status?: 'completed' | 'failed' | 'skipped' | string
  return_code?: number | null
  duration_sec?: number
  stdout_tail?: string
  stderr_tail?: string
  previous_revision?: string
  revision?: string
  restart_required?: boolean
}

export interface MaintenanceProgress {
  running: boolean
  task_kind?: 'data_update' | 'version_update' | string
  task_id?: string
  total?: number
  completed?: number
  current?: string
  elapsed_sec?: number
  error?: string
  results?: MaintenanceTaskResult[]
}

export interface RepositoryStatus {
  available: boolean
  can_update?: boolean
  error?: string
  branch?: string
  revision?: string
  short_revision?: string
  subject?: string
  remote?: string
  remote_url?: string
  github_remote?: boolean
  update_branch?: string
  target_ref?: string
  target_revision?: string
  ahead?: number | null
  behind?: number | null
  update_available?: boolean
  diverged?: boolean
  dirty?: boolean
  dirty_count?: number
}

export interface AdminStatusResponse {
  maintenance: MaintenanceProgress
  repository: RepositoryStatus
}

export interface DataUpdatePayload {
  start_date: string
  end_date: string
  update_stock: boolean
  update_index: boolean
  update_etf: boolean
  rebuild_panel: boolean
  dry_run: boolean
  max_workers: number
  symbol_type: 'stock' | 'index' | 'etf'
  codes?: string
}

export interface MaintenanceStartResponse {
  status?: string
  error?: string
  message?: string
  progress?: MaintenanceProgress
}

export interface InstalledStrategy {
  name: string
  module: string
  source_file: string
  file_count: number
  size_bytes: number
  overwritten: boolean
  archive_id?: string
  warnings?: string[]
}

export interface StrategyUploadResponse {
  ok?: boolean
  strategy?: InstalledStrategy
  error?: string
}

export interface StrategyDeleteResponse {
  ok?: boolean
  name?: string
  deleted?: boolean
  recoverable?: boolean
  archive_id?: string
  error?: string
}

export interface StrategyRenamePayload {
  name: string
  title?: string
}

export interface RenamedStrategy {
  old_name: string
  name: string
  title: string
  renamed: boolean
  identifier_changed: boolean
  title_changed: boolean
  cache_preserved: boolean
}

export interface StrategyRenameResponse {
  ok?: boolean
  strategy?: RenamedStrategy
  error?: string
}

export class AdminApiError extends Error {
  readonly status: number
  readonly data: Record<string, unknown> | null

  constructor(status: number, message: string, data: Record<string, unknown> | null) {
    super(message)
    this.name = 'AdminApiError'
    this.status = status
    this.data = data
  }
}

function adminHeaders(token: string, includeContentType = false): HeadersInit {
  return withAdminToken({
    Accept: 'application/json',
    ...(includeContentType ? { 'Content-Type': 'application/json' } : {}),
  }, token)
}

async function readPayload(response: Response): Promise<Record<string, unknown> | null> {
  try {
    const payload = await response.json()
    return payload && typeof payload === 'object' ? payload as Record<string, unknown> : null
  } catch {
    return null
  }
}

function errorMessage(response: Response, payload: Record<string, unknown> | null): string {
  const message = payload?.message || payload?.error
  return typeof message === 'string' && message.trim() ? message.trim() : `request_failed:${response.status}`
}

function requirePayload<T>(response: Response, payload: Record<string, unknown> | null): T {
  if (payload) return payload as T
  throw new AdminApiError(response.status, 'invalid_response_payload', null)
}

async function getAdminJson<T>(url: string, token: string): Promise<T> {
  const response = await fetch(url, { headers: adminHeaders(token) })
  const payload = await readPayload(response)
  if (!response.ok) throw new AdminApiError(response.status, errorMessage(response, payload), payload)
  return requirePayload<T>(response, payload)
}

async function postAdminJson<T>(url: string, payload: unknown, token: string): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: adminHeaders(token, true),
    body: JSON.stringify(payload ?? {}),
  })
  const responsePayload = await readPayload(response)
  if (!response.ok) {
    throw new AdminApiError(response.status, errorMessage(response, responsePayload), responsePayload)
  }
  return requirePayload<T>(response, responsePayload)
}

async function mutateAdmin<T>(url: string, init: RequestInit, token: string): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      ...adminHeaders(token),
      ...(init.headers || {}),
    },
  })
  const responsePayload = await readPayload(response)
  if (!response.ok) {
    throw new AdminApiError(response.status, errorMessage(response, responsePayload), responsePayload)
  }
  return requirePayload<T>(response, responsePayload)
}

export function fetchAdminStatus(token: string): Promise<AdminStatusResponse> {
  return getAdminJson<AdminStatusResponse>('/api/admin/status', token)
}

export function fetchMaintenanceStatus(token: string): Promise<MaintenanceProgress> {
  return getAdminJson<MaintenanceProgress>('/api/admin/task/status', token)
}

export function startDataUpdate(payload: DataUpdatePayload, token: string): Promise<MaintenanceStartResponse> {
  return postAdminJson<MaintenanceStartResponse>('/api/admin/data-update/run', payload, token)
}

export function startVersionUpdate(token: string): Promise<MaintenanceStartResponse> {
  return postAdminJson<MaintenanceStartResponse>('/api/admin/version-update/run', {}, token)
}

export function uploadStrategy(form: FormData, token: string): Promise<StrategyUploadResponse> {
  return mutateAdmin<StrategyUploadResponse>('/api/admin/strategies/upload', {
    method: 'POST',
    body: form,
  }, token)
}

export function renameStrategy(
  currentName: string,
  payload: StrategyRenamePayload,
  token: string,
): Promise<StrategyRenameResponse> {
  return mutateAdmin<StrategyRenameResponse>(
    `/api/admin/strategies/${encodeURIComponent(currentName)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    },
    token,
  )
}

export function deleteStrategy(name: string, token: string): Promise<StrategyDeleteResponse> {
  return mutateAdmin<StrategyDeleteResponse>(`/api/admin/strategies/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  }, token)
}
