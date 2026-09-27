/**
 * Feature-local hydraulic simulation contracts.
 *
 * Wire DTOs intentionally retain the backend snake_case field names.  The
 * conversion boundary in `normalizers.ts` is the only place that should need
 * to change if a transport field is added; components should not depend on
 * Axios response objects or legacy application types.
 */

export const SIMULATION_STATUSES = [
  'queued',
  'leased',
  'running',
  'retry_wait',
  'cancelling',
  'cancelled',
  'completed',
  'failed',
  'timed_out',
] as const

export type SimulationStatus = (typeof SIMULATION_STATUSES)[number]
export const SIMULATION_TASK_STATUSES = SIMULATION_STATUSES
export type SimulationTaskStatus = SimulationStatus

export const TERMINAL_SIMULATION_STATUSES: ReadonlySet<SimulationStatus> = new Set([
  'cancelled',
  'completed',
  'failed',
  'timed_out',
])

export const ACTIVE_SIMULATION_STATUSES: ReadonlySet<SimulationStatus> = new Set([
  'queued',
  'leased',
  'running',
  'retry_wait',
  'cancelling',
])

export type SimulationPhase =
  | 'accepted'
  | 'materializing'
  | 'validating'
  | 'solving'
  | 'quality'
  | 'persisting'
  | 'finalizing'
export type SimulationTaskPhase = SimulationPhase

export type SimulationTaskFilter = 'all' | 'active' | SimulationStatus

export const SIMULATION_SOLVER_IDS = ['swmm_kw', 'swmm_dw', 'py_mw', 'py_dw', 'py_ponding'] as const

export type SimulationSolverId = (typeof SIMULATION_SOLVER_IDS)[number]
export type SimulationSolverSelection = 'auto' | 'explicit'

/**
 * Known stable codes are useful for exhaustiveness checks.  The open string
 * arm lets a newer server add a diagnostic without making an older client
 * unusable; callers must still treat unknown codes as non-retryable unless
 * the HTTP retry policy explicitly says otherwise.
 */
export const KNOWN_SIMULATION_ERROR_CODES = [
  'AUTH_REQUIRED',
  'UNAUTHENTICATED',
  'FORBIDDEN',
  'NETWORK_NOT_FOUND',
  'SIMULATION_NOT_FOUND',
  'SIMULATION_NOT_CANCELLABLE',
  'SIMULATION_TIMEOUT',
  'IDEMPOTENCY_KEY_REUSED',
  'NETWORK_REVISION_CONFLICT',
  'COMPARISON_INCOMPATIBLE',
  'FRAME_RETENTION_EXPIRED',
  'FRAME_SCHEMA_INVALID',
  'FRAME_ATTEMPT_CONFLICT',
  'LEASE_EXPIRED',
  'NUMERICAL_UNSTABLE',
  'NUMERICAL_STABILITY_LIMIT',
  'RESULT_PERSIST_FAILED',
  'MASS_BALANCE_FAILED',
  'OUTPUT_LIMIT_EXCEEDED',
  'PROPERTIES_SCHEMA_INVALID',
  'SOLVER_UNAVAILABLE',
  'SOLVER_VERSION_MISMATCH',
  'UNSUPPORTED_FEATURE',
  'UNSUPPORTED_REGIME',
  'RATE_LIMITED',
  'DEPENDENCY_NOT_READY',
  'SPATIAL_BACKEND_UNAVAILABLE',
  'FORECAST_UNAVAILABLE',
  'FORECAST_POLICY_UNAVAILABLE',
  'VALIDATION_ERROR',
  'MALFORMED_REQUEST',
  'HTTP_ERROR',
] as const

export type KnownSimulationErrorCode = (typeof KNOWN_SIMULATION_ERROR_CODES)[number]
export type SimulationErrorCode = KnownSimulationErrorCode | (string & {})

export interface ApiFieldError {
  field?: string
  loc?: Array<string | number>
  message: string
  code?: string
  retryable: boolean
  details?: Record<string, unknown>
}

interface SimulationSubmitRequestBase {
  network_id: string
  network_revision_id: string
  rainfall_scenario_id: string
  /** The frozen simulation submit schema requires a persisted parameter set. */
  parameter_set_id: string
  /** Null requests a server-created immutable dry snapshot for this revision. */
  initial_state_id?: string | null
  start_at: string
  duration_seconds: number
  frame_interval_seconds: number
  ponding_enabled: boolean
  strict_stability: boolean
}

export interface SimulationAutoSubmitRequest extends SimulationSubmitRequestBase {
  solver_selection: 'auto'
  requested_solver_id?: SimulationSolverId | null
}

