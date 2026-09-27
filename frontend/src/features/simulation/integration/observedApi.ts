import api from '@/services/api'
import type { ObservedPredictedPoint } from '../comparison'

export interface ObservedComparisonResponse {
  schema_version: number | string
  simulation_id: string
  status: string
  pairs: ObservedPredictedPoint[]
  coverage?: Record<string, unknown>
  metrics?: Record<string, unknown>
}

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {}
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function normalizePair(value: unknown): ObservedPredictedPoint | null {
  const pair = record(value)
  const timestamp =
    typeof pair.timestamp === 'string'
      ? pair.timestamp
      : typeof pair.time === 'string'
        ? pair.time
        : undefined
  if (!timestamp || !Number.isFinite(Date.parse(timestamp))) return null
  return {
    timestamp,
    observed: finiteOrNull(pair.observed ?? pair.observed_value),
    predicted: finiteOrNull(pair.predicted ?? pair.predicted_value),
    lower_bound: finiteOrNull(pair.lower_bound),
    upper_bound: finiteOrNull(pair.upper_bound),
  }
}

export async function loadObservedComparison(
  simulationId: string,
  signal?: AbortSignal
): Promise<ObservedComparisonResponse> {
  const response = await api.get(
    `/simulations/${encodeURIComponent(simulationId)}/observed-comparison`,
    {
      signal,
    }
  )
  const root = record(record(response.data).data ?? response.data)
  const pairs = Array.isArray(root.pairs) ? root.pairs.map(normalizePair).filter(Boolean) : []
  return {
    schema_version: typeof root.schema_version === 'number' ? root.schema_version : 1,
    simulation_id: String(root.simulation_id ?? simulationId),
    status: String(root.status ?? 'not_ready'),
    pairs: pairs as ObservedPredictedPoint[],
    coverage: record(root.coverage),
    metrics: record(root.metrics),
  }
}

export const observedComparisonApi = { load: loadObservedComparison }
