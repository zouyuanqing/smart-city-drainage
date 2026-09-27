import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { getStoredAccessToken, hasRequiredRole, type AppRole } from './index'

export interface ProtectedRouteProps {
  children: ReactNode
  requiredRole?: AppRole
}

/** Authenticated route boundary; the API remains the final RBAC authority. */
export function ProtectedRoute({ children, requiredRole = 'viewer' }: ProtectedRouteProps) {
  const location = useLocation()
  const token = getStoredAccessToken()
  if (!token) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  if (!hasRequiredRole(requiredRole)) {
    return <Navigate to="/dashboard" replace state={{ forbidden: true, from: location.pathname }} />
  }
  return <>{children}</>
}

export default ProtectedRoute
