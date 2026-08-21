import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AdminApiError,
  deleteStrategy,
  fetchAdminStatus,
  fetchMaintenanceStatus,
  renameStrategy,
  startDataUpdate,
  startVersionUpdate,
  uploadStrategy,
} from './admin'

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('admin API', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns status JSON and attaches the admin token', async () => {
    const payload = {
      maintenance: { running: false },
      repository: { available: true },
    }
    fetchMock.mockResolvedValueOnce(jsonResponse(payload))

    await expect(fetchAdminStatus('secret')).resolves.toEqual(payload)
    expect(fetchMock).toHaveBeenCalledWith('/api/admin/status', {
      headers: {
        Accept: 'application/json',
        'X-Web-Admin-Token': 'secret',
      },
    })
  })

  it('omits an empty token and rejects a successful non-object JSON payload', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse('not-an-object'))

    await expect(fetchMaintenanceStatus('')).rejects.toMatchObject({
      name: 'AdminApiError',
      status: 200,
      message: 'invalid_response_payload',
      data: null,
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/admin/task/status', {
      headers: { Accept: 'application/json' },
    })
  })

  it('rejects a successful response that is not JSON', async () => {
    fetchMock.mockResolvedValueOnce(new Response('ok', { status: 200 }))

    await expect(fetchAdminStatus('secret')).rejects.toMatchObject({
      name: 'AdminApiError',
      status: 200,
      message: 'invalid_response_payload',
      data: null,
    })
  })

  it('serializes data-update and version-update requests', async () => {
    const dataPayload = {
      start_date: '2025-01-01',
      end_date: '2025-01-31',
      update_stock: true,
      update_index: false,
      update_etf: false,
      rebuild_panel: true,
      dry_run: true,
      max_workers: 2,
      symbol_type: 'stock' as const,
    }
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'started' }))
    await expect(startDataUpdate(dataPayload, 'secret')).resolves.toEqual({ status: 'started' })
    expect(fetchMock).toHaveBeenLastCalledWith('/api/admin/data-update/run', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Web-Admin-Token': 'secret',
      },
      body: JSON.stringify(dataPayload),
    })

    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'started' }))
    await startVersionUpdate('secret')
    expect(fetchMock).toHaveBeenLastCalledWith('/api/admin/version-update/run', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Web-Admin-Token': 'secret',
      },
      body: '{}',
    })
  })

  it('uses browser-managed content type for uploads and serializes strategy mutations', async () => {
    const form = new FormData()
    form.set('strategy', 'alpha')
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }))
    await uploadStrategy(form, 'secret')
    expect(fetchMock).toHaveBeenLastCalledWith('/api/admin/strategies/upload', {
      method: 'POST',
      body: form,
      headers: {
        Accept: 'application/json',
        'X-Web-Admin-Token': 'secret',
      },
    })

    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true, deleted: true }))
    await deleteStrategy('alpha/beta 中文', 'secret')
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/admin/strategies/alpha%2Fbeta%20%E4%B8%AD%E6%96%87',
      {
        method: 'DELETE',
        headers: {
          Accept: 'application/json',
          'X-Web-Admin-Token': 'secret',
        },
      },
    )

    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true, strategy: { name: 'beta' } }))
    await renameStrategy(
      'alpha/beta 中文',
      { name: 'renamed_alpha', title: '重命名策略' },
      'secret',
    )
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/admin/strategies/alpha%2Fbeta%20%E4%B8%AD%E6%96%87',
      {
        method: 'PATCH',
        body: JSON.stringify({ name: 'renamed_alpha', title: '重命名策略' }),
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Web-Admin-Token': 'secret',
        },
      },
    )
  })

  it.each([
    [jsonResponse({ message: '  denied  ' }, 403), 'denied', { message: '  denied  ' }],
    [jsonResponse({ error: 'busy' }, 409), 'busy', { error: 'busy' }],
    [new Response('unavailable', { status: 503 }), 'request_failed:503', null],
  ])('throws AdminApiError for failed status responses %#', async (response, message, data) => {
    fetchMock.mockResolvedValueOnce(response)

    let thrown: unknown
    try {
      await fetchAdminStatus('secret')
    } catch (error) {
      thrown = error
    }
    expect(thrown).toBeInstanceOf(AdminApiError)
    expect(thrown).toMatchObject({ status: response.status, message, data })
  })

  it('throws from failed POST and mutation responses', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ error: 'already running' }, 409))
    await expect(startVersionUpdate('secret')).rejects.toMatchObject({
      name: 'AdminApiError',
      status: 409,
      message: 'already running',
    })

    fetchMock.mockResolvedValueOnce(jsonResponse({ message: 'invalid archive' }, 422))
    await expect(uploadStrategy(new FormData(), 'secret')).rejects.toMatchObject({
      name: 'AdminApiError',
      status: 422,
      message: 'invalid archive',
    })
  })

  it('propagates network errors without wrapping them', async () => {
    const networkError = new TypeError('connection reset')
    fetchMock.mockRejectedValueOnce(networkError)

    await expect(fetchAdminStatus('secret')).rejects.toBe(networkError)
  })
})
