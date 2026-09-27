import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import App from '@/App'
import i18n from '@/i18n'
import { ComparisonWorkbench } from '@/features/simulation/integration/ComparisonWorkbench'
import { buildObservedPredictedOption } from '@/features/simulation/charts/chartOptions'
import { RainfallScenarioEditor } from '@/features/simulation/rainfall/RainfallScenarioEditor'
import { createDefaultRainfallScenario } from '@/features/simulation/rainfall/model'
import { TimelineControl } from '@/features/simulation/timeline/TimelineControl'
import type {
  AlignedSimulationPoint,
  SimulationComparisonResponse,
} from '@/features/simulation/comparison'
import type { SimulationTask } from '@/features/simulation/model/types'

const echartsSpies = vi.hoisted(() => ({
  init: vi.fn(),
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
}))

vi.mock('echarts/core', () => ({
  init: echartsSpies.init,
  use: vi.fn(),
}))

vi.mock('@/services/api', () => ({
  default: {},
  modelAPI: {
    getStatus: vi.fn().mockResolvedValue({ data: { is_ready: false, active_version: null } }),
  },
}))

vi.mock('@/pages/Dashboard', async () => {
  const { useLocation } = await import('react-router-dom')
  return {
    Dashboard: () => {
      const location = useLocation()
      return <output data-testid="app-dashboard-route">{location.pathname}</output>
    },
  }
})

vi.mock('@/pages/SimulationPage', () => ({
  default: () => <div data-testid="app-simulation-route">Simulation route ready</div>,
}))

vi.mock('@/pages/SettingsPage', async () => {
  const { useLocation } = await import('react-router-dom')
  function SettingsPageProbe() {
    const location = useLocation()
    return <output data-testid="app-settings-route">{location.pathname}</output>
  }
  return { default: SettingsPageProbe }
})

vi.mock('@/features/simulation/rainfall/RainfallChart', () => ({
  RainfallChart: ({ samples }: { samples: readonly unknown[] }) => (
    <div data-testid="rainfall-chart" data-point-count={samples.length} />
  ),
}))

const NETWORK_ID = '11111111-1111-4111-8111-111111111111'
const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const MANIFEST_ID = '33333333-3333-4333-8333-333333333333'
const RAINFALL_ID = '44444444-4444-4444-8444-444444444444'
const HASH = 'a'.repeat(64)
const BASELINE_ID = '55555555-5555-4555-8555-555555555555'
const CANDIDATE_ID = '66666666-6666-4666-8666-666666666666'
const BASELINE_ATTEMPT = '77777777-7777-4777-8777-777777777777'
const CANDIDATE_ATTEMPT = '88888888-8888-4888-8888-888888888888'

function task(id: string, publishedAttemptId: string): SimulationTask {
  return {
    schema_version: 1,
    id,
    status: 'completed',
    progress: 100,
    attempt: 1,
    max_attempts: 3,
    network_id: NETWORK_ID,
    network_revision_id: REVISION_ID,
    target_manifest_id: MANIFEST_ID,
    target_manifest_sha256: HASH,
    rainfall_scenario_id: RAINFALL_ID,
    parameter_set_id: '99999999-9999-4999-8999-999999999999',
    solver_selection: 'auto',
    solver_id: 'py_mw',
    solver_version: 'solver-v1',
    published_attempt_id: publishedAttemptId,
    published_attempt_no: 1,
    frame_count: 2,
    created_at: '2026-09-24T00:00:00Z',
    started_at: '2026-09-24T00:00:00Z',
    finished_at: '2026-09-24T00:01:00Z',
  }
}

const baselineTask = task(BASELINE_ID, BASELINE_ATTEMPT)
const candidateTask = task(CANDIDATE_ID, CANDIDATE_ATTEMPT)

const comparison: SimulationComparisonResponse = {
  schema_version: 'simulation-comparison-v2',
  compatible: true,
  network_revision_id: REVISION_ID,
  target_manifest_id: MANIFEST_ID,
  target_manifest_sha256: HASH,
  frame_grid: {
    start_at: '2026-09-24T00:00:00Z',
    end_at: '2026-09-24T00:05:00Z',
    interval_seconds: 300,
    frame_count: 2,
  },
  baseline: {
    simulation_id: BASELINE_ID,
    published_attempt_id: BASELINE_ATTEMPT,
    published_attempt_no: 1,
  },
  candidate: {
    simulation_id: CANDIDATE_ID,
    published_attempt_id: CANDIDATE_ATTEMPT,
    published_attempt_no: 1,
  },
  metrics: [
    {
      key: 'max_node_depth_m',
      label: 'Max node depth',
      unit: 'm',
      baseline: 0.74,
      candidate: 0.61,
      absolute_delta: -0.13,
      relative_delta: -0.1757,
      better_direction: 'lower',
    },
  ],
}

