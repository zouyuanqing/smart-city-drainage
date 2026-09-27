import {
  NETWORK_TARGET_TYPES,
  SIMULATION_SOLVER_IDS,
  SIMULATION_STATUSES,
  type GeoJsonFeature,
  type GeoJsonFeatureCollection,
  type NetworkMapData,
  type NetworkTargetType,
  type SimulationComparison,
  type SimulationComparisonMetric,
  type SimulationFrame,
  type SimulationFrameIndex,
  type SimulationFrameQuality,
  type SimulationListQuery,
  type SimulationNodeFrameValues,
  type SimulationPhase,
  type SimulationPipeFrameValues,
  type SimulationPondingFrameValues,
  type SimulationResult,
  type SimulationSolverId,
  type SimulationSolverSelection,
  type SimulationStatus,
  type SimulationSubmitRequest,
  type SimulationTargetManifest,
  type SimulationTask,
  type SimulationTaskPage,
} from './types'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const SHA256_RE = /^[0-9a-f]{64}$/
const UTC_RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/
const GEOMETRY_TYPES = new Set(['Point', 'LineString', 'Polygon', 'MultiPolygon'])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function fail(message: string): never {
  throw new TypeError(message)
}

function requiredUuid(value: unknown, field: string): string {
  if (typeof value !== 'string' || !UUID_RE.test(value)) {
    fail(`Simulation response has invalid ${field}`)
  }
  return value.toLowerCase()
}

function optionalUuid(value: unknown, field: string): string | undefined {
  if (value === undefined || value === null) return undefined
  return requiredUuid(value, field)
}

function requiredHash(value: unknown, field: string): string {
  if (typeof value !== 'string' || !SHA256_RE.test(value)) {
    fail(`Simulation response has invalid ${field}`)
  }
  return value.toLowerCase()
}

function optionalHash(value: unknown, field: string): string | undefined {
  if (value === undefined || value === null) return undefined
  return requiredHash(value, field)
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== 'string' || value.trim().length === 0) {
    fail(`Simulation response is missing ${field}`)
  }
  return value
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim().length > 0 ? value : undefined
}

function optionalNullableString(value: unknown): string | null | undefined {
  if (value === null) return null
  return optionalString(value)
}

function integer(value: unknown, field: string, min: number, max: number): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min || value > max) {
    fail(`Simulation response has invalid ${field}`)
  }
  return value
}

function optionalNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function optionalNullableNumber(value: unknown): number | null | undefined {
  if (value === null) return null
  return optionalNumber(value)
}

function utcTimestamp(value: unknown, field: string): string {
  const text = requiredString(value, field)
  if (!UTC_RFC3339_RE.test(text) || !Number.isFinite(Date.parse(text))) {
    fail(`Simulation response has invalid ${field}`)
  }
  return text
}

function optionalTimestamp(value: unknown, field: string): string | null | undefined {
  if (value === null) return null
  if (value === undefined) return undefined
  return utcTimestamp(value, field)
}

function assertFiniteTree(value: unknown, field: string): void {
  if (typeof value === 'number' && !Number.isFinite(value)) {
    fail(`Simulation response has invalid ${field}`)
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => assertFiniteTree(entry, `${field}[${index}]`))
    return
  }
  if (isRecord(value)) {
    for (const [key, entry] of Object.entries(value)) assertFiniteTree(entry, `${field}.${key}`)
  }
}

export function normalizeSimulationStatus(value: unknown): SimulationStatus {
  if (typeof value === 'string' && (SIMULATION_STATUSES as readonly string[]).includes(value)) {
    return value as SimulationStatus
  }
  fail('Simulation response contains an unsupported status')
}

function normalizePhase(value: unknown): SimulationPhase | undefined {
  const phases = new Set<SimulationPhase>([
    'accepted',
    'materializing',
    'validating',
    'solving',
    'quality',
    'persisting',
    'finalizing',
  ])
  return typeof value === 'string' && phases.has(value as SimulationPhase)
    ? (value as SimulationPhase)
    : undefined
}

function normalizeSolverSelection(value: unknown): SimulationSolverSelection {
  if (value === 'auto' || value === 'explicit') return value
  fail('Simulation response has invalid solver_selection')
}

function normalizeLinks(value: unknown): SimulationTask['links'] {
  if (value === undefined || value === null) return undefined
  if (!isRecord(value)) fail('Simulation response has invalid links')
  const links: NonNullable<SimulationTask['links']> = {}
  for (const key of ['self', 'cancel', 'result', 'frames'] as const) {
    const link = optionalString(value[key])
    if (link) links[key] = link
  }
  return Object.keys(links).length > 0 ? links : undefined
}

