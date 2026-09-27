import L from 'leaflet'
import type {
  BBox,
  BoundedMapFeature,
  FeatureInteraction,
  HydraulicColorMetric,
  HydraulicLayer,
  LonLat,
  MapLabels,
  MapLayerVisibility,
  NetworkMapData,
  NormalizedMapData,
  RenderStats,
  SimulationFrame,
} from './types'
import { normalizeMapData } from './mapData'
import { FrameLookup } from './frameLookup'
import {
  getFeatureStyle,
  type FeatureMapStyle,
  type FeatureStyleLabels,
  formatColorMetricValue,
} from './colorScales'
import {
  encodeFrame,
  FrameCache,
  frameCacheKey,
  isCachedFrame,
  type CachedFrame,
  validateCachedFrame,
  validateFrame,
} from './frameCache'
import {
  bboxIntersects,
  decimateFeatures,
  getLodProfile,
  isMajorPipeFeature,
  ViewportIndex,
} from './viewport'

type MapFrameInteraction = FeatureInteraction

/**
 * Software-raster stroke budget for one layer.
 *
 * Measured on the frozen G5 target environment (Node 18.20.8, 4 cores, 8 GiB,
 * `--disable-gpu` software rasterisation) with 10 000 pipes / 5 000 nodes /
 * 1 000 catchments: the per-frame cost is dominated by stroke rasterisation, not
 * by the renderer itself (median script time 1.4 ms against a 21.5 ms median
 * frame). Round caps and dash patterns each cost roughly a third of that frame
 * budget on their own, and both are unresolvable detail once a layer still has
 * hundreds of stroked features on screen.
 *
 * So a layer is stroked with simplified detail when the LOD band already says
 * the view is not a detail view, or when the post-decimation feature count still
 * exceeds this budget. Geometry, colour, width, ordering and every reported
 * statistic are unchanged; only the dash pattern and the cap style degrade, and
 * both come back as soon as the layer is sparse enough to resolve them.
 */
const SIMPLIFIED_STROKE_FEATURES = 512

/**
 * Cached unit circle for node glyphs.
 *
 * `CanvasRenderingContext2D.arc` rebuilds and re-validates an approximated arc
 * on every call, and the render loop calls it once per visible node per frame
 * (hundreds at the contracted 5 000-node scale). The glyph is always a circle
 * scaled by a per-feature radius, so the geometry can be built once and reused
 * under a per-feature transform.
 *
 * `Path2D` is absent in non-browser hosts (the jsdom unit tests), so the lookup
 * returns null there and the caller falls back to `arc`.
 */
let UNIT_CIRCLE: Path2D | null = null
function unitCircle(): Path2D | null {
  if (UNIT_CIRCLE) return UNIT_CIRCLE
  // A missing Path2D is not memoised: the absence is a property of the host, and
  // caching it would pin the fallback for the whole process even if the host
  // gains one later.
  if (typeof Path2D !== 'function') return null
  const path = new Path2D()
  path.arc(0, 0, 1, 0, Math.PI * 2)
  UNIT_CIRCLE = path
  return UNIT_CIRCLE
}

/** Stable identity so the line-dash cache in {@link HydraulicCanvasLayer} holds. */
const SOLID_LINE_DASH: readonly number[] = []

function shouldSimplifyStroke(
  profile: ReturnType<typeof getLodProfile>,
  drawnFeatures: number
): boolean {
  return profile.strokeDetail === 'simplified' || drawnFeatures > SIMPLIFIED_STROKE_FEATURES
}

export interface HydraulicCanvasLayerOptions {
  pane?: string
  interactive?: boolean
  tooltip?: boolean
  colorMetric?: HydraulicColorMetric
  layerVisibility?: Partial<MapLayerVisibility>
  maxFeaturesPerLayer?: number | Partial<Record<HydraulicLayer, number>>
  viewportCellSize?: number
  frameCache?: FrameCache
  frameCacheKey?: string
  clearFrameCacheOnRemove?: boolean
  onFeatureClick?: (event: MapFrameInteraction) => void
  onFeatureHover?: (event: MapFrameInteraction | null) => void
  onFeatureFocus?: (event: MapFrameInteraction | null) => void
  onTooltip?: (event: MapFrameInteraction) => void
  onRenderStats?: (stats: RenderStats) => void
  onFrameError?: (errors: readonly string[]) => void
  labels?: MapLabels
}

interface VisibleState {
  nodes: readonly BoundedMapFeature[]
  pipes: readonly BoundedMapFeature[]
  catchments: readonly BoundedMapFeature[]
  ponding: readonly BoundedMapFeature[]
}

interface CanvasPoint {
  x: number
  y: number
}

const LAYERS: readonly HydraulicLayer[] = ['catchments', 'ponding', 'pipes', 'nodes']
const HIT_LAYERS: readonly HydraulicLayer[] = ['nodes', 'pipes', 'ponding', 'catchments']
const HIT_LAYER_PRIORITY: Record<HydraulicLayer, number> = {
  nodes: 0,
  pipes: 1,
  ponding: 2,
  catchments: 3,
}

function now(): number {
  return typeof performance !== 'undefined' && typeof performance.now === 'function'
    ? performance.now()
    : Date.now()
}

/**
 * Reusable projection argument. `L.Map.latLngToContainerPoint` reads the value
 * through `toLatLng` without retaining it, so one scratch tuple can serve every
 * coordinate of every feature instead of allocating one array per coordinate.
 */
const PROJECTION_SCRATCH: [number, number] = [0, 0]

function projectLongitudeLatitude(map: L.Map, longitude: number, latitude: number): L.Point {
  PROJECTION_SCRATCH[0] = latitude
  PROJECTION_SCRATCH[1] = longitude
  return map.latLngToContainerPoint(PROJECTION_SCRATCH)
}

