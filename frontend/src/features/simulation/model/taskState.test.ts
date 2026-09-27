import { describe, expect, it } from 'vitest'
import {
  canTransitionSimulationStatus,
  filterSimulationTasks,
  getSimulationRetryModel,
  isCancellableSimulationStatus,
  mergeSimulationTask,
  simulationRequestFingerprint,
} from './taskState'
import { simulationSubmitFixture, simulationTaskFixture } from '../test/fixtures'
import type { SimulationSubmitRequest, SimulationTask } from './types'

function task(overrides: Partial<SimulationTask> = {}): SimulationTask {
  return {
    ...simulationTaskFixture,
    status: 'running',
    progress: 50,
    attempt: 1,
    ...overrides,
  } as SimulationTask
}

describe('simulation task state', () => {
  it('keeps progress and the first terminal winner immutable', () => {
    const completed = task({
      status: 'completed',
      progress: 100,
      updated_at: '2026-09-24T10:03:00Z',
    })
    const merged = mergeSimulationTask(
      completed,
      task({ status: 'running', progress: 70, updated_at: '2026-09-24T10:02:00Z' })
    )
    expect(merged).toMatchObject({ status: 'completed', progress: 100 })
  })

  it('rejects impossible same-attempt terminal jumps', () => {
    const queued = task({ status: 'queued', attempt: 0, progress: 0 })
    const completed = task({
      status: 'completed',
      attempt: 0,
      progress: 100,
      published_attempt_id: '77777777-7777-4777-8777-777777777777',
      published_attempt_no: 1,
      frame_count: 2,
      updated_at: '2026-09-24T10:03:00Z',
    })
    expect(mergeSimulationTask(queued, completed)).toBe(queued)

    const retrying = task({
      status: 'retry_wait',
      attempt: 1,
      next_attempt_at: '2026-09-24T10:02:00Z',
      retry_backoff_seconds: 10,
      last_retry_error_code: 'LEASE_EXPIRED',
      updated_at: '2026-09-24T10:01:00Z',
    })
    expect(mergeSimulationTask(retrying, completed)).toBe(retrying)
  })

  it('never mixes another immutable revision or target manifest into a task', () => {
    const current = task()
    const otherRevision = task({
      network_revision_id: '99999999-9999-4999-8999-999999999999',
      status: 'completed',
      progress: 100,
    })
    const otherManifest = task({
      target_manifest_id: '88888888-8888-4888-8888-888888888888',
      status: 'failed',
    })
    expect(mergeSimulationTask(current, otherRevision)).toBe(current)
    expect(mergeSimulationTask(current, otherManifest)).toBe(current)
  })

  it('does not let a stale packet undo retry_wait', () => {
    const waiting = task({
      status: 'retry_wait',
      progress: 64,
      last_retry_error_code: 'RESULT_PERSIST_FAILED',
      next_attempt_at: '2026-09-24T10:02:00Z',
      retry_backoff_seconds: 60,
      updated_at: '2026-09-24T10:02:00Z',
    })
    const merged = mergeSimulationTask(
      waiting,
      task({ status: 'running', progress: 60, updated_at: '2026-09-24T10:01:30Z' })
    )
    expect(merged.status).toBe('retry_wait')
    expect(merged.progress).toBe(64)
  })

  it('models scheduled, exhausted, and request-gated manual retry states', () => {
    const waiting = task({
      status: 'retry_wait',
      attempt: 2,
      next_attempt_at: '2026-09-24T10:01:00Z',
      retry_backoff_seconds: 10,
      last_retry_error_code: 'RESULT_PERSIST_FAILED',
    })
    expect(getSimulationRetryModel(waiting, Date.parse('2026-09-24T10:00:30Z'))).toMatchObject({
      kind: 'scheduled',
      retryable: true,
      retryInSeconds: 30,
    })

    const failed = task({
      status: 'failed',
      attempt: 3,
      error_code: 'RESULT_PERSIST_FAILED',
      request: simulationSubmitFixture,
    })
    expect(getSimulationRetryModel(failed).kind).toBe('exhausted')
    expect(getSimulationRetryModel({ ...failed, attempt: 2, request: undefined }).retryable).toBe(
      false
    )
    expect(isCancellableSimulationStatus('queued')).toBe(true)
    expect(isCancellableSimulationStatus('cancelling')).toBe(false)
  })

  it('allows only the frozen simulation transition edges', () => {
    expect(canTransitionSimulationStatus('queued', 'cancelling')).toBe(true)
    expect(canTransitionSimulationStatus('running', 'completed')).toBe(true)
    expect(canTransitionSimulationStatus('retry_wait', 'leased')).toBe(true)
    expect(canTransitionSimulationStatus('queued', 'completed')).toBe(false)
    expect(canTransitionSimulationStatus('retry_wait', 'completed')).toBe(false)
    expect(canTransitionSimulationStatus('completed', 'failed')).toBe(false)
  })

  it('filters active and exact terminal groups and fingerprints key order independently', () => {
    const tasks = [
      task({ id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', status: 'queued', attempt: 0 }),
      task({
        id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
        status: 'completed',
        progress: 100,
      }),
      task({ id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc', status: 'failed' }),
    ]
    expect(filterSimulationTasks(tasks, 'active').map(({ id }) => id)).toEqual([
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    ])
    expect(filterSimulationTasks(tasks, 'completed').map(({ id }) => id)).toEqual([
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    ])
    expect(filterSimulationTasks(tasks, 'failed').map(({ id }) => id)).toEqual([
      'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
    ])

    const reordered = Object.fromEntries(
      Object.entries(simulationSubmitFixture).reverse()
    ) as SimulationSubmitRequest
    expect(simulationRequestFingerprint(reordered)).toBe(
      simulationRequestFingerprint(simulationSubmitFixture)
    )
  })
})
