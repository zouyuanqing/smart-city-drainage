import type {
  FrameFields,
  NodeFrameData,
  PipeFrameData,
  PondingFrameData,
  SimulationFrame,
} from './types'

export const DEFAULT_FRAME_CACHE_BYTES = 256 * 1024 * 1024
const HASH_RE = /^[0-9a-f]{64}$/
const UTC_RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/

export interface CachedSeries {
  featureIndex: Uint32Array
  values: Float32Array
  /** 0 = finite value, 1 = null/missing. */
  flags: Uint8Array
}

export type CachedLayerData = Record<string, CachedSeries>

export interface CachedFrame {
  key: string
  simulationId: string
  networkRevisionId: string
  targetManifestId: string
  targetManifestSha256: string
  publishedAttemptId: string
  publishedAttemptNo: number
  frameIndex: number
  frameTime: string
  checksum?: string
  nodes: CachedLayerData
  pipes: CachedLayerData
  ponding: CachedLayerData
  bytes: number
}

export interface FrameValidationResult {
  valid: boolean
  errors: string[]
}

function viewBytes(view: ArrayBufferView): number {
  return view.byteLength
}

function seriesBytes(series: CachedSeries): number {
  return viewBytes(series.featureIndex) + viewBytes(series.values) + viewBytes(series.flags)
}

function layerBytes(layer: CachedLayerData): number {
  return Object.values(layer).reduce((total, series) => total + seriesBytes(series), 0)
}

function frameMetadataBytes(frame: CachedFrame): number {
  const metadata = {
    key: frame.key,
    simulationId: frame.simulationId,
    networkRevisionId: frame.networkRevisionId,
    targetManifestId: frame.targetManifestId,
    targetManifestSha256: frame.targetManifestSha256,
    publishedAttemptId: frame.publishedAttemptId,
    publishedAttemptNo: frame.publishedAttemptNo,
    frameIndex: frame.frameIndex,
    frameTime: frame.frameTime,
    checksum: frame.checksum ?? null,
    layers: [frame.nodes, frame.pipes, frame.ponding].map((layer) => Object.keys(layer).sort()),
  }
  return new TextEncoder().encode(JSON.stringify(metadata)).byteLength
}

/** Deterministic cache charge for every typed series plus its frame metadata. */
export function cachedFrameBytes(frame: CachedFrame): number {
  return (
    layerBytes(frame.nodes) +
    layerBytes(frame.pipes) +
    layerBytes(frame.ponding) +
    frameMetadataBytes(frame)
  )
}

function validateIndexes(
  layer: string,
  field: string,
  indexes: readonly number[] | undefined,
  values: readonly unknown[] | undefined,
  errors: string[]
): number[] {
  if (!indexes && !values) return []
  if (!indexes || !values) {
    errors.push(`${layer}.${field} must provide feature_index and values together`)
    return []
  }
  if (indexes.length !== values.length) {
    errors.push(
      `${layer}.${field} count mismatch: ${indexes.length} indexes vs ${values.length} values`
    )
  }
  let previous = -1
  for (let index = 0; index < Math.min(indexes.length, values.length); index += 1) {
    const featureIndex = indexes[index]
    if (!Number.isSafeInteger(featureIndex) || featureIndex < 0) {
      errors.push(`${layer}.${field} has invalid feature_index at row ${index}`)
    } else if (featureIndex <= previous) {
      errors.push(`${layer}.${field} feature_index must be strictly ascending`)
    }
    previous = featureIndex
  }
  return [...indexes]
}

function validateSeries(
  layer: string,
  field: string,
  indexes: readonly number[] | undefined,
  values: readonly (number | boolean | null)[] | undefined,
  errors: string[]
): void {
  if (!indexes && !values) return
  const checkedIndexes = validateIndexes(layer, field, indexes, values, errors)
  const count = Math.min(checkedIndexes.length, values?.length ?? 0)
  for (let index = 0; index < count; index += 1) {
    const value = values?.[index]
    if (typeof value === 'boolean') continue
    if (value !== null && (typeof value !== 'number' || !Number.isFinite(value) || value < 0)) {
      errors.push(`${layer}.${field} has invalid value at row ${index}`)
    }
  }
}

function validateLayer(
  layer: string,
  data: { feature_index: readonly number[]; [field: string]: unknown } | undefined,
  fields: readonly string[],
  errors: string[]
): void {
  if (!data) return
  for (const field of fields) {
    const values = data[field]
    if (values === undefined) continue
    validateSeries(
      layer,
      field,
      data.feature_index,
      values as readonly (number | boolean | null)[],
      errors
    )
  }
}

