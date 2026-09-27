import type {
  SimulationClient as FoundationSimulationClient,
  SimulationCommandContext as FoundationSimulationCommandContext,
  SimulationCommandResult as FoundationSimulationCommandResult,
  SimulationIdempotencyReceipt as FoundationSimulationIdempotencyReceipt,
  SimulationListQuery as FoundationSimulationListQuery,
  SimulationPhase as FoundationSimulationPhase,
  SimulationStatus as FoundationSimulationStatus,
  SimulationStreamEvent as FoundationSimulationStreamEvent,
  SimulationSubmitRequest as FoundationSimulationSubmitRequest,
  SimulationTask as FoundationSimulationTask,
  SimulationTaskFilter as FoundationSimulationTaskFilter,
  SimulationTaskPage as FoundationSimulationTaskPage,
} from '../model/types'

export {
  ACTIVE_SIMULATION_STATUSES,
  SIMULATION_STATUSES as SIMULATION_TASK_STATUSES,
  TERMINAL_SIMULATION_STATUSES,
} from '../model/types'
export { isActiveSimulationStatus, isTerminalSimulationStatus } from '../model/taskState'

export type SimulationTaskStatus = FoundationSimulationStatus
export type SimulationTaskPhase = FoundationSimulationPhase
export type SimulationTaskFilter = FoundationSimulationTaskFilter
export type SimulationSubmitRequest = FoundationSimulationSubmitRequest
export type SimulationTask = FoundationSimulationTask
export type SimulationTaskPage = FoundationSimulationTaskPage
export type SimulationTaskListQuery = FoundationSimulationListQuery
export type SimulationTaskCommandContext = FoundationSimulationCommandContext
export type SimulationTaskCommandResult = FoundationSimulationCommandResult
export type SimulationTaskClient = Pick<
  FoundationSimulationClient,
  'list' | 'get' | 'submit' | 'cancel'
>
export type SimulationIdempotencyReceipt = FoundationSimulationIdempotencyReceipt
export type SimulationIdempotencyState = SimulationIdempotencyReceipt['state']
export type SimulationTaskStreamEvent = FoundationSimulationStreamEvent
