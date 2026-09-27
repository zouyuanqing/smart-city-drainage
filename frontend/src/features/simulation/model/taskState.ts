import {
  ACTIVE_SIMULATION_STATUSES,
  TERMINAL_SIMULATION_STATUSES,
  type SimulationErrorCode,
  type SimulationPhase,
  type SimulationStatus,
  type SimulationSubmitRequest,
  type SimulationTask,
  type SimulationTaskFilter,
} from './types'

const ALLOWED_TRANSITIONS: Record<SimulationStatus, ReadonlySet<SimulationStatus>> = {
  queued: new Set(['leased', 'cancelling']),
  leased: new Set(['running', 'retry_wait', 'failed', 'cancelling']),
  running: new Set(['retry_wait', 'failed', 'timed_out', 'completed', 'cancelling']),
  retry_wait: new Set(['leased', 'failed', 'cancelling']),
  cancelling: new Set(['cancelled']),
  cancelled: new Set(),
  completed: new Set(),
  failed: new Set(),
  timed_out: new Set(),
}

const RETRYABLE_TASK_CODES = new Set<SimulationErrorCode>([
  'LEASE_EXPIRED',
  'NUMERICAL_UNSTABLE',
  'NUMERICAL_STABILITY_LIMIT',
  'RESULT_PERSIST_FAILED',
])

export function isTerminalSimulationStatus(status: SimulationStatus): boolean {
  return TERMINAL_SIMULATION_STATUSES.has(status)
}

export function isActiveSimulationStatus(status: SimulationStatus): boolean {
  return ACTIVE_SIMULATION_STATUSES.has(status)
}

export function canTransitionSimulationStatus(
  from: SimulationStatus,
  to: SimulationStatus
): boolean {
  return ALLOWED_TRANSITIONS[from].has(to)
}

export function isCancellableSimulationStatus(status: SimulationStatus): boolean {
  return (
    status === 'queued' || status === 'leased' || status === 'running' || status === 'retry_wait'
  )
}

export function isManuallyRetryable(task: SimulationTask): boolean {
  return task.status === 'failed' || task.status === 'timed_out'
}

/** A manual retry needs the original submit payload (or an external resolver). */
export function hasRetryRequest(task: SimulationTask): boolean {
  return Boolean(task.request)
}

/** Server-side retryability is separate from a user clicking “retry”. */
export function isRetryableTaskError(code: SimulationErrorCode | null | undefined): boolean {
  return Boolean(code && RETRYABLE_TASK_CODES.has(code))
}

export type SimulationRetryModel =
  | { kind: 'none'; retryable: false; nextAttemptAt: null; retryInSeconds: null; errorCode: null }
  | {
      kind: 'scheduled'
      retryable: boolean
      nextAttemptAt: string | null
      retryInSeconds: number | null
      errorCode: SimulationErrorCode | null
    }
  | {
      kind: 'manual'
      retryable: boolean
      nextAttemptAt: null
      retryInSeconds: null
      errorCode: SimulationErrorCode | null
    }
  | {
      kind: 'exhausted'
      retryable: false
      nextAttemptAt: null
      retryInSeconds: null
      errorCode: SimulationErrorCode | null
    }

export function getSimulationRetryModel(
  task: SimulationTask,
  now = Date.now()
): SimulationRetryModel {
  const errorCode = task.error_code ?? task.last_retry_error_code ?? null
  if (task.status === 'retry_wait') {
    const due = task.next_attempt_at ? Date.parse(task.next_attempt_at) : Number.NaN
    const retryInSeconds = Number.isFinite(due) ? Math.max(0, Math.ceil((due - now) / 1_000)) : null
    return {
      kind: 'scheduled',
      retryable: isRetryableTaskError(errorCode),
      nextAttemptAt: task.next_attempt_at ?? null,
      retryInSeconds,
      errorCode,
    }
  }
  if (task.status === 'failed' || task.status === 'timed_out') {
    if (task.attempt >= task.max_attempts && isRetryableTaskError(errorCode)) {
      return {
        kind: 'exhausted',
        retryable: false,
        nextAttemptAt: null,
        retryInSeconds: null,
        errorCode,
      }
    }
    return {
      kind: 'manual',
      retryable: isManuallyRetryable(task) && hasRetryRequest(task),
      nextAttemptAt: null,
      retryInSeconds: null,
      errorCode,
    }
  }
  return {
    kind: 'none',
    retryable: false,
    nextAttemptAt: null,
    retryInSeconds: null,
    errorCode: null,
  }
}

export function phaseForProgress(progress: number): SimulationPhase {
  const bounded = Math.max(0, Math.min(100, Number.isFinite(progress) ? progress : 0))
  if (bounded === 0) return 'accepted'
  if (bounded <= 4) return 'validating'
  if (bounded <= 79) return 'solving'
  if (bounded <= 94) return 'quality'
  if (bounded < 100) return 'persisting'
  return 'finalizing'
}