/** Validate the exact submit command before it can reach Axios. */
export function normalizeSimulationSubmitRequest(value: unknown): SimulationSubmitRequest {
  if (!isRecord(value)) fail('Simulation submit request must be an object')
  const solverSelection = normalizeSolverSelection(value.solver_selection)
  const requestedSolverId =
    value.requested_solver_id === null
      ? null
      : optionalString(value.requested_solver_id) ?? undefined
  if (solverSelection === 'explicit' && !requestedSolverId) {
    fail('explicit solver selection requires requested_solver_id')
  }
  if (
    requestedSolverId &&
    !SIMULATION_SOLVER_IDS.includes(requestedSolverId as (typeof SIMULATION_SOLVER_IDS)[number])
  ) {
    fail('requested_solver_id is not a supported solver')
  }
  const solverId = requestedSolverId as SimulationSolverId | null | undefined
  if (value.ponding_enabled === undefined || value.strict_stability === undefined) {
    fail('Simulation submit request requires ponding_enabled and strict_stability')
  }
  if (typeof value.ponding_enabled !== 'boolean' || typeof value.strict_stability !== 'boolean') {
    fail('Simulation submit request has invalid boolean fields')
  }

  const base = {
    network_id: requiredUuid(value.network_id, 'network_id'),
    network_revision_id: requiredUuid(value.network_revision_id, 'network_revision_id'),
    rainfall_scenario_id: requiredUuid(value.rainfall_scenario_id, 'rainfall_scenario_id'),
    parameter_set_id: requiredUuid(value.parameter_set_id, 'parameter_set_id'),
    ...(value.initial_state_id === null
      ? { initial_state_id: null }
      : value.initial_state_id === undefined
        ? {}
        : { initial_state_id: requiredUuid(value.initial_state_id, 'initial_state_id') }),
    start_at: utcTimestamp(value.start_at, 'start_at'),
    duration_seconds: integer(value.duration_seconds, 'duration_seconds', 1, 259_200),
    frame_interval_seconds: integer(
      value.frame_interval_seconds,
      'frame_interval_seconds',
      1,
      86_400
    ),
    ponding_enabled: value.ponding_enabled,
    strict_stability: value.strict_stability,
  }
  if (base.duration_seconds % base.frame_interval_seconds !== 0) {
    fail('duration_seconds must align to frame_interval_seconds')
  }
  if (solverSelection === 'explicit') {
    return {
      ...base,
      solver_selection: 'explicit',
      requested_solver_id: requiredString(solverId, 'requested_solver_id') as SimulationSolverId,
    }
  }
  return {
    ...base,
    solver_selection: 'auto',
    ...(solverId === null
      ? { requested_solver_id: null }
      : solverId
        ? { requested_solver_id: solverId }
        : {}),
  }
}

function normalizeRequest(value: unknown): SimulationSubmitRequest | undefined {
  if (value === undefined || value === null) return undefined
  return normalizeSimulationSubmitRequest(value)
}

/**
 * Normalize an authoritative REST task snapshot. Required identity and progress
 * metadata are never synthesized from defaults.
 */
