import type {
  BBox,
  BoundedMapFeature,
  HydraulicLayer,
  LonLat,
  MapFeature,
  MapGeometry,
} from './types'

export class InvalidBBoxError extends Error {
  constructor(
    message = 'bbox must be finite west,south,east,north with west < east and south < north'
  ) {
    super(message)
    this.name = 'InvalidBBoxError'
  }
}

const DEFAULT_CELL_SIZE_DEGREES = 0.05
const MAX_CELLS_PER_FEATURE = 256
const MAX_CELLS_PER_QUERY = 4_096

export function isFiniteCoordinate(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function isValidBBox(bbox: Partial<BBox> | null | undefined): bbox is BBox {
  return (
    !!bbox &&
    isFiniteCoordinate(bbox.west) &&
    isFiniteCoordinate(bbox.south) &&
    isFiniteCoordinate(bbox.east) &&
    isFiniteCoordinate(bbox.north) &&
    bbox.west >= -180 &&
    bbox.east <= 180 &&
    bbox.south >= -90 &&
    bbox.north <= 90 &&
    bbox.west < bbox.east &&
    bbox.south < bbox.north
  )
}

export function normalizeBBox(bbox: Partial<BBox> | null | undefined): BBox {
  if (!isValidBBox(bbox)) throw new InvalidBBoxError()
  return {
    west: bbox.west,
    south: bbox.south,
    east: bbox.east,
    north: bbox.north,
  }
}

function isValidSpatialBounds(bbox: Partial<BBox> | null | undefined): bbox is BBox {
  return (
    !!bbox &&
    isFiniteCoordinate(bbox.west) &&
    isFiniteCoordinate(bbox.south) &&
    isFiniteCoordinate(bbox.east) &&
    isFiniteCoordinate(bbox.north) &&
    bbox.west >= -180 &&
    bbox.east <= 180 &&
    bbox.south >= -90 &&
    bbox.north <= 90 &&
    bbox.west <= bbox.east &&
    bbox.south <= bbox.north
  )
}

function normalizeSpatialBounds(bbox: Partial<BBox> | null | undefined): BBox {
  if (!isValidSpatialBounds(bbox)) throw new InvalidBBoxError('geometry bounds are invalid')
  return {
    west: bbox.west,
    south: bbox.south,
    east: bbox.east,
    north: bbox.north,
  }
}

export function parseBBox(value: string): BBox {
  const parts = value.trim().split(',')
  if (parts.length !== 4) throw new InvalidBBoxError('bbox must contain exactly four numbers')
  const numbers = parts.map((part) => Number(part.trim()))
  return normalizeBBox({
    west: numbers[0],
    south: numbers[1],
    east: numbers[2],
    north: numbers[3],
  })
}

export function formatBBox(bbox: BBox): string {
  const normalized = normalizeBBox(bbox)
  return [normalized.west, normalized.south, normalized.east, normalized.north]
    .map((value) => String(value))
    .join(',')
}

export function bboxHash(bbox: BBox): string {
  const normalized = normalizeBBox(bbox)
  // Fixed precision keeps a query/cache key stable across equivalent numeric input.
  return [normalized.west, normalized.south, normalized.east, normalized.north]
    .map((value) => value.toFixed(7))
    .join('/')
}

export function bboxIntersects(left: BBox, right: BBox): boolean {
  // Query bboxes must have non-zero area. Geometry bboxes may be a point or
  // a perfectly vertical/horizontal line, so use the looser bounds check for
  // the candidate side.
  if (!isValidBBox(left) || !isValidSpatialBounds(right)) return false
  return !(
    left.east < right.west ||
    left.west > right.east ||
    left.north < right.south ||
    left.south > right.north
  )
}

export function bboxContains(outer: BBox, inner: BBox): boolean {
  if (!isValidBBox(outer) || !isValidBBox(inner)) return false
  return (
    outer.west <= inner.west &&
    outer.south <= inner.south &&
    outer.east >= inner.east &&
    outer.north >= inner.north
  )
}

export function expandBBox(bbox: BBox, paddingDegrees: number): BBox {
  const normalized = normalizeBBox(bbox)
  if (!isFiniteCoordinate(paddingDegrees) || paddingDegrees < 0) {
    throw new InvalidBBoxError('bbox padding must be a finite non-negative number')
  }
  return normalizeBBox({
    west: Math.max(-180, normalized.west - paddingDegrees),
    south: Math.max(-90, normalized.south - paddingDegrees),
    east: Math.min(180, normalized.east + paddingDegrees),
    north: Math.min(90, normalized.north + paddingDegrees),
  })
}

function updateBounds(
  bounds: { west: number; south: number; east: number; north: number } | null,
  coordinate: readonly [number, number]
): { west: number; south: number; east: number; north: number } {
  const [longitude, latitude] = coordinate
  if (!isFiniteCoordinate(longitude) || !isFiniteCoordinate(latitude)) {
    throw new InvalidBBoxError('geometry contains a non-finite coordinate')
  }
  if (!bounds) return { west: longitude, south: latitude, east: longitude, north: latitude }
  bounds.west = Math.min(bounds.west, longitude)
  bounds.south = Math.min(bounds.south, latitude)
  bounds.east = Math.max(bounds.east, longitude)
  bounds.north = Math.max(bounds.north, latitude)
  return bounds
}

function visitCoordinates(geometry: MapGeometry, visit: (coordinate: LonLat) => void): void {
  if (geometry.type === 'Point') {
    visit(geometry.coordinates)
    return
  }
  if (geometry.type === 'LineString') {
    for (const coordinate of geometry.coordinates) visit(coordinate)
    return
  }
  if (geometry.type === 'Polygon') {
    for (const ring of geometry.coordinates) {
      for (const coordinate of ring) visit(coordinate)
    }
    return
  }
  for (const polygon of geometry.coordinates) {
    for (const ring of polygon) {
      for (const coordinate of ring) visit(coordinate)
    }
  }
}

export function boundsForGeometry(geometry: MapGeometry): BBox {
  let bounds: { west: number; south: number; east: number; north: number } | null = null
  visitCoordinates(geometry, (coordinate) => {
    bounds = updateBounds(bounds, coordinate)
  })
  if (!bounds) throw new InvalidBBoxError('geometry has no coordinates')
  return normalizeSpatialBounds(bounds as BBox)
}

export function boundsForFeature(feature: MapFeature): BBox {
  const bounded = feature as MapFeature & { bounds?: BBox }
  return bounded.bounds && isValidSpatialBounds(bounded.bounds)
    ? bounded.bounds
    : boundsForGeometry(feature.geometry)
}

export function countFeaturesInBBox<TFeature extends MapFeature>(
  features: readonly TFeature[],
  bbox: BBox
): number {
  const normalized = normalizeBBox(bbox)
  return features.reduce(
    (count, feature) => count + (bboxIntersects(normalized, boundsForFeature(feature)) ? 1 : 0),
    0
  )
}

export interface IndexedItem<TItem> {
  item: TItem
  bounds: BBox
}

function cellRange(
  bounds: BBox,
  cellSize: number
): { minX: number; maxX: number; minY: number; maxY: number } {
  return {
    minX: Math.floor(bounds.west / cellSize),
    maxX: Math.floor(bounds.east / cellSize),
    minY: Math.floor(bounds.south / cellSize),
    maxY: Math.floor(bounds.north / cellSize),
  }
}

/**
 * Small immutable-bounds spatial index used by the canvas layer. It avoids a
 * full scan for every pan/zoom while remaining cheap to rebuild when a new
 * immutable network revision is received.
 */
export class ViewportIndex<TItem> {
  /**
   * Two-level numeric cell map. Numeric keys keep cell lookup allocation-free;
   * a string `x:y` key allocated a fresh string for every cell of every query,
   * which is thousands of short-lived strings per layer per frame at the
   * contracted 10k-pipe scale.
   */
  private readonly cells = new Map<number, Map<number, number[]>>()
  private readonly items: TItem[] = []
  private readonly bounds: BBox[] = []
  private readonly oversized: number[] = []
  private readonly cellSize: number
  /**
   * Reusable de-duplication marker for {@link query}, one byte per item.
   *
   * A query visits every candidate in the covered cells, and a feature spanning
   * several cells is therefore seen more than once. Hashing those repeats
   * through a fresh `Set` cost an allocation plus a hash insert per candidate on
   * every layer of every frame; a stamped `Uint8Array` costs one array for the
   * lifetime of the index. `queryGeneration` is the stamp, so the marker never
   * has to be cleared between queries.
   */
  private readonly seen: number[] = []
  private queryGeneration = 0

  constructor(
    items: readonly TItem[],
    getBounds: (item: TItem) => BBox,
    cellSize = DEFAULT_CELL_SIZE_DEGREES
  ) {
    this.cellSize =
      isFiniteCoordinate(cellSize) && cellSize > 0 ? cellSize : DEFAULT_CELL_SIZE_DEGREES
    for (const item of items) this.insert(item, getBounds(item))
  }

  get size(): number {
    return this.items.length
  }

  all(): readonly TItem[] {
    return this.items
  }

  private insert(item: TItem, itemBounds: BBox): void {
    const bounds = normalizeSpatialBounds(itemBounds)
    const itemIndex = this.items.length
    this.items.push(item)
    this.bounds.push(bounds)

    const range = cellRange(bounds, this.cellSize)
    const width = range.maxX - range.minX + 1
    const height = range.maxY - range.minY + 1
    if (width * height > MAX_CELLS_PER_FEATURE) {
      this.oversized.push(itemIndex)
      return
    }
    for (let x = range.minX; x <= range.maxX; x += 1) {
      let column = this.cells.get(x)
      if (!column) {
        column = new Map<number, number[]>()
        this.cells.set(x, column)
      }
      for (let y = range.minY; y <= range.maxY; y += 1) {
        const bucket = column.get(y)
        if (bucket) bucket.push(itemIndex)
        else column.set(y, [itemIndex])
      }
    }
  }

  query(bbox: BBox): TItem[] {
    if (!isValidBBox(bbox) || this.items.length === 0) return []
    const range = cellRange(bbox, this.cellSize)
    const width = range.maxX - range.minX + 1
    const height = range.maxY - range.minY + 1
    const { west, south, east, north } = bbox
    // `bbox` is validated once above and every `this.bounds[i]` was validated
    // at insert time, so the overlap test below is pure arithmetic. Routing it
    // through `bboxIntersects` re-validated both operands for every candidate.
    const bounds = this.bounds
    if (width * height > MAX_CELLS_PER_QUERY) {
      const matched: TItem[] = []
      for (let itemIndex = 0; itemIndex < this.items.length; itemIndex += 1) {
        const itemBounds = bounds[itemIndex]
        if (
          !(
            east < itemBounds.west ||
            west > itemBounds.east ||
            north < itemBounds.south ||
            south > itemBounds.north
          )
        ) {
          matched.push(this.items[itemIndex])
        }
      }
      return matched
    }
    const seen = this.seen
    const generation = this.nextQueryGeneration()
    const candidates: number[] = []
    const mark = (itemIndex: number): void => {
      if (seen[itemIndex] === generation) return
      seen[itemIndex] = generation
      candidates.push(itemIndex)
    }
    for (let index = 0; index < this.oversized.length; index += 1) mark(this.oversized[index])
    for (let x = range.minX; x <= range.maxX; x += 1) {
      const column = this.cells.get(x)
      if (!column) continue
      for (let y = range.minY; y <= range.maxY; y += 1) {
        const bucket = column.get(y)
        if (bucket) for (let index = 0; index < bucket.length; index += 1) mark(bucket[index])
      }
    }
    const matchedIndexes: number[] = []
    for (let index = 0; index < candidates.length; index += 1) {
      const itemIndex = candidates[index]
      const itemBounds = bounds[itemIndex]
      if (
        east < itemBounds.west ||
        west > itemBounds.east ||
        north < itemBounds.south ||
        south > itemBounds.north
      ) {
        continue
      }
      matchedIndexes.push(itemIndex)
    }
    matchedIndexes.sort((left, right) => left - right)
    const matched: TItem[] = new Array(matchedIndexes.length)
    for (let index = 0; index < matchedIndexes.length; index += 1) {
      matched[index] = this.items[matchedIndexes[index]]
    }
    return matched
  }

  private nextQueryGeneration(): number {
    // The marker is a plain number array stamped with a monotonically
    // increasing generation, so it never has to be cleared. The counter is
    // bumped past any value already stored, which keeps correctness even if a
    // caller reaches Number.MAX_SAFE_INTEGER.
    this.queryGeneration = this.queryGeneration >= 1_000_000_000 ? 1 : this.queryGeneration + 1
    return this.queryGeneration
  }

  clear(): void {
    this.cells.clear()
    this.items.length = 0
    this.bounds.length = 0
    this.oversized.length = 0
    this.seen.length = 0
    this.queryGeneration = 0
  }
}

export interface LodProfile {
  zoom: number
  showCatchments: boolean
  showPipes: boolean
  showNodes: boolean
  showPonding: boolean
  majorPipesOnly: boolean
  catchmentMode: 'aggregate' | 'simplified' | 'visible'
  showLabels: boolean
  showCriticalLabels: boolean
  simplify: boolean
  /**
   * Stroke-detail budget for the whole pass. `simplified` drops the per-feature
   * dash pattern and the round line caps, which is what the LOD bands below the
   * detail zoom already imply visually: a 2 px dash and a 1.5 px round cap are
   * not resolvable at those scales, but they are the most expensive part of a
   * software-rasterised stroke (see `SIMPLIFIED_STROKE_FEATURES`).
   */
  strokeDetail: 'full' | 'simplified'
  maxFeaturesPerLayer: number
}

export function getLodProfile(zoom: number): LodProfile {
  const safeZoom = isFiniteCoordinate(zoom) ? zoom : 0
  if (safeZoom < 11) {
    return {
      zoom: safeZoom,
      showCatchments: true,
      showPipes: true,
      showNodes: false,
      showPonding: true,
      majorPipesOnly: true,
      catchmentMode: 'aggregate',
      showLabels: false,
      showCriticalLabels: false,
      simplify: true,
      strokeDetail: 'simplified',
      maxFeaturesPerLayer: 2_000,
    }
  }
  if (safeZoom < 14) {
    return {
      zoom: safeZoom,
      showCatchments: true,
      showPipes: true,
      showNodes: false,
      showPonding: true,
      majorPipesOnly: false,
      catchmentMode: 'simplified',
      showLabels: false,
      showCriticalLabels: false,
      simplify: true,
      strokeDetail: 'simplified',
      maxFeaturesPerLayer: 5_000,
    }
  }
  if (safeZoom < 16) {
    return {
      zoom: safeZoom,
      showCatchments: true,
      showPipes: true,
      showNodes: true,
      showPonding: true,
      majorPipesOnly: false,
      catchmentMode: 'visible',
      showLabels: true,
      showCriticalLabels: false,
      simplify: false,
      strokeDetail: 'full',
      maxFeaturesPerLayer: 10_000,
    }
  }
  return {
    zoom: safeZoom,
    showCatchments: true,
    showPipes: true,
    showNodes: true,
    showPonding: true,
    majorPipesOnly: false,
    catchmentMode: 'visible',
    showLabels: true,
    showCriticalLabels: true,
    simplify: false,
    strokeDetail: 'full',
    maxFeaturesPerLayer: 25_000,
  }
}

export interface DecimateOptions<TItem> {
  maxFeatures?: number
  preserve?: (item: TItem, index: number) => boolean
}

/**
 * Deterministically samples a visible subset while retaining critical features.
 * The returned objects are the original immutable objects, and no feature_index
 * is ever renumbered.
 */
export function decimateFeatures<TItem extends MapFeature>(
  features: readonly TItem[],
  options: DecimateOptions<TItem> = {}
): TItem[] {
  const maxFeatures = options.maxFeatures
  if (maxFeatures !== undefined && (!Number.isFinite(maxFeatures) || maxFeatures <= 0)) return []
  if (maxFeatures === undefined || features.length <= maxFeatures) return [...features]

  const keep = new Set<number>()
  const criticalIndexes: number[] = []
  for (let index = 0; index < features.length; index += 1) {
    if (options.preserve?.(features[index], index)) criticalIndexes.push(index)
  }
  criticalIndexes.forEach((index) => keep.add(index))
  if (keep.size > maxFeatures) {
    const criticalSet = new Set(criticalIndexes.slice(0, maxFeatures))
    return features.filter((_, index) => criticalSet.has(index))
  }

  const candidates = features.map((_, index) => index).filter((index) => !keep.has(index))
  const remaining = maxFeatures - keep.size
  if (remaining > 0 && candidates.length > 0) {
    const step = candidates.length / remaining
    for (let slot = 0; slot < remaining; slot += 1) {
      keep.add(candidates[Math.min(candidates.length - 1, Math.floor(slot * step))])
    }
    for (const index of candidates) {
      if (keep.size >= maxFeatures) break
      keep.add(index)
    }
  }
  return features.filter((_, index) => keep.has(index))
}

export function isMajorPipeFeature(feature: MapFeature): boolean {
  if (feature.properties?.major === true) return true
  const diameter = feature.properties?.nominal_diameter_m
  const upstreamArea = feature.properties?.upstream_area_m2
  return (
    (typeof diameter === 'number' && Number.isFinite(diameter) && diameter >= 0.8) ||
    (typeof upstreamArea === 'number' && Number.isFinite(upstreamArea) && upstreamArea >= 1_000_000)
  )
}

export function selectVisibleFeatures<TItem extends MapFeature>(
  features: readonly TItem[],
  bbox: BBox,
  zoom: number,
  layer: HydraulicLayer,
  index?: ViewportIndex<TItem>,
  maxFeatures?: number
): TItem[] {
  const profile = getLodProfile(zoom)
  const visible = index
    ? index.query(bbox)
    : features.filter((feature) => bboxIntersects(bbox, boundsForFeature(feature)))
  const lodVisible =
    layer === 'pipes' && profile.majorPipesOnly
      ? visible.filter((feature) => isMajorPipeFeature(feature))
      : visible
  const limit = maxFeatures === undefined ? profile.maxFeaturesPerLayer : maxFeatures
  return decimateFeatures(lodVisible, {
    maxFeatures: limit,
    preserve: (feature) =>
      feature.properties?.critical === true ||
      (layer === 'pipes' && profile.majorPipesOnly && isMajorPipeFeature(feature)),
  })
}

export function isFeatureInBBox<TFeature extends BoundedMapFeature>(
  feature: TFeature,
  bbox: BBox
): boolean {
  return bboxIntersects(bbox, feature.bounds)
}
