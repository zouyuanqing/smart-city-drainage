import { simulationApi } from '../api/client'
import type { SimulationClient } from '../model/types'

export {
  createSimulationApiClient as createSimulationTaskClient,
  getSimulationApiErrorMessage as getSimulationTaskErrorMessage,
} from '../api/client'
export { ApiClientError as SimulationTaskClientError } from '../model/errors'
export { normalizeSimulationTask } from '../model/normalizers'

/** Task panel reuses the foundation-owned authenticated simulation client. */
export const defaultSimulationTaskClient: SimulationClient = simulationApi