export interface SimulationExplicitSubmitRequest extends SimulationSubmitRequestBase {
  solver_selection: 'explicit'
  requested_solver_id: SimulationSolverId
}

/** Closed command boundary for POST /api/simulations. */
export type SimulationSubmitRequest = SimulationAutoSubmitRequest | SimulationExplicitSubmitRequest

export interface SimulationLinks {
  self?: string
  cancel?: string
  result?: string
  frames?: string
}

export interface SimulationTask {
  schema_version?: 1
  id: string
  status: SimulationStatus
  progress: number
  attempt: number
  max_attempts: number
  network_id: string
  network_revision_id: string
  target_manifest_id: string
  target_manifest_sha256: string
  rainfall_scenario_id: string
  /** FK-backed persisted parameter set used by the simulation request. */
  parameter_set_id?: string
  solver_selection: SimulationSolverSelection
  requested_solver_id?: string
  solver_id: string
  solver_version: string
  solver_fallback_reason?: string
  network_revision_content_sha256?: string
  snapshot_content_sha256?: string
  initial_state_id?: string
  initial_state_content_sha256?: string
  next_attempt_at?: string | null
  retry_backoff_seconds?: number | null
  last_retry_error_code?: SimulationErrorCode | null
  error_code?: SimulationErrorCode | null
  error_message?: string | null
  phase?: SimulationPhase
  published_attempt_id?: string | null
  published_attempt_no?: number | null
  frame_count?: number
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  updated_at?: string | null
  links?: SimulationLinks
  /** Retained only when the submit action supplied it; useful for a manual retry. */
  request?: SimulationSubmitRequest
  retry_submission_id?: string
}

export interface SimulationTaskPage {
  items: SimulationTask[]
  nextCursor: string | null
}

export interface SimulationListQuery {
  status?: SimulationStatus
  networkId?: string
  rainfallScenarioId?: string
  cursor?: string
  limit?: number
  signal?: AbortSignal
}

export interface SimulationCommandContext {
  token?: string | null
  signal?: AbortSignal
}

export interface SimulationCommandResult {
  task: SimulationTask
  idempotencyReplayed: boolean
}

export type SimulationTaskListQuery = SimulationListQuery
export type SimulationTaskCommandContext = SimulationCommandContext
export type SimulationTaskCommandResult = SimulationCommandResult

export interface SimulationClient {
  list(query?: SimulationListQuery, context?: SimulationCommandContext): Promise<SimulationTaskPage>
  get(simulationId: string, context?: SimulationCommandContext): Promise<SimulationTask>
  submit(
    request: SimulationSubmitRequest,
    idempotencyKey: string,
    context?: SimulationCommandContext
  ): Promise<SimulationCommandResult>
  cancel(
    simulationId: string,
    idempotencyKey: string,
    context?: SimulationCommandContext
  ): Promise<SimulationCommandResult>
  result?(simulationId: string, context?: SimulationCommandContext): Promise<SimulationResult>
  frames?(simulationId: string, context?: SimulationCommandContext): Promise<SimulationFrameIndex>
  frame?(
    simulationId: string,
    frameIndex: number,
    context?: SimulationCommandContext
  ): Promise<SimulationFrame>
}

export type SimulationTaskClient = SimulationClient

export interface SimulationResult {
  schema_version: 1 | string
  simulation_id: string
  network_revision_id: string
  target_manifest_id: string
  target_manifest_sha256: string
  published_attempt_id: string
  published_attempt_no: number
  frame_count: number
  completed_at: string
  summary: Record<string, unknown>
  quality: Record<string, unknown>
  metrics: Record<string, unknown>
}

export interface SimulationFrameIndexEntry {
  frame_index: number
  frame_time: string
  etag: string
  checksum: string
  bytes: number
}

export interface SimulationFrameIndex {
  schema_version: 1 | string
  simulation_id: string
  network_revision_id: string
  target_manifest_id: string
  target_manifest_sha256: string
  published_attempt_id: string
  published_attempt_no: number
  frame_interval_seconds: number
  frame_count: number
  frames: SimulationFrameIndexEntry[]
}

export type SimulationNullableNumber = number | null
export type SimulationNullableBoolean = boolean | null

export interface SimulationNodeFrameValues {
  feature_index: number[]
  depth_m?: SimulationNullableNumber[]
  fill_ratio?: SimulationNullableNumber[]
  surcharged?: SimulationNullableBoolean[]
}

export interface SimulationPipeFrameValues {
  feature_index: number[]
  fill_ratio?: SimulationNullableNumber[]
  flow_m3s?: SimulationNullableNumber[]
  depth_m?: SimulationNullableNumber[]
  surcharged?: SimulationNullableBoolean[]
}

