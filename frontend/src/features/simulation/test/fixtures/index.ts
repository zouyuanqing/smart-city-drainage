import {
  normalizeNetworkMapData,
  normalizeSimulationFrame,
  normalizeSimulationTask,
} from '../../model/normalizers'
import type { SimulationSubmitRequest } from '../../model/types'
import apiErrorJson from './api-error-v1.json'
import frameJson from './simulation-frame-v1.json'
import listJson from './simulation-list-v1.json'
import networkMapJson from './network-map-v1.json'
import taskJson from './simulation-task-v1.json'

/** Golden fixtures are validated at import time; no `as unknown` bypass is used. */
export const simulationTaskFixture = normalizeSimulationTask(taskJson)
export const simulationListFixture = listJson
export const simulationFrameFixture = normalizeSimulationFrame(frameJson)
export const networkMapFixture = normalizeNetworkMapData(networkMapJson)
export const apiErrorFixture = apiErrorJson

export const simulationSubmitFixture: SimulationSubmitRequest = {
  network_id: '11111111-1111-4111-8111-111111111111',
  network_revision_id: '22222222-2222-4222-8222-222222222222',
  rainfall_scenario_id: '44444444-4444-4444-8444-444444444444',
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

export function cloneFixture<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

export const fixtures = {
  task: simulationTaskFixture,
  list: simulationListFixture,
  frame: simulationFrameFixture,
  networkMap: networkMapFixture,
  apiError: apiErrorFixture,
  submit: simulationSubmitFixture,
}
