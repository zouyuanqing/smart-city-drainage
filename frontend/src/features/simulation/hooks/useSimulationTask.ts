import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  defaultSimulationTaskClient,
  getSimulationTaskErrorMessage,
  SimulationTaskClientError,
} from '../tasks/api'
import {
  filterSimulationTasks,
  isManuallyRetryable,
  mergeSimulationTasks,
  simulationRequestFingerprint,
} from '../tasks/taskState'
import {
  isActiveSimulationStatus,
  isTerminalSimulationStatus,
  type SimulationIdempotencyReceipt,
  type SimulationSubmitRequest,
  type SimulationTask,
  type SimulationTaskClient,
  type SimulationTaskFilter,
} from '../tasks/types'
import type { SimulationStreamStatus } from './useSimulationStream'

const DEFAULT_POLL_INTERVAL_MS = 2_000
const DEFAULT_HIDDEN_POLL_INTERVAL_MS = 10_000
const DEFAULT_CONNECTED_SAFETY_POLL_MS = 30_000
const DEFAULT_PAGE_SIZE = 50
const MAX_POLL_BACKOFF_MS = 30_000
const HIGH_FREQUENCY_DETAIL_STATUSES = new Set<SimulationTask['status']>([
  'leased',
  'running',
  'cancelling',
])

let localKeySequence = 0

function createIdempotencyKey(): string {
  const uuid = globalThis.crypto?.randomUUID?.()
  if (uuid) return `simulation-${uuid}`
  localKeySequence += 1
  return `simulation-${Date.now().toString(36)}-${localKeySequence.toString(36)}`
}

function setOfState(set: Set<string>): string[] {
  return [...set].sort()
}

export interface SharedSimulationStream {
  status: SimulationStreamStatus
  reconnect: () => void
}

export interface UseSimulationTaskOptions {
  networkId: string
  token?: string | null
  client?: SimulationTaskClient
  /** @deprecated The task controller consumes a provider-owned stream. */
  enableStream?: boolean
  stream?: SharedSimulationStream
  streamEndpoint?: string | ((networkId: string) => string)
  streamFetchImpl?: typeof fetch
  getToken?: () => string | null | undefined
  pollIntervalMs?: number
  hiddenPollIntervalMs?: number
  connectedPollIntervalMs?: number
  pageSize?: number
  keyFactory?: () => string
  resolveRetryRequest?: (task: SimulationTask) => SimulationSubmitRequest | undefined
}

export interface UseSimulationTaskResult {
  tasks: SimulationTask[]
  filteredTasks: SimulationTask[]
  activeTasks: SimulationTask[]
  statusFilter: SimulationTaskFilter
  setStatusFilter: (filter: SimulationTaskFilter) => void
  loading: boolean
  refreshing: boolean
  loadingMore: boolean
  hasNextPage: boolean
  error: string | null
  actionError: string | null
  isStale: boolean
  lastSyncedAt: string | null
  streamStatus: SimulationStreamStatus
  idempotencyReceipt: SimulationIdempotencyReceipt
  pendingCancellationIds: string[]
  pendingRetryIds: string[]
  submitting: boolean
  refresh: () => Promise<void>
  loadMore: () => Promise<void>
  refreshTask: (simulationId: string) => Promise<SimulationTask | null>
  submit: (request: SimulationSubmitRequest) => Promise<SimulationTask>
  cancel: (simulationId: string) => Promise<SimulationTask>
  retryFailed: (simulationId: string) => Promise<SimulationTask>
  clearActionError: () => void
  reconnectStream: () => void
}

