import {
  MAX_CUSTOM_DURATION_SECONDS,
  MAX_RAINFALL_NAME_LENGTH,
  getExpectedSampleCount,
  isRainfallReturnPeriod,
  isRainfallStepSeconds,
  isValidRainfallSeed,
  isValidRainfallTimestamp,
  type RainfallSample,
  type RainfallScenario,
} from './model'

export type RainfallScenarioKind = 'design' | 'custom'
export type RainfallCreateAlgorithm =
  | 'synthetic-design-chicago-v1'
  | 'synthetic-custom-v1'
  | 'synthetic-random-block-v1'

export interface RainfallCreatePoint {
  point_index: number
  valid_at: string
  intensity_mm_h: number
}

export interface RainfallScenarioCreateRequest {
  network_id: string
  name: string
  kind: RainfallScenarioKind
  return_period_years: Exclude<number | 'custom', 'custom'> | null
  step_seconds: 60 | 300 | 900
  duration_seconds: number
  source_type: 'synthetic'
  algorithm: RainfallCreateAlgorithm
  random_seed: number
  points: RainfallCreatePoint[]
  content_sha256: string
}

export interface RainfallScenarioCreateCommand {
  request: RainfallScenarioCreateRequest
  idempotencyKey: string
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const HASH_RE = /^[0-9a-f]{64}$/

function assertCreateInput(
  scenario: RainfallScenario,
  networkId: string,
  idempotencyKey: string,
  contentSha256?: string
): void {
  if (!UUID_RE.test(networkId)) throw new TypeError('network_id must be a UUID')
  if (
    typeof idempotencyKey !== 'string' ||
    idempotencyKey.length < 8 ||
    idempotencyKey.length > 128
  ) {
    throw new TypeError('Idempotency-Key must contain 8..128 characters')
  }
  if (typeof scenario.name !== 'string' || scenario.name.trim().length === 0) {
    throw new TypeError('Scenario name is required')
  }
  if (scenario.name.length > MAX_RAINFALL_NAME_LENGTH) {
    throw new TypeError('Scenario name is too long')
  }
  if (!isRainfallReturnPeriod(scenario.return_period_years)) {
    throw new TypeError('return_period_years is invalid')
  }
  if (!isRainfallStepSeconds(scenario.step_seconds)) throw new TypeError('step_seconds is invalid')
  if (
    !Number.isSafeInteger(scenario.duration_seconds) ||
    scenario.duration_seconds <= 0 ||
    scenario.duration_seconds > MAX_CUSTOM_DURATION_SECONDS
  ) {
    throw new TypeError('duration_seconds is outside the frozen 72-hour budget')
  }
  if (scenario.source_type !== 'synthetic') throw new TypeError('source_type must be synthetic')
  if (!isValidRainfallSeed(scenario.random_seed)) throw new TypeError('random_seed is invalid')
  if (!Array.isArray(scenario.samples) || scenario.samples.length === 0) {
    throw new TypeError('At least one rainfall point is required')
  }
  if (
    scenario.samples.length !==
    getExpectedSampleCount(scenario.duration_seconds, scenario.step_seconds)
  ) {
    throw new TypeError('rainfall point count does not match duration/step')
  }
  const points = scenario.samples.map(toCreatePoint)
  for (let index = 0; index < points.length; index += 1) {
    if (points[index].point_index !== index) throw new TypeError('point_index must be dense')
    if (index > 0 && Date.parse(points[index].valid_at) <= Date.parse(points[index - 1].valid_at)) {
      throw new TypeError('valid_at values must be strictly increasing')
    }
  }
  if (contentSha256 !== undefined && !HASH_RE.test(contentSha256)) {
    throw new TypeError('content_sha256 must be a SHA-256 hash')
  }
}

function toCreatePoint(sample: RainfallSample, index: number): RainfallCreatePoint {
  if (!isValidRainfallTimestamp(sample.time)) {
    throw new TypeError(`samples[${index}].time must be UTC RFC3339`)
  }
  if (
    typeof sample.rainfall_mm_per_hour !== 'number' ||
    !Number.isFinite(sample.rainfall_mm_per_hour) ||
    sample.rainfall_mm_per_hour < 0
  ) {
    throw new TypeError(`samples[${index}].rainfall_mm_per_hour is invalid`)
  }
  return {
    point_index: index,
    valid_at: sample.time,
    intensity_mm_h: sample.rainfall_mm_per_hour,
  }
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record)
    .filter((key) => record[key] !== undefined)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(',')}}`
}

async function sha256Hex(value: string): Promise<string> {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) throw new Error('Web Crypto SubtleCrypto is required to sign rainfall content')
  const bytes = new TextEncoder().encode(value)
  const digest = await subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

/** Build the exact frozen create request and its required idempotency command. */
export async function buildRainfallScenarioCreateCommand(
  scenario: RainfallScenario,
  networkId: string,
  idempotencyKey: string,
  contentSha256?: string
): Promise<RainfallScenarioCreateCommand> {
  assertCreateInput(scenario, networkId, idempotencyKey, contentSha256)
  const kind: RainfallScenarioKind = scenario.return_period_years === 'custom' ? 'custom' : 'design'
  const base = {
    network_id: networkId,
    name: scenario.name,
    kind,
    return_period_years:
      scenario.return_period_years === 'custom' ? null : scenario.return_period_years,
    step_seconds: scenario.step_seconds,
    duration_seconds: scenario.duration_seconds,
    source_type: 'synthetic' as const,
    algorithm:
      kind === 'custom'
        ? ('synthetic-custom-v1' as const)
        : ('synthetic-design-chicago-v1' as const),
    random_seed: scenario.random_seed,
    points: scenario.samples.map(toCreatePoint),
  }
  const content_sha256 = contentSha256 ?? (await sha256Hex(canonicalJson(base)))
  return {
    request: { ...base, content_sha256 },
    idempotencyKey,
  }
}

export function isRainfallCreateRequest(value: unknown): value is RainfallScenarioCreateRequest {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return (
    typeof record.network_id === 'string' &&
    UUID_RE.test(record.network_id) &&
    (record.kind === 'design' || record.kind === 'custom') &&
    record.source_type === 'synthetic' &&
    typeof record.content_sha256 === 'string' &&
    HASH_RE.test(record.content_sha256) &&
    Array.isArray(record.points)
  )
}
