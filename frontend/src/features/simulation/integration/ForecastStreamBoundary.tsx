import { lazy, Suspense } from 'react'
import { useTranslation } from 'react-i18next'
import { getStoredAccessToken } from '@/auth'
import { ForecastStreamProvider } from '../forecast/ForecastStreamProvider'
import { HydraulicRealtimeProvider } from '../realtime/HydraulicRealtimeProvider'

const LazyForecastDashboard = lazy(() => import('../forecast/ForecastDashboard'))

export interface ForecastStreamBoundaryProps {
  networkId: string
  children: React.ReactNode
  /** Optional transport injection for integration hosts and deterministic tests. */
  fetchImpl?: typeof fetch
}

/** Keeps the authenticated hydraulic connection at one route/layout boundary. */
export function ForecastStreamBoundary({
  networkId,
  children,
  fetchImpl,
}: ForecastStreamBoundaryProps) {
  const token = getStoredAccessToken() ?? ''
  return (
    <HydraulicRealtimeProvider networkId={networkId} token={token} fetchImpl={fetchImpl}>
      <ForecastStreamProvider networkId={networkId} token={token} fetchImpl={fetchImpl}>
        {children}
      </ForecastStreamProvider>
    </HydraulicRealtimeProvider>
  )
}

export interface ForecastDashboardBoundaryProps {
  networkId: string
  topN?: number
}

export function ForecastDashboardBoundary({ networkId, topN = 5 }: ForecastDashboardBoundaryProps) {
  const { t } = useTranslation()
  const token = getStoredAccessToken() ?? ''
  return (
    <Suspense fallback={<p className="text-text-muted text-xs">{t('forecast.loading')}</p>}>
      <LazyForecastDashboard networkId={networkId} token={token} topN={topN} />
    </Suspense>
  )
}

export default ForecastStreamBoundary
