import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { deleteJson, getJson, HttpError, postForm, postJson } from './http'
import { storeAdminToken } from './admin-auth'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('HTTP helpers', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns JSON and forwards the request signal on a successful GET', async () => {
    const controller = new AbortController()
    const payload = { ok: true, items: [1, 2] }
    fetchMock.mockResolvedValueOnce(jsonResponse(payload))

    await expect(getJson<typeof payload>('/api/items', controller.signal)).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledWith('/api/items', {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    })
  })

  it('throws an HttpError with the server message and payload on a failed GET', async () => {
    const payload = { error: { message: '  invalid request  ' }, request_id: 'req-1' }
    fetchMock.mockResolvedValueOnce(jsonResponse(payload, 422))

    let thrown: unknown
    try {
      await getJson('/api/items')
    } catch (error) {
      thrown = error
    }

    expect(thrown).toBeInstanceOf(HttpError)
    expect(thrown).toMatchObject({
      name: 'HttpError',
      message: 'invalid request',
      status: 422,
      data: payload,
    })
  })

  it.each([
    [{ error: '  access denied  ' }, 'access denied'],
    [{ message: '  request rejected  ' }, 'request rejected'],
    [{ error: { message: '' } }, 'request_failed:400'],
    ['plain JSON value', 'request_failed:400'],
  ])('normalizes alternative error payloads %#', async (payload, expectedMessage) => {
    fetchMock.mockResolvedValueOnce(jsonResponse(payload, 400))

    await expect(getJson('/api/items')).rejects.toMatchObject({
      message: expectedMessage,
      status: 400,
      data: payload,
    })
  })

  it('uses a stable fallback error when a failed GET is not JSON', async () => {
    fetchMock.mockResolvedValueOnce(new Response('temporarily unavailable', { status: 503 }))

    await expect(getJson('/api/items')).rejects.toMatchObject({
      name: 'HttpError',
      message: 'request_failed:503',
      status: 503,
      data: null,
    })
  })

  it('normalizes a network failure into an actionable HTTP error', async () => {
    const networkError = new TypeError('network unavailable')
    fetchMock.mockRejectedValueOnce(networkError)

    await expect(getJson('/api/items')).rejects.toMatchObject({
      name: 'HttpError',
      message: '无法连接 Web 服务，请确认服务正在运行后刷新页面重试。',
      status: 0,
      data: null,
    })
  })

  it('rejects a successful GET whose response is not JSON', async () => {
    fetchMock.mockResolvedValueOnce(new Response('ok', { status: 200 }))

    await expect(getJson('/api/items')).rejects.toBeInstanceOf(SyntaxError)
  })

  it('serializes POST payloads and returns response metadata', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 7 }, 201))

    await expect(postJson<{ id: number }>('/api/items', { name: 'alpha' })).resolves.toEqual({
      ok: true,
      status: 201,
      data: { id: 7 },
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/items', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify({ name: 'alpha' }),
    })
  })

  it('returns null data for non-JSON mutation responses', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    await expect(postJson('/api/items', null)).resolves.toEqual({ ok: true, status: 204, data: null })

    const form = new FormData()
    form.set('strategy', 'alpha')
    fetchMock.mockResolvedValueOnce(new Response('bad gateway', { status: 502 }))
    await expect(postForm('/api/upload', form)).resolves.toEqual({ ok: false, status: 502, data: null })
    expect(fetchMock).toHaveBeenLastCalledWith('/api/upload', {
      method: 'POST',
      headers: { Accept: 'application/json' },
      body: form,
    })
  })

  it('sends DELETE requests through the shared JSON result contract', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ deleted: true }))

    await expect(deleteJson<{ deleted: boolean }>('/api/items/7')).resolves.toEqual({
      ok: true,
      status: 200,
      data: { deleted: true },
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/items/7', {
      method: 'DELETE',
      headers: { Accept: 'application/json' },
    })
  })

  it('attaches the stored admin token only when a sensitive request opts in', async () => {
    storeAdminToken('session-secret')
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }))

    await getJson('/api/logs', undefined, true)

    expect(fetchMock).toHaveBeenCalledWith('/api/logs', {
      headers: {
        Accept: 'application/json',
        'X-Web-Admin-Token': 'session-secret',
      },
      signal: undefined,
    })
  })
})
