import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { storeAdminToken } from './admin-auth'
import { fetchBacktestStatus, startBacktestRun } from './backtest'
import { fetchLogFiles, fetchLogTail, sendClientLog } from './logs'

function jsonResponse(payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('sensitive Web APIs', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    storeAdminToken('session-secret')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    window.sessionStorage.clear()
  })

  it('authenticates backtest status and submission with the shared session token', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ running: false }))
    await fetchBacktestStatus()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/backtest/status', {
      headers: {
        Accept: 'application/json',
        'X-Web-Admin-Token': 'session-secret',
      },
      signal: undefined,
    })

    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'started' }))
    await startBacktestRun({ names: ['alpha'] })
    expect(fetchMock).toHaveBeenLastCalledWith('/api/backtest/run', {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'X-Web-Admin-Token': 'session-secret',
      },
      body: JSON.stringify({ names: ['alpha'] }),
    })
  })

  it('authenticates log reads and client log writes', async () => {
    fetchMock.mockImplementation(async () => jsonResponse({ files: [], entries: [], ok: true }))

    await fetchLogFiles(25)
    await fetchLogTail(10, 'web-20260726.jsonl')
    await fetchLogTail()
    await sendClientLog({ message: 'client event' })

    for (const call of fetchMock.mock.calls) {
      const headers = (call[1]?.headers || {}) as Record<string, string>
      expect(headers['X-Web-Admin-Token']).toBe('session-secret')
    }
  })
})