export function validateFrame(
  frame: SimulationFrame,
  expectedChecksum?: string
): FrameValidationResult {
  const errors: string[] = []
  if (frame.schema_version !== 'hydraulic-frame-v1') {
    errors.push('schema_version must be hydraulic-frame-v1')
  }
  if (!Number.isSafeInteger(frame.frame_index) || frame.frame_index < 0) {
    errors.push('frame_index must be a non-negative integer')
  }
  if (
    typeof frame.frame_time !== 'string' ||
    !UTC_RFC3339_RE.test(frame.frame_time) ||
    !Number.isFinite(Date.parse(frame.frame_time))
  ) {
    errors.push('frame_time must be a valid UTC RFC3339 timestamp')
  }
  for (const [field, value] of [
    ['network_revision_id', frame.network_revision_id],
    ['target_manifest_id', frame.target_manifest_id],
    ['published_attempt_id', frame.published_attempt_id],
  ] as const) {
    if (typeof value !== 'string' || value.length === 0) errors.push(`${field} is required`)
  }
  if (
    typeof frame.target_manifest_sha256 !== 'string' ||
    !HASH_RE.test(frame.target_manifest_sha256)
  ) {
    errors.push('target_manifest_sha256 must be a SHA-256 hash')
  }
  if (!Number.isSafeInteger(frame.published_attempt_no) || frame.published_attempt_no < 1) {
    errors.push('published_attempt_no must be a positive integer')
  }
  const checksum = frame.checksum ?? frame.payload_sha256
  if (checksum !== undefined && (typeof checksum !== 'string' || !HASH_RE.test(checksum))) {
    errors.push('frame checksum must be a SHA-256 hash')
  }
  if (expectedChecksum) {
    if (!checksum) errors.push('frame checksum is required')
    else if (checksum !== expectedChecksum) errors.push('frame checksum does not match the index')
  }
  validateLayer(
    'nodes',
    frame.nodes as NodeFrameData & { [field: string]: unknown },
    ['depth_m', 'fill_ratio', 'surcharged'],
    errors
  )
  validateLayer(
    'pipes',
    frame.pipes as PipeFrameData & { [field: string]: unknown },
    ['fill_ratio', 'flow_m3s', 'depth_m', 'surcharged'],
    errors
  )
  validateLayer(
    'ponding',
    frame.ponding as PondingFrameData & { [field: string]: unknown },
    ['depth_m', 'flow_m3s'],
    errors
  )
  return { valid: errors.length === 0, errors }
}

function createSeries(
  indexes: readonly number[],
  values: readonly (number | boolean | null)[]
): CachedSeries {
  const featureIndex = new Uint32Array(indexes.length)
  const typedValues = new Float32Array(values.length)
  const flags = new Uint8Array(values.length)
  for (let index = 0; index < indexes.length; index += 1) {
    featureIndex[index] = indexes[index]
    const value = values[index]
    if (value === null || value === undefined) {
      typedValues[index] = Number.NaN
      flags[index] = 1
    } else if (typeof value === 'boolean') {
      typedValues[index] = value ? 1 : 0
      flags[index] = 0
    } else {
      typedValues[index] = value
      flags[index] = 0
    }
  }
  return { featureIndex, values: typedValues, flags }
}

function makeLayer(
  data: { feature_index: readonly number[]; [field: string]: unknown } | undefined,
  fields: readonly string[]
): CachedLayerData {
  const layer: CachedLayerData = {}
  if (!data) return layer
  for (const field of fields) {
    const values = data[field] as readonly (number | boolean | null)[] | undefined
    if (values) layer[field] = createSeries(data.feature_index, values)
  }
  return layer
}

export function encodeFrame(key: string, frame: SimulationFrame): CachedFrame {
  const validation = validateFrame(frame)
  if (!validation.valid) {
    throw new Error(`Invalid simulation frame: ${validation.errors.join('; ')}`)
  }
  const nodes = makeLayer(frame.nodes as NodeFrameData & { [field: string]: unknown }, [
    'depth_m',
    'fill_ratio',
    'surcharged',
  ])
  const pipes = makeLayer(frame.pipes as PipeFrameData & { [field: string]: unknown }, [
    'fill_ratio',
    'flow_m3s',
    'depth_m',
    'surcharged',
  ])
  const ponding = makeLayer(frame.ponding as PondingFrameData & { [field: string]: unknown }, [
    'depth_m',
    'flow_m3s',
  ])
  const checksum = frame.checksum ?? frame.payload_sha256
  const cached: CachedFrame = {
    key,
    simulationId: frame.simulation_id,
    networkRevisionId: frame.network_revision_id,
    targetManifestId: frame.target_manifest_id,
    targetManifestSha256: frame.target_manifest_sha256,
    publishedAttemptId: frame.published_attempt_id,
    publishedAttemptNo: frame.published_attempt_no,
    frameIndex: frame.frame_index,
    frameTime: frame.frame_time,
    ...(checksum ? { checksum } : {}),
    nodes,
    pipes,
    ponding,
    bytes: 0,
  }
  cached.bytes = cachedFrameBytes(cached)
  return cached
}

