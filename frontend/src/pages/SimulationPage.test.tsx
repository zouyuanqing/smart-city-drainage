import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SimulationPage from './SimulationPage'

const ids = vi.hoisted(() => ({
  NETWORK_ID: '11111111-1111-4111-8111-111111111111',
  REVISION_ID: '22222222-2222-4222-8222-222222222222',
  TASK_ID: '55555555-5555-4555-8555-555555555555',
  PARAMETER_SET_ID: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
}))

const mocks = vi.hoisted(() => ({
  workspace: {
    networks: [{ id: ids.NETWORK_ID, network_revision_id: ids.REVISION_ID, name: 'Test network' }],
    selectedNetwork: {
      id: ids.NETWORK_ID,
      network_revision_id: ids.REVISION_ID,
      name: 'Test network',
      parameter_set_id: ids.PARAMETER_SET_ID,
    },
    selectedNetworkId: ids.NETWORK_ID,
    selectNetwork: vi.fn(),
    networkLoading: false,
    networkError: null,
    mapData: null,
    mapLoading: false,
    mapError: null,
    frameIndex: {
      frame_count: 2,
      frame_interval_seconds: 300,
      frames: [
        { frame_index: 0, frame_time: '2026-09-24T00:00:00Z', checksum: 'a'.repeat(64) },
        { frame_index: 1, frame_time: '2026-09-24T00:05:00Z', checksum: 'a'.repeat(64) },
      ],
    },
    currentFrame: null,
    frameLoading: false,
    frameError: null,
    selectedSimulationId: ids.TASK_ID,
    selectSimulation: vi.fn(),
    layerVisibility: { nodes: true, pipes: true, catchments: true, ponding: true },
    setLayerVisibility: vi.fn(),
    colorMetric: 'fill_ratio',
    setColorMetric: vi.fn(),
    renderStats: null,
    setRenderStats: vi.fn(),
    loadFrame: vi.fn(async () => undefined),
    prefetchFrame: vi.fn(async () => undefined),
    refreshMap: vi.fn(async () => undefined),
  },
  taskController: {
    tasks: [],
    filteredTasks: [],
    activeTasks: [],
    statusFilter: 'all',
    setStatusFilter: vi.fn(),
    loading: false,
    refreshing: false,
    loadingMore: false,
    hasNextPage: false,
    error: null,
    actionError: null,
    isStale: false,
    lastSyncedAt: null,
    streamStatus: 'connected',
    idempotencyReceipt: { state: 'idle', key: null, taskId: null },
    pendingCancellationIds: [],
    pendingRetryIds: [],
    submitting: false,
    refresh: vi.fn(async () => undefined),
    loadMore: vi.fn(async () => undefined),
    refreshTask: vi.fn(async () => null),
    submit: vi.fn(async () => {
      throw new Error('not used')
    }),
    cancel: vi.fn(async () => {
      throw new Error('not used')
    }),
    retryFailed: vi.fn(async () => {
      throw new Error('not used')
    }),
    clearActionError: vi.fn(),
    reconnectStream: vi.fn(),
  },
  useSimulationTask: vi.fn(),
  rainfallList: vi.fn(async () => [
    {
      id: '44444444-4444-4444-8444-444444444444',
      name: 'Fixture rainfall',
      return_period_years: 10,
      duration_seconds: 86_400,
      step_seconds: 300,
      source_type: 'synthetic',
      algorithm: 'synthetic-design-chicago-v1',
      random_seed: 1,
      samples: [],
    },
  ]),
  rainfallCreate: vi.fn(async () => ({ id: '66666666-6666-4666-8666-666666666666' })),
  panelProps: null as Record<string, unknown> | null,
  editorProps: null as Record<string, unknown> | null,
}))

const { NETWORK_ID } = ids

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string; [key: string]: unknown }) => {
      let value = options?.defaultValue ?? key
      for (const [name, replacement] of Object.entries(options ?? {})) {
        if (name === 'defaultValue' || replacement === undefined) continue
        value = value.split(`{{${name}}}`).join(String(replacement))
      }
      return value
    },
  }),
}))

vi.mock('@/auth', () => ({
  canWriteSimulation: () => true,
  getStoredAccessToken: () => 'jwt-token',
  getStoredRole: () => 'operator',
}))

vi.mock('@/features/simulation/integration/useHydraulicWorkspace', () => ({
  useHydraulicWorkspace: () => mocks.workspace,
}))