function cloneVisibility(visibility: MapLayerVisibility): MapLayerVisibility {
  return {
    nodes: visibility.nodes,
    pipes: visibility.pipes,
    catchments: visibility.catchments,
    ponding: visibility.ponding,
  }
}

function emptyLayerCounts(): Record<HydraulicLayer, number> {
  return { nodes: 0, pipes: 0, catchments: 0, ponding: 0 }
}

function asBBox(map: L.Map): BBox {
  const bounds = map.getBounds() as L.LatLngBounds & {
    getWest?: () => number
    getSouth?: () => number
    getEast?: () => number
    getNorth?: () => number
  }
  return {
    west: bounds.getWest(),
    south: bounds.getSouth(),
    east: bounds.getEast(),
    north: bounds.getNorth(),
  }
}

function asPoint(value: L.Point | { x: number; y: number }): CanvasPoint {
  return { x: Number(value.x), y: Number(value.y) }
}

function mapSize(map: L.Map): CanvasPoint {
  const size = map.getSize()
  return { x: Math.max(1, Number(size.x)), y: Math.max(1, Number(size.y)) }
}

function mapZoom(map: L.Map): number {
  const zoom = map.getZoom()
  return Number.isFinite(zoom) ? zoom : 13
}

function featureDistance(
  feature: BoundedMapFeature,
  point: CanvasPoint,
  project: (coordinate: LonLat) => CanvasPoint
): number {
  const geometry = feature.geometry
  if (geometry.type === 'Point') return distance(project(geometry.coordinates), point)
  if (geometry.type === 'LineString')
    return distanceToLine(point, geometry.coordinates.map(project))
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
  let best = Number.POSITIVE_INFINITY
  for (const polygon of polygons) {
    const projectedRings = polygon.map((ring) => ring.map(project))
    if (pointInPolygonWithHoles(point, projectedRings)) return 0
    for (const points of projectedRings) {
      for (let index = 1; index < points.length; index += 1) {
        best = Math.min(best, distanceToSegment(point, points[index - 1], points[index]))
      }
    }
  }
  return best
}

function distance(left: CanvasPoint, right: CanvasPoint): number {
  return Math.hypot(left.x - right.x, left.y - right.y)
}

function distanceToLine(point: CanvasPoint, line: readonly CanvasPoint[]): number {
  let best = Number.POSITIVE_INFINITY
  if (line.length === 1) return distance(point, line[0])
  for (let index = 1; index < line.length; index += 1) {
    best = Math.min(best, distanceToSegment(point, line[index - 1], line[index]))
  }
  return best
}

function distanceToSegment(point: CanvasPoint, start: CanvasPoint, end: CanvasPoint): number {
  const dx = end.x - start.x
  const dy = end.y - start.y
  if (dx === 0 && dy === 0) return distance(point, start)
  const t = Math.max(
    0,
    Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy))
  )
  return distance(point, { x: start.x + t * dx, y: start.y + t * dy })
}

function pointInPolygon(point: CanvasPoint, ring: readonly CanvasPoint[]): boolean {
  let inside = false
  for (
    let index = 0, previous = ring.length - 1;
    index < ring.length;
    previous = index, index += 1
  ) {
    const currentPoint = ring[index]
    const previousPoint = ring[previous]
    const intersects =
      currentPoint.y > point.y !== previousPoint.y > point.y &&
      point.x <
        ((previousPoint.x - currentPoint.x) * (point.y - currentPoint.y)) /
          (previousPoint.y - currentPoint.y || Number.EPSILON) +
          currentPoint.x
    if (intersects) inside = !inside
  }
  return inside
}

function pointInPolygonWithHoles(
  point: CanvasPoint,
  rings: readonly (readonly CanvasPoint[])[]
): boolean {
  if (rings.length === 0 || !pointInPolygon(point, rings[0])) return false
  return !rings.slice(1).some((hole) => pointInPolygon(point, hole))
}

/** A ring needs at least three vertices to enclose area. Projection preserves
 * the vertex count, so the source ring length is a valid pre-projection test. */
function ringHasArea(pointCount: number): boolean {
  return pointCount >= 3
}

function getLayerFeatures(
  data: NormalizedMapData | null,
  layer: HydraulicLayer
): readonly BoundedMapFeature[] {
  if (!data) return []
  switch (layer) {
    case 'nodes':
      return data.nodes
    case 'pipes':
      return data.pipes
    case 'catchments':
      return data.catchments
    case 'ponding':
      return data.ponding
  }
}

function isNormalizedMapData(data: NetworkMapData | NormalizedMapData): data is NormalizedMapData {
  return 'schemaVersion' in data && 'targetManifestSha256' in data
}

function layerLimit(
  options: HydraulicCanvasLayerOptions,
  layer: HydraulicLayer
): number | undefined {
  const configured = options.maxFeaturesPerLayer
  if (typeof configured === 'number') return configured
  return configured?.[layer]
}