export function isCachedFrame(frame: SimulationFrame | CachedFrame): frame is CachedFrame {
  return (
    'key' in frame &&
    'frameIndex' in frame &&
    'frameTime' in frame &&
    'networkRevisionId' in frame &&
    'targetManifestSha256' in frame
  )
}

export function validateCachedFrame(
  frame: CachedFrame,
  expectedChecksum?: string
): FrameValidationResult {
  const errors: string[] = []
  const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
  for (const [field, value] of [
    ['simulationId', frame.simulationId],
    ['networkRevisionId', frame.networkRevisionId],
    ['targetManifestId', frame.targetManifestId],
    ['publishedAttemptId', frame.publishedAttemptId],
  ] as const) {
    if (typeof value !== 'string' || !uuidPattern.test(value)) {
      errors.push(`${field} must be a UUID`)
    }
  }
  if (typeof frame.targetManifestSha256 !== 'string' || !HASH_RE.test(frame.targetManifestSha256)) {
    errors.push('targetManifestSha256 must be a SHA-256 hash')
  }
  if (!Number.isSafeInteger(frame.publishedAttemptNo) || frame.publishedAttemptNo < 1) {
    errors.push('publishedAttemptNo must be a positive integer')
  }
  if (!Number.isSafeInteger(frame.frameIndex) || frame.frameIndex < 0) {
    errors.push('frameIndex must be a non-negative integer')
  }
  if (!UTC_RFC3339_RE.test(frame.frameTime) || !Number.isFinite(Date.parse(frame.frameTime))) {
    errors.push('frameTime must be a valid UTC RFC3339 timestamp')
  }
  if (frame.checksum && !HASH_RE.test(frame.checksum)) {
    errors.push('checksum must be a SHA-256 hash')
  }
  if (expectedChecksum && frame.checksum !== expectedChecksum) {
    errors.push('checksum does not match the index')
  }
  for (const layer of [frame.nodes, frame.pipes, frame.ponding]) {
    for (const series of Object.values(layer)) {
      if (
        series.featureIndex.length !== series.values.length ||
        series.featureIndex.length !== series.flags.length
      ) {
        errors.push('decoded series lengths do not match')
        continue
      }
      let previous = -1
      for (let index = 0; index < series.featureIndex.length; index += 1) {
        const featureIndex = series.featureIndex[index]
        if (featureIndex <= previous)
          errors.push('decoded feature indexes are not strictly ascending')
        previous = featureIndex
        if (series.flags[index] > 1) errors.push('decoded series contains an invalid flag')
        if (
          series.flags[index] === 0 &&
          (!Number.isFinite(series.values[index]) || series.values[index] < 0)
        ) {
          errors.push('decoded series contains an invalid value')
        }
      }
    }
  }
  return { valid: errors.length === 0, errors }
}

