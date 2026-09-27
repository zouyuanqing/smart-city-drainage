import { describe, expect, it } from 'vitest'
import { canAccessRoute, getRouteAccess } from './routeAccess'

describe('route access matrix', () => {
  it('keeps simulation read-only for viewers and reserves settings for admins', () => {
    expect(getRouteAccess('/simulation')).toEqual({ path: '/simulation', requiredRole: 'viewer' })
    expect(canAccessRoute('/simulation', 'viewer')).toBe(true)
    expect(canAccessRoute('/simulation', 'operator')).toBe(true)
    expect(canAccessRoute('/simulation', 'admin')).toBe(true)
    expect(canAccessRoute('/settings', 'operator')).toBe(false)
    expect(canAccessRoute('/settings', 'admin')).toBe(true)
  })

  it('fails closed for missing or unknown roles', () => {
    expect(canAccessRoute('/dashboard', null)).toBe(false)
    expect(canAccessRoute('/dashboard', 'root' as never)).toBe(false)
    expect(canAccessRoute('/unknown', 'admin')).toBe(false)
  })
})
