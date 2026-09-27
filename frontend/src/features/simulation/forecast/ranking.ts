import type {
  ForecastAlert,
  ForecastEvent,
  ForecastRiskItem,
  ForecastRiskRankingResponse,
  ForecastSnapshot,
} from './types'

/** Applies the API's canonical tie-break order: score desc, expected time asc, id asc. */
export function sortRiskItems<T extends Pick<ForecastAlert, 'risk_score' | 'expected_at' | 'id'>>(
  items: readonly T[]
): T[] {
  return [...items].sort(
    (left, right) =>
      right.risk_score - left.risk_score ||
      Date.parse(left.expected_at) - Date.parse(right.expected_at) ||
      left.id.localeCompare(right.id)
  )
}

export function topRiskItems<T extends Pick<ForecastAlert, 'risk_score' | 'expected_at' | 'id'>>(
  items: readonly T[],
  limit: number
): T[] {
  if (!Number.isFinite(limit) || limit <= 0) return []
  return sortRiskItems(items).slice(0, limit)
}

export function summariseTwoHourForecast(
  snapshot: ForecastSnapshot,
  now = Date.now()
): {
  active: number
  observed: number
  predicted: number
  highestRisk: ForecastAlert | null
} {
  const serverNow = Date.parse(snapshot.server_time)
  const referenceNow = Number.isFinite(serverNow) ? serverNow : now
  const horizonStart = referenceNow - 2 * 60 * 60 * 1000
  const horizonEnd = referenceNow + 2 * 60 * 60 * 1000
  const active = snapshot.alerts.filter((alert) => {
    if (alert.status !== 'open' && alert.status !== 'acknowledged') return false
    const expected = Date.parse(alert.expected_at)
    if (!Number.isFinite(expected)) return false
    return alert.phase === 'observed'
      ? expected >= horizonStart && expected <= referenceNow
      : expected >= referenceNow && expected <= horizonEnd
  })
  return {
    active: active.length,
    observed: active.filter((alert) => alert.phase === 'observed').length,
    predicted: active.filter((alert) => alert.phase === 'predicted').length,
    highestRisk: sortRiskItems(active)[0] ?? null,
  }
}

/**
 * Applies an event to a local snapshot. REST remains the resync source of truth.
 * Resolved and expired alerts are retained in the snapshot but excluded from active views.
 */
export function applyForecastEvent(
  alerts: readonly ForecastAlert[],
  event: ForecastEvent,
  expectedNetworkId?: string
): ForecastAlert[] {
  if (expectedNetworkId && event.network_id !== expectedNetworkId) return [...alerts]
  const index = alerts.findIndex((alert) => alert.id === event.alert.id)
  if (index === -1) {
    return event.event_type === 'alert.created' ? [event.alert, ...alerts] : [...alerts]
  }
  const current = alerts[index]
  if (current.status === 'resolved' || current.status === 'expired') return [...alerts]
  if (event.alert.version !== current.version + 1) return [...alerts]

  const next = [...alerts]
  if (event.event_type === 'alert.resolved' || event.event_type === 'alert.expired') {
    next[index] = {
      ...event.alert,
      status: event.event_type === 'alert.resolved' ? 'resolved' : 'expired',
    }
  } else {
    next[index] = event.alert
  }
  return next
}

function isFiniteNumber(value: unknown, min = -Infinity, max = Infinity): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max
}

function normalizeRiskItem(value: unknown): ForecastRiskItem {
  if (typeof value !== 'object' || value === null)
    throw new TypeError('risk item must be an object')
  const item = value as Record<string, unknown>
  const alert = item.alert ?? item
  const score = item.score_components
  if (
    typeof item.policy_version !== 'string' ||
    !item.policy_version ||
    typeof score !== 'object' ||
    score === null
  ) {
    throw new TypeError('risk item is missing policy score metadata')
  }
  const components = score as Record<string, unknown>
  if (
    !['confidence', 'severity', 'urgency', 'impact'].every((key) => isFiniteNumber(components[key]))
  ) {
    throw new TypeError('risk score components must be finite')
  }
  if (
    typeof alert !== 'object' ||
    alert === null ||
    typeof (alert as Record<string, unknown>).id !== 'string' ||
    !isFiniteNumber((alert as Record<string, unknown>).risk_score, 0, 100) ||
    !isFiniteNumber((alert as Record<string, unknown>).confidence, 0, 1)
  ) {
    throw new TypeError('risk item has an invalid alert envelope')
  }
  return {
    ...(alert as unknown as ForecastAlert),
    score_components: {
      confidence: components.confidence as number,
      severity: components.severity as number,
      urgency: components.urgency as number,
      impact: components.impact as number,
    },
    policy_version: item.policy_version,
  }
}

export function normalizeForecastSnapshot(
  value: unknown,
  expectedNetworkId?: string
): ForecastSnapshot {
  const root =
    typeof value === 'object' && value !== null && 'data' in value
      ? (value as { data: unknown }).data
      : value
  if (typeof root !== 'object' || root === null) {
    throw new TypeError('forecast snapshot must be an object')
  }
  const record = root as Record<string, unknown>
  if (
    record.schema_version !== 1 ||
    (record.data_state !== 'ready' &&
      record.data_state !== 'stale' &&
      record.data_state !== 'not_ready') ||
    typeof record.network_id !== 'string' ||
    (expectedNetworkId !== undefined && record.network_id !== expectedNetworkId) ||
    typeof record.server_time !== 'string' ||
    !Number.isFinite(Date.parse(record.server_time)) ||
    !Array.isArray(record.alerts)
  ) {
    throw new TypeError('forecast snapshot metadata is invalid')
  }
  return record as unknown as ForecastSnapshot
}

export function normalizeForecastRiskRanking(
  value: unknown,
  expectedNetworkId?: string
): ForecastRiskRankingResponse {
  const root =
    typeof value === 'object' && value !== null && 'data' in value
      ? (value as { data: unknown }).data
      : value
  if (typeof root !== 'object' || root === null) {
    throw new TypeError('risk-ranking response must be an object')
  }
  const record = root as Record<string, unknown>
  if (
    record.schema_version !== 1 ||
    (record.data_state !== 'ready' &&
      record.data_state !== 'stale' &&
      record.data_state !== 'not_ready') ||
    typeof record.network_id !== 'string' ||
    (expectedNetworkId !== undefined && record.network_id !== expectedNetworkId) ||
    typeof record.risk_policy_version !== 'string' ||
    !record.risk_policy_version ||
    typeof record.server_time !== 'string' ||
    !Number.isFinite(Date.parse(record.server_time)) ||
    !Array.isArray(record.items)
  ) {
    throw new TypeError('risk-ranking response metadata is invalid')
  }
  return {
    schema_version: 1,
    data_state: record.data_state,
    network_id: record.network_id,
    nowcast_run_id:
      record.nowcast_run_id === null || record.nowcast_run_id === undefined
        ? null
        : String(record.nowcast_run_id),
    risk_policy_version: record.risk_policy_version,
    items: record.items.map(normalizeRiskItem),
    server_time: record.server_time,
  }
}
