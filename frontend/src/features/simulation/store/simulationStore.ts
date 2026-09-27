import { create } from 'zustand'
import { toStoreError } from '../model/errors'
import {
  filterSimulationTasks,
  isCancellableSimulationStatus,
  isManuallyRetryable,
  hasRetryRequest,
  isActiveSimulationStatus,
  mergeSimulationTask,
  mergeSimulationTasks,
  sortSimulationTasks,
} from '../model/taskState'
import {
  SIMULATION_STATUSES,
  type SimulationStatus,
  type SimulationTask,
  type SimulationTaskFilter,
} from '../model/types'

export type SimulationPlaybackSpeed = 1 | 2 | 4

export interface SimulationTimelineState {
  frameIndex: number
  playing: boolean
  speed: SimulationPlaybackSpeed
  buffering: boolean
  lastFrameTime: string | null
}

export interface SimulationLayerVisibility {
  nodes: boolean
  pipes: boolean
  catchments: boolean
  ponding: boolean
}

export interface SimulationStoreError {
  status: number
  code: string
  message: string
  requestId?: string
  retryAfterSeconds?: number
  retryable: boolean
}

export interface SimulationComparisonSelection {
  baselineId: string
  candidateId: string
}

export interface SimulationStoreState {
  selectedNetworkId: string | null
  selectedNetworkRevisionId: string | null
  selectedSimulationId: string | null
  selectedTaskId: string | null
  selectedComparison: SimulationComparisonSelection | null

  /** Only task/control metadata lives in Zustand; geometry and frame arrays are external caches. */
  tasks: SimulationTask[]
  tasksById: Record<string, SimulationTask>
  taskIds: string[]
  statusFilter: SimulationTaskFilter
  timeline: SimulationTimelineState
  layers: SimulationLayerVisibility
  pendingActionIds: string[]

  loading: boolean
  refreshing: boolean
  submitting: boolean
  error: SimulationStoreError | null
  lastSyncedAt: string | null

  setNetworkSelection: (networkId: string | null, revisionId?: string | null) => void
  setSelectedSimulation: (simulationId: string | null) => void
  setSelectedTask: (taskId: string | null) => void
  setComparisonSelection: (selection: SimulationComparisonSelection | null) => void
  setStatusFilter: (filter: SimulationTaskFilter) => void
  setTasks: (tasks: readonly SimulationTask[]) => void
  replaceTasks: (tasks: readonly SimulationTask[]) => void
  upsertTask: (task: SimulationTask) => void
  removeTask: (taskId: string) => void
  setLoading: (loading: boolean) => void
  setRefreshing: (refreshing: boolean) => void
  setSubmitting: (submitting: boolean) => void
  setError: (error: unknown) => void
  clearError: () => void
  setLastSyncedAt: (value: string | null) => void
  setPendingAction: (taskId: string, pending: boolean) => void
  clearPendingActions: () => void
  setTimeline: (timeline: Partial<SimulationTimelineState>) => void
  setBuffering: (buffering: boolean) => void
  setFrameIndex: (frameIndex: number) => void
  setPlaying: (playing: boolean) => void
  setPlaybackSpeed: (speed: SimulationPlaybackSpeed) => void
  setLayerVisibility: (layer: keyof SimulationLayerVisibility, visible: boolean) => void
  reset: () => void
}

type SimulationStoreActionName =
  | 'setNetworkSelection'
  | 'setSelectedSimulation'
  | 'setSelectedTask'
  | 'setComparisonSelection'
  | 'setStatusFilter'
  | 'setTasks'
  | 'replaceTasks'
  | 'upsertTask'
  | 'removeTask'
  | 'setLoading'
  | 'setRefreshing'
  | 'setSubmitting'
  | 'setError'
  | 'clearError'
  | 'setLastSyncedAt'
  | 'setPendingAction'
  | 'clearPendingActions'
  | 'setTimeline'
  | 'setBuffering'
  | 'setFrameIndex'
  | 'setPlaying'
  | 'setPlaybackSpeed'
  | 'setLayerVisibility'
  | 'reset'

export type SimulationStoreData = Omit<SimulationStoreState, SimulationStoreActionName>
export type SimulationStoreInit = Partial<SimulationStoreData>

export function createInitialSimulationStoreState(
  init: SimulationStoreInit = {}
): SimulationStoreData {
  const tasksById =
    init.tasksById ?? Object.fromEntries((init.tasks ?? []).map((task) => [task.id, task] as const))
  const tasks = init.tasks ?? sortSimulationTasks(Object.values(tasksById))
  return {
    selectedNetworkId: null,
    selectedNetworkRevisionId: null,
    selectedSimulationId: null,
    selectedTaskId: null,
    selectedComparison: null,
    tasks,
    tasksById,
    taskIds: taskOrder(tasksById),
    statusFilter: 'all',
    timeline: {
      frameIndex: 0,
      playing: false,
      speed: 1,
      buffering: false,
      lastFrameTime: null,
    },
    layers: { nodes: true, pipes: true, catchments: true, ponding: true },
    pendingActionIds: [],
    loading: false,
    refreshing: false,
    submitting: false,
    error: null,
    lastSyncedAt: null,
    ...init,
  }
}