function definedEntries<T extends object>(value: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(value).filter(([, entry]) => entry !== undefined)
  ) as Partial<T>
}

function taskTime(task: SimulationTask): number {
  const value = task.updated_at ?? task.finished_at ?? task.created_at
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? timestamp : 0
}

export function hasSameImmutableTaskIdentity(
  current: SimulationTask,
  incoming: SimulationTask
): boolean {
  return (
    current.id === incoming.id &&
    current.network_id === incoming.network_id &&
    current.network_revision_id === incoming.network_revision_id &&
    current.target_manifest_id === incoming.target_manifest_id &&
    current.target_manifest_sha256 === incoming.target_manifest_sha256 &&
    current.rainfall_scenario_id === incoming.rainfall_scenario_id
  )
}

function chooseStatus(current: SimulationTask, incoming: SimulationTask): SimulationStatus | null {
  if (current.status === incoming.status) return current.status
  if (incoming.attempt < current.attempt) return null
  if (taskTime(incoming) < taskTime(current)) return null

  if (incoming.attempt > current.attempt) {
    const isNewAttemptState =
      incoming.status === 'leased' ||
      incoming.status === 'running' ||
      incoming.status === 'retry_wait' ||
      incoming.status === 'cancelling'
    const mayStartNewAttempt =
      current.status === 'queued' ||
      current.status === 'leased' ||
      current.status === 'running' ||
      current.status === 'retry_wait'
    return isNewAttemptState && mayStartNewAttempt ? incoming.status : null
  }

  return canTransitionSimulationStatus(current.status, incoming.status) ? incoming.status : null
}

/**
 * Merge REST/SSE snapshots defensively. Identity is immutable, progress is
 * monotonic, and a status is accepted only through the frozen transition table.
 */
export function mergeSimulationTask(
  current: SimulationTask | undefined,
  incoming: SimulationTask
): SimulationTask {
  if (!current) return incoming
  if (!hasSameImmutableTaskIdentity(current, incoming)) return current

  const status = chooseStatus(current, incoming)
  if (status === null) return current
  const terminalLocked = isTerminalSimulationStatus(current.status)
  const merged = {
    ...current,
    ...definedEntries(incoming),
    status,
    progress: Math.max(current.progress, incoming.progress),
    attempt: Math.max(current.attempt, incoming.attempt),
  } as SimulationTask

  if (terminalLocked) {
    merged.status = current.status
    merged.progress = Math.max(current.progress, incoming.progress)
    merged.attempt = current.attempt
    merged.finished_at = current.finished_at ?? incoming.finished_at
    merged.error_code = current.error_code ?? incoming.error_code
    merged.error_message = current.error_message ?? incoming.error_message
    merged.published_attempt_id = current.published_attempt_id ?? incoming.published_attempt_id
    merged.published_attempt_no = current.published_attempt_no ?? incoming.published_attempt_no
  }

  if (taskTime(incoming) < taskTime(current) && !isTerminalSimulationStatus(incoming.status)) {
    merged.updated_at = current.updated_at ?? incoming.updated_at
  }
  return merged
}

export function sortSimulationTasks(tasks: readonly SimulationTask[]): SimulationTask[] {
  return [...tasks].sort((left, right) => {
    const byCreatedAt = Date.parse(right.created_at) - Date.parse(left.created_at)
    if (Number.isFinite(byCreatedAt) && byCreatedAt !== 0) return byCreatedAt
    return right.id.localeCompare(left.id)
  })
}

export function mergeSimulationTasks(
  current: readonly SimulationTask[],
  incoming: readonly SimulationTask[]
): SimulationTask[] {
  const byId = new Map(current.map((task) => [task.id, task]))
  for (const task of incoming) byId.set(task.id, mergeSimulationTask(byId.get(task.id), task))
  return sortSimulationTasks([...byId.values()])
}

export function filterSimulationTasks(
  tasks: readonly SimulationTask[],
  filter: SimulationTaskFilter
): SimulationTask[] {
  if (filter === 'all') return [...tasks]
  if (filter === 'active') return tasks.filter((task) => isActiveSimulationStatus(task.status))
  return tasks.filter((task) => task.status === filter)
}

export function simulationRequestFingerprint(request: SimulationSubmitRequest): string {
  return stableSerialize(request)
}

/** Deterministic JSON used for local idempotency bookkeeping, not a cryptographic hash. */
export function stableSerialize(value: unknown): string {
  if (value === undefined) return 'null'
  if (value === null || typeof value !== 'object') {
    const serialized = JSON.stringify(value)
    return serialized === undefined ? 'null' : serialized
  }
  if (Array.isArray(value)) return `[${value.map(stableSerialize).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record)
    .filter((key) => record[key] !== undefined)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableSerialize(record[key])}`)
    .join(',')}}`
}

export function simulationErrorMessage(task: SimulationTask): string | null {
  if (task.error_message) return task.error_message
  if (task.error_code) return task.error_code
  if (task.last_retry_error_code) return task.last_retry_error_code
  return null
}
