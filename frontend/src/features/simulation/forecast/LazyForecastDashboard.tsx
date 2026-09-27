import { lazy, Suspense } from 'react'
import type { ForecastDashboardProps } from './ForecastDashboard'

const LazyForecastDashboard = lazy(() => import('./ForecastDashboard'))

export function LazyForecastDashboardBoundary(props: ForecastDashboardProps) {
  return (
    <Suspense fallback={<div>正在加载预测…</div>}>
      <LazyForecastDashboard {...props} />
    </Suspense>
  )
}

export default LazyForecastDashboardBoundary