/** A single imperative Canvas layer for all hydraulic geometry. */
export class HydraulicCanvasLayer extends L.Layer {
  private readonly layerOptions: HydraulicCanvasLayerOptions
  private map: L.Map | null = null
  private canvas: HTMLCanvasElement | null = null
  private context: CanvasRenderingContext2D | null = null
  private tooltipElement: HTMLDivElement | null = null
  private renderHandle: number | ReturnType<typeof setTimeout> | null = null
  private renderPending = false
  private destroyed = false
  private data: NormalizedMapData | null = null
  private frame: SimulationFrame | CachedFrame | null = null
  private frameLookup = new FrameLookup(null)
  private frameKey: string | null
  private indexes: Record<HydraulicLayer, ViewportIndex<BoundedMapFeature>> | null = null
  private readonly visibility: MapLayerVisibility
  private colorMetric: HydraulicColorMetric
  private selectedFeatureIds = new Set<string>()
  private lastStats: RenderStats
  private lastVisible: VisibleState = { nodes: [], pipes: [], catchments: [], ponding: [] }
  private mapCleanups: (() => void)[] = []
  private canvasCleanups: (() => void)[] = []
  private readonly boundMouseMove = (event: MouseEvent) => this.handleMouseMove(event)
  private readonly boundClick = (event: MouseEvent) => this.handleClick(event)
  private readonly boundKeyDown = (event: KeyboardEvent) => this.handleKeyDown(event)
  private readonly boundMouseLeave = () => this.hideTooltip()
  private lastHoverAt = 0
  /** Device pixel ratio the current pass was seeded with (see prepareCanvas). */
  private pixelRatio = 1
  private keyboardFeatureIndex = -1
  /**
   * Dash pattern currently applied to the 2D context. `setLineDash` is a
   * comparatively expensive native call in software rasterization and the dash
   * pattern is shared per band, so it is only re-applied when it changes.
   * Reset to null on every `prepareCanvas` so each pass re-seeds the context.
   */
  private appliedDash: readonly number[] | null = null
  /** Style options shared by every feature drawn in one pass. */
  private readonly drawLabels: FeatureStyleLabels = {
    noData: 'No data',
    predicted: 'Predicted',
    observed: 'Observed',
  }

  constructor(options: HydraulicCanvasLayerOptions = {}) {
    super(options)
    this.layerOptions = {
      interactive: true,
      tooltip: true,
      clearFrameCacheOnRemove: false,
      ...options,
    }
    this.visibility = {
      nodes: options.layerVisibility?.nodes ?? true,
      pipes: options.layerVisibility?.pipes ?? true,
      catchments: options.layerVisibility?.catchments ?? true,
      ponding: options.layerVisibility?.ponding ?? true,
    }
    this.colorMetric = options.colorMetric ?? 'fill_ratio'
    this.frameKey = options.frameCacheKey ?? null
    this.lastStats = {
      zoom: 13,
      visible: emptyLayerCounts(),
      drawn: emptyLayerCounts(),
      indexed: emptyLayerCounts(),
      decimated: emptyLayerCounts(),
      frameCacheKey: this.frameKey,
      renderMs: 0,
    }
  }

  get isDestroyed(): boolean {
    return this.destroyed
  }

  getRenderStats(): RenderStats {
    return {
      ...this.lastStats,
      visible: { ...this.lastStats.visible },
      drawn: { ...this.lastStats.drawn },
      indexed: { ...this.lastStats.indexed },
      decimated: { ...this.lastStats.decimated },
    }
  }

  getLayerVisibility(): MapLayerVisibility {
    return cloneVisibility(this.visibility)
  }

  setLayerVisibility(visibility: Partial<MapLayerVisibility>): this {
    for (const layer of LAYERS) {
      const value = visibility[layer]
      if (typeof value === 'boolean') this.visibility[layer] = value
    }
    this.requestRender()
    return this
  }

  toggleLayer(layer: HydraulicLayer, visible?: boolean): this {
    this.visibility[layer] = visible ?? !this.visibility[layer]
    this.requestRender()
    return this
  }

  setColorMetric(metric: HydraulicColorMetric): this {
    this.colorMetric = metric
    this.requestRender()
    return this
  }

  getColorMetric(): HydraulicColorMetric {
    return this.colorMetric
  }

  setSelectedFeatures(featureIds: Iterable<string>): this {
    this.selectedFeatureIds = new Set(featureIds)
    this.requestRender()
    return this
  }

  getSelectedFeatures(): string[] {
    return [...this.selectedFeatureIds]
  }

  setData(data: NetworkMapData | NormalizedMapData): this {
    if (this.destroyed) return this
    const nextData = isNormalizedMapData(data) ? data : normalizeMapData(data)
    const identityChanged =
      this.data !== null &&
      (this.data.networkRevisionId !== nextData.networkRevisionId ||
        this.data.targetManifestId !== nextData.targetManifestId ||
        this.data.targetManifestSha256 !== nextData.targetManifestSha256)
    this.data = nextData
    if (identityChanged) {
      if (this.frameKey) this.layerOptions.frameCache?.delete(this.frameKey)
      this.frame = null
      this.frameLookup = new FrameLookup(null)
      this.frameKey = null
      this.keyboardFeatureIndex = -1
    }
    this.layerOptions.frameCache?.setGeometryBytes(estimateGeometryBytes(nextData))
    this.rebuildIndexes()
    this.requestRender()
    return this
  }

  getData(): NormalizedMapData | null {
    return this.data
  }

  getViewportBBox(): BBox | null {
    if (this.map) return asBBox(this.map)
    return this.data?.bbox ?? null
  }

  getVisibleFeatures(layer: HydraulicLayer): readonly BoundedMapFeature[] {
    return [...this.lastVisible[layer]]
  }

  getKeyboardFeatures(): readonly BoundedMapFeature[] {
    return HIT_LAYERS.flatMap((layer) => this.lastVisible[layer])
  }

  getCanvasElement(): HTMLCanvasElement | null {
    return this.canvas
  }

