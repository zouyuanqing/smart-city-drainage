import { act } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useSimulationTask } from '../hooks/useSimulationTask'
import { SimulationTaskClientError } from '../tasks/api'
import type {
  SimulationSubmitRequest,
  SimulationTask,
  SimulationTaskClient,
  SimulationTaskCommandContext,
  SimulationTaskCommandResult,
} from '../tasks/types'

const submitRequest: SimulationSubmitRequest = {
  network_id: 'network-1',
  network_revision_id: 'revision-1',
  rainfall_scenario_id: 'rainfall-1',
  parameter_set_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  initial_state_id: null,
  solver_selection: 'auto',
  requested_solver_id: null,
  start_at: '2026-09-24T00:00:00Z',
  duration_seconds: 86_400,
  frame_interval_seconds: 300,
  ponding_enabled: false,
  strict_stability: true,
}

function task(overrides: Partial<SimulationTask> = {}): SimulationTask {
  return {
    id: 'simulation-1',
    status: 'queued',
    progress: 0,
    attempt: 0,
    max_attempts: 3,
    network_id: 'network-1',
    network_revision_id: 'revision-1',
    target_manifest_id: '33333333-3333-4333-8333-333333333333',
    target_manifest_sha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    rainfall_scenario_id: 'rainfall-1',
    solver_selection: 'auto',
    solver_id: 'py_mw',
    solver_version: 'fixture-1',
    created_at: '2026-09-24T00:00:00Z',
    updated_at: '2026-09-24T00:00:00Z',
    ...overrides,
  } as SimulationTask
}

function commandResult(value: SimulationTask): SimulationTaskCommandResult {
  return { task: value, idempotencyReplayed: false }
}

function createClient(overrides: Partial<SimulationTaskClient> = {}): SimulationTaskClient {
  return {
    list: vi.fn(async () => ({ items: [], nextCursor: null })),
    get: vi.fn(async () => task()),
    submit: vi.fn(async () => commandResult(task())),
    cancel: vi.fn(async () => commandResult(task())),
    ...overrides,
  }
}

function keyFactory(): () => string {
  let value = 0
  return () => `test-idempotency-${++value}`
}