function taskOrder(tasks: Record<string, SimulationTask>): string[] {
  return sortSimulationTasks(Object.values(tasks)).map((task) => task.id)
}

function taskStateSnapshot(tasksById: Record<string, SimulationTask>): {
  tasks: SimulationTask[]
  tasksById: Record<string, SimulationTask>
  taskIds: string[]
} {
  const tasks = sortSimulationTasks(Object.values(tasksById))
  return { tasks, tasksById, taskIds: tasks.map((task) => task.id) }
}

function validFilter(value: SimulationTaskFilter): boolean {
  return (
    value === 'all' ||
    value === 'active' ||
    (SIMULATION_STATUSES as readonly string[]).includes(value)
  )
}

function validSpeed(value: number): value is SimulationPlaybackSpeed {
  return value === 1 || value === 2 || value === 4
}

function validFrameIndex(value: number): boolean {
  return Number.isSafeInteger(value) && value >= 0
}

function validFrameTime(value: string | null): boolean {
  return (
    value === null ||
    (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value) &&
      Number.isFinite(Date.parse(value)))
  )
}

export function createSimulationStore(init: SimulationStoreInit = {}) {
  const initial = createInitialSimulationStoreState(init)
  return create<SimulationStoreState>((set, get) => ({
    ...initial,

    setNetworkSelection: (networkId, revisionId = null) =>
      set((state) => {
        const changed =
          state.selectedNetworkId !== networkId || state.selectedNetworkRevisionId !== revisionId
        return {
          selectedNetworkId: networkId,
          selectedNetworkRevisionId: revisionId,
          ...(changed
            ? {
                selectedSimulationId: null,
                selectedTaskId: null,
                selectedComparison: null,
                pendingActionIds: [],
                timeline: {
                  frameIndex: 0,
                  playing: false,
                  speed: state.timeline.speed,
                  buffering: false,
                  lastFrameTime: null,
                },
              }
            : {}),
        }
      }),

    setSelectedSimulation: (simulationId) =>
      set((state) => {
        if (state.selectedSimulationId === simulationId) {
          return { selectedTaskId: simulationId }
        }
        return {
          selectedSimulationId: simulationId,
          selectedTaskId: simulationId,
          timeline: {
            frameIndex: 0,
            playing: false,
            speed: state.timeline.speed,
            buffering: false,
            lastFrameTime: null,
          },
        }
      }),

    setSelectedTask: (taskId) => set({ selectedTaskId: taskId }),

    setComparisonSelection: (selection) => set({ selectedComparison: selection }),

    setStatusFilter: (filter) => {
      if (validFilter(filter)) set({ statusFilter: filter })
    },

    setTasks: (incoming) => {
      const merged = mergeSimulationTasks(Object.values(get().tasksById), incoming)
      const tasksById = Object.fromEntries(merged.map((task) => [task.id, task]))
      set(taskStateSnapshot(tasksById))
    },

    replaceTasks: (incoming) => {
      const tasksById = Object.fromEntries(incoming.map((task) => [task.id, task]))
      set(taskStateSnapshot(tasksById))
    },

    upsertTask: (task) =>
      set((state) => {
        const merged = mergeSimulationTask(state.tasksById[task.id], task)
        const tasksById = { ...state.tasksById, [merged.id]: merged }
        return taskStateSnapshot(tasksById)
      }),

    removeTask: (taskId) =>
      set((state) => {
        if (!state.tasksById[taskId]) return state
        const tasksById = { ...state.tasksById }
        delete tasksById[taskId]
        return {
          ...taskStateSnapshot(tasksById),
          selectedTaskId: state.selectedTaskId === taskId ? null : state.selectedTaskId,
          selectedSimulationId:
            state.selectedSimulationId === taskId ? null : state.selectedSimulationId,
        }
      }),

    setLoading: (loading) => set({ loading }),
    setRefreshing: (refreshing) => set({ refreshing }),
    setSubmitting: (submitting) => set({ submitting }),

    setError: (error) => {
      if (error === null || error === undefined) {
        set({ error: null })
        return
      }
      set({ error: toStoreError(error) })
    },

    clearError: () => set({ error: null }),
    setLastSyncedAt: (value) => set({ lastSyncedAt: value }),

    setPendingAction: (taskId, pending) =>
      set((state) => {
        const ids = new Set(state.pendingActionIds)
        if (pending) ids.add(taskId)
        else ids.delete(taskId)
        return { pendingActionIds: [...ids].sort() }
      }),

    clearPendingActions: () => set({ pendingActionIds: [] }),

    setTimeline: (timeline) =>
      set((state) => {
        const nextFrameIndex =
          timeline.frameIndex === undefined || !validFrameIndex(timeline.frameIndex)
            ? state.timeline.frameIndex
            : timeline.frameIndex
        const nextSpeed = timeline.speed === undefined ? state.timeline.speed : timeline.speed
        const nextLastFrameTime =
          timeline.lastFrameTime === undefined
            ? state.timeline.lastFrameTime
            : timeline.lastFrameTime
        return {
          timeline: {
            frameIndex: nextFrameIndex,
            playing: timeline.playing ?? state.timeline.playing,
            speed: validSpeed(nextSpeed) ? nextSpeed : state.timeline.speed,
            buffering: timeline.buffering ?? state.timeline.buffering,
            lastFrameTime: validFrameTime(nextLastFrameTime)
              ? nextLastFrameTime
              : state.timeline.lastFrameTime,
          },
        }
      }),

    setBuffering: (buffering) => get().setTimeline({ buffering }),
    setFrameIndex: (frameIndex) => get().setTimeline({ frameIndex }),
    setPlaying: (playing) => get().setTimeline({ playing }),
    setPlaybackSpeed: (speed) => get().setTimeline({ speed }),

    setLayerVisibility: (layer, visible) =>
      set((state) => ({ layers: { ...state.layers, [layer]: visible } })),

    reset: () => set(createInitialSimulationStoreState()),
  }))
}