export function normalizeSimulationTask(value: unknown): SimulationTask {
  if (!isRecord(value)) fail('Simulation task response must be an object')
  if (value.schema_version !== 1) fail('Simulation task response has invalid schema_version')

  const id = requiredUuid(value.id ?? value.simulation_id, 'id')
  const status = normalizeSimulationStatus(value.status)
  if (value.phase !== undefined && !normalizePhase(value.phase)) {
    fail('Simulation task response has invalid phase')
  }
  const progressValue = value.progress ?? value.progress_pct
  const progress = integer(progressValue, 'progress', 0, 100)
  const attempt = integer(value.attempt, 'attempt', 0, 3)
  const maxAttempts = integer(value.max_attempts, 'max_attempts', 1, 3)
  if (maxAttempts !== 3) fail('Simulation task response must use max_attempts=3')
  if (attempt > maxAttempts) fail('Simulation task response has invalid attempt')

  const nextAttemptAt = optionalTimestamp(
    value.next_attempt_at ?? value.nextAttemptAt,
    'next_attempt_at'
  )
  const retryBackoff = optionalNullableNumber(
    value.retry_backoff_seconds ?? value.retryBackoffSeconds
  )
  const lastRetryError = optionalNullableString(
    value.last_retry_error_code ?? value.lastRetryErrorCode
  )
  if (status === 'retry_wait') {
    if (!nextAttemptAt || !retryBackoff || retryBackoff <= 0 || !lastRetryError) {
      fail('retry_wait task is missing frozen retry metadata')
    }
  } else if (nextAttemptAt != null || retryBackoff != null || lastRetryError != null) {
    fail('non-retry_wait task must not contain retry metadata')
  }

  const publishedAttemptId = optionalNullableString(
    value.published_attempt_id ?? value.publishedAttemptId
  )
  const publishedAttemptNo = optionalNullableNumber(
    value.published_attempt_no ?? value.publishedAttemptNo
  )
  const frameCount = value.frame_count ?? value.frameCount
  if (status === 'completed') {
    if (!publishedAttemptId) fail('completed task is missing published_attempt_id')
    requiredUuid(publishedAttemptId, 'published_attempt_id')
    if (!Number.isSafeInteger(publishedAttemptNo) || (publishedAttemptNo as number) < 1) {
      fail('completed task has invalid published_attempt_no')
    }
    integer(frameCount, 'frame_count', 1, Number.MAX_SAFE_INTEGER)
  } else if (publishedAttemptId != null || publishedAttemptNo != null || frameCount != null) {
    fail('non-completed task must not publish a result attempt')
  }

  const errorCode = optionalNullableString(value.error_code ?? value.errorCode)
  if (status === 'cancelled' && errorCode !== 'SIMULATION_CANCELLED') {
    fail('cancelled task must use SIMULATION_CANCELLED')
  }
  if (status === 'timed_out' && errorCode !== 'SIMULATION_TIMEOUT') {
    fail('timed_out task must use SIMULATION_TIMEOUT')
  }

  for (const field of [
    'network_revision_content_sha256',
    'snapshot_content_sha256',
    'initial_state_content_sha256',
  ] as const) {
    if (value[field] !== undefined && !optionalHash(value[field], field)) {
      fail(`Simulation task response has invalid ${field}`)
    }
  }
  for (const field of ['initial_state_id', 'retry_submission_id'] as const) {
    if (value[field] !== undefined && value[field] !== null && !optionalUuid(value[field], field)) {
      fail(`Simulation task response has invalid ${field}`)
    }
  }
  const parameterSetId = optionalUuid(
    value.parameter_set_id ?? value.parameterSetId,
    'parameter_set_id'
  )
  const task: SimulationTask = {
    schema_version: 1,
    id,
    status,
    progress,
    attempt,
    max_attempts: maxAttempts,
    network_id: requiredUuid(value.network_id ?? value.networkId, 'network_id'),
    network_revision_id: requiredUuid(
      value.network_revision_id ?? value.networkRevisionId,
      'network_revision_id'
    ),
    target_manifest_id: requiredUuid(
      value.target_manifest_id ?? value.targetManifestId,
      'target_manifest_id'
    ),
    target_manifest_sha256: requiredHash(
      value.target_manifest_sha256 ?? value.targetManifestSha256,
      'target_manifest_sha256'
    ),
    rainfall_scenario_id: requiredUuid(
      value.rainfall_scenario_id ?? value.rainfallScenarioId,
      'rainfall_scenario_id'
    ),
    ...(parameterSetId ? { parameter_set_id: parameterSetId } : {}),
    solver_selection: normalizeSolverSelection(value.solver_selection),
    solver_id: requiredString(value.solver_id, 'solver_id'),
    solver_version: requiredString(value.solver_version, 'solver_version'),
    created_at: utcTimestamp(value.created_at, 'created_at'),
    ...(optionalString(value.requested_solver_id)
      ? { requested_solver_id: optionalString(value.requested_solver_id) }
      : {}),
    ...(optionalString(value.solver_fallback_reason)
      ? { solver_fallback_reason: optionalString(value.solver_fallback_reason) }
      : {}),
    ...(optionalHash(value.network_revision_content_sha256, 'network_revision_content_sha256')
      ? {
          network_revision_content_sha256: optionalHash(
            value.network_revision_content_sha256,
            'network_revision_content_sha256'
          ),
        }
      : {}),
    ...(optionalHash(value.snapshot_content_sha256, 'snapshot_content_sha256')
      ? {
          snapshot_content_sha256: optionalHash(
            value.snapshot_content_sha256,
            'snapshot_content_sha256'
          ),
        }
      : {}),
    ...(optionalUuid(value.initial_state_id, 'initial_state_id')
      ? { initial_state_id: optionalUuid(value.initial_state_id, 'initial_state_id') }
      : {}),
    ...(optionalHash(value.initial_state_content_sha256, 'initial_state_content_sha256')
      ? {
          initial_state_content_sha256: optionalHash(
            value.initial_state_content_sha256,
            'initial_state_content_sha256'
          ),
        }
      : {}),
    ...(nextAttemptAt !== undefined ? { next_attempt_at: nextAttemptAt } : {}),
    ...(retryBackoff !== undefined ? { retry_backoff_seconds: retryBackoff } : {}),
    ...(lastRetryError !== undefined ? { last_retry_error_code: lastRetryError } : {}),
    ...(errorCode !== undefined ? { error_code: errorCode } : {}),
    ...(optionalNullableString(value.error_message ?? value.errorMessage) !== undefined
      ? { error_message: optionalNullableString(value.error_message ?? value.errorMessage) }
      : {}),
    ...(normalizePhase(value.phase) ? { phase: normalizePhase(value.phase) } : {}),
    ...(publishedAttemptId !== undefined ? { published_attempt_id: publishedAttemptId } : {}),
    ...(publishedAttemptNo !== undefined ? { published_attempt_no: publishedAttemptNo } : {}),
    ...(frameCount !== undefined
      ? { frame_count: integer(frameCount, 'frame_count', 1, Number.MAX_SAFE_INTEGER) }
      : {}),
    ...(optionalTimestamp(value.started_at ?? value.startedAt, 'started_at') !== undefined
      ? { started_at: optionalTimestamp(value.started_at ?? value.startedAt, 'started_at') }
      : {}),
    ...(optionalTimestamp(value.finished_at ?? value.finishedAt, 'finished_at') !== undefined
      ? { finished_at: optionalTimestamp(value.finished_at ?? value.finishedAt, 'finished_at') }
      : {}),
    ...(optionalTimestamp(value.updated_at ?? value.updatedAt, 'updated_at') !== undefined
      ? { updated_at: optionalTimestamp(value.updated_at ?? value.updatedAt, 'updated_at') }
      : {}),
    ...(normalizeLinks(value.links) ? { links: normalizeLinks(value.links) } : {}),
    ...(normalizeRequest(value.request ?? value.submit_request)
      ? { request: normalizeRequest(value.request ?? value.submit_request) }
      : {}),
    ...(optionalUuid(value.retry_submission_id, 'retry_submission_id')
      ? {
          retry_submission_id: optionalUuid(value.retry_submission_id, 'retry_submission_id'),
        }
      : {}),
  }
  return task
}

export function unwrapApiData(value: unknown): unknown {
  if (!isRecord(value)) return value
  return isRecord(value.data) ? value.data : value
}

