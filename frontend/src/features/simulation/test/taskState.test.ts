import { describe, expect, it } from 'vitest'
import {
  filterSimulationTasks,
  mergeSimulationTask,
  simulationRequestFingerprint,
} from '../tasks/taskState'
import type { SimulationSubmitRequest, SimulationTask } from '../tasks/types'

const request: SimulationSubmitRequest = {
  network_id: 'network-1',
  network_revision_id: 'revision-1',
  rainfall_scenario_id: 'rainfall-1',
  parameter_set_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  solver_selection: 'auto',
  start_at: '2026-09-24T00:00:00Z',
  duration_seconds: 86_400,
  frame_interval_seconds: 300,
  ponding_enabled: false,
  strict_stability: true,
}

function task(overrides: Partial<SimulationTask> = {}): SimulationTask {
  return {
    id: 'simulation-1',
    status: 'running',
    progress: 50,
    attempt: 1,
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
    updated_at: '2026-09-24T00:01:00Z',
    ...overrides,
  } as SimulationTask
}

describe('simulation task state', () => {
  it('keeps progress monotonic and never regresses a terminal snapshot', () => {
    const completed = task({
      status: 'completed',
      progress: 100,
      finished_at: '2026-09-24T00:03:00Z',
      updated_at: '2026-09-24T00:03:00Z',
    })

    const lateRunning = mergeSimulationTask(
      completed,
      task({ status: 'running', progress: 72, updated_at: '2026-09-24T00:02:00Z' })
    )
    const lateCancelled = mergeSimulationTask(
      completed,
      task({ status: 'cancelled', progress: 100, updated_at: '2026-09-24T00:04:00Z' })
    )

    expect(lateRunning).toMatchObject({ status: 'completed', progress: 100 })
    expect(lateCancelled).toMatchObject({ status: 'completed', progress: 100 })
  })

  it('does not let a stale same-attempt packet undo retry_wait', () => {
    const waiting = task({
      status: 'retry_wait',
      progress: 64,
      last_retry_error_code: 'RESULT_PERSIST_FAILED',
      updated_at: '2026-09-24T00:02:00Z',
    })

    const merged = mergeSimulationTask(
      waiting,
      task({ status: 'running', progress: 60, updated_at: '2026-09-24T00:01:30Z' })
    )

    expect(merged.status).toBe('retry_wait')
    expect(merged.progress).toBe(64)
    expect(merged.last_retry_error_code).toBe('RESULT_PERSIST_FAILED')
  })

  it('accepts a later attempt and preserves the first terminal race winner', () => {
    const retry = task({
      status: 'leased',
      attempt: 2,
      progress: 64,
      updated_at: '2026-09-24T00:02:00Z',
    })
    expect(
      mergeSimulationTask(
        retry,
        task({
          status: 'running',
          attempt: 2,
          progress: 65,
          updated_at: '2026-09-24T00:03:00Z',
        })
      ).status
    ).toBe('running')

    const cancelling = task({ status: 'cancelling', progress: 80 })
    const cancelled = mergeSimulationTask(cancelling, task({ status: 'cancelled', progress: 80 }))
    expect(
      mergeSimulationTask(cancelled, task({ status: 'completed', progress: 100 })).status
    ).toBe('cancelled')
  })

  it('filters active, terminal, and exact status groups', () => {
    const tasks = [
      task({ id: 'queued', status: 'queued' }),
      task({ id: 'completed', status: 'completed', progress: 100 }),
      task({ id: 'failed', status: 'failed' }),
      task({ id: 'cancelled', status: 'cancelled' }),
    ]

    expect(filterSimulationTasks(tasks, 'active').map(({ id }) => id)).toEqual(['queued'])
    expect(filterSimulationTasks(tasks, 'completed').map(({ id }) => id)).toEqual(['completed'])
    expect(filterSimulationTasks(tasks, 'failed').map(({ id }) => id)).toEqual(['failed'])
  })

  it('creates a stable idempotency fingerprint independent of object key order', () => {
    const reordered = {
      strict_stability: true,
      ponding_enabled: false,
      frame_interval_seconds: 300,
      duration_seconds: 86_400,
      start_at: '2026-09-24T00:00:00Z',
      solver_selection: 'auto',
      rainfall_scenario_id: 'rainfall-1',
      parameter_set_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      network_revision_id: 'revision-1',
      network_id: 'network-1',
    } as SimulationSubmitRequest

    expect(simulationRequestFingerprint(request)).toBe(simulationRequestFingerprint(reordered))
  })
})