export function useSimulationTask(options: UseSimulationTaskOptions): UseSimulationTaskResult {
  const client = options.client ?? defaultSimulationTaskClient
  const pageSize = Math.max(1, Math.min(200, options.pageSize ?? DEFAULT_PAGE_SIZE))
  const pollIntervalMs = Math.max(250, options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS)
  const hiddenPollIntervalMs = Math.max(
    pollIntervalMs,
    options.hiddenPollIntervalMs ?? DEFAULT_HIDDEN_POLL_INTERVAL_MS
  )
  const connectedPollIntervalMs = Math.max(
    pollIntervalMs,
    options.connectedPollIntervalMs ?? DEFAULT_CONNECTED_SAFETY_POLL_MS
  )
  const keyFactory = options.keyFactory ?? createIdempotencyKey
  const resolveRetryRequest = options.resolveRetryRequest
  const sharedStream = options.stream
  const networkGenerationRef = useRef(0)
  const networkAbortRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)
  const nextCursorRef = useRef<string | null>(null)
  const loadingMoreRef = useRef(false)
  const pollFailureCountRef = useRef(0)
  const requestByTaskIdRef = useRef(new Map<string, SimulationSubmitRequest>())
  const keyByFingerprintRef = useRef(new Map<string, string>())
  const rotateIdempotencyKeyRef = useRef(new Set<string>())
  const taskByIdempotencyKeyRef = useRef(new Map<string, string>())
  const submitInFlightRef = useRef(new Map<string, Promise<SimulationTask>>())
  const cancelInFlightRef = useRef(new Map<string, Promise<SimulationTask>>())
  const cancelKeyRef = useRef(new Map<string, string>())
  const retryInFlightRef = useRef(new Map<string, Promise<SimulationTask>>())
  const pendingCancellationIdsRef = useRef(new Set<string>())
  const pendingRetryIdsRef = useRef(new Set<string>())

  const [tasks, setTasks] = useState<SimulationTask[]>([])
  const tasksRef = useRef<SimulationTask[]>([])
  const [statusFilter, setStatusFilter] = useState<SimulationTaskFilter>('all')
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasNextPage, setHasNextPage] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [isStale, setIsStale] = useState(false)
  const [lastSyncedAt, setLastSyncedAt] = useState<string | null>(null)
  const [idempotencyReceipt, setIdempotencyReceipt] = useState<SimulationIdempotencyReceipt>({
    state: 'idle',
    key: null,
    taskId: null,
  })
  const [pendingCancellationIds, setPendingCancellationIds] = useState<string[]>([])
  const [pendingRetryIds, setPendingRetryIds] = useState<string[]>([])
  const [submittingCount, setSubmittingCount] = useState(0)

  const isCurrent = useCallback((generation: number, signal?: AbortSignal) => {
    return (
      mountedRef.current &&
      generation === networkGenerationRef.current &&
      !signal?.aborted &&
      !networkAbortRef.current?.signal.aborted
    )
  }, [])

  const commitTasks = useCallback(
    (
      incoming: readonly SimulationTask[] | ((current: SimulationTask[]) => SimulationTask[]),
      generation = networkGenerationRef.current
    ) => {
      setTasks((current) => {
        if (!isCurrent(generation)) return current
        const next =
          typeof incoming === 'function'
            ? incoming(current)
            : mergeSimulationTasks(current, incoming)
        const scoped = next.filter((task) => task.network_id === options.networkId)
        tasksRef.current = scoped
        for (const task of scoped) {
          if (task.request) requestByTaskIdRef.current.set(task.id, task.request)
        }
        return scoped
      })
    },
    [isCurrent, options.networkId]
  )

  const activeTasks = useMemo(
    () => tasks.filter((task) => isActiveSimulationStatus(task.status)),
    [tasks]
  )
  const filteredTasks = useMemo(
    () => filterSimulationTasks(tasks, statusFilter),
    [statusFilter, tasks]
  )
  const activeTaskSignature = activeTasks
    .map((task) => `${task.id}:${task.status}:${task.attempt}`)
    .sort()
    .join('|')

  const refreshTask = useCallback(
    async (simulationId: string): Promise<SimulationTask | null> => {
      const generation = networkGenerationRef.current
      const signal = networkAbortRef.current?.signal
      try {
        const task = await client.get(simulationId, { token: options.token, signal })
        if (!isCurrent(generation, signal)) return task
        commitTasks([task], generation)
        setIsStale(false)
        return task
      } catch {
        if (isCurrent(generation, signal)) setIsStale(true)
        return null
      }
    },
    [client, commitTasks, isCurrent, options.token]
  )

  const refresh = useCallback(async () => {
    const generation = networkGenerationRef.current
    const signal = networkAbortRef.current?.signal
    setRefreshing(true)
    try {
      const page = await client.list(
        { networkId: options.networkId, limit: pageSize },
        { token: options.token, signal }
      )
      if (!isCurrent(generation, signal)) return
      commitTasks(page.items, generation)
      nextCursorRef.current = page.nextCursor
      setHasNextPage(Boolean(page.nextCursor))
      setError(null)
      setIsStale(false)
      setLastSyncedAt(new Date().toISOString())
    } catch (refreshError) {
      if (!isCurrent(generation, signal)) return
      setError(getSimulationTaskErrorMessage(refreshError))
      setIsStale(tasksRef.current.length > 0)
    } finally {
      if (isCurrent(generation, signal)) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [client, commitTasks, isCurrent, options.networkId, options.token, pageSize])

  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !nextCursorRef.current) return
    const generation = networkGenerationRef.current
    const signal = networkAbortRef.current?.signal
    loadingMoreRef.current = true
    setLoadingMore(true)
    try {
      const page = await client.list(
        {
          networkId: options.networkId,
          cursor: nextCursorRef.current,
          limit: pageSize,
        },
        { token: options.token, signal }
      )
      if (!isCurrent(generation, signal)) return
      commitTasks(page.items, generation)
      nextCursorRef.current = page.nextCursor
      setHasNextPage(Boolean(page.nextCursor))
    } catch (loadError) {
      if (isCurrent(generation, signal)) setError(getSimulationTaskErrorMessage(loadError))
    } finally {
      loadingMoreRef.current = false
      if (isCurrent(generation, signal)) setLoadingMore(false)
    }
  }, [client, commitTasks, isCurrent, options.networkId, options.token, pageSize])

  const pollSnapshot = useCallback(
    async (generation: number, signal?: AbortSignal): Promise<boolean> => {
      const listPromise = client.list(
        { networkId: options.networkId, limit: pageSize },
        { token: options.token, signal }
      )
      const activeIds = tasksRef.current
        .filter((task) => HIGH_FREQUENCY_DETAIL_STATUSES.has(task.status))
        .map((task) => task.id)
      const [pageResult, detailResults] = await Promise.all([
        listPromise,
        Promise.allSettled(
          activeIds.map((simulationId) =>
            client.get(simulationId, { token: options.token, signal })
          )
        ),
      ])
      if (!isCurrent(generation, signal)) return false
      commitTasks(pageResult.items, generation)
      for (const result of detailResults) {
        if (result.status === 'fulfilled') commitTasks([result.value], generation)
      }
      const partialFailure = detailResults.some((result) => result.status === 'rejected')
      if (partialFailure) setIsStale(true)
      else {
        setIsStale(false)
        setError(null)
        setLastSyncedAt(new Date().toISOString())
      }
      return !partialFailure
    },
    [client, commitTasks, isCurrent, options.networkId, options.token, pageSize]
  )

  const pollActive = useCallback(async (): Promise<boolean> => {
    const generation = networkGenerationRef.current
    const signal = networkAbortRef.current?.signal
    try {
      const healthy = await pollSnapshot(generation, signal)
      if (healthy) pollFailureCountRef.current = 0
      else pollFailureCountRef.current += 1
      return healthy
    } catch {
      if (isCurrent(generation, signal)) {
        pollFailureCountRef.current += 1
        setIsStale(true)
      }
      return false
    }
  }, [isCurrent, pollSnapshot])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      networkAbortRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    networkGenerationRef.current += 1
    networkAbortRef.current?.abort()
    const controller = new AbortController()
    networkAbortRef.current = controller
    tasksRef.current = []
    nextCursorRef.current = null
    requestByTaskIdRef.current.clear()
    keyByFingerprintRef.current.clear()
    rotateIdempotencyKeyRef.current.clear()
    taskByIdempotencyKeyRef.current.clear()
    submitInFlightRef.current.clear()
    cancelInFlightRef.current.clear()
    retryInFlightRef.current.clear()
    cancelKeyRef.current.clear()
    pendingCancellationIdsRef.current.clear()
    pendingRetryIdsRef.current.clear()
    setTasks([])
    setStatusFilter('all')
    setHasNextPage(false)
    setError(null)
    setActionError(null)
    setIsStale(false)
    setLastSyncedAt(null)
    setIdempotencyReceipt({ state: 'idle', key: null, taskId: null })
    setPendingCancellationIds([])
    setPendingRetryIds([])
    setSubmittingCount(0)
    setLoading(true)
    void refresh()
    let active = true
    return () => {
      if (active) controller.abort()
      active = false
    }
  }, [options.networkId, refresh])

  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const schedule = () => {
      if (stopped) return
      const hidden = typeof document !== 'undefined' && document.visibilityState === 'hidden'
      const baseInterval =
        sharedStream?.status === 'connected'
          ? connectedPollIntervalMs
          : hidden
            ? hiddenPollIntervalMs
            : pollIntervalMs
      const delay = Math.min(baseInterval * 2 ** pollFailureCountRef.current, MAX_POLL_BACKOFF_MS)
      timer = setTimeout(async () => {
        if (stopped) return
        await pollActive()
        if (!stopped) schedule()
      }, delay)
    }
    const handleVisibility = () => {
      if (timer) clearTimeout(timer)
      schedule()
    }
    schedule()
    document.addEventListener('visibilitychange', handleVisibility)
    return () => {
      stopped = true
      if (timer) clearTimeout(timer)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [
    activeTaskSignature,
    connectedPollIntervalMs,
    hiddenPollIntervalMs,
    pollActive,
    pollIntervalMs,
    sharedStream?.status,
  ])

  const performSubmit = useCallback(
    async (
      request: SimulationSubmitRequest,
      forceNewKey: boolean,
      originTaskId?: string
    ): Promise<SimulationTask> => {
      const generation = networkGenerationRef.current
      const signal = networkAbortRef.current?.signal
      const fingerprint = simulationRequestFingerprint(request)
      const operationKey = forceNewKey
        ? `retry:${originTaskId ?? 'unknown'}:${fingerprint}`
        : `submit:${fingerprint}`
      const existing = submitInFlightRef.current.get(operationKey)
      if (existing) return existing
      const rotateKey = rotateIdempotencyKeyRef.current.has(fingerprint)
      const idempotencyKey =
        forceNewKey || rotateKey
          ? keyFactory()
          : keyByFingerprintRef.current.get(fingerprint) ?? keyFactory()
      rotateIdempotencyKeyRef.current.delete(fingerprint)
      keyByFingerprintRef.current.set(fingerprint, idempotencyKey)
      setSubmittingCount((count) => count + 1)
      setActionError(null)
      setIdempotencyReceipt({ state: 'pending', key: idempotencyKey, taskId: null })
      const promise = client
        .submit(request, idempotencyKey, { token: options.token, signal })
        .then((result) => {
          const knownTaskId = taskByIdempotencyKeyRef.current.get(idempotencyKey)
          const replayed = result.idempotencyReplayed || knownTaskId === result.task.id
          taskByIdempotencyKeyRef.current.set(idempotencyKey, result.task.id)
          const task: SimulationTask = { ...result.task, request: result.task.request ?? request }
          if (isCurrent(generation, signal)) {
            requestByTaskIdRef.current.set(task.id, request)
            commitTasks([task], generation)
            setIdempotencyReceipt({
              state: replayed ? 'replayed' : 'new',
              key: idempotencyKey,
              taskId: task.id,
            })
            if (originTaskId) {
              const origin = tasksRef.current.find((candidate) => candidate.id === originTaskId)
              if (origin) commitTasks([{ ...origin, retry_submission_id: task.id }], generation)
            }
          }
          return task
        })
        .catch((submitError) => {
          const message = getSimulationTaskErrorMessage(submitError)
          const conflict =
            submitError instanceof SimulationTaskClientError &&
            submitError.code === 'IDEMPOTENCY_KEY_REUSED'
          if (conflict) {
            keyByFingerprintRef.current.delete(fingerprint)
            rotateIdempotencyKeyRef.current.add(fingerprint)
          }
          if (isCurrent(generation, signal)) {
            setIdempotencyReceipt({
              state: conflict ? 'conflict' : 'pending',
              key: idempotencyKey,
              taskId: null,
              message,
            })
            setActionError(message)
          }
          throw submitError
        })
        .finally(() => {
          if (submitInFlightRef.current.get(operationKey) === promise) {
            submitInFlightRef.current.delete(operationKey)
          }
          if (isCurrent(generation, signal)) setSubmittingCount((count) => Math.max(0, count - 1))
        })
      submitInFlightRef.current.set(operationKey, promise)
      return promise
    },
    [client, commitTasks, isCurrent, keyFactory, options.token]
  )

  const submit = useCallback(
    (request: SimulationSubmitRequest) =>
      performSubmit(request, idempotencyReceipt.state === 'conflict'),
    [idempotencyReceipt.state, performSubmit]
  )

  const cancel = useCallback(
    async (simulationId: string): Promise<SimulationTask> => {
      const existing = cancelInFlightRef.current.get(simulationId)
      if (existing) return existing
      const generation = networkGenerationRef.current
      const signal = networkAbortRef.current?.signal
      const idempotencyKey = cancelKeyRef.current.get(simulationId) ?? keyFactory()
      cancelKeyRef.current.set(simulationId, idempotencyKey)
      pendingCancellationIdsRef.current.add(simulationId)
      setPendingCancellationIds(setOfState(pendingCancellationIdsRef.current))
      setActionError(null)
      const promise = client
        .cancel(simulationId, idempotencyKey, { token: options.token, signal })
        .then((result) => {
          if (isCurrent(generation, signal)) commitTasks([result.task], generation)
          return result.task
        })
        .catch(async (cancelError) => {
          try {
            const authoritative = await client.get(simulationId, { token: options.token, signal })
            if (isCurrent(generation, signal)) commitTasks([authoritative], generation)
            if (isTerminalSimulationStatus(authoritative.status)) return authoritative
          } catch {
            // Preserve the cancellation error when REST reconciliation also fails.
          }
          if (isCurrent(generation, signal))
            setActionError(getSimulationTaskErrorMessage(cancelError))
          throw cancelError
        })
        .finally(() => {
          if (cancelInFlightRef.current.get(simulationId) === promise) {
            cancelInFlightRef.current.delete(simulationId)
          }
          if (networkGenerationRef.current === generation) {
            pendingCancellationIdsRef.current.delete(simulationId)
            setPendingCancellationIds(setOfState(pendingCancellationIdsRef.current))
          }
        })
      cancelInFlightRef.current.set(simulationId, promise)
      return promise
    },
    [client, commitTasks, isCurrent, keyFactory, options.token]
  )

  const retryFailed = useCallback(
    async (simulationId: string): Promise<SimulationTask> => {
      const existing = retryInFlightRef.current.get(simulationId)
      if (existing) return existing
      const task = tasksRef.current.find((candidate) => candidate.id === simulationId)
      if (!task || !isManuallyRetryable(task)) {
        throw new Error('Only failed or timed-out simulation tasks can be resubmitted')
      }
      const request =
        requestByTaskIdRef.current.get(simulationId) ?? task.request ?? resolveRetryRequest?.(task)
      if (!request) {
        const message = 'The original simulation request is unavailable; rebuild it before retrying'
        setActionError(message)
        throw new Error(message)
      }
      const generation = networkGenerationRef.current
      pendingRetryIdsRef.current.add(simulationId)
      setPendingRetryIds(setOfState(pendingRetryIdsRef.current))
      const promise = performSubmit(request, true, simulationId).finally(() => {
        if (retryInFlightRef.current.get(simulationId) === promise) {
          retryInFlightRef.current.delete(simulationId)
        }
        if (networkGenerationRef.current === generation) {
          pendingRetryIdsRef.current.delete(simulationId)
          setPendingRetryIds(setOfState(pendingRetryIdsRef.current))
        }
      })
      retryInFlightRef.current.set(simulationId, promise)
      return promise
    },
    [performSubmit, resolveRetryRequest]
  )

  return {
    tasks,
    filteredTasks,
    activeTasks,
    statusFilter,
    setStatusFilter,
    loading,
    refreshing,
    loadingMore,
    hasNextPage,
    error,
    actionError,
    isStale,
    lastSyncedAt,
    streamStatus: sharedStream?.status ?? 'disabled',
    idempotencyReceipt,
    pendingCancellationIds,
    pendingRetryIds,
    submitting: submittingCount > 0,
    refresh,
    loadMore,
    refreshTask,
    submit,
    cancel,
    retryFailed,
    clearActionError: () => setActionError(null),
    reconnectStream: sharedStream?.reconnect ?? (() => undefined),
  }
}