const comparisonSeries: AlignedSimulationPoint[] = [
  {
    timestamp: '2026-09-24T00:00:00Z',
    timestamp_ms: Date.parse('2026-09-24T00:00:00Z'),
    baseline: 0.74,
    candidate: 0.61,
    delta: -0.13,
  },
  {
    timestamp: '2026-09-24T00:05:00Z',
    timestamp_ms: Date.parse('2026-09-24T00:05:00Z'),
    baseline: 0.76,
    candidate: 0.62,
    delta: -0.14,
  },
]

const timelineLabels = {
  title: 'Replay timeline',
  play: 'Play',
  pause: 'Pause',
  playbackSpeed: 'Playback speed',
  framesPerSecond: (speed: number) => `${speed} frames per second`,
  seek: 'Seek timeline',
  currentTime: 'Simulation time',
  framePosition: (index: number, total: number) => `Frame ${index + 1} / ${total}`,
  frameInterval: (minutes: number) => `${minutes} minutes per frame`,
  pageHidden: 'Page hidden',
  playbackUnavailable: 'Playback unavailable',
}

beforeAll(async () => {
  await i18n.changeLanguage('en')
})

beforeEach(() => {
  localStorage.setItem('scn_language', 'en')
  echartsSpies.init.mockReturnValue({
    setOption: echartsSpies.setOption,
    resize: echartsSpies.resize,
    dispose: echartsSpies.dispose,
  })
})

afterEach(async () => {
  cleanup()
  vi.clearAllMocks()
  localStorage.clear()
  await i18n.changeLanguage('en')
})