  setFrame(
    frame: SimulationFrame | CachedFrame | null,
    cacheKey?: string,
    expectedChecksum?: string
  ): this {
    if (this.destroyed) return this
    if (frame === null) {
      this.frame = null
      this.frameLookup = new FrameLookup(null)
      this.frameKey = cacheKey ?? null
      this.requestRender()
      return this
    }

    const validation = isCachedFrame(frame)
      ? validateCachedFrame(frame, expectedChecksum)
      : validateFrame(frame, expectedChecksum)
    if (!validation.valid) {
      this.layerOptions.onFrameError?.(validation.errors)
      return this
    }
    const decoded = isCachedFrame(frame)
      ? frame
      : encodeFrame(cacheKey ?? this.frameKey ?? 'unkeyed', frame)
    if (
      this.data &&
      (decoded.networkRevisionId !== this.data.networkRevisionId ||
        decoded.targetManifestId !== this.data.targetManifestId ||
        decoded.targetManifestSha256 !== this.data.targetManifestSha256)
    ) {
      this.layerOptions.onFrameError?.([
        'frame target manifest does not match the active network revision',
      ])
      return this
    }
    const nextKey = cacheKey ?? decoded.key
    if (this.layerOptions.frameCache) {
      try {
        this.layerOptions.frameCache.put(nextKey, decoded)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        this.layerOptions.onFrameError?.([message])
        return this
      }
    }
    this.frame = decoded
    this.frameLookup = new FrameLookup(decoded)
    this.frameKey = nextKey
    this.requestRender()
    return this
  }

  getFrame(): CachedFrame | null {
    return this.frame && isCachedFrame(this.frame) ? this.frame : null
  }

  getFrameCacheKey(): string | null {
    return this.frameKey
  }

  setFrameCacheKey(key: string | null): this {
    this.frameKey = key
    this.lastStats.frameCacheKey = key
    return this
  }

  /** Public deterministic render entry for tests and explicit redraws. */
  renderNow(): RenderStats {
    if (this.destroyed || !this.map || !this.data) return this.getRenderStats()
    this.cancelScheduledRender()
    const started = now()
    const bbox = asBBox(this.map)
    const zoom = mapZoom(this.map)
    const profile = getLodProfile(zoom)
    const visible = emptyLayerCounts()
    const indexed = emptyLayerCounts()
    const decimated = emptyLayerCounts()
    const selectedByLayer: VisibleState = { nodes: [], pipes: [], catchments: [], ponding: [] }

    for (const layer of LAYERS) {
      if (!this.visibility[layer] || !this.isLayerVisibleAtZoom(layer, profile)) continue
      const features = getLayerFeatures(this.data, layer)
      const index = this.indexes?.[layer]
      const candidates = index
        ? index.query(bbox)
        : features.filter((feature) => bboxIntersects(bbox, feature.bounds))
      const lodCandidates =
        layer === 'pipes' && profile.majorPipesOnly
          ? candidates.filter((feature) => isMajorPipeFeature(feature))
          : candidates
      indexed[layer] = lodCandidates.length
      const limit = layerLimit(this.layerOptions, layer) ?? profile.maxFeaturesPerLayer
      // `decimateFeatures` returns a defensive copy of its input; when the layer
      // is already within budget that copy is a per-frame allocation of the whole
      // visible set for no reason, and this loop only reads the result.
      const selected =
        lodCandidates.length <= limit
          ? lodCandidates
          : decimateFeatures(lodCandidates, {
              maxFeatures: limit,
              preserve: (feature) =>
                feature.properties?.critical === true ||
                (layer === 'pipes' && profile.majorPipesOnly && isMajorPipeFeature(feature)),
            })
      selectedByLayer[layer] = selected
      visible[layer] = selected.length
      decimated[layer] = Math.max(0, lodCandidates.length - selected.length)
    }
    this.lastVisible = selectedByLayer

    const drawn = emptyLayerCounts()
    if (this.context && this.canvas) {
      this.prepareCanvas()
      this.context.clearRect(0, 0, this.canvas.width, this.canvas.height)
      for (const layer of LAYERS) {
        const features = selectedByLayer[layer]
        const simplifiedStroke = shouldSimplifyStroke(profile, features.length)
        // Cap/join style is constant for the whole pass, so it is applied once
        // instead of once per feature: it is the stroke state the rasteriser
        // keys its cached stroke geometry on, and re-asserting an unchanged
        // value per feature invalidates that cache for nothing. Only the layers
        // that stroke an open path ever set it before, so a node pass (a closed
        // circle, where neither cap nor join is observable) still inherits the
        // value its predecessor left, exactly as it used to.
        if (features.length > 0 && (layer === 'pipes' || layer === 'catchments')) {
          this.applyStrokeDetail(simplifiedStroke)
        }
        for (const feature of features) {
          this.drawFeature(layer, feature, profile, simplifiedStroke)
        }
        drawn[layer] = features.length
      }
      // A node glyph is drawn under a per-glyph transform; restore the base one.
      this.applyBaseTransform()
    }
    this.lastStats = {
      zoom,
      visible,
      drawn,
      indexed,
      decimated,
      frameCacheKey: this.frameKey,
      renderMs: Math.max(0, now() - started),
    }
    this.layerOptions.onRenderStats?.(this.getRenderStats())
    return this.getRenderStats()
  }

  requestRender(): this {
    if (this.destroyed || !this.map || this.renderPending) return this
    this.renderPending = true
    const run = () => {
      this.renderHandle = null
      this.renderPending = false
      this.renderNow()
    }
    if (typeof requestAnimationFrame === 'function') this.renderHandle = requestAnimationFrame(run)
    else this.renderHandle = setTimeout(run, 0)
    return this
  }