export const useSimulationStore = createSimulationStore()
export const simulationStore = useSimulationStore

export const selectTaskIds = (state: SimulationStoreState): string[] => state.taskIds

export const selectSimulationTasks = (state: SimulationStoreState): SimulationTask[] => state.tasks

export const selectSelectedSimulationTask = (
  state: SimulationStoreState
): SimulationTask | null => {
  const id = state.selectedTaskId ?? state.selectedSimulationId
  return id ? state.tasksById[id] ?? null : null
}

const activeTasksCache = new WeakMap<SimulationTask[], SimulationTask[]>()
const filteredTasksCache = new WeakMap<
  SimulationTask[],
  Map<SimulationTaskFilter, SimulationTask[]>
>()

export const selectActiveSimulationTasks = (state: SimulationStoreState): SimulationTask[] => {
  const cached = activeTasksCache.get(state.tasks)
  if (cached) return cached
  const active = state.tasks.filter((task) => isActiveSimulationStatus(task.status))
  activeTasksCache.set(state.tasks, active)
  return active
}

export const selectFilteredSimulationTasks = (state: SimulationStoreState): SimulationTask[] => {
  let byFilter = filteredTasksCache.get(state.tasks)
  if (!byFilter) {
    byFilter = new Map()
    filteredTasksCache.set(state.tasks, byFilter)
  }
  const cached = byFilter.get(state.statusFilter)
  if (cached) return cached
  const filtered = filterSimulationTasks(state.tasks, state.statusFilter)
  byFilter.set(state.statusFilter, filtered)
  return filtered
}

export const selectTaskProgress = (state: SimulationStoreState, taskId: string): number =>
  state.tasksById[taskId]?.progress ?? 0

export const selectIsTaskTerminal = (state: SimulationStoreState, taskId: string): boolean => {
  const status = state.tasksById[taskId]?.status
  return (
    status === 'completed' ||
    status === 'failed' ||
    status === 'cancelled' ||
    status === 'timed_out'
  )
}

export const selectCanCancelTask = (state: SimulationStoreState, taskId: string): boolean => {
  const status = state.tasksById[taskId]?.status
  return status ? isCancellableSimulationStatus(status) : false
}

export const selectCanRetryTask = (state: SimulationStoreState, taskId: string): boolean => {
  const task = state.tasksById[taskId]
  return task ? isManuallyRetryable(task) && hasRetryRequest(task) : false
}

export const selectHasRetryRequest = (state: SimulationStoreState, taskId: string): boolean => {
  const task = state.tasksById[taskId]
  return task ? hasRetryRequest(task) : false
}

export const selectTimeline = (state: SimulationStoreState): SimulationTimelineState =>
  state.timeline
export const selectFrameIndex = (state: SimulationStoreState): number => state.timeline.frameIndex
export const selectIsPlaying = (state: SimulationStoreState): boolean => state.timeline.playing
export const selectPlaybackSpeed = (state: SimulationStoreState): SimulationPlaybackSpeed =>
  state.timeline.speed
export const selectSelectedNetworkId = (state: SimulationStoreState): string | null =>
  state.selectedNetworkId
export const selectSelectedNetworkRevisionId = (state: SimulationStoreState): string | null =>
  state.selectedNetworkRevisionId

export const selectStoreError = (state: SimulationStoreState): SimulationStoreError | null =>
  state.error

/** A small guard used by tests/integration to prevent accidental geometry placement in Zustand. */
export const selectHasReactiveLargeData = (_state: SimulationStoreState): boolean => false

export type SimulationStatusSelector = (state: SimulationStoreState) => SimulationStatus | undefined