export function normalizeSimulationTaskPage(value: unknown): SimulationTaskPage {
  const outer = isRecord(value) ? value : undefined
  const root = unwrapApiData(value)
  if (Array.isArray(root)) return { items: root.map(normalizeSimulationTask), nextCursor: null }
  if (!isRecord(root)) fail('Simulation list response must be an object or array')
  const rawItems = root.items ?? root.simulations
  if (!Array.isArray(rawItems)) fail('Simulation list response is missing items')
  const nextCursor =
    outer?.next_cursor ?? outer?.nextCursor ?? root.next_cursor ?? root.nextCursor ?? null
  if (nextCursor !== null && (typeof nextCursor !== 'string' || nextCursor.length === 0)) {
    fail('Simulation list response has invalid next_cursor')
  }
  return { items: rawItems.map(normalizeSimulationTask), nextCursor }
}

function requiredRecord(value: unknown, field: string): Record<string, unknown> {
  if (!isRecord(value)) fail(`Simulation response has invalid ${field}`)
  assertFiniteTree(value, field)
  return value
}

export function normalizeSimulationResult(value: unknown): SimulationResult {
  const root = unwrapApiData(value)
  if (!isRecord(root)) fail('Simulation result must be an object')
  const schemaVersion = root.schema_version ?? root.schemaVersion
  if ((typeof schemaVersion !== 'number' && typeof schemaVersion !== 'string') || !schemaVersion) {
    fail('Simulation result has invalid schema_version')
  }
  return {
    schema_version: schemaVersion as 1 | string,
    simulation_id: requiredUuid(root.simulation_id ?? root.simulationId, 'simulation_id'),
    network_revision_id: requiredUuid(
      root.network_revision_id ?? root.networkRevisionId,
      'network_revision_id'
    ),
    target_manifest_id: requiredUuid(
      root.target_manifest_id ?? root.targetManifestId,
      'target_manifest_id'
    ),
    target_manifest_sha256: requiredHash(
      root.target_manifest_sha256 ?? root.targetManifestSha256,
      'target_manifest_sha256'
    ),
    published_attempt_id: requiredUuid(
      root.published_attempt_id ?? root.publishedAttemptId,
      'published_attempt_id'
    ),
    published_attempt_no: integer(
      root.published_attempt_no ?? root.publishedAttemptNo,
      'published_attempt_no',
      1,
      3
    ),
    frame_count: integer(
      root.frame_count ?? root.frameCount,
      'frame_count',
      1,
      Number.MAX_SAFE_INTEGER
    ),
    completed_at: utcTimestamp(root.completed_at ?? root.completedAt, 'completed_at'),
    summary: requiredRecord(root.summary, 'summary'),
    quality: requiredRecord(root.quality ?? root.quality_report, 'quality'),
    metrics: requiredRecord(root.metrics, 'metrics'),
  }
}

function normalizeFeatureIndexes(value: unknown, field: string): number[] {
  if (!Array.isArray(value)) fail(`${field} must contain feature_index`)
  let previous = -1
  return value.map((entry, index) => {
    const featureIndex = integer(
      entry,
      `${field}.feature_index[${index}]`,
      0,
      Number.MAX_SAFE_INTEGER
    )
    if (featureIndex <= previous) fail(`${field}.feature_index must be strictly ascending`)
    previous = featureIndex
    return featureIndex
  })
}

function normalizeNumberSeries(
  value: unknown,
  indexes: number[],
  field: string
): Array<number | null> {
  if (!Array.isArray(value)) fail(`${field} must be an array`)
  if (value.length !== indexes.length) fail(`${field} count does not match feature_index`)
  return value.map((entry, index) => {
    if (entry === null) return null
    if (typeof entry !== 'number' || !Number.isFinite(entry) || entry < 0) {
      fail(`${field}[${index}] must be a finite non-negative number or null`)
    }
    return entry
  })
}

function normalizeBooleanSeries(
  value: unknown,
  indexes: number[],
  field: string
): Array<boolean | null> {
  if (!Array.isArray(value)) fail(`${field} must be an array`)
  if (value.length !== indexes.length) fail(`${field} count does not match feature_index`)
  return value.map((entry, index) => {
    if (entry === null || typeof entry === 'boolean') return entry
    fail(`${field}[${index}] must be boolean or null`)
  })
}

function normalizeNodeFrame(value: unknown, field: string): SimulationNodeFrameValues {
  const root = requiredRecord(value, field)
  const featureIndex = normalizeFeatureIndexes(root.feature_index, field)
  return {
    feature_index: featureIndex,
    ...(root.depth_m !== undefined
      ? { depth_m: normalizeNumberSeries(root.depth_m, featureIndex, `${field}.depth_m`) }
      : {}),
    ...(root.fill_ratio !== undefined
      ? {
          fill_ratio: normalizeNumberSeries(root.fill_ratio, featureIndex, `${field}.fill_ratio`),
        }
      : {}),
    ...(root.surcharged !== undefined
      ? {
          surcharged: normalizeBooleanSeries(root.surcharged, featureIndex, `${field}.surcharged`),
        }
      : {}),
  }
}

function normalizePipeFrame(value: unknown, field: string): SimulationPipeFrameValues {
  const root = requiredRecord(value, field)
  const featureIndex = normalizeFeatureIndexes(root.feature_index, field)
  return {
    feature_index: featureIndex,
    ...(root.fill_ratio !== undefined
      ? {
          fill_ratio: normalizeNumberSeries(root.fill_ratio, featureIndex, `${field}.fill_ratio`),
        }
      : {}),
    ...(root.flow_m3s !== undefined
      ? { flow_m3s: normalizeNumberSeries(root.flow_m3s, featureIndex, `${field}.flow_m3s`) }
      : {}),
    ...(root.depth_m !== undefined
      ? { depth_m: normalizeNumberSeries(root.depth_m, featureIndex, `${field}.depth_m`) }
      : {}),
    ...(root.surcharged !== undefined
      ? {
          surcharged: normalizeBooleanSeries(root.surcharged, featureIndex, `${field}.surcharged`),
        }
      : {}),
  }
}