  override onAdd(map: L.Map): this {
    if (this.destroyed) return this
    if (this.map && this.map !== map) this.onRemove(this.map)
    this.map = map
    const pane =
      map.getPanes?.()[this.layerOptions.pane ?? 'overlayPane'] ??
      map.getPanes?.().overlayPane ??
      map.getContainer()
    this.canvas = L.DomUtil.create('canvas', 'hydraulic-map-canvas') as HTMLCanvasElement
    this.canvas.setAttribute('role', 'application')
    this.canvas.tabIndex = 0
    this.canvas.setAttribute(
      'aria-label',
      this.layerOptions.labels?.canvasLabel ?? 'Hydraulic network canvas'
    )
    this.canvas.style.position = 'absolute'
    this.canvas.style.left = '0'
    this.canvas.style.top = '0'
    this.canvas.style.pointerEvents = this.layerOptions.interactive === false ? 'none' : 'auto'
    pane.appendChild(this.canvas)
    this.context = this.canvas.getContext('2d')
    if (this.layerOptions.interactive !== false) {
      this.canvas.addEventListener('mousemove', this.boundMouseMove)
      this.canvas.addEventListener('click', this.boundClick)
      this.canvas.addEventListener('keydown', this.boundKeyDown)
      this.canvas.addEventListener('mouseleave', this.boundMouseLeave)
      this.canvasCleanups.push(() => {
        this.canvas?.removeEventListener('mousemove', this.boundMouseMove)
        this.canvas?.removeEventListener('click', this.boundClick)
        this.canvas?.removeEventListener('keydown', this.boundKeyDown)
        this.canvas?.removeEventListener('mouseleave', this.boundMouseLeave)
      })
    }
    if (this.layerOptions.tooltip !== false) this.createTooltip(map)
    const renderHandler = () => this.requestRender()
    for (const event of ['move', 'zoom', 'moveend', 'zoomend', 'resize', 'viewreset'] as const) {
      map.on(event, renderHandler as L.LeafletEventHandlerFn)
      this.mapCleanups.push(() => map.off(event, renderHandler as L.LeafletEventHandlerFn))
    }
    this.rebuildIndexes()
    this.prepareCanvas()
    this.requestRender()
    return this
  }

  override onRemove(_map: L.Map): this {
    this.cancelScheduledRender()
    for (const cleanup of this.mapCleanups.splice(0)) cleanup()
    for (const cleanup of this.canvasCleanups.splice(0)) cleanup()
    this.canvas?.parentNode?.removeChild(this.canvas)
    this.canvas = null
    this.context = null
    this.tooltipElement?.parentNode?.removeChild(this.tooltipElement)
    this.tooltipElement = null
    this.lastVisible = { nodes: [], pipes: [], catchments: [], ponding: [] }
    this.map = null
    if (this.layerOptions.clearFrameCacheOnRemove) this.layerOptions.frameCache?.clear()
    return this
  }

  destroy(): void {
    if (this.destroyed) return
    if (this.map) this.onRemove(this.map)
    this.destroyed = true
    this.data = null
    this.frame = null
    this.frameLookup = new FrameLookup(null)
    this.indexes = null
    this.selectedFeatureIds.clear()
    this.cancelScheduledRender()
    if (this.layerOptions.clearFrameCacheOnRemove) this.layerOptions.frameCache?.clear()
  }

  /** Hit-test in screen space; no DOM marker is created per feature. */
  hitTest(latlng: L.LatLngExpression, pixelRadius = 8): MapFrameInteraction | null {
    if (!this.map || !this.data) return null
    const resolved = L.latLng(latlng)
    const point = asPoint(this.map.latLngToContainerPoint(resolved))
    let best: {
      layer: HydraulicLayer
      feature: BoundedMapFeature
      distance: number
      priority: number
    } | null = null
    for (const layer of HIT_LAYERS) {
      for (const feature of this.lastVisible[layer]) {
        const distanceValue = featureDistance(feature, point, (coordinate) =>
          asPoint(this.map!.latLngToContainerPoint([coordinate[1], coordinate[0]]))
        )
        const isBetter =
          !best ||
          distanceValue + 0.5 < best.distance ||
          (Math.abs(distanceValue - best.distance) <= 0.5 &&
            HIT_LAYER_PRIORITY[layer] < best.priority)
        if (distanceValue <= pixelRadius && isBetter) {
          best = { layer, feature, distance: distanceValue, priority: HIT_LAYER_PRIORITY[layer] }
        }
      }
    }
    if (!best) return null
    return this.createInteraction(best.layer, best.feature, {
      lat: resolved.lat,
      lng: resolved.lng,
    })
  }

  private rebuildIndexes(): void {
    if (!this.data) {
      this.indexes = null
      return
    }
    this.indexes = {
      nodes: new ViewportIndex(
        this.data.nodes,
        (feature) => feature.bounds,
        this.layerOptions.viewportCellSize
      ),
      pipes: new ViewportIndex(
        this.data.pipes,
        (feature) => feature.bounds,
        this.layerOptions.viewportCellSize
      ),
      catchments: new ViewportIndex(
        this.data.catchments,
        (feature) => feature.bounds,
        this.layerOptions.viewportCellSize
      ),
      ponding: new ViewportIndex(
        this.data.ponding,
        (feature) => feature.bounds,
        this.layerOptions.viewportCellSize
      ),
    }
  }

  private isLayerVisibleAtZoom(
    layer: HydraulicLayer,
    profile: ReturnType<typeof getLodProfile>
  ): boolean {
    if (layer === 'nodes') return profile.showNodes
    if (layer === 'pipes') return profile.showPipes
    if (layer === 'catchments') return profile.showCatchments
    return profile.showPonding
  }

