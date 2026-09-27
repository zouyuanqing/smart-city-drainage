/**
 * 根组件 — 路由与全局 Layout
 */

import { lazy, Suspense, useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppLayout } from '@components/layout/AppLayout'
import { ErrorBoundary, Loading } from '@components/common'
import { LandingPage } from '@pages/LandingPage'
import { LoginPage } from '@pages/LoginPage'
import { ProtectedRoute } from '@/auth/RouteGuard'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'

const Dashboard = lazy(() =>
  import('@/pages/Dashboard').then((module) => ({ default: module.Dashboard }))
)
const MapView = lazy(() =>
  import('@/pages/MapView').then((module) => ({ default: module.MapView }))
)
const VideoMonitor = lazy(() =>
  import('@/pages/VideoMonitor').then((module) => ({ default: module.VideoMonitor }))
)
const AlertCenter = lazy(() =>
  import('@/pages/AlertCenter').then((module) => ({ default: module.AlertCenter }))
)
const SettingsPage = lazy(() =>
  import('@/pages/SettingsPage').then((module) => ({ default: module.SettingsPage }))
)
const SimulationPage = lazy(() => import('@/pages/SimulationPage'))

function AuthenticatedPage({
  children,
  requiredRole,
}: {
  children: React.ReactNode
  requiredRole?: 'viewer' | 'operator' | 'admin'
}) {
  return (
    <ProtectedRoute requiredRole={requiredRole}>
      <AppLayout>
        <ErrorBoundary>
          <Suspense fallback={<Loading tip="Loading module…" />}>{children}</Suspense>
        </ErrorBoundary>
      </AppLayout>
    </ProtectedRoute>
  )
}

export default function App() {
  useEffect(() => {
    document.documentElement.classList.add('dark')
  }, [])

  useKeyboardShortcuts()

  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/dashboard"
        element={
          <AuthenticatedPage>
            <Dashboard />
          </AuthenticatedPage>
        }
      />
      <Route
        path="/map"
        element={
          <AuthenticatedPage>
            <MapView />
          </AuthenticatedPage>
        }
      />
      <Route
        path="/simulation"
        element={
          <AuthenticatedPage>
            <SimulationPage />
          </AuthenticatedPage>
        }
      />
      <Route
        path="/video"
        element={
          <AuthenticatedPage requiredRole="operator">
            <VideoMonitor />
          </AuthenticatedPage>
        }
      />
      <Route
        path="/alerts"
        element={
          <AuthenticatedPage requiredRole="operator">
            <AlertCenter />
          </AuthenticatedPage>
        }
      />
      <Route
        path="/settings"
        element={
          <AuthenticatedPage requiredRole="admin">
            <SettingsPage />
          </AuthenticatedPage>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