function normalizePondingFrame(value: unknown, field: string): SimulationPondingFrameValues {
  const root = requiredRecord(value, field)
  const featureIndex = normalizeFeatureIndexes(root.feature_index, field)
  return {
    feature_index: featureIndex,
    ...(root.depth_m !== undefined
      ? { depth_m: normalizeNumberSeries(root.depth_m, featureIndex, `${field}.depth_m`) }
      : {}),
    ...(root.flow_m3s !== undefined
      ? { flow_m3s: normalizeNumberSeries(root.flow_m3s, featureIndex, `${field}.flow_m3s`) }
      : {}),
  }
}

function normalizeFrameQuality(value: unknown): SimulationFrameQuality {
  const root = requiredRecord(value, 'quality')
  if (root.status !== 'verified' && root.status !== 'warning' && root.status !== 'failed') {
    fail('Simulation frame has invalid quality.status')
  }
  assertFiniteTree(root, 'quality')
  return root as unknown as SimulationFrameQuality
}

export function normalizeSimulationFrameIndex(value: unknown): SimulationFrameIndex {
  const root = unwrapApiData(value)
  if (!isRecord(root)) fail('Simulation frame index must be an object')
  const rawFrames = root.frames
  if (!Array.isArray(rawFrames)) fail('Simulation frame index is missing frames')
  const frameCount = integer(
    root.frame_count ?? root.frameCount,
    'frame_count',
    1,
    Number.MAX_SAFE_INTEGER
  )
  const intervalSeconds = integer(
    root.frame_interval_seconds ?? root.frameIntervalSeconds,
    'frame_interval_seconds',
    1,
    86_400
  )
  const frames = rawFrames.map((item, index) => {
    if (!isRecord(item)) fail(`Simulation frame entry[${index}] must be an object`)
    if (item.frame_index !== index) fail('Simulation frame indexes must be dense and ascending')
    const frameTime = utcTimestamp(item.frame_time ?? item.time, 'frame_time')
    if (index > 0) {
      const previous = utcTimestamp(
        (rawFrames[index - 1] as Record<string, unknown>).frame_time ??
          (rawFrames[index - 1] as Record<string, unknown>).time,
        'frame_time'
      )
      if (Date.parse(frameTime) - Date.parse(previous) !== intervalSeconds * 1_000) {
        fail('Simulation frame index has an invalid time grid')
      }
    }
    return {
      frame_index: index,
      frame_time: frameTime,
      etag: requiredString(item.etag, 'etag'),
      checksum: requiredHash(item.checksum, 'checksum'),
      bytes: integer(item.bytes, 'bytes', 0, 67_108_864),
    }
  })
  if (frames.length !== frameCount) fail('Simulation frame index has an invalid frame_count')
  return {
    schema_version: (root.schema_version ?? root.schemaVersion) as 1 | string,
    simulation_id: requiredUuid(root.simulation_id ?? root.simulationId, 'simulation_id'),
    network_revision_id: requiredUuid(
      root.network_revision_id ?? root.networkRevisionId,
      'network_revision_id'
    ),
    target_manifest_id: requiredUuid(
      root.target_manifest_id ?? root.targetManifestId,
      'target_manifest_id'
    ),
    target_manifest_sha256: requiredHash(
      root.target_manifest_sha256 ?? root.targetManifestSha256,
      'target_manifest_sha256'
    ),
    published_attempt_id: requiredUuid(
      root.published_attempt_id ?? root.publishedAttemptId,
      'published_attempt_id'
    ),
    published_attempt_no: integer(
      root.published_attempt_no ?? root.publishedAttemptNo,
      'published_attempt_no',
      1,
      3
    ),
    frame_interval_seconds: intervalSeconds,
    frame_count: frameCount,
    frames,
  }
}

export function normalizeSimulationFrame(value: unknown): SimulationFrame {
  const root = unwrapApiData(value)
  if (!isRecord(root)) fail('Simulation frame must be an object')
  if (root.schema_version !== 'hydraulic-frame-v1') {
    fail('Simulation frame has invalid schema_version')
  }
  const frameIndex = integer(
    root.frame_index ?? root.frameIndex,
    'frame_index',
    0,
    Number.MAX_SAFE_INTEGER
  )
  const checksum = optionalHash(root.checksum, 'checksum')
  const payloadSha256 = optionalHash(root.payload_sha256, 'payload_sha256')
  return {
    schema_version: 'hydraulic-frame-v1',
    simulation_id: requiredUuid(root.simulation_id ?? root.simulationId, 'simulation_id'),
    network_revision_id: requiredUuid(
      root.network_revision_id ?? root.networkRevisionId,
      'network_revision_id'
    ),
    target_manifest_id: requiredUuid(
      root.target_manifest_id ?? root.targetManifestId,
      'target_manifest_id'
    ),
    target_manifest_sha256: requiredHash(
      root.target_manifest_sha256 ?? root.targetManifestSha256,
      'target_manifest_sha256'
    ),
    published_attempt_id: requiredUuid(
      root.published_attempt_id ?? root.publishedAttemptId,
      'published_attempt_id'
    ),
    published_attempt_no: integer(
      root.published_attempt_no ?? root.publishedAttemptNo,
      'published_attempt_no',
      1,
      3
    ),
    solver_id: requiredString(root.solver_id, 'solver_id'),
    ...(optionalString(root.solver_version)
      ? { solver_version: optionalString(root.solver_version) }
      : {}),
    frame_index: frameIndex,
    frame_time: utcTimestamp(root.frame_time ?? root.time, 'frame_time'),
    ...(root.interval_seconds !== undefined
      ? { interval_seconds: integer(root.interval_seconds, 'interval_seconds', 1, 86_400) }
      : {}),
    ...(root.nodes !== undefined ? { nodes: normalizeNodeFrame(root.nodes, 'nodes') } : {}),
    ...(root.pipes !== undefined ? { pipes: normalizePipeFrame(root.pipes, 'pipes') } : {}),
    ...(root.ponding !== undefined
      ? { ponding: normalizePondingFrame(root.ponding, 'ponding') }
      : {}),
    quality: normalizeFrameQuality(root.quality),
    ...(checksum ? { checksum } : {}),
    ...(payloadSha256 ? { payload_sha256: payloadSha256 } : {}),
  }
}

