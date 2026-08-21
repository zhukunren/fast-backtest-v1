import { describe, expect, it } from 'vitest'

import { isSameOriginDevRequest } from './vite.config'

describe('Vite API proxy origin handling', () => {
  it('recognizes the browser origin of the current dev server', () => {
    expect(isSameOriginDevRequest('http://127.0.0.1:5173', '127.0.0.1:5173')).toBe(true)
    expect(isSameOriginDevRequest('http://LOCALHOST:5173', 'localhost:5173')).toBe(true)
  })

  it('does not rewrite untrusted or malformed origins', () => {
    expect(isSameOriginDevRequest('https://attacker.example', '127.0.0.1:5173')).toBe(false)
    expect(isSameOriginDevRequest('https://127.0.0.1:5173', '127.0.0.1:5173')).toBe(false)
    expect(isSameOriginDevRequest('http://127.0.0.1:5173/path', '127.0.0.1:5173')).toBe(false)
    expect(isSameOriginDevRequest('not-an-origin', '127.0.0.1:5173')).toBe(false)
    expect(isSameOriginDevRequest('http://127.0.0.1:5173', undefined)).toBe(false)
  })
})
