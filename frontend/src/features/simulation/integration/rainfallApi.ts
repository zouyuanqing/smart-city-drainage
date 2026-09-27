import api from '@/services/api'
import type { AxiosRequestConfig } from 'axios'
import {
  buildRainfallScenarioCreateCommand,
  type RainfallScenarioCreateCommand,
  type RainfallScenarioCreateRequest,
} from '../rainfall/apiAdapter'
import type { RainfallScenario } from '../rainfall/model'

function unwrap(value: unknown): Record<string, unknown> {
  const root = typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
  return typeof root.data === 'object' && root.data !== null
    ? (root.data as Record<string, unknown>)
    : root
}

function normalizeScenario(value: unknown): RainfallScenario {
  const root = unwrap(value)
  const samples = Array.isArray(root.samples)
    ? root.samples
    : Array.isArray(root.points)
      ? root.points
      : []
  return {
    id: typeof root.id === 'string' ? root.id : undefined,
    network_id: typeof root.network_id === 'string' ? root.network_id : undefined,
    root_scenario_id: typeof root.root_scenario_id === 'string' ? root.root_scenario_id : null,
    version: typeof root.version === 'number' ? root.version : 1,
    name: typeof root.name === 'string' ? root.name : 'Rainfall scenario',
    kind: root.kind === 'custom' || root.kind === 'synthetic' ? root.kind : 'design',
    return_period_years:
      root.return_period_years === 'custom' || typeof root.return_period_years === 'number'
        ? root.return_period_years
        : 10,
    duration_seconds: Number(root.duration_seconds ?? 86_400),
    step_seconds: Number(root.step_seconds ?? 300),
    source_type: 'synthetic',
    algorithm:
      root.algorithm === 'synthetic-custom-v1' || root.algorithm === 'synthetic-random-block-v1'
        ? root.algorithm
        : 'synthetic-design-chicago-v1',
    random_seed: Number(root.random_seed ?? 1),
    content_sha256: typeof root.content_sha256 === 'string' ? root.content_sha256 : undefined,
    samples: samples.map((sample, index) => {
      const point =
        typeof sample === 'object' && sample !== null ? (sample as Record<string, unknown>) : {}
      return {
        time: String(point.time ?? point.valid_at ?? new Date(index * 300_000).toISOString()),
        rainfall_mm_per_hour: Number(point.rainfall_mm_per_hour ?? point.intensity_mm_h ?? 0),
        point_index: Number(point.point_index ?? index),
      }
    }),
    created_at: typeof root.created_at === 'string' ? root.created_at : undefined,
  } as RainfallScenario
}

export async function listRainfallScenarios(
  networkId: string,
  signal?: AbortSignal
): Promise<RainfallScenario[]> {
  const response = await api.get('/rainfall-scenarios', {
    params: { network_id: networkId, limit: 200 },
    signal,
  })
  const root = unwrap(response.data)
  const items = Array.isArray(root.items) ? root.items : []
  return items.map(normalizeScenario)
}

export async function createRainfallScenario(
  command: RainfallScenarioCreateCommand,
  options?: AxiosRequestConfig
): Promise<RainfallScenario> {
  const response = await api.post<RainfallScenarioCreateRequest>(
    '/rainfall-scenarios',
    command.request,
    {
      ...options,
      headers: { ...(options?.headers ?? {}), 'Idempotency-Key': command.idempotencyKey },
    }
  )
  return normalizeScenario(response.data)
}

export async function createRainfallScenarioFromDraft(
  scenario: RainfallScenario,
  networkId: string,
  idempotencyKey: string
): Promise<RainfallScenario> {
  return createRainfallScenario(
    await buildRainfallScenarioCreateCommand(scenario, networkId, idempotencyKey)
  )
}

export const rainfallScenarioApi = {
  list: listRainfallScenarios,
  create: createRainfallScenario,
  createFromDraft: createRainfallScenarioFromDraft,
}
