import type { SimulationTask } from './types'

export {
  filterSimulationTasks,
  mergeSimulationTask,
  mergeSimulationTasks,
  simulationRequestFingerprint,
  sortSimulationTasks,
} from '../model/taskState'

/** A failed/timed-out task can be resubmitted when this panel can resolve its original request. */
export function isManuallyRetryable(task: SimulationTask): boolean {
  return task.status === 'failed' || task.status === 'timed_out'
}
