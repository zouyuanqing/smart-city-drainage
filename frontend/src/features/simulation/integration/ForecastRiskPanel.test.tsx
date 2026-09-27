import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ForecastRiskPanel } from './ForecastRiskPanel'

const networkMock = vi.hoisted(() => ({
  state: {
    networks: [] as Array<{ id: string; name: string; network_revision_id: string }>,
    selectedNetwork: null as { id: string; name: string; network_revision_id: string } | null,
    selectedNetworkId: null as string | null,
    loading: false,
    error: null as string | null,
    setSelectedNetworkId: vi.fn(),
    refresh: vi.fn(),
  },
}))
vi.mock('./useHydraulicNetworks', () => ({ useHydraulicNetworks: () => networkMock.state }))

vi.mock('@/components/layout/ForecastRiskCard', () => ({
  ForecastRiskCard: ({ networkId }: { networkId: string | null }) => (
    <div data-testid="forecast-risk-card">{networkId ?? 'no-network'}</div>
  ),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

describe('ForecastRiskPanel', () => {
  it('passes the selected network to the lazy forecast card', () => {
    networkMock.state.networks = [
      { id: 'network-1', name: 'Network 1', network_revision_id: 'revision-1' },
    ]
    networkMock.state.selectedNetworkId = 'network-1'
    render(<ForecastRiskPanel compact />)
    expect(screen.getByTestId('forecast-risk-card')).toHaveTextContent('network-1')
    expect(screen.getByTestId('forecast-network-select')).toBeInTheDocument()
  })
})