  private prepareCanvas(): void {
    if (!this.canvas || !this.context || !this.map) return
    const size = mapSize(this.map)
    const dpr = typeof devicePixelRatio === 'number' && devicePixelRatio > 0 ? devicePixelRatio : 1
    const width = Math.round(size.x * dpr)
    const height = Math.round(size.y * dpr)
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width
      this.canvas.height = height
      this.canvas.style.width = `${size.x}px`
      this.canvas.style.height = `${size.y}px`
    }
    this.pixelRatio = dpr
    this.context.setTransform(dpr, 0, 0, dpr, 0, 0)
    // Re-seed the dash cache: the 2D context outlives a single pass.
    this.appliedDash = null
  }

  /** Cap/join style for one layer pass; see the call site in renderNow. */
  private applyStrokeDetail(simplified: boolean): void {
    if (!this.context) return
    this.context.lineCap = simplified ? 'butt' : 'round'
    this.context.lineJoin = simplified ? 'miter' : 'round'
  }

  /** Re-apply the line dash only when the pattern actually changes. */
  private applyDash(dash: readonly number[]): void {
    if (this.appliedDash === dash) return
    this.context?.setLineDash(dash as number[])
    this.appliedDash = dash
  }

  private drawFeature(
    layer: HydraulicLayer,
    feature: BoundedMapFeature,
    profile: ReturnType<typeof getLodProfile>,
    simplifiedStroke: boolean
  ): void {
    if (!this.context || !this.map) return
    const style = getFeatureStyle(feature, layer, this.frameLookup, {
      metric: this.colorMetric,
      selected: this.selectedFeatureIds.has(feature.id),
      labels: this.styleLabels(),
    })
    if (feature.geometry.type === 'Point') {
      this.drawNode(feature, style)
    } else if (feature.geometry.type === 'LineString') {
      this.drawPipe(feature, style, simplifiedStroke)
    } else {
      this.drawPolygon(feature, style, layer, profile, simplifiedStroke)
    }
    if (this.shouldDrawLabel(feature, style, profile)) this.drawLabel(feature, style)
  }

  private styleLabels(): FeatureStyleLabels {
    const labels = this.layerOptions.labels
    this.drawLabels.noData = labels?.noDataLabel ?? 'No data'
    this.drawLabels.predicted = labels?.predictedLabel ?? 'Predicted'
    this.drawLabels.observed = labels?.observedLabel ?? 'Observed'
    return this.drawLabels
  }

  private drawNode(feature: BoundedMapFeature, style: FeatureMapStyle): void {
    if (!this.context || !this.map || feature.geometry.type !== 'Point') return
    const coordinate = feature.geometry.coordinates
    const point = projectLongitudeLatitude(this.map, coordinate[0], coordinate[1])
    const context = this.context
    // A node with a non-critical style has strokeColor === fillColor, both
    // fully opaque, so `fill()` of radius r followed by `stroke()` of the same
    // circle paints exactly one opaque disc of radius r + lineWidth / 2 (the
    // annulus straddles the fill's edge, so their union is the wider disc).
    // That is one rasterised path instead of two. A critical node keeps both:
    // its stroke is a distinct white outline and the two passes are not
    // interchangeable.
    const merged = style.fillColor === style.strokeColor
    const circle = unitCircle()
    if (circle) {
      // Draw under a transform that scales the cached unit circle to the glyph
      // radius; `prepareCanvas` re-seeds the base transform on every pass and
      // it is restored immediately below so later draws stay in container space.
      const radius = merged ? style.radius + style.lineWidth / 2 : style.radius
      this.applyUnitCircle(point.x, point.y, radius)
      context.fillStyle = style.fillColor
      context.fill(circle)
      if (!merged) {
        context.lineWidth = style.lineWidth / style.radius
        context.strokeStyle = style.strokeColor
        context.stroke(circle)
      }
      this.applyBaseTransform()
      return
    }
    // No save/restore: every draw helper sets the full state it reads, and
    // this context is re-seeded by prepareCanvas on each pass.
    context.beginPath()
    context.arc(
      point.x,
      point.y,
      merged ? style.radius + style.lineWidth / 2 : style.radius,
      0,
      Math.PI * 2
    )
    context.fillStyle = style.fillColor
    context.fill()
    if (merged) return
    context.lineWidth = style.lineWidth
    context.strokeStyle = style.strokeColor
    context.stroke()
  }

  /** Scale the cached unit circle onto one device-space glyph. */
  private applyUnitCircle(x: number, y: number, radius: number): void {
    const dpr = this.pixelRatio
    this.context?.setTransform(dpr * radius, 0, 0, dpr * radius, dpr * x, dpr * y)
  }

  private applyBaseTransform(): void {
    const dpr = this.pixelRatio
    this.context?.setTransform(dpr, 0, 0, dpr, 0, 0)
  }

  private drawPipe(
    feature: BoundedMapFeature,
    style: FeatureMapStyle,
    simplifiedStroke: boolean
  ): void {
    if (
      !this.context ||
      !this.map ||
      feature.geometry.type !== 'LineString' ||
      feature.geometry.coordinates.length < 2
    )
      return
    const context = this.context
    const coordinates = feature.geometry.coordinates
    context.beginPath()
    for (let index = 0; index < coordinates.length; index += 1) {
      const coordinate = coordinates[index]
      const point = projectLongitudeLatitude(this.map, coordinate[0], coordinate[1])
      if (index === 0) context.moveTo(point.x, point.y)
      else context.lineTo(point.x, point.y)
    }
    // Cap/join for this pass were applied once in renderNow.
    context.lineWidth = style.lineWidth
    context.strokeStyle = style.color
    this.applyDash(simplifiedStroke ? SOLID_LINE_DASH : style.dash)
    context.stroke()
  }

  private drawPolygon(
    feature: BoundedMapFeature,
    style: FeatureMapStyle,
    layer: HydraulicLayer,
    profile: ReturnType<typeof getLodProfile>,
    simplifiedStroke: boolean
  ): void {
    if (
      !this.context ||
      !this.map ||
      feature.geometry.type === 'Point' ||
      feature.geometry.type === 'LineString'
    )
      return
    const context = this.context
    const polygons =
      feature.geometry.type === 'Polygon'
        ? [feature.geometry.coordinates]
        : feature.geometry.coordinates
    const outlineOnly = layer === 'catchments' && profile.catchmentMode === 'aggregate'
    for (const polygon of polygons) {
      if (polygon.length === 0 || !ringHasArea(polygon[0].length)) continue
      context.beginPath()
      for (const ring of polygon) {
        for (let index = 0; index < ring.length; index += 1) {
          const coordinate = ring[index]
          const point = projectLongitudeLatitude(this.map, coordinate[0], coordinate[1])
          if (index === 0) context.moveTo(point.x, point.y)
          else context.lineTo(point.x, point.y)
        }
        context.closePath()
      }
      if (!outlineOnly) {
        context.globalAlpha = style.critical ? 0.72 : 0.42
        context.fillStyle = style.fillColor
        context.fill('evenodd')
        context.globalAlpha = 1
      }
      context.lineWidth = style.lineWidth
      context.strokeStyle = style.strokeColor
      this.applyDash(simplifiedStroke ? SOLID_LINE_DASH : style.dash)
      context.stroke()
    }
  }

  private shouldDrawLabel(
    feature: BoundedMapFeature,
    style: FeatureMapStyle,
    profile: ReturnType<typeof getLodProfile>
  ): boolean {
    if (!this.map) return false
    if (!profile.showLabels) return false
    if (this.selectedFeatureIds.has(feature.id)) return true
    return profile.showCriticalLabels && (style.critical || feature.properties?.critical === true)
  }

  private drawLabel(feature: BoundedMapFeature, style: FeatureMapStyle): void {
    if (!this.context || !this.map) return
    const first = firstCoordinate(feature)
    if (!first) return
    const point = projectLongitudeLatitude(this.map, first[0], first[1])
    this.context.font = '11px sans-serif'
    this.context.fillStyle = '#ffffff'
    this.context.strokeStyle = '#111827'
    this.context.lineWidth = 3
    this.context.strokeText(style.label, point.x + style.radius + 3, point.y - 3)
    this.context.fillText(style.label, point.x + style.radius + 3, point.y - 3)
  }

  private createTooltip(map: L.Map): void {
    if (this.tooltipElement || this.layerOptions.tooltip === false) return
    const tooltip = L.DomUtil.create('div', 'hydraulic-map-tooltip') as HTMLDivElement
    tooltip.setAttribute('role', 'tooltip')
    tooltip.style.position = 'absolute'
    tooltip.style.pointerEvents = 'none'
    tooltip.style.display = 'none'
    tooltip.style.zIndex = '1000'
    map.getContainer().appendChild(tooltip)
    this.tooltipElement = tooltip
  }

  private createInteraction(
    layer: HydraulicLayer,
    feature: BoundedMapFeature,
    latlng: { lat: number; lng: number }
  ): MapFrameInteraction {
    const style = getFeatureStyle(feature, layer, this.frameLookup, {
      metric: this.colorMetric,
      selected: this.selectedFeatureIds.has(feature.id),
      labels: {
        noData: this.layerOptions.labels?.noDataLabel ?? 'No data',
        predicted: this.layerOptions.labels?.predictedLabel ?? 'Predicted',
        observed: this.layerOptions.labels?.observedLabel ?? 'Observed',
      },
    })
    return {
      layer,
      feature,
      latlng,
      frame: this.frame,
      metric: this.colorMetric,
      value: style.value,
      style,
    }
  }

  private eventPoint(event: MouseEvent): CanvasPoint {
    if (this.canvas) {
      const rect = this.canvas.getBoundingClientRect()
      if (Number.isFinite(event.clientX) && Number.isFinite(event.clientY)) {
        return { x: event.clientX - rect.left, y: event.clientY - rect.top }
      }
    }
    return { x: Number(event.offsetX) || 0, y: Number(event.offsetY) || 0 }
  }

  private eventLatLng(point: CanvasPoint): L.LatLng | null {
    if (!this.map) return null
    try {
      return this.map.containerPointToLatLng(L.point(point.x, point.y))
    } catch {
      return null
    }
  }

  private handleMouseMove(event: MouseEvent): void {
    if (!this.map || !this.data) return
    const timestamp = now()
    if (timestamp - this.lastHoverAt < 33) return
    this.lastHoverAt = timestamp
    const point = this.eventPoint(event)
    const latlng = this.eventLatLng(point)
    if (!latlng) return
    let closest: {
      layer: HydraulicLayer
      feature: BoundedMapFeature
      distance: number
      priority: number
    } | null = null
    for (const layer of HIT_LAYERS) {
      for (const feature of this.lastVisible[layer]) {
        const distanceValue = featureDistance(feature, point, (coordinate) =>
          asPoint(this.map!.latLngToContainerPoint([coordinate[1], coordinate[0]]))
        )
        const isBetter =
          !closest ||
          distanceValue + 0.5 < closest.distance ||
          (Math.abs(distanceValue - closest.distance) <= 0.5 &&
            HIT_LAYER_PRIORITY[layer] < closest.priority)
        if (distanceValue <= 8 && isBetter) {
          closest = { layer, feature, distance: distanceValue, priority: HIT_LAYER_PRIORITY[layer] }
        }
      }
    }
    if (!closest) {
      this.layerOptions.onFeatureHover?.(null)
      this.hideTooltip()
      return
    }
    const interaction = this.createInteraction(closest.layer, closest.feature, {
      lat: latlng.lat,
      lng: latlng.lng,
    })
    this.layerOptions.onFeatureHover?.(interaction)
    this.showTooltip(interaction, point)
  }

  private handleKeyDown(event: KeyboardEvent): void {
    const features = this.getKeyboardFeatures()
    if (features.length === 0) return
    let nextIndex = this.keyboardFeatureIndex
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = (nextIndex + 1) % features.length
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = (nextIndex - 1 + features.length) % features.length
    } else if (event.key === 'Home') {
      nextIndex = 0
    } else if (event.key === 'End') {
      nextIndex = features.length - 1
    } else if (event.key === 'Enter' || event.key === ' ') {
      if (this.keyboardFeatureIndex >= 0) {
        event.preventDefault()
        const feature = features[this.keyboardFeatureIndex]
        const layer = HIT_LAYERS.find((candidate) =>
          this.lastVisible[candidate].some((entry) => entry.id === feature.id)
        )
        if (layer) {
          this.layerOptions.onFeatureClick?.(
            this.createInteraction(layer, feature, {
              lat: (feature.bounds.south + feature.bounds.north) / 2,
              lng: (feature.bounds.west + feature.bounds.east) / 2,
            })
          )
        }
      }
      return
    } else {
      return
    }
    event.preventDefault()
    this.keyboardFeatureIndex = nextIndex
    const feature = features[nextIndex]
    const layer = HIT_LAYERS.find((candidate) =>
      this.lastVisible[candidate].some((entry) => entry.id === feature.id)
    )
    if (!layer) return
    const interaction = this.createInteraction(layer, feature, {
      lat: (feature.bounds.south + feature.bounds.north) / 2,
      lng: (feature.bounds.west + feature.bounds.east) / 2,
    })
    this.selectedFeatureIds = new Set([feature.id])
    this.canvas?.setAttribute('aria-label', interaction.style.accessibleLabel)
    this.layerOptions.onFeatureFocus?.(interaction)
    this.requestRender()
  }

  private handleClick(event: MouseEvent): void {
    if (!this.map || !this.data) return
    const point = this.eventPoint(event)
    const latlng = this.eventLatLng(point)
    if (!latlng) return
    const interaction = this.hitTest(latlng, 8)
    if (interaction) this.layerOptions.onFeatureClick?.(interaction)
  }

  private showTooltip(interaction: MapFrameInteraction, point: CanvasPoint): void {
    if (!this.tooltipElement) return
    this.tooltipElement.replaceChildren()
    const title = document.createElement('strong')
    title.textContent = interaction.style.label
    this.tooltipElement.appendChild(title)
    const labels = this.layerOptions.labels
    const rows = [
      [labels?.layerLabel(interaction.layer) ?? 'Layer', interaction.layer],
      [
        labels?.metricLabel(interaction.metric) ?? 'Metric',
        formatColorMetricValue(
          interaction.metric,
          interaction.value,
          labels?.noDataLabel ?? 'No data'
        ),
      ],
      [labels?.idLabel ?? 'ID', interaction.feature.id],
    ] as const
    for (const [label, value] of rows) {
      const row = document.createElement('div')
      row.textContent = `${label}: ${value}`
      this.tooltipElement.appendChild(row)
    }
    this.tooltipElement.style.display = 'block'
    this.tooltipElement.style.left = `${Math.max(0, point.x + 12)}px`
    this.tooltipElement.style.top = `${Math.max(0, point.y + 12)}px`
    this.layerOptions.onTooltip?.(interaction)
  }

  private hideTooltip(): void {
    if (this.tooltipElement) this.tooltipElement.style.display = 'none'
    this.layerOptions.onFeatureHover?.(null)
  }

  private cancelScheduledRender(): void {
    if (this.renderHandle === null) return
    if (typeof cancelAnimationFrame === 'function' && typeof this.renderHandle === 'number')
      cancelAnimationFrame(this.renderHandle)
    else clearTimeout(this.renderHandle)
    this.renderHandle = null
    this.renderPending = false
  }
}