describe('useSimulationTask', () => {
  it('reuses the same key and marks a repeated submit as an idempotent replay', async () => {
    const submitted = task()
    const client = createClient({
      submit: vi.fn(async () => commandResult(submitted)),
    })
    const factory = keyFactory()
    const view = renderHook(() =>
      useSimulationTask({
        networkId: 'network-1',
        client,
        enableStream: false,
        pollIntervalMs: 60_000,
        keyFactory: factory,
      })
    )
    await waitFor(() => expect(view.result.current.loading).toBe(false))

    await act(async () => {
      await view.result.current.submit(submitRequest)
    })
    await act(async () => {
      await view.result.current.submit(submitRequest)
    })

    expect(client.submit).toHaveBeenCalledTimes(2)
    expect((client.submit as ReturnType<typeof vi.fn>).mock.calls[0]?.[1]).toBe(
      'test-idempotency-1'
    )
    expect((client.submit as ReturnType<typeof vi.fn>).mock.calls[1]?.[1]).toBe(
      'test-idempotency-1'
    )
    expect(view.result.current.tasks).toHaveLength(1)
    expect(view.result.current.idempotencyReceipt).toMatchObject({
      state: 'replayed',
      key: 'test-idempotency-1',
      taskId: 'simulation-1',
    })

    view.unmount()
  })

  it('uses REST reconciliation when a cancel request loses the terminal-state race', async () => {
    const running = task({
      status: 'running',
      progress: 92,
      attempt: 1,
      updated_at: '2026-09-24T00:01:00Z',
    })
    const completed = task({
      status: 'completed',
      progress: 100,
      attempt: 1,
      finished_at: '2026-09-24T00:02:00Z',
      updated_at: '2026-09-24T00:02:00Z',
    })
    const client = createClient({
      list: vi.fn(async () => ({ items: [running], nextCursor: null })),
      cancel: vi.fn(async () => {
        throw new SimulationTaskClientError({
          status: 409,
          code: 'SIMULATION_NOT_CANCELLABLE',
          detail: 'simulation already completed',
          errors: [],
        })
      }),
      get: vi.fn(async () => completed),
    })
    const factory = keyFactory()
    const view = renderHook(() =>
      useSimulationTask({
        networkId: 'network-1',
        client,
        enableStream: false,
        pollIntervalMs: 60_000,
        keyFactory: factory,
      })
    )
    await waitFor(() => expect(view.result.current.tasks[0]?.status).toBe('running'))

    let authoritative: SimulationTask | undefined
    await act(async () => {
      authoritative = await view.result.current.cancel('simulation-1')
    })

    expect(authoritative?.status).toBe('completed')
    expect(view.result.current.tasks[0]).toMatchObject({ status: 'completed', progress: 100 })
    expect(view.result.current.actionError).toBeNull()

    view.unmount()
  })

  it('resubmits a failed task with a fresh key and links it to the source task', async () => {
    const failed = task({
      status: 'failed',
      progress: 73,
      attempt: 3,
      error_code: 'RESULT_PERSIST_FAILED',
      error_message: 'result transaction failed',
      request: submitRequest,
      updated_at: '2026-09-24T00:03:00Z',
    })
    const replacement = task({
      id: 'simulation-2',
      status: 'queued',
      progress: 0,
      attempt: 0,
      created_at: '2026-09-24T00:04:00Z',
      updated_at: '2026-09-24T00:04:00Z',
    })
    const client = createClient({
      list: vi.fn(async () => ({ items: [failed], nextCursor: null })),
      submit: vi.fn(async () => commandResult(replacement)),
    })
    const factory = keyFactory()
    const view = renderHook(() =>
      useSimulationTask({
        networkId: 'network-1',
        client,
        enableStream: false,
        pollIntervalMs: 60_000,
        keyFactory: factory,
      })
    )
    await waitFor(() => expect(view.result.current.tasks[0]?.status).toBe('failed'))

    let retried: SimulationTask | undefined
    await act(async () => {
      retried = await view.result.current.retryFailed('simulation-1')
    })

    expect(retried?.id).toBe('simulation-2')
    expect((client.submit as ReturnType<typeof vi.fn>).mock.calls[0]?.[1]).toBe(
      'test-idempotency-1'
    )
    expect(
      view.result.current.tasks.find(({ id }) => id === 'simulation-1')?.retry_submission_id
    ).toBe('simulation-2')
    expect(view.result.current.idempotencyReceipt.state).toBe('new')

    view.unmount()
  })

  it('polls active tasks and replaces the stale snapshot with REST progress', async () => {
    const running = task({ status: 'running', progress: 20, attempt: 1 })
    const runningAgain = task({ status: 'running', progress: 55, attempt: 1 })
    const completed = task({
      status: 'completed',
      progress: 100,
      attempt: 1,
      finished_at: '2026-09-24T00:04:00Z',
      updated_at: '2026-09-24T00:04:00Z',
    })
    const get = vi.fn(
      async (
        _simulationId: string,
        _context?: SimulationTaskCommandContext
      ): Promise<SimulationTask> => runningAgain
    )
    get.mockResolvedValueOnce(runningAgain).mockResolvedValueOnce(completed)
    const client = createClient({
      list: vi.fn(async () => ({ items: [running], nextCursor: null })),
      get,
    })
    const view = renderHook(() =>
      useSimulationTask({
        networkId: 'network-1',
        client,
        enableStream: false,
        pollIntervalMs: 250,
        connectedPollIntervalMs: 60_000,
      })
    )

    await waitFor(() => expect(view.result.current.tasks[0]?.progress).toBeGreaterThan(20), {
      timeout: 2_000,
    })
    await waitFor(() => expect(view.result.current.tasks[0]?.status).toBe('completed'), {
      timeout: 2_000,
    })
    expect(get).toHaveBeenCalled()

    view.unmount()
  })

  it('surfaces an idempotency conflict and generates a fresh key for the corrected retry', async () => {
    const conflict = new SimulationTaskClientError({
      status: 409,
      code: 'IDEMPOTENCY_KEY_REUSED',
      detail: 'key was used with a different request',
      errors: [],
    })
    const submit = vi.fn(
      async (
        _request: SimulationSubmitRequest,
        _idempotencyKey: string,
        _context?: SimulationTaskCommandContext
      ): Promise<SimulationTaskCommandResult> => commandResult(task())
    )
    submit.mockRejectedValueOnce(conflict).mockResolvedValueOnce(commandResult(task()))
    const client = createClient({ submit })
    const factory = keyFactory()
    const view = renderHook(() =>
      useSimulationTask({
        networkId: 'network-1',
        client,
        enableStream: false,
        pollIntervalMs: 60_000,
        keyFactory: factory,
      })
    )
    await waitFor(() => expect(view.result.current.loading).toBe(false))

    await act(async () => {
      await expect(view.result.current.submit(submitRequest)).rejects.toBe(conflict)
    })
    expect(view.result.current.idempotencyReceipt.state).toBe('conflict')
    expect(view.result.current.actionError).toContain('different request')

    await act(async () => {
      await view.result.current.submit(submitRequest)
    })
    expect((client.submit as ReturnType<typeof vi.fn>).mock.calls[1]?.[1]).toBe(
      'test-idempotency-2'
    )
    expect(view.result.current.idempotencyReceipt.state).toBe('new')

    view.unmount()
  })
})