export interface SimulationPondingFrameValues {
  feature_index: number[]
  depth_m?: SimulationNullableNumber[]
  flow_m3s?: SimulationNullableNumber[]
}

export interface SimulationFrameQuality {
  status: 'verified' | 'warning' | 'failed'
  mass_residual_relative?: number
  max_courant?: number
  [key: string]: unknown
}

export interface SimulationFrame {
  schema_version: 'hydraulic-frame-v1'
  simulation_id: string
  network_revision_id: string
  target_manifest_id: string
  target_manifest_sha256: string
  published_attempt_id: string
  published_attempt_no: number
  solver_id: string
  solver_version?: string
  frame_index: number
  frame_time: string
  interval_seconds?: number
  nodes?: SimulationNodeFrameValues
  pipes?: SimulationPipeFrameValues
  ponds?: SimulationPondingFrameValues
  ponding?: SimulationPondingFrameValues
  quality: SimulationFrameQuality
  checksum?: string
  payload_sha256?: string
}

export interface NetworkSummary {
  id: string
  name?: string
  status?: 'active' | 'archived' | string
  network_revision_id: string
  target_manifest_id?: string
  target_manifest_sha256?: string
  parameter_set_id?: string
  crs?: 'EPSG:4326' | string
  bounds?: [number, number, number, number]
  counts?: {
    nodes?: number
    pipes?: number
    catchments?: number
    ponding_cells?: number
    [key: string]: number | undefined
  }
  simulation_ready?: boolean
  [key: string]: unknown
}

export interface GeoJsonFeature {
  type: 'Feature'
  id: string
  geometry: unknown
  properties: Record<string, unknown>
}

export interface GeoJsonFeatureCollection {
  type: 'FeatureCollection'
  features: GeoJsonFeature[]
}

/**
 * Geometry is a transport/result type only. It must be consumed by a
 * dedicated cache/renderer and is intentionally not part of Zustand state.
 */
export interface NetworkMapData {
  schema_version: 'network-map-v2'
  network_id: string
  network_revision_id: string
  target_manifest_id: string
  target_manifest_sha256: string
  crs: 'EPSG:4326'
  bbox: [number, number, number, number]
  zoom: number
  complete: boolean
  simplified: boolean
  truncated_layers: string[]
  source_counts: Record<string, number>
  returned_counts: Record<string, number>
  nextCursor?: string | null
  nodes: GeoJsonFeatureCollection
  pipes: GeoJsonFeatureCollection
  catchments: GeoJsonFeatureCollection
  ponding?: GeoJsonFeatureCollection
  generated_at: string
}

export const NETWORK_TARGET_TYPES = ['nodes', 'pipes', 'catchments', 'pond_cells'] as const
export type NetworkTargetType = (typeof NETWORK_TARGET_TYPES)[number]

/** Canonical immutable network-revision target ordering consumed by map/frame/A-B. */
export interface SimulationTargetManifest {
  schema_version: 1 | string
  network_id: string
  network_revision_id: string
  target_manifest_id: string
  target_manifest_sha256: string
  target_types: NetworkTargetType[]
  counts: Record<NetworkTargetType, number>
  feature_index: Record<NetworkTargetType, number[]>
  target_ids?: Partial<Record<NetworkTargetType, string[]>>
  units?: Record<string, string>
}

export interface SimulationComparisonMetric {
  key: string
  unit: string
  baseline: number | null
  candidate: number | null
  absolute_delta: number | null
  relative_delta: number | null
  better_direction: 'lower' | 'higher' | null
}

export interface SimulationComparison {
  schema_version: 'simulation-comparison-v2'
  compatible: boolean
  network_revision_id: string
  target_manifest_id: string
  target_manifest_sha256: string
  frame_grid: {
    start_at: string
    end_at: string
    interval_seconds: number
    frame_count: number
  }
  baseline: SimulationComparisonSide
  candidate: SimulationComparisonSide
  metrics: SimulationComparisonMetric[]
  reason?: string
}

export interface SimulationComparisonSide {
  simulation_id: string
  published_attempt_id: string
  published_attempt_no: number
  rainfall_scenario_id: string
  solver: string
  parameters_hash: string
}

export interface SimulationStreamEvent {
  schema_version: 1
  event_id: string
  sequence: number
  event_type: string
  occurred_at: string
  network_id?: string
  simulation_id?: string
  simulation?: Partial<SimulationTask> & { id: string }
  task?: Partial<SimulationTask> & { id: string }
  from_sequence?: number
  to_sequence?: number
}

export interface SimulationIdempotencyReceipt {
  state: 'idle' | 'pending' | 'new' | 'replayed' | 'conflict'
  key: string | null
  taskId: string | null
  message?: string
}
