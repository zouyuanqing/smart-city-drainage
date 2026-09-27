import type { AppRole } from './roles'
import { hasRequiredRole } from './roles'

export type RouteRole = AppRole

export interface RouteAccessRule {
  path: string
  requiredRole: RouteRole
}

export const AUTHENTICATED_ROUTE_ACCESS: readonly RouteAccessRule[] = [
  { path: '/dashboard', requiredRole: 'viewer' },
  { path: '/map', requiredRole: 'viewer' },
  { path: '/simulation', requiredRole: 'viewer' },
  { path: '/video', requiredRole: 'operator' },
  { path: '/alerts', requiredRole: 'operator' },
  { path: '/settings', requiredRole: 'admin' },
]

export function getRouteAccess(pathname: string): RouteAccessRule | null {
  return (
    AUTHENTICATED_ROUTE_ACCESS.find(
      (rule) => pathname === rule.path || pathname.startsWith(`${rule.path}/`)
    ) ?? null
  )
}

export function canAccessRoute(
  pathname: string,
  role: Parameters<typeof hasRequiredRole>[1] = undefined
): boolean {
  const rule = getRouteAccess(pathname)
  return rule ? hasRequiredRole(rule.requiredRole, role) : false
}