function simpleHash(value: string): string {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

export function fieldsHash(fields: FrameFields | string | undefined): string {
  if (typeof fields === 'string') return simpleHash(fields)
  if (!fields) return 'all'
  const canonical = [
    `nodes:${(fields.nodes ?? []).slice().sort().join(',')}`,
    `pipes:${(fields.pipes ?? []).slice().sort().join(',')}`,
    `ponding:${(fields.ponding ?? []).slice().sort().join(',')}`,
  ].join('|')
  return simpleHash(canonical)
}

export function frameCacheKey(
  simulationId: string,
  frameIndex: number,
  fields: FrameFields | string | undefined,
  networkRevisionId: string
): string {
  return `v1/${simulationId}/${frameIndex}/${fieldsHash(fields)}/${networkRevisionId}`
}

export class FrameCache {
  readonly maxBytes: number
  private readonly entries = new Map<string, CachedFrame>()
  private readonly pinned = new Set<string>()
  private currentBytes = 0
  private geometryBytes = 0

  constructor(maxBytes = DEFAULT_FRAME_CACHE_BYTES) {
    if (!Number.isFinite(maxBytes) || maxBytes <= 0) {
      throw new Error('FrameCache maxBytes must be positive')
    }
    this.maxBytes = Math.floor(maxBytes)
  }

  get sizeBytes(): number {
    return this.currentBytes + this.geometryBytes
  }

  get dynamicBytes(): number {
    return this.currentBytes
  }

  get staticGeometryBytes(): number {
    return this.geometryBytes
  }

  get size(): number {
    return this.entries.size
  }

  setGeometryBytes(bytes: number): void {
    if (!Number.isSafeInteger(bytes) || bytes < 0) {
      throw new Error('FrameCache geometry bytes must be a non-negative integer')
    }
    this.geometryBytes = bytes
    if (this.geometryBytes > this.maxBytes) {
      this.geometryBytes = 0
      throw new Error('Static map geometry exceeds FrameCache capacity')
    }
    this.evictIfNeeded(null)
  }

  keys(): string[] {
    return [...this.entries.keys()]
  }

  has(key: string): boolean {
    return this.entries.has(key)
  }

  isPinned(key: string): boolean {
    return this.pinned.has(key)
  }

  get(key: string): CachedFrame | undefined {
    const entry = this.entries.get(key)
    if (!entry) return undefined
    this.entries.delete(key)
    this.entries.set(key, entry)
    return entry
  }

  put(key: string, frame: SimulationFrame | CachedFrame): CachedFrame {
    const entry = isCachedFrame(frame)
      ? { ...frame, bytes: cachedFrameBytes(frame) }
      : encodeFrame(key, frame)
    const validation = validateCachedFrame(entry)
    if (!validation.valid) {
      throw new Error(`Invalid cached frame: ${validation.errors.join('; ')}`)
    }
    if (entry.bytes + this.geometryBytes > this.maxBytes) {
      throw new Error(`Frame ${key} exceeds FrameCache capacity`)
    }
    const existing = this.entries.get(key)
    const wasPinned = this.pinned.has(key)
    if (existing) {
      this.entries.delete(key)
      this.currentBytes -= existing.bytes
    }
    this.entries.set(key, entry)
    this.currentBytes += entry.bytes
    try {
      this.evictIfNeeded(key)
    } catch (error) {
      this.entries.delete(key)
      this.currentBytes -= entry.bytes
      if (existing) {
        this.entries.set(key, existing)
        this.currentBytes += existing.bytes
        if (wasPinned) this.pinned.add(key)
      }
      throw error
    }
    return entry
  }

  pin(key: string): void {
    if (this.entries.has(key)) this.pinned.add(key)
  }

  unpin(key: string): void {
    this.pinned.delete(key)
  }

  delete(key: string): boolean {
    const entry = this.entries.get(key)
    if (!entry) return false
    this.entries.delete(key)
    this.pinned.delete(key)
    this.currentBytes -= entry.bytes
    return true
  }

  clear(): void {
    this.entries.clear()
    this.pinned.clear()
    this.currentBytes = 0
    this.geometryBytes = 0
  }

  destroy(): void {
    this.clear()
  }

  private evictIfNeeded(protectedKey: string | null): void {
    if (this.sizeBytes <= this.maxBytes) return
    for (const [key, entry] of this.entries) {
      if (this.sizeBytes <= this.maxBytes) break
      if (key === protectedKey || this.pinned.has(key)) continue
      this.entries.delete(key)
      this.currentBytes -= entry.bytes
    }
    if (this.sizeBytes > this.maxBytes) {
      throw new Error('FrameCache capacity is occupied by static geometry or pinned frames')
    }
  }
}

export type FrameRequestPriority = 'current' | 'next' | 'previous'

export interface FrameRequestOptions {
  priority?: FrameRequestPriority
  signal?: AbortSignal
}

interface PendingRequest {
  key: string
  priority: number
  run: () => void
  cancel: () => void
}

function createAbortError(): Error {
  if (typeof DOMException !== 'undefined') {
    return new DOMException('Frame request aborted', 'AbortError')
  }
  const error = new Error('Frame request aborted')
  error.name = 'AbortError'
  return error
}

export class FrameRequestScheduler {
  readonly maxConcurrent: number
  private active = 0
  private readonly queue: PendingRequest[] = []
  private disposed = false

  constructor(maxConcurrent = 2) {
    this.maxConcurrent = Math.max(1, Math.min(8, Math.floor(maxConcurrent)))
  }

  get pending(): number {
    return this.queue.length
  }

  get inFlight(): number {
    return this.active
  }

  request<T>(
    key: string,
    loader: (signal?: AbortSignal) => Promise<T>,
    options: FrameRequestOptions = {}
  ): Promise<T> {
    if (this.disposed) return Promise.reject(new Error('FrameRequestScheduler is disposed'))
    const priority = options.priority === 'current' ? 0 : options.priority === 'next' ? 1 : 2
    return new Promise<T>((resolve, reject) => {
      let settled = false
      const abort = () => {
        if (settled) return
        settled = true
        const index = this.queue.findIndex((request) => request.key === key && request.run === run)
        if (index >= 0) this.queue.splice(index, 1)
        reject(createAbortError())
      }
      const run = async () => {
        if (settled || this.disposed) return
        this.active += 1
        try {
          const value = await loader(options.signal)
          if (!settled) {
            settled = true
            resolve(value)
          }
        } catch (error) {
          if (!settled) {
            settled = true
            reject(error)
          }
        } finally {
          this.active -= 1
          this.pump()
        }
      }
      options.signal?.addEventListener('abort', abort, { once: true })
      if (options.signal?.aborted) {
        abort()
        return
      }
      const entry: PendingRequest = {
        key,
        priority,
        run: () => void run(),
        cancel: abort,
      }
      const insertion = this.queue.findIndex((candidate) => candidate.priority > priority)
      if (insertion < 0) this.queue.push(entry)
      else this.queue.splice(insertion, 0, entry)
      this.pump()
    })
  }

  cancel(key: string): void {
    for (let index = this.queue.length - 1; index >= 0; index -= 1) {
      if (this.queue[index].key === key) this.queue[index].cancel()
    }
  }

  reset(): void {
    while (this.queue.length > 0) this.queue.pop()?.cancel()
  }

  dispose(): void {
    this.disposed = true
    this.reset()
  }

  private pump(): void {
    while (!this.disposed && this.active < this.maxConcurrent && this.queue.length > 0) {
      this.queue.shift()?.run()
    }
  }
}

interface InFlightFrame {
  promise: Promise<CachedFrame>
  controller: AbortController
}

export class FrameCacheClient {
  readonly cache: FrameCache
  readonly scheduler: FrameRequestScheduler
  private readonly inFlight = new Map<string, InFlightFrame>()
  private generation = 0
  private disposed = false

  constructor(cache = new FrameCache(), maxConcurrent = 2) {
    this.cache = cache
    this.scheduler = new FrameRequestScheduler(maxConcurrent)
  }

  get(key: string): CachedFrame | undefined {
    return this.cache.get(key)
  }

  getOrLoad(
    key: string,
    loader: (signal?: AbortSignal) => Promise<SimulationFrame>,
    options: FrameRequestOptions = {}
  ): Promise<CachedFrame> {
    const cached = this.cache.get(key)
    if (cached) return Promise.resolve(cached)
    const existing = this.inFlight.get(key)
    if (existing) return existing.promise
    if (this.disposed) return Promise.reject(new Error('FrameCacheClient is disposed'))

    const generation = this.generation
    const controller = new AbortController()
    const forwardAbort = () => controller.abort(options.signal?.reason)
    if (options.signal?.aborted) controller.abort(options.signal.reason)
    else options.signal?.addEventListener('abort', forwardAbort, { once: true })

    const promise = this.scheduler
      .request(
        key,
        async () => {
          const doubleChecked = this.cache.get(key)
          if (doubleChecked) return doubleChecked
          if (generation !== this.generation || controller.signal.aborted) throw createAbortError()
          const frame = await loader(controller.signal)
          if (generation !== this.generation || controller.signal.aborted) throw createAbortError()
          return this.cache.put(key, frame)
        },
        { ...options, signal: controller.signal }
      )
      .finally(() => {
        options.signal?.removeEventListener('abort', forwardAbort)
        if (this.inFlight.get(key)?.promise === promise) this.inFlight.delete(key)
      })
    this.inFlight.set(key, { promise, controller })
    return promise
  }

  pin(key: string): void {
    this.cache.pin(key)
  }

  unpin(key: string): void {
    this.cache.unpin(key)
  }

  clear(): void {
    this.generation += 1
    for (const request of this.inFlight.values()) request.controller.abort(createAbortError())
    this.inFlight.clear()
    this.scheduler.reset()
    this.cache.clear()
  }

  dispose(): void {
    if (this.disposed) return
    this.clear()
    this.scheduler.dispose()
    this.disposed = true
  }
}