function validateCoordinates(value: unknown, field: string, depth = 0): void {
  if (depth > 32 || !Array.isArray(value) || value.length === 0) {
    fail(`${field} has invalid coordinates`)
  }
  if (value.every((entry) => typeof entry === 'number')) {
    if (value.length !== 2) fail(`${field} must contain [longitude, latitude]`)
    const [longitude, latitude] = value as number[]
    if (
      !Number.isFinite(longitude) ||
      !Number.isFinite(latitude) ||
      longitude < -180 ||
      longitude > 180 ||
      latitude < -90 ||
      latitude > 90
    ) {
      fail(`${field} has out-of-range coordinates`)
    }
    return
  }
  value.forEach((entry, index) => validateCoordinates(entry, `${field}[${index}]`, depth + 1))
}

function normalizeGeoJsonFeature(value: unknown, field: string): GeoJsonFeature {
  if (!isRecord(value) || value.type !== 'Feature') fail(`${field} must be a GeoJSON Feature`)
  const id = requiredUuid(value.id, `${field}.id`)
  const properties = requiredRecord(value.properties, `${field}.properties`)
  const featureIndex = integer(
    properties.feature_index,
    `${field}.properties.feature_index`,
    0,
    Number.MAX_SAFE_INTEGER
  )
  if (!isRecord(value.geometry) || !GEOMETRY_TYPES.has(String(value.geometry.type))) {
    fail(`${field}.geometry is invalid`)
  }
  validateCoordinates(value.geometry.coordinates, `${field}.geometry.coordinates`)
  return {
    type: 'Feature',
    id,
    geometry: value.geometry,
    properties: { ...properties, feature_index: featureIndex },
  }
}

function normalizeFeatureCollection(value: unknown, field: string): GeoJsonFeatureCollection {
  if (!isRecord(value) || value.type !== 'FeatureCollection' || !Array.isArray(value.features)) {
    fail(`${field} must be a GeoJSON FeatureCollection`)
  }
  let previous = -1
  const features = value.features.map((feature, index) => {
    const normalized = normalizeGeoJsonFeature(feature, `${field}.features[${index}]`)
    const featureIndex = normalized.properties.feature_index as number
    if (featureIndex <= previous)
      fail(`${field}.properties.feature_index must be strictly ascending`)
    previous = featureIndex
    return normalized
  })
  return { type: 'FeatureCollection', features }
}

function normalizeCounts(value: unknown, field: string): Record<string, number> {
  const root = requiredRecord(value, field)
  return Object.fromEntries(
    Object.entries(root).map(([key, count]) => [
      key,
      integer(count, `${field}.${key}`, 0, Number.MAX_SAFE_INTEGER),
    ])
  )
}

function normalizeBbox(value: unknown): [number, number, number, number] {
  if (!Array.isArray(value) || value.length !== 4) fail('Network map data has invalid bbox')
  const bbox = value.map((entry, index) => {
    if (typeof entry !== 'number' || !Number.isFinite(entry)) {
      fail(`Network map bbox[${index}] must be finite`)
    }
    return entry
  }) as [number, number, number, number]
  const [west, south, east, north] = bbox
  if (west < -180 || east > 180 || south < -90 || north > 90 || west > east || south > north) {
    fail('Network map data has invalid bbox')
  }
  return bbox
}

