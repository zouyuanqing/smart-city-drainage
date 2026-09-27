import { describe, expect, it } from 'vitest'
import { ApiClientError } from '../model/errors'
import type { SimulationTask } from '../model/types'
import {
  createSimulationStore,
  selectActiveSimulationTasks,
  selectCanRetryTask,
  selectFilteredSimulationTasks,
  selectSelectedSimulationTask,
  selectSimulationTasks,
} from './simulationStore'
import { simulationSubmitFixture, simulationTaskFixture } from '../test/fixtures'

const SECOND_SIMULATION_ID = '66666666-6666-4666-8666-666666666666'
const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const NEXT_REVISION_ID = '99999999-9999-4999-8999-999999999999'

function task(overrides: Partial<SimulationTask> = {}): SimulationTask {
  return { ...simulationTaskFixture, ...overrides } as SimulationTask
}

describe('simulation Zustand store', () => {
  it('merges task snapshots and keeps derived selector references stable', () => {
    const store = createSimulationStore()
    const running = task({ status: 'running', progress: 40, attempt: 1 })
    const completed = task({
      status: 'completed',
      progress: 100,
      attempt: 1,
      published_attempt_id: '77777777-7777-4777-8777-777777777777',
      published_attempt_no: 1,
      frame_count: 2,
      updated_at: '2026-09-24T10:03:00Z',
    })

    store.getState().setTasks([running])
    const tasks = selectSimulationTasks(store.getState())
    const active = selectActiveSimulationTasks(store.getState())
    const filtered = selectFilteredSimulationTasks(store.getState())
    store.getState().setLoading(true)
    expect(selectSimulationTasks(store.getState())).toBe(tasks)
    expect(selectActiveSimulationTasks(store.getState())).toBe(active)
    expect(selectFilteredSimulationTasks(store.getState())).toBe(filtered)

    store.getState().setTasks([completed])
    expect(selectSimulationTasks(store.getState())).toHaveLength(1)
    expect(selectActiveSimulationTasks(store.getState())).toHaveLength(0)
    expect(selectFilteredSimulationTasks(store.getState())[0]).toMatchObject({
      status: 'completed',
      progress: 100,
    })

    store.getState().setSelectedSimulation(simulationTaskFixture.id)
    expect(selectSelectedSimulationTask(store.getState())?.status).toBe('completed')
  })

  it('atomically clears old revision timeline, simulation, task, comparison, and pending actions', () => {
    const store = createSimulationStore()
    store.getState().setNetworkSelection('11111111-1111-4111-8111-111111111111', REVISION_ID)
    store.getState().setSelectedSimulation(simulationTaskFixture.id)
    store.getState().setTimeline({ frameIndex: 12, playing: true, buffering: true })
    store.getState().setComparisonSelection({
      baselineId: simulationTaskFixture.id,
      candidateId: SECOND_SIMULATION_ID,
    })
    store.getState().setPendingAction(simulationTaskFixture.id, true)

    store.getState().setNetworkSelection('11111111-1111-4111-8111-111111111111', NEXT_REVISION_ID)
    expect(store.getState()).toMatchObject({
      selectedNetworkRevisionId: NEXT_REVISION_ID,
      selectedSimulationId: null,
      selectedTaskId: null,
      selectedComparison: null,
      pendingActionIds: [],
      timeline: {
        frameIndex: 0,
        playing: false,
        buffering: false,
        lastFrameTime: null,
      },
    })
  })

  it('tracks timeline/layer controls and rejects NaN frame indexes', () => {
    const store = createSimulationStore()
    store.getState().setFrameIndex(288)
    store.getState().setPlaying(true)
    store.getState().setPlaybackSpeed(4)
    store.getState().setBuffering(true)
    store.getState().setLayerVisibility('nodes', false)
    store.getState().setFrameIndex(Number.NaN)

    expect(store.getState().timeline).toEqual({
      frameIndex: 288,
      playing: true,
      speed: 4,
      buffering: true,
      lastFrameTime: null,
    })
    expect(store.getState().layers.nodes).toBe(false)
    const keys = Object.keys(store.getState())
    expect(keys).not.toContain('geometry')
    expect(keys).not.toContain('frameData')
    expect(keys).not.toContain('mapData')
  })

  it('only exposes manual retry when the original request is available', () => {
    const store = createSimulationStore()
    const failed = task({
      status: 'failed',
      progress: 72,
      attempt: 2,
      error_code: 'RESULT_PERSIST_FAILED',
      error_message: 'result transaction failed',
      request: simulationSubmitFixture,
    })
    store.getState().replaceTasks([failed])
    expect(selectCanRetryTask(store.getState(), failed.id)).toBe(true)

    const withoutRequest = task({
      id: SECOND_SIMULATION_ID,
      status: 'failed',
      attempt: 2,
      request: undefined,
    })
    store.getState().upsertTask(withoutRequest)
    expect(selectCanRetryTask(store.getState(), withoutRequest.id)).toBe(false)

    store.getState().setError(
      new ApiClientError({
        status: 503,
        code: 'DEPENDENCY_NOT_READY',
        detail: 'dependency is not ready',
        requestId: '99999999-9999-4999-8999-999999999999',
        retryAfterSeconds: 4,
      })
    )
    expect(store.getState().error).toMatchObject({
      status: 503,
      code: 'DEPENDENCY_NOT_READY',
      retryAfterSeconds: 4,
      retryable: true,
    })
  })
})
