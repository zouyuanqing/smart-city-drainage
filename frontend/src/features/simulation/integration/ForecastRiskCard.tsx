import { Suspense } from 'react'
import { useTranslation } from 'react-i18next'
import { ForecastStreamBoundary } from './ForecastStreamBoundary'
import { ForecastDashboardBoundary } from './ForecastStreamBoundary'

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
  return (
    <section
      className="bg-cyber-dark border border-cyber-border rounded-lg p-3"
      data-testid="forecast-risk-card"
    >
      <div className="panel-title mb-3">{t('forecast.title')}</div>
      {withProvider ? (
        <ForecastStreamBoundary networkId={networkId}>
          <Suspense fallback={<p className="text-text-muted text-xs">{t('forecast.loading')}</p>}>
            <ForecastDashboardBoundary networkId={networkId} topN={topN} />
          </Suspense>
        </ForecastStreamBoundary>
      ) : (
        <ForecastDashboardBoundary networkId={networkId} topN={topN} />
      )}
    </section>
  )
}

export default ForecastRiskCard
