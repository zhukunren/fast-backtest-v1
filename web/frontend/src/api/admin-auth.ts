export const ADMIN_TOKEN_STORAGE_KEY = 'strategy-library:admin-token'

export function readStoredAdminToken(): string {
  try {
    return window.sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY) || ''
  } catch {
    return ''
  }
}

export function storeAdminToken(value: string): void {
  try {
    if (value) window.sessionStorage.setItem(ADMIN_TOKEN_STORAGE_KEY, value)
    else window.sessionStorage.removeItem(ADMIN_TOKEN_STORAGE_KEY)
  } catch {
    // Session persistence is optional in restricted browser contexts.
  }
}

export function withAdminToken(
  headers: Record<string, string>,
  token = readStoredAdminToken(),
): Record<string, string> {
  return token ? { ...headers, 'X-Web-Admin-Token': token } : headers
}