describe('frontend acceptance: App routes/i18n, editor, timeline, A/B and observed/predicted', () => {
  it('uses the real App route guard and i18n wiring for viewer and operator roles', async () => {
    localStorage.setItem('scn_access_token', 'viewer-token')
    localStorage.setItem('scn_user_role', 'viewer')
    const view = render(
      <MemoryRouter initialEntries={['/simulation']}>
        <App />
      </MemoryRouter>
    )

    expect(await screen.findByTestId('app-simulation-route')).toHaveTextContent(
      'Simulation route ready'
    )
    const englishNavigation = screen.getByTestId('nav-simulation').textContent
    expect(englishNavigation).toBeTruthy()
    await act(async () => {
      await i18n.changeLanguage('zh')
    })
    await waitFor(() =>
      expect(screen.getByTestId('nav-simulation')).not.toHaveTextContent(englishNavigation ?? '')
    )

    view.unmount()
    cleanup()
    localStorage.setItem('scn_access_token', 'operator-token')
    localStorage.setItem('scn_user_role', 'operator')
    render(
      <MemoryRouter initialEntries={['/settings']}>
        <App />
      </MemoryRouter>
    )
    expect(await screen.findByTestId('app-dashboard-route')).toHaveTextContent('/dashboard')
    expect(screen.queryByTestId('app-settings-route')).not.toBeInTheDocument()
  })

  it('surfaces typed rainfall context errors and keeps disabled editor controls inert', async () => {
    const onCreateScenario = vi.fn()
    const view = render(
      <RainfallScenarioEditor
        initialScenario={createDefaultRainfallScenario({
          duration_seconds: 3_600,
          step_seconds: 900,
        })}
        onCreateScenario={onCreateScenario}
      />
    )

    fireEvent.click(screen.getByTestId('rainfall-submit'))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/networkId/))
    expect(onCreateScenario).not.toHaveBeenCalled()
    expect(screen.getByTestId('rainfall-validation')).toHaveAttribute('data-valid', 'true')
    expect(screen.getByTestId('rainfall-submit')).not.toBeDisabled()

    view.rerender(
      <RainfallScenarioEditor
        initialScenario={createDefaultRainfallScenario({
          duration_seconds: 3_600,
          step_seconds: 900,
        })}
        onCreateScenario={onCreateScenario}
        disabled
      />
    )
    expect(screen.getByTestId('rainfall-name')).toBeDisabled()
    expect(screen.getByTestId('rainfall-duration-hours')).toBeDisabled()
    expect(screen.getByTestId('rainfall-submit')).toBeDisabled()
    fireEvent.click(screen.getByTestId('rainfall-submit'))
    fireEvent.click(screen.getByTestId('rainfall-preset-50'))
    expect(onCreateScenario).not.toHaveBeenCalled()
  })

  it('advances real 4x RAF frames, commits seeks and aborts active prefetches', () => {
    const onFrameChange = vi.fn()
    const onPlayingChange = vi.fn()
    const prefetchSignals: AbortSignal[] = []
    let now = 0
    let nextHandle = 1
    const callbacks = new Map<number, FrameRequestCallback>()
    const runtime = {
      now: () => now,
      requestFrame: (callback: FrameRequestCallback) => {
        const handle = nextHandle++
        callbacks.set(handle, callback)
        return handle
      },
      cancelFrame: (handle: number) => callbacks.delete(handle),
    }
    const flushFrame = () => {
      const pending = [...callbacks.values()]
      callbacks.clear()
      pending.forEach((callback) => callback(now))
    }
    const view = render(
      <TimelineControl
        frameCount={5}
        frameTimes={[
          '2026-09-24T00:00:00Z',
          '2026-09-24T00:05:00Z',
          '2026-09-24T00:10:00Z',
          '2026-09-24T00:15:00Z',
          '2026-09-24T00:20:00Z',
        ]}
        initialFrameIndex={1}
        labels={timelineLabels}
        onFrameChange={onFrameChange}
        onPlayingChange={onPlayingChange}
        prefetchEnabled
        prefetchFrame={(_index, signal) => {
          prefetchSignals.push(signal)
          return new Promise(() => undefined)
        }}
        runtime={runtime}
      />
    )

    const timeline = screen.getByTestId('simulation-frame-timeline')
    expect(prefetchSignals.some((signal) => !signal.aborted)).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '4 frames per second' }))
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    act(() => {
      now = 250
      flushFrame()
    })
    expect(timeline).toHaveAttribute('data-frame-index', '2')
    act(() => {
      now = 500
      flushFrame()
    })
    expect(timeline).toHaveAttribute('data-frame-index', '3')
    expect(onFrameChange).toHaveBeenLastCalledWith(3)

    fireEvent.keyDown(timeline, { key: 'End' })
    expect(timeline).toHaveAttribute('data-frame-index', '4')
    act(() => {
      now = 750
      flushFrame()
      now = 1_000
      flushFrame()
    })
    fireEvent.keyDown(timeline, { key: 'ArrowLeft', shiftKey: true })
    expect(timeline).toHaveAttribute('data-frame-index', '0')
    expect(onPlayingChange).toHaveBeenLastCalledWith(false)

    view.unmount()
    expect(prefetchSignals.every((signal) => signal.aborted)).toBe(true)
  })

  it('renders real A/B metrics/chart, switches modes and blocks incompatible difference', async () => {
    const onRefresh = vi.fn()
    const view = render(
      <ComparisonWorkbench
        tasks={[baselineTask, candidateTask]}
        baseline={baselineTask}
        candidate={candidateTask}
        comparison={comparison}
        series={comparisonSeries}
        onBaselineChange={vi.fn()}
        onCandidateChange={vi.fn()}
        onRefresh={onRefresh}
      />
    )

    expect(screen.getByTestId('comparison-kpis')).toHaveTextContent('2')
    expect(screen.getByTestId('comparison-metrics')).toHaveTextContent(/0\.13/)
    expect(screen.getByRole('img', { name: /comparison/i })).toBeInTheDocument()
    await waitFor(() => expect(echartsSpies.setOption).toHaveBeenCalled())
    const overlayOptionCount = echartsSpies.setOption.mock.calls.length
    fireEvent.click(screen.getByRole('button', { name: 'Δ' }))
    await waitFor(() =>
      expect(echartsSpies.setOption.mock.calls.length).toBeGreaterThan(overlayOptionCount)
    )
    fireEvent.click(screen.getByRole('button', { name: 'Refresh comparison' }))
    expect(onRefresh).toHaveBeenCalledTimes(1)

    view.rerender(
      <ComparisonWorkbench
        tasks={[baselineTask, candidateTask]}
        baseline={baselineTask}
        candidate={candidateTask}
        comparison={{ ...comparison, compatible: false, reason: 'frame grid mismatch' }}
        series={comparisonSeries}
        onBaselineChange={vi.fn()}
        onCandidateChange={vi.fn()}
        onRefresh={onRefresh}
      />
    )
    expect(screen.getByTestId('comparison-incompatible')).toHaveTextContent('frame grid mismatch')
    expect(screen.getByRole('button', { name: 'Δ' })).toBeDisabled()
  })

  it('keeps observed/predicted gaps, uncertainty and issue-time marks explicit', () => {
    const option = buildObservedPredictedOption(
      [
        {
          timestamp: '2026-09-24T00:00:00Z',
          observed: 0.2,
          predicted: 0.25,
          lower_bound: 0.2,
          upper_bound: 0.3,
        },
        {
          timestamp: '2026-09-24T00:05:00Z',
          observed: null,
          predicted: 0.4,
          lower_bound: null,
          upper_bound: 0.5,
        },
      ],
      {
        issueTime: '2026-09-24T00:00:00Z',
        labels: {
          observed: 'Observed',
          predicted: 'Predicted',
          uncertainty: 'Uncertainty',
          issuedAt: 'Issued at',
        },
        reducedMotion: false,
      }
    )

    const series = new Map(option.series?.map((entry) => [entry.id, entry]))
    expect(series.get('observed')).toMatchObject({ type: 'line', connectNulls: false })
    expect(series.get('predicted')).toMatchObject({ type: 'line', connectNulls: false })
    expect(series.get('uncertainty-band')).toMatchObject({ stack: 'uncertainty' })
    expect(series.get('predicted')?.markLine?.data).toEqual([
      expect.objectContaining({ xAxis: Date.parse('2026-09-24T00:00:00Z') }),
    ])
  })
})
