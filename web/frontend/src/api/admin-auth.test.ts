import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ADMIN_TOKEN_STORAGE_KEY,
  readStoredAdminToken,
  storeAdminToken,
  withAdminToken,
} from './admin-auth'

describe('admin token storage', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.sessionStorage.clear()
  })

  it('stores, reads, and removes the shared session token', () => {
    storeAdminToken('session-secret')

    expect(window.sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)).toBe('session-secret')
    expect(readStoredAdminToken()).toBe('session-secret')
    expect(withAdminToken({ Accept: 'application/json' })).toEqual({
      Accept: 'application/json',
      'X-Web-Admin-Token': 'session-secret',
    })

    storeAdminToken('')
    expect(readStoredAdminToken()).toBe('')
    expect(withAdminToken({ Accept: 'application/json' })).toEqual({ Accept: 'application/json' })
  })

  it('supports an explicit token without consulting session storage', () => {
    expect(withAdminToken({ Accept: 'application/json' }, 'explicit-secret')).toEqual({
      Accept: 'application/json',
      'X-Web-Admin-Token': 'explicit-secret',
    })
  })

  it('fails closed to an empty value when session storage is unavailable', () => {
    vi.spyOn(window.sessionStorage, 'getItem').mockImplementation(() => {
      throw new Error('storage disabled')
    })
    expect(readStoredAdminToken()).toBe('')

    vi.spyOn(window.sessionStorage, 'setItem').mockImplementation(() => {
      throw new Error('storage disabled')
    })
    expect(() => storeAdminToken('secret')).not.toThrow()
  })
})