function firstCoordinate(feature: BoundedMapFeature): LonLat | null {
  const geometry = feature.geometry
  if (geometry.type === 'Point') return geometry.coordinates
  if (geometry.type === 'LineString') return geometry.coordinates[0] ?? null
  if (geometry.type === 'Polygon') return geometry.coordinates[0]?.[0] ?? null
  return geometry.coordinates[0]?.[0]?.[0] ?? null
}

function estimateValueBytes(value: unknown): number {
  if (typeof value === 'number') return 8
  if (typeof value === 'string') return value.length * 2
  if (typeof value === 'boolean') return 4
  if (Array.isArray(value)) {
    return 24 + value.reduce((total, entry) => total + estimateValueBytes(entry), 0)
  }
  if (value && typeof value === 'object') {
    return 32 + Object.values(value).reduce((total, entry) => total + estimateValueBytes(entry), 0)
  }
  return 0
}

function estimateGeometryBytes(data: NormalizedMapData): number {
  return ['nodes', 'pipes', 'catchments', 'ponding'].reduce(
    (total, layer) =>
      total +
      data[layer as HydraulicLayer].reduce(
        (layerTotal, feature) => layerTotal + estimateValueBytes(feature),
        0
      ),
    0
  )
}

export function createHydraulicCanvasLayer(
  options: HydraulicCanvasLayerOptions = {}
): HydraulicCanvasLayer {
  return new HydraulicCanvasLayer(options)
}

export { frameCacheKey }
