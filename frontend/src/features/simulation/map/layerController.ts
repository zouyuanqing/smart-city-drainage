import type L from 'leaflet'
import type {
  FrameFields,
  HydraulicColorMetric,
  HydraulicLayer,
  MapLayerVisibility,
  NetworkMapData,
  NormalizedMapData,
  SimulationFrame,
} from './types'
import { HydraulicCanvasLayer, type HydraulicCanvasLayerOptions } from './canvasLayer'
import { encodeFrame, FrameCache, FrameCacheClient, type CachedFrame } from './frameCache'
import { FrameDecoder } from './frameDecoder'

export interface SimulationMapControllerOptions extends HydraulicCanvasLayerOptions {
  /** Create an internal cache when the caller does not provide one. */
  ownFrameCache?: boolean
  frameClient?: FrameCacheClient
  frameDecoder?: FrameDecoder
}

/** Command-style owner for one Leaflet map and the shared typed-array path. */
export class SimulationMapController {
  readonly layer: HydraulicCanvasLayer
  readonly frameCache: FrameCache | null
  readonly frameClient: FrameCacheClient | null
  readonly frameDecoder: FrameDecoder | null
  private mountedMap: L.Map | null = null
  private disposed = false

  constructor(options: SimulationMapControllerOptions = {}) {
    const shouldCreateCache = options.ownFrameCache ?? options.frameCache === undefined
    this.frameCache =
      options.frameClient?.cache ??
      options.frameCache ??
      (shouldCreateCache ? new FrameCache() : null)
    this.frameClient =
      options.frameClient ?? (this.frameCache ? new FrameCacheClient(this.frameCache) : null)
    this.frameDecoder = options.frameDecoder ?? null
    this.layer = new HydraulicCanvasLayer({
      ...options,
      ...(this.frameCache ? { frameCache: this.frameCache } : {}),
    })
  }

  get isDisposed(): boolean {
    return this.disposed
  }

  get isMounted(): boolean {
    return this.mountedMap !== null
  }

  addTo(map: L.Map): HydraulicCanvasLayer {
    if (this.disposed) return this.layer
    if (this.mountedMap && this.mountedMap !== map) this.remove()
    this.mountedMap = map
    this.layer.addTo(map)
    return this.layer
  }

  mount(map: L.Map): HydraulicCanvasLayer {
    return this.addTo(map)
  }

  setData(data: NetworkMapData | NormalizedMapData): this {
    this.layer.setData(data)
    return this
  }

  setFrame(
    frame: SimulationFrame | CachedFrame | null,
    cacheKey?: string,
    expectedChecksum?: string
  ): this {
    this.layer.setFrame(frame, cacheKey, expectedChecksum)
    return this
  }

  /** Decode through the worker (or deterministic fallback) and render typed arrays only. */
  async loadFrame(
    frame: SimulationFrame | CachedFrame,
    cacheKey: string,
    fields?: FrameFields,
    expectedChecksum?: string
  ): Promise<CachedFrame> {
    if (this.disposed) throw new Error('SimulationMapController is disposed')
    if ('key' in frame) {
      if (expectedChecksum && frame.checksum !== expectedChecksum) {
        throw new Error('frame checksum does not match the index')
      }
      this.frameClient?.cache.put(cacheKey, frame)
      this.layer.setFrame(frame, cacheKey, expectedChecksum)
      return frame
    }
    const cached = this.frameDecoder
      ? await this.frameDecoder.decode(frame, fields, expectedChecksum)
      : encodeFrame(cacheKey, frame)
    if (expectedChecksum && cached.checksum !== expectedChecksum) {
      throw new Error('frame checksum does not match the index')
    }
    this.frameClient?.cache.put(cacheKey, cached)
    this.layer.setFrame(cached, cacheKey, expectedChecksum)
    return cached
  }

  setColorMetric(metric: HydraulicColorMetric): this {
    this.layer.setColorMetric(metric)
    return this
  }

  setLayerVisibility(visibility: Partial<MapLayerVisibility>): this {
    this.layer.setLayerVisibility(visibility)
    return this
  }

  toggleLayer(layer: HydraulicLayer, visible?: boolean): this {
    this.layer.toggleLayer(layer, visible)
    return this
  }

  setSelectedFeatures(featureIds: Iterable<string>): this {
    this.layer.setSelectedFeatures(featureIds)
    return this
  }

  remove(): this {
    if (this.mountedMap) this.mountedMap.removeLayer(this.layer)
    this.mountedMap = null
    return this
  }

  dispose(): void {
    if (this.disposed) return
    this.remove()
    this.layer.destroy()
    this.frameClient?.dispose()
    this.frameDecoder?.dispose()
    this.disposed = true
  }
}

export function createSimulationMapController(
  options: SimulationMapControllerOptions = {}
): SimulationMapController {
  return new SimulationMapController(options)
}

export { HydraulicCanvasLayer }
export type { HydraulicCanvasLayerOptions, CachedFrame }
