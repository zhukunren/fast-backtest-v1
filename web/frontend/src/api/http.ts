import { withAdminToken } from './admin-auth'

const NETWORK_ERROR_MESSAGE = '无法连接 Web 服务，请确认服务正在运行后刷新页面重试。'

export class HttpError extends Error {
  readonly status: number
  readonly data: unknown

  constructor(status: number, message: string, data: unknown = null) {
    super(message)
    this.name = 'HttpError'
    this.status = status
    this.data = data
  }
}

async function readResponsePayload(response: Response): Promise<unknown> {
  try {
    return await response.json()
  } catch {
    return null
  }
}

function responseErrorMessage(status: number, payload: unknown): string {
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>
    const error = record.error
    if (typeof error === 'string' && error.trim()) return error.trim()
    if (error && typeof error === 'object') {
      const message = (error as Record<string, unknown>).message
      if (typeof message === 'string' && message.trim()) return message.trim()
    }
    if (typeof record.message === 'string' && record.message.trim()) return record.message.trim()
  }
  return `request_failed:${status}`
}

async function fetchResponse(...args: Parameters<typeof fetch>): Promise<Response> {
  try {
    return await fetch(...args)
  } catch (error) {
    // Browsers use TypeError when no HTTP response is available, for example
    // when the local Flask service or Vite proxy is not running.
    if (error instanceof TypeError) {
      throw new HttpError(0, NETWORK_ERROR_MESSAGE)
    }
    throw error
  }
}

export async function getJson<T>(url: string, signal?: AbortSignal, adminAuth = false): Promise<T> {
  const headers = { Accept: 'application/json' }
  const response = await fetchResponse(url, {
    headers: adminAuth ? withAdminToken(headers) : headers,
    signal,
  })

  if (!response.ok) {
    const payload = await readResponsePayload(response)
    throw new HttpError(response.status, responseErrorMessage(response.status, payload), payload)
  }

  return (await response.json()) as T
}

export interface JsonResult<T> {
  ok: boolean
  status: number
  data: T | null
}

export async function postJson<T>(url: string, payload: unknown, adminAuth = false): Promise<JsonResult<T>> {
  const headers = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  }
  const response = await fetchResponse(url, {
    method: 'POST',
    headers: adminAuth ? withAdminToken(headers) : headers,
    body: JSON.stringify(payload ?? {}),
  })

  let data: T | null = null
  try {
    data = (await response.json()) as T
  } catch {
    data = null
  }

  return {
    ok: response.ok,
    status: response.status,
    data,
  }
}

async function responseJsonResult<T>(response: Response): Promise<JsonResult<T>> {
  let data: T | null = null
  try {
    data = (await response.json()) as T
  } catch {
    data = null
  }

  return {
    ok: response.ok,
    status: response.status,
    data,
  }
}

export async function postForm<T>(url: string, payload: FormData): Promise<JsonResult<T>> {
  const response = await fetchResponse(url, {
    method: 'POST',
    headers: {
      Accept: 'application/json',
    },
    body: payload,
  })
  return responseJsonResult<T>(response)
}

export async function deleteJson<T>(url: string, adminAuth = false): Promise<JsonResult<T>> {
  const headers = { Accept: 'application/json' }
  const response = await fetchResponse(url, {
    method: 'DELETE',
    headers: adminAuth ? withAdminToken(headers) : headers,
  })
  return responseJsonResult<T>(response)
}
