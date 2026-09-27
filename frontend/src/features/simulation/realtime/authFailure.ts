/** Shared terminal handling for bearer-authenticated fetch/SSE transports. */
export type SseAuthorizationStatus = 401 | 403

export function isSseAuthorizationFailure(status: number): status is SseAuthorizationStatus {
  return status === 401 || status === 403
}

export function clearStoredAuthentication(): void {
  try {
    globalThis.localStorage?.removeItem('scn_access_token')
    globalThis.localStorage?.removeItem('scn_user')
    globalThis.localStorage?.removeItem('scn_user_role')
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

export function redirectToLogin(): void {
  if (typeof window === 'undefined' || window.location.pathname === '/login') return
  try {
    window.location.assign('/login')
  } catch {
    // Test environments may expose a non-navigable location object.
  }
}

/**
 * 401 invalidates the session; 403 is a terminal authorization result and must
 * not be converted into an endlessly retried transport error.
 */
export function handleSseAuthorizationFailure(
  status: SseAuthorizationStatus,
  options: { clearSession?: boolean; redirect?: boolean } = {}
): void {
  if (status !== 401) return
  if (options.clearSession !== false) clearStoredAuthentication()
  if (options.redirect !== false) redirectToLogin()
}

/** SSE comments and blank dispatches are keepalives, not protocol events. */
export function isSseKeepaliveBlock(block: string): boolean {
  const lines = block.split(/\r?\n/).filter((line) => line.trim().length > 0)
  return lines.length === 0 || lines.every((line) => line.startsWith(':'))
}
