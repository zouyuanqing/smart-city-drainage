import { useMemo } from 'react'
import { Select, Spin, Tag } from 'antd'
import { useTranslation } from 'react-i18next'
import { ForecastRiskCard } from '@/components/layout/ForecastRiskCard'
import { useHydraulicNetworks } from './useHydraulicNetworks'

export interface ForecastRiskPanelProps {
  compact?: boolean
}

/** Dashboard forecast/risk integration with network selection kept outside the reducer. */
export function ForecastRiskPanel({ compact = false }: ForecastRiskPanelProps) {
  const { t } = useTranslation()
  const networkState = useHydraulicNetworks()
  const options = useMemo(
    () =>
      networkState.networks.map((network) => ({
        value: network.id,
        label: network.name ?? network.id,
      })),
    [networkState.networks]
  )

  if (networkState.loading) {
    return (
      <div className="flex items-center justify-center p-3" data-testid="forecast-risk-loading">
        <Spin size="small" />
      </div>
    )
  }

  return (
    <div className={compact ? 'space-y-2' : 'space-y-3'} data-testid="forecast-risk-panel">
      <div className="flex items-center gap-2">
        <span className="text-text-muted text-xs font-mono whitespace-nowrap">
          {t('network.label')}
        </span>
        <Select
          size="small"
          className="min-w-0 flex-1"
          value={networkState.selectedNetworkId ?? undefined}
          placeholder={t('network.placeholder')}
          options={options}
          onChange={networkState.setSelectedNetworkId}
          loading={networkState.loading}
          aria-label={t('network.label')}
          data-testid="forecast-network-select"
        />
        {networkState.selectedNetwork?.simulation_ready ? (
          <Tag color="green">{t('network.ready')}</Tag>
        ) : null}
      </div>
      {networkState.error ? (
        <p className="text-neon-red text-xs" role="alert">
          {networkState.error}
        </p>
      ) : null}
      <ForecastRiskCard networkId={networkState.selectedNetworkId} withProvider={true} />
    </div>
  )
}

export default ForecastRiskPanel
