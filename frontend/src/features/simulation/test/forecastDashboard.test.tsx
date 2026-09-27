import { render, screen, waitFor } from '@testing-library/react'
import { act } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ForecastDashboard from '@/features/simulation/forecast/ForecastDashboard'
import {
  applyForecastEvent,
  sortRiskItems,
  topRiskItems,
} from '@/features/simulation/forecast/ranking'
import { parseForecastSse } from '@/features/simulation/forecast/stream'
import type {
  ForecastAlert,
  ForecastEvent,
  ForecastRiskRankingResponse,
  ForecastSnapshot,
} from '@/features/simulation/forecast/types'

const streamMock = vi.hoisted(() => ({ useHydraulicForecastStream: vi.fn() }))
vi.mock('@/features/simulation/forecast/stream', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/simulation/forecast/stream')>()),
  useHydraulicForecastStream: streamMock.useHydraulicForecastStream,
}))

const alert = (overrides: Partial<ForecastAlert> = {}): ForecastAlert => ({
  id: 'a-low',
  type: 'flood_forecast',
  status: 'open',
  phase: 'predicted',
  target: { type: 'node', id: 'node-low' },
  expected_at: '2026-09-24T10:30:00Z',
  lead_time_minutes: 30,
  measurements: { depth_m: 0.2 },
  confidence: 0.8,
  confidence_method: 'ensemble_exceedance_frequency',
  risk_score: 50,
  version: 1,
  ...overrides,
})

const snapshot = (alerts: ForecastAlert[]): ForecastSnapshot => ({
  schema_version: 1,
  data_state: 'ready',
  network_id: 'network-1',
  nowcast_run: null,
  alerts,
  age_seconds: 10,
  server_time: '2026-09-24T10:00:00Z',
})

const riskRanking = (alerts: ForecastAlert[]): ForecastRiskRankingResponse => ({
  schema_version: 1,
  data_state: 'ready',
  network_id: 'network-1',
  nowcast_run_id: 'run-1',
  risk_policy_version: 'policy-v1',
  items: alerts.map((item) => ({
    ...item,
    score_components: { confidence: item.confidence, severity: 1, urgency: 1, impact: 1 },
    policy_version: 'policy-v1',
  })),
  server_time: '2026-09-24T10:00:00Z',
})

describe('Forecast Dashboard', () => {
  beforeEach(() => streamMock.useHydraulicForecastStream.mockReset())

  it('renders an explicit empty state', async () => {
    render(
      <ForecastDashboard
        networkId="network-1"
        token="token"
        fetchSnapshot={async () => snapshot([])}
        fetchRiskRanking={async (networkId) => ({ ...riskRanking([]), network_id: networkId })}
      />
    )
    expect(await screen.findByTestId('forecast-empty')).toHaveTextContent('暂无风险预警')
    expect(screen.getByText('未来 2 小时暂无风险预警')).toBeInTheDocument()
  })

  it('orders risk by score descending and limits the Top-N result', () => {
    const items = [
      alert({ id: 'b', risk_score: 70 }),
      alert({ id: 'a', risk_score: 90 }),
      alert({ id: 'c', risk_score: 50 }),
    ]
    expect(topRiskItems(items, 2).map((item) => item.id)).toEqual(['a', 'b'])
    expect(sortRiskItems([items[0], items[1]]).map((item) => item.id)).toEqual(['a', 'b'])
  })

  it('renders distinct predicted/observed labels and applies an observed SSE update', async () => {
    const initial = alert({ risk_score: 82 })
    render(
      <ForecastDashboard
        networkId="network-1"
        token="token"
        fetchSnapshot={async () => snapshot([initial])}
        fetchRiskRanking={async () => riskRanking([initial])}
      />
    )
    expect(await screen.findByLabelText('预计')).toHaveAttribute('data-phase', 'predicted')

    const streamOptions = streamMock.useHydraulicForecastStream.mock.calls[0][0]
    const event: ForecastEvent = {
      schema_version: 1,
      event_id: 'event-2',
      sequence: 2,
      event_type: 'alert.observed',
      occurred_at: '2026-09-24T10:01:00Z',
      network_id: 'network-1',
      nowcast_run_id: 'run-1',
      simulation_id: 'simulation-1',
      alert: { ...initial, phase: 'observed', risk_score: 88, version: 2 },
    }
    act(() => streamOptions.onEvent(event))

    await waitFor(() =>
      expect(screen.getByLabelText('已发生')).toHaveAttribute('data-phase', 'observed')
    )
    expect(screen.getByText(/88\.0 分/)).toBeInTheDocument()
    expect(applyForecastEvent([initial], event)[0].phase).toBe('observed')
  })

  it('does not commit a previous network snapshot after a deferred switch', async () => {
    let resolveA!: (value: ForecastSnapshot) => void
    let resolveB!: (value: ForecastSnapshot) => void
    const pendingA = new Promise<ForecastSnapshot>((resolve) => {
      resolveA = resolve
    })
    const pendingB = new Promise<ForecastSnapshot>((resolve) => {
      resolveB = resolve
    })
    const snapshotFor = (networkId: string): ForecastSnapshot => ({
      ...snapshot([]),
      network_id: networkId,
      alerts: [alert({ id: `${networkId}-alert`, target: { type: 'node', id: networkId } })],
    })
    const view = render(
      <ForecastDashboard
        networkId="network-a"
        token="token"
        fetchSnapshot={() => pendingA}
        fetchRiskRanking={async (networkId) => ({ ...riskRanking([]), network_id: networkId })}
      />
    )
    view.rerender(
      <ForecastDashboard
        networkId="network-b"
        token="token"
        fetchSnapshot={() => pendingB}
        fetchRiskRanking={async (networkId) => ({ ...riskRanking([]), network_id: networkId })}
      />
    )
    await act(async () => {
      resolveB(snapshotFor('network-b'))
      await pendingB
    })
    await waitFor(() => expect(screen.getByText(/network-b/)).toBeInTheDocument())
    await act(async () => {
      resolveA(snapshotFor('network-a'))
      await pendingA
    })
    expect(screen.queryByText('network-a')).not.toBeInTheDocument()
  })

  it('parses the canonical forecast SSE frame', () => {
    const frame = [
      'id: 1842',
      'event: forecast',
      'data: {"schema_version":1,"event_id":"event-1","sequence":1842,',
      'data: "event_type":"alert.created","occurred_at":"2026-09-24T10:02:11Z",',
      'data: "network_id":"network-1","nowcast_run_id":"run-1","simulation_id":"sim-1",',
      `data: "alert":${JSON.stringify(alert())}}`,
      '',
    ].join('\n')
    expect(parseForecastSse(frame)?.event?.event_id).toBe('event-1')
  })
})
