import { lazy, Suspense } from 'react'
import { useTranslation } from 'react-i18next'
import { getStoredAccessToken } from '@/auth'
import { ForecastStreamBoundary } from '@/features/simulation/integration'

const LazyForecastDashboard = lazy(() => import('@/features/simulation/forecast/ForecastDashboard'))

export interface ForecastRiskCardProps {
  networkId: string | null
  topN?: number
  withProvider?: boolean
}

/** Dashboard owns no forecast reducer or stream; it only supplies the selected network. */
export function ForecastRiskCard({
  networkId,
  topN = 5,
  withProvider = true,
}: ForecastRiskCardProps) {
  const { t } = useTranslation()
  const token = getStoredAccessToken() ?? ''
  if (!networkId) {
    return (
      <section
        className="bg-cyber-dark border border-cyber-border rounded-lg p-3"
        data-testid="forecast-risk-card"
      >
        <p className="text-text-muted text-xs">{t('forecast.networkRequired')}</p>
      </section>
    )
  }
  const body = (
    <Suspense fallback={<p className="text-text-muted text-xs">{t('forecast.loading')}</p>}>
      <LazyForecastDashboard networkId={networkId} token={token} topN={topN} />
    </Suspense>
  )
  return (
    <section
      className="bg-cyber-dark border border-cyber-border rounded-lg p-3"
      data-testid="forecast-risk-card"
    >
      <div className="panel-title mb-3">{t('forecast.title')}</div>
      {withProvider ? (
        <ForecastStreamBoundary networkId={networkId}>{body}</ForecastStreamBoundary>
      ) : (
        body
      )}
    </section>
  )
}

export default ForecastRiskCard
