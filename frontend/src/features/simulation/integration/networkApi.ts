import api from '@/services/api'
import type { NetworkSummary } from '../model/types'

export interface HydraulicNetworkPage {
  items: NetworkSummary[]
  nextCursor: string | null
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null) return {}
  return value as Record<string, unknown>
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function normalizeNetwork(value: unknown): NetworkSummary {
  const record = asRecord(value)
  const id = optionalString(record.id)
  const revisionId = optionalString(record.network_revision_id ?? record.current_revision_id)
  if (!id || !revisionId) throw new TypeError('Network summary is missing identity')
  const bounds =
    Array.isArray(record.bounds) && record.bounds.length === 4 ? record.bounds : undefined
  const currentRevision = asRecord(record.current_revision)
  const counts = asRecord(record.counts)
  return {
    id,
    name: optionalString(record.name),
    status: typeof record.status === 'string' ? record.status : undefined,
    network_revision_id: revisionId,
    target_manifest_id: optionalString(record.target_manifest_id),
    target_manifest_sha256: optionalString(record.target_manifest_sha256),
    parameter_set_id: optionalString(
      record.parameter_set_id ??
        record.parameterSetId ??
        record.default_parameter_set_id ??
        currentRevision.parameter_set_id ??
        currentRevision.parameterSetId
    ),
    bounds: bounds as [number, number, number, number] | undefined,
    counts: Object.fromEntries(
      Object.entries(counts).filter(
        (entry): entry is [string, number] => typeof entry[1] === 'number'
      )
    ),
    simulation_ready: record.simulation_ready === true,
  }
}

function unwrap(value: unknown): Record<string, unknown> {
  const root = asRecord(value)
  return asRecord(root.data ?? value)
}

export async function listHydraulicNetworks(
  params: { status?: string; cursor?: string; limit?: number } = {},
  signal?: AbortSignal
): Promise<HydraulicNetworkPage> {
  const response = await api.get('/networks', { params, signal })
  const root = unwrap(response.data)
  const items = Array.isArray(root.items) ? root.items : []
  return {
    items: items.map(normalizeNetwork),
    nextCursor: typeof root.next_cursor === 'string' ? root.next_cursor : null,
  }
}

export const hydraulicNetworkApi = { list: listHydraulicNetworks }