export function normalizeNetworkMapData(value: unknown): NetworkMapData {
  const outer = isRecord(value) ? value : undefined
  const root = unwrapApiData(value)
  if (!isRecord(root)) fail('Network map data must be an object')
  if (root.schema_version !== 'network-map-v2') fail('Network map data has invalid schema_version')
  if (root.crs !== 'EPSG:4326') fail('Network map data must use EPSG:4326')
  if (typeof root.complete !== 'boolean' || typeof root.simplified !== 'boolean') {
    fail('Network map data has invalid completeness metadata')
  }
  const truncatedLayers = root.truncated_layers
  if (
    !Array.isArray(truncatedLayers) ||
    truncatedLayers.some((layer) => typeof layer !== 'string' || layer.length === 0)
  ) {
    fail('Network map data has invalid truncated_layers')
  }
  if (root.complete && truncatedLayers.length > 0) {
    fail('complete network map data must not contain truncated_layers')
  }
  if (!root.complete && truncatedLayers.length === 0) {
    fail('partial network map data must identify truncated_layers')
  }
  const nodes = normalizeFeatureCollection(root.nodes, 'nodes')
  const pipes = normalizeFeatureCollection(root.pipes, 'pipes')
  const catchments = normalizeFeatureCollection(root.catchments, 'catchments')
  const ponding =
    root.ponding === undefined ? undefined : normalizeFeatureCollection(root.ponding, 'ponding')
  const returnedCounts = normalizeCounts(root.returned_counts, 'returned_counts')
  const nextCursor =
    typeof outer?.next_cursor === 'string'
      ? outer.next_cursor
      : typeof root.next_cursor === 'string'
        ? root.next_cursor
        : typeof outer?.nextCursor === 'string'
          ? outer.nextCursor
          : typeof root.nextCursor === 'string'
            ? root.nextCursor
            : null
  for (const [layer, collection] of Object.entries({
    nodes,
    pipes,
    catchments,
    ...(ponding ? { ponding } : {}),
  })) {
    if (returnedCounts[layer] !== collection.features.length) {
      fail(`returned_counts.${layer} does not match the feature collection`)
    }
  }
  return {
    schema_version: 'network-map-v2',
    network_id: requiredUuid(root.network_id, 'network_id'),
    network_revision_id: requiredUuid(root.network_revision_id, 'network_revision_id'),
    target_manifest_id: requiredUuid(root.target_manifest_id, 'target_manifest_id'),
    target_manifest_sha256: requiredHash(root.target_manifest_sha256, 'target_manifest_sha256'),
    crs: 'EPSG:4326',
    bbox: normalizeBbox(root.bbox),
    zoom: integer(root.zoom, 'zoom', 0, 24),
    complete: root.complete,
    simplified: root.simplified,
    truncated_layers: [...truncatedLayers],
    source_counts: normalizeCounts(root.source_counts, 'source_counts'),
    returned_counts: returnedCounts,
    ...(nextCursor ? { nextCursor } : {}),
    nodes,
    pipes,
    catchments,
    ...(ponding ? { ponding } : {}),
    generated_at: utcTimestamp(root.generated_at, 'generated_at'),
  }
}

export function normalizeSimulationTargetManifest(value: unknown): SimulationTargetManifest {
  const root = unwrapApiData(value)
  if (!isRecord(root)) fail('Network target manifest must be an object')
  const manifest = isRecord(root.manifest) ? root.manifest : root
  const schemaVersion = root.schema_version ?? manifest.schema_version
  if ((typeof schemaVersion !== 'number' && typeof schemaVersion !== 'string') || !schemaVersion) {
    fail('Network target manifest has invalid schema_version')
  }
  const rawTargetTypes = manifest.target_types
  if (!Array.isArray(rawTargetTypes) || rawTargetTypes.length !== NETWORK_TARGET_TYPES.length) {
    fail('Network target manifest has invalid target_types')
  }
  const targetTypes = [...rawTargetTypes] as NetworkTargetType[]
  if (
    new Set(targetTypes).size !== NETWORK_TARGET_TYPES.length ||
    targetTypes.some((type) => !NETWORK_TARGET_TYPES.includes(type))
  ) {
    fail('Network target manifest has invalid target_types')
  }
  const rawCounts = requiredRecord(manifest.counts, 'counts')
  const rawIndexes = requiredRecord(manifest.feature_index, 'feature_index')
  const counts = {} as Record<NetworkTargetType, number>
  const featureIndex = {} as Record<NetworkTargetType, number[]>
  for (const type of NETWORK_TARGET_TYPES) {
    counts[type] = integer(rawCounts[type], `counts.${type}`, 0, Number.MAX_SAFE_INTEGER)
    const indexes = rawIndexes[type]
    if (!Array.isArray(indexes) || indexes.length !== counts[type]) {
      fail(`feature_index.${type} does not match counts.${type}`)
    }
    featureIndex[type] = indexes.map((entry, index) => {
      if (entry !== index) fail(`feature_index.${type} must be dense and ascending from zero`)
      return entry
    })
  }
  let targetIds: SimulationTargetManifest['target_ids']
  if (manifest.target_ids !== undefined) {
    const ids = requiredRecord(manifest.target_ids, 'target_ids')
    targetIds = {}
    for (const type of NETWORK_TARGET_TYPES) {
      const values = ids[type]
      if (!Array.isArray(values) || values.length !== counts[type]) {
        fail(`target_ids.${type} does not match counts.${type}`)
      }
      targetIds[type] = values.map((entry, index) =>
        requiredUuid(entry, `target_ids.${type}[${index}]`)
      )
    }
  }
  return {
    schema_version: schemaVersion as 1 | string,
    network_id: requiredUuid(root.network_id ?? manifest.network_id, 'network_id'),
    network_revision_id: requiredUuid(
      root.network_revision_id ?? manifest.network_revision_id,
      'network_revision_id'
    ),
    target_manifest_id: requiredUuid(
      root.target_manifest_id ?? root.id ?? manifest.target_manifest_id ?? manifest.id,
      'target_manifest_id'
    ),
    target_manifest_sha256: requiredHash(
      root.target_manifest_sha256 ??
        root.content_sha256 ??
        manifest.target_manifest_sha256 ??
        manifest.content_sha256,
      'target_manifest_sha256'
    ),
    target_types: targetTypes,
    counts,
    feature_index: featureIndex,
    ...(targetIds ? { target_ids: targetIds } : {}),
    ...(manifest.units !== undefined
      ? { units: requiredRecord(manifest.units, 'units') as Record<string, string> }
      : {}),
  }
}