vi.mock('@/features/simulation/hooks/useSimulationTask', () => ({
  useSimulationTask: mocks.useSimulationTask,
}))

vi.mock('@/features/simulation/integration/rainfallApi', () => ({
  rainfallScenarioApi: { list: mocks.rainfallList, create: mocks.rainfallCreate },
}))

vi.mock('@/features/simulation/integration/observedApi', () => ({
  loadObservedComparison: vi.fn(async () => ({ status: 'not_ready', pairs: [] })),
}))

vi.mock('@/features/simulation/integration/comparisonApi', () => ({
  loadComparisonSeries: vi.fn(async () => []),
}))

vi.mock('@/features/simulation/api', () => ({
  simulationApi: {
    compare: vi.fn(),
    frame: vi.fn(),
  },
}))

vi.mock('@/features/simulation/integration/HydraulicMapPanel', () => ({
  HydraulicMapPanel: () => <div data-testid="hydraulic-map" />,
}))

vi.mock('@/features/simulation/charts/ObservedPredictedChart', () => ({
  ObservedPredictedChart: () => <div data-testid="observed-chart" />,
}))

vi.mock('@/features/simulation/integration/ComparisonWorkbench', () => ({
  ComparisonWorkbench: () => <div data-testid="comparison-workbench" />,
}))

vi.mock('@/components/layout/ForecastRiskCard', () => ({
  ForecastRiskCard: () => <div data-testid="forecast-risk-card" />,
}))

vi.mock('@/features/simulation/tasks/TaskPanel', () => ({
  SimulationTaskPanel: (props: Record<string, unknown>) => {
    mocks.panelProps = props
    return <div data-testid="simulation-task-panel" />
  },
}))

vi.mock('@/features/simulation/timeline/TimelineControl', () => ({
  TimelineControl: () => <div data-testid="timeline-control" />,
}))

vi.mock('@/features/simulation/timeline/DepthLegend', () => ({
  DepthLegend: () => <div data-testid="depth-legend" />,
}))

vi.mock('@/features/simulation/rainfall/RainfallScenarioEditor', () => ({
  RainfallScenarioEditor: (props: Record<string, unknown>) => {
    mocks.editorProps = props
    return (
      <button
        type="button"
        data-testid="rainfall-create"
        onClick={() => {
          const onCreateScenario = props.onCreateScenario as (command: unknown) => Promise<void>
          void onCreateScenario({ request: { network_id: NETWORK_ID }, idempotencyKey: 'key' })
        }}
      />
    )
  },
}))

function pendingSseResponse(): Response {
  return {
    ok: true,
    status: 200,
    body: { getReader: () => ({ read: vi.fn(() => new Promise<never>(() => undefined)) }) },
  } as unknown as Response
}

afterEach(() => {
  vi.unstubAllGlobals()
  mocks.panelProps = null
  mocks.editorProps = null
})

describe('SimulationPage composition', () => {
  it('mounts one stream owner, one task controller, and a usable rainfall create boundary', async () => {
    const values = new Map<string, string>([['scn_access_token', 'jwt-token']])
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    })
    mocks.useSimulationTask.mockReturnValue(mocks.taskController)
    const fetchMock = vi.fn(async () => pendingSseResponse())
    vi.stubGlobal('fetch', fetchMock)

    render(<SimulationPage />)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(mocks.useSimulationTask).toHaveBeenCalled()
    expect(
      mocks.useSimulationTask.mock.results.every((result) => result.value === mocks.taskController)
    ).toBe(true)
    expect(mocks.panelProps?.controller).toBe(mocks.taskController)
    expect(mocks.editorProps?.networkId).toBe(NETWORK_ID)
    expect(mocks.editorProps?.idempotencyKey).toEqual(expect.any(String))

    await waitFor(() => expect(screen.getByTestId('simulation-submit')).not.toBeDisabled())
    mocks.taskController.submit.mockResolvedValue({} as never)
    fireEvent.click(screen.getByTestId('simulation-submit'))
    expect(mocks.taskController.submit).toHaveBeenCalledWith(
      expect.objectContaining({ parameter_set_id: ids.PARAMETER_SET_ID })
    )

    fireEvent.click(screen.getByTestId('rainfall-create'))
    await waitFor(() => expect(mocks.rainfallCreate).toHaveBeenCalledTimes(1))
  })
})
