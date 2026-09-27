export type AppRole = 'admin' | 'operator' | 'viewer'

const ROLE_RANK: Record<AppRole, number> = {
  viewer: 1,
  operator: 2,
  admin: 3,
}

export function isAppRole(value: unknown): value is AppRole {
  return value === 'viewer' || value === 'operator' || value === 'admin'
}

export function getStoredRole(): AppRole | null {
  try {
    const value = globalThis.localStorage?.getItem('scn_user_role')
    return isAppRole(value) ? value : null
  } catch {
    return null
  }
}

export function hasRequiredRole(requiredRole: AppRole, role = getStoredRole()): boolean {
  if (!isAppRole(requiredRole) || !role) return false
  return ROLE_RANK[role] >= ROLE_RANK[requiredRole]
}

export function canWriteSimulation(role = getStoredRole()): boolean {
  return hasRequiredRole('operator', role)
}

export function canAdminister(role = getStoredRole()): boolean {
  return hasRequiredRole('admin', role)
}

export function getStoredAccessToken(): string | null {
  try {
    return globalThis.localStorage?.getItem('scn_access_token') ?? null
  } catch {
    return null
  }
}