function normalizeMetric(value: unknown, index: number): SimulationComparisonMetric {
  if (!isRecord(value)) fail(`comparison.metrics[${index}] must be an object`)
  const optionalMetricNumber = (field: string): number | null => {
    const entry = value[field]
    if (entry === null) return null
    if (typeof entry !== 'number' || !Number.isFinite(entry)) {
      fail(`comparison.metrics[${index}].${field} must be finite or null`)
    }
    return entry
  }
  const betterDirection = value.better_direction
  if (betterDirection !== null && betterDirection !== 'lower' && betterDirection !== 'higher') {
    fail(`comparison.metrics[${index}].better_direction is invalid`)
  }
  return {
    key: requiredString(value.key, `comparison.metrics[${index}].key`),
    unit: requiredString(value.unit, `comparison.metrics[${index}].unit`),
    baseline: optionalMetricNumber('baseline'),
    candidate: optionalMetricNumber('candidate'),
    absolute_delta: optionalMetricNumber('absolute_delta'),
    relative_delta: optionalMetricNumber('relative_delta'),
    better_direction: betterDirection,
  }
}

function normalizeComparisonSide(value: unknown, field: string): SimulationComparison['baseline'] {
  if (!isRecord(value)) fail(`${field} must be an object`)
  const rawHash = requiredString(value.parameters_hash, `${field}.parameters_hash`)
  const normalizedHash = rawHash.startsWith('sha256:') ? rawHash.slice(7) : rawHash
  requiredHash(normalizedHash, `${field}.parameters_hash`)
  return {
    simulation_id: requiredUuid(value.simulation_id, `${field}.simulation_id`),
    published_attempt_id: requiredUuid(value.published_attempt_id, `${field}.published_attempt_id`),
    published_attempt_no: integer(
      value.published_attempt_no,
      `${field}.published_attempt_no`,
      1,
      3
    ),
    rainfall_scenario_id: requiredUuid(value.rainfall_scenario_id, `${field}.rainfall_scenario_id`),
    solver: requiredString(value.solver, `${field}.solver`),
    parameters_hash: rawHash,
  }
}

export function normalizeSimulationComparison(value: unknown): SimulationComparison {
  const root = unwrapApiData(value)
  if (!isRecord(root)) fail('Simulation comparison must be an object')
  if (root.schema_version !== 'simulation-comparison-v2') {
    fail('Simulation comparison has invalid schema_version')
  }
  if (typeof root.compatible !== 'boolean') fail('Simulation comparison has invalid compatible')
  if (!isRecord(root.frame_grid)) fail('Simulation comparison has invalid frame_grid')
  const startAt = utcTimestamp(root.frame_grid.start_at, 'frame_grid.start_at')
  const endAt = utcTimestamp(root.frame_grid.end_at, 'frame_grid.end_at')
  const intervalSeconds = integer(
    root.frame_grid.interval_seconds,
    'frame_grid.interval_seconds',
    1,
    86_400
  )
  const frameCount = integer(
    root.frame_grid.frame_count,
    'frame_grid.frame_count',
    1,
    Number.MAX_SAFE_INTEGER
  )
  if (Date.parse(endAt) - Date.parse(startAt) !== (frameCount - 1) * intervalSeconds * 1_000) {
    fail('Simulation comparison has an invalid frame grid')
  }
  if (!Array.isArray(root.metrics)) fail('Simulation comparison is missing metrics')
  if (root.compatible === false && !optionalString(root.reason)) {
    fail('incompatible simulation comparison requires a reason')
  }
  return {
    schema_version: 'simulation-comparison-v2',
    compatible: root.compatible,
    network_revision_id: requiredUuid(root.network_revision_id, 'network_revision_id'),
    target_manifest_id: requiredUuid(root.target_manifest_id, 'target_manifest_id'),
    target_manifest_sha256: requiredHash(root.target_manifest_sha256, 'target_manifest_sha256'),
    frame_grid: {
      start_at: startAt,
      end_at: endAt,
      interval_seconds: intervalSeconds,
      frame_count: frameCount,
    },
    baseline: normalizeComparisonSide(root.baseline, 'baseline'),
    candidate: normalizeComparisonSide(root.candidate, 'candidate'),
    metrics: root.metrics.map(normalizeMetric),
    ...(optionalString(root.reason) ? { reason: optionalString(root.reason) } : {}),
  }
}

/** Compatibility aliases for callers that use “to/from” naming at DTO boundaries. */
export const toSimulationTask = normalizeSimulationTask
export const fromSimulationTask = normalizeSimulationTask
export const toSimulationTaskPage = normalizeSimulationTaskPage
export const toSimulationFrame = normalizeSimulationFrame
export const toNetworkMapData = normalizeNetworkMapData
export const toSimulationTargetManifest = normalizeSimulationTargetManifest
export const toSimulationComparison = normalizeSimulationComparison

export function simulationListQueryParams(query: SimulationListQuery = {}): Record<string, string> {
  const params: Record<string, string> = {}
  if (query.status) params.status = query.status
  if (query.networkId) params.network_id = requiredUuid(query.networkId, 'network_id')
  if (query.rainfallScenarioId) {
    params.rainfall_scenario_id = requiredUuid(query.rainfallScenarioId, 'rainfall_scenario_id')
  }
  if (query.cursor !== undefined) {
    if (typeof query.cursor !== 'string' || query.cursor.length === 0) {
      fail('Simulation list cursor must be a non-empty string')
    }
    params.cursor = query.cursor
  }
  const limit = query.limit === undefined ? 50 : query.limit
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 200) {
    fail('Simulation list limit must be an integer between 1 and 200')
  }
  params.limit = String(limit)
  return params
}
