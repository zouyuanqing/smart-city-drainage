/**
 * 根组件 — 路由与全局 Layout
 */

import { Routes, Route, Navigate } from 'react-router-dom'
import { AppLayout } from '@components/layout/AppLayout'
import { ErrorBoundary } from '@components/common'
import { Dashboard } from '@pages/Dashboard'
import { LandingPage } from '@pages/LandingPage'
import { LoginPage } from '@pages/LoginPage'
import { MapView } from '@pages/MapView'
import { VideoMonitor } from '@pages/VideoMonitor'
import { AlertCenter } from '@pages/AlertCenter'
import { SettingsPage } from '@pages/SettingsPage'
import { useEffect } from 'react'
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts'

// 路由守卫: 未登录重定向
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = localStorage.getItem('scn_access_token')
  if (!token) {
    return <Navigate to="/login" replace />
  }
  return <>{children}</>
}

export default function App() {
  // 全局暗黑模式
  useEffect(() => {
    document.documentElement.classList.add('dark')
  }, [])

  // 全局键盘快捷键
  useKeyboardShortcuts()

  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <AppLayout>
              <ErrorBoundary>
                <Dashboard />
              </ErrorBoundary>
            </AppLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/map"
        element={
          <ProtectedRoute>
            <AppLayout>
              <ErrorBoundary>
                <MapView />
              </ErrorBoundary>
            </AppLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/video"
        element={
          <ProtectedRoute>
            <AppLayout>
              <ErrorBoundary>
                <VideoMonitor />
              </ErrorBoundary>
            </AppLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/alerts"
        element={
          <ProtectedRoute>
            <AppLayout>
              <ErrorBoundary>
                <AlertCenter />
              </ErrorBoundary>
            </AppLayout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/settings"
        element={
          <ProtectedRoute>
            <AppLayout>
              <ErrorBoundary>
                <SettingsPage />
              </ErrorBoundary>
            </AppLayout>
          </ProtectedRoute>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
