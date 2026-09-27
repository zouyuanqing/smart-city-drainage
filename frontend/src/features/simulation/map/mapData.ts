import type {
  BoundedMapFeature,
  CatchmentMapFeature,
  FeatureCollection,
  HydraulicLayer,
  MapFeature,
  NetworkMapData,
  NodeMapFeature,
  NormalizedMapData,
  PipeMapFeature,
  PondingMapFeature,
} from './types'
import { boundsForGeometry } from './viewport'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export class MapDataNormalizationError extends Error {
  readonly layer: HydraulicLayer
  readonly featureIndex: number

  constructor(layer: HydraulicLayer, featureIndex: number, message: string) {
    super(`${layer}[${featureIndex}]: ${message}`)
    this.name = 'MapDataNormalizationError'
    this.layer = layer
    this.featureIndex = featureIndex
  }
}

function featureList(payload: unknown, layer: HydraulicLayer): readonly MapFeature[] {
  if (payload === undefined) {
    if (layer === 'ponding') return []
    throw new MapDataNormalizationError(layer, -1, 'layer payload is required')
  }
  if (Array.isArray(payload)) return payload
  const collection = payload as FeatureCollection
  if (collection.type !== 'FeatureCollection' || !Array.isArray(collection.features)) {
    throw new MapDataNormalizationError(layer, -1, 'layer must be a FeatureCollection')
  }
  return collection.features
}

function normalizeFeature<TFeature extends MapFeature>(
  feature: TFeature,
  layer: HydraulicLayer,
  fallbackIndex: number
): BoundedMapFeature<TFeature> {
  try {
    if (!feature || typeof feature !== 'object') throw new Error('feature must be an object')
    if (feature.type !== undefined && feature.type !== 'Feature') {
      throw new Error('feature.type must be Feature')
    }
    if (typeof feature.id !== 'string' || !UUID_RE.test(feature.id)) {
      throw new Error('feature.id must be a UUID')
    }
    if (!feature.geometry || typeof feature.geometry !== 'object') {
      throw new Error('geometry is required')
    }
    const rawIndex = feature.feature_index ?? feature.properties?.feature_index
    if (!Number.isSafeInteger(rawIndex) || (rawIndex as number) < 0) {
      throw new Error('feature_index must be a non-negative integer')
    }
    const featureIndex = rawIndex as number
    const bounds = boundsForGeometry(feature.geometry)
    const properties = { ...(feature.properties ?? {}), feature_index: featureIndex }
    return {
      ...feature,
      id: feature.id,
      feature_index: featureIndex,
      properties,
      bounds,
    } as BoundedMapFeature<TFeature>
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    throw new MapDataNormalizationError(layer, fallbackIndex, message)
  }
}

function normalizeLayer<TFeature extends MapFeature>(
  payload: unknown,
  layer: HydraulicLayer
): readonly BoundedMapFeature<TFeature>[] {
  const features = featureList(payload, layer)
  let previousIndex = -1
  return features.map((feature, index) => {
    const normalized = normalizeFeature(feature as TFeature, layer, index)
    if (normalized.feature_index <= previousIndex) {
      throw new MapDataNormalizationError(layer, index, 'feature_index must be strictly ascending')
    }
    previousIndex = normalized.feature_index
    return normalized
  })
}

function tupleBBox(bbox: NetworkMapData['bbox']): NormalizedMapData['bbox'] {
  if (!Array.isArray(bbox) || bbox.length !== 4) {
    throw new MapDataNormalizationError('nodes', -1, 'map bbox must be [west,south,east,north]')
  }
  const [west, south, east, north] = bbox
  if (
    ![west, south, east, north].every(
      (value) => typeof value === 'number' && Number.isFinite(value)
    )
  ) {
    throw new MapDataNormalizationError('nodes', -1, 'map bbox must contain finite numbers')
  }
  if (west < -180 || east > 180 || south < -90 || north > 90 || west >= east || south >= north) {
    throw new MapDataNormalizationError('nodes', -1, 'map bbox is outside EPSG:4326')
  }
  return { west, south, east, north }
}

function countMap(value: Record<string, number>, field: string): Record<string, number> {
  return Object.fromEntries(
    Object.entries(value).map(([key, count]) => {
      if (!Number.isSafeInteger(count) || count < 0) {
        throw new MapDataNormalizationError(
          'nodes',
          -1,
          `${field}.${key} must be a non-negative integer`
        )
      }
      return [key, count]
    })
  )
}

/** Convert a canonical API snapshot into a fail-closed renderer snapshot. */
export function normalizeMapData(data: NetworkMapData): NormalizedMapData {
  if (data.schema_version !== 'network-map-v2') {
    throw new MapDataNormalizationError('nodes', -1, 'unsupported map schema_version')
  }
  if (data.crs !== 'EPSG:4326') {
    throw new MapDataNormalizationError('nodes', -1, 'map CRS must be EPSG:4326')
  }
  if (
    !Number.isFinite(data.zoom) ||
    data.zoom < 0 ||
    data.zoom > 24 ||
    !Number.isSafeInteger(data.zoom)
  ) {
    throw new MapDataNormalizationError('nodes', -1, 'zoom must be an integer between 0 and 24')
  }
  if (!Array.isArray(data.truncated_layers)) {
    throw new MapDataNormalizationError('nodes', -1, 'truncated_layers is required')
  }
  if (data.complete && data.truncated_layers.length > 0) {
    throw new MapDataNormalizationError('nodes', -1, 'complete data cannot list truncated layers')
  }
  if (!data.complete && data.truncated_layers.length === 0) {
    throw new MapDataNormalizationError('nodes', -1, 'partial data must identify truncated layers')
  }

  const nodes = normalizeLayer<NodeMapFeature>(data.nodes, 'nodes')
  const pipes = normalizeLayer<PipeMapFeature>(data.pipes, 'pipes')
  const catchments = normalizeLayer<CatchmentMapFeature>(data.catchments, 'catchments')
  const ponding = normalizeLayer<PondingMapFeature>(data.ponding, 'ponding')
  const sourceCounts = countMap(data.source_counts, 'source_counts')
  for (const layer of ['nodes', 'pipes', 'catchments']) {
    if (sourceCounts[layer] === undefined) {
      throw new MapDataNormalizationError(
        layer as HydraulicLayer,
        -1,
        `source_counts.${layer} is required`
      )
    }
  }
  const returnedCounts = countMap(data.returned_counts, 'returned_counts')
  const layerCollections: Record<string, readonly unknown[]> = {
    nodes,
    pipes,
    catchments,
    ...(data.ponding === undefined ? {} : { ponding }),
  }
  for (const [layer, features] of Object.entries(layerCollections)) {
    if (returnedCounts[layer] === undefined) {
      throw new MapDataNormalizationError(
        layer as HydraulicLayer,
        -1,
        `returned_counts.${layer} is required`
      )
    }
    if (returnedCounts[layer] !== features.length) {
      throw new MapDataNormalizationError(
        layer as HydraulicLayer,
        -1,
        `returned_counts.${layer} does not match feature count`
      )
    }
  }

  return {
    schemaVersion: 'network-map-v2',
    networkId: data.network_id,
    networkRevisionId: data.network_revision_id,
    targetManifestId: data.target_manifest_id,
    targetManifestSha256: data.target_manifest_sha256,
    crs: 'EPSG:4326',
    bbox: tupleBBox(data.bbox),
    zoom: data.zoom,
    complete: data.complete,
    simplified: data.simplified,
    truncatedLayers: [...data.truncated_layers] as HydraulicLayer[],
    sourceCounts: Object.freeze(sourceCounts),
    returnedCounts: Object.freeze(returnedCounts),
    nodes,
    pipes,
    catchments,
    ponding,
  }
}

export function cloneMapData(data: NormalizedMapData): NormalizedMapData {
  return {
    ...data,
    truncatedLayers: [...data.truncatedLayers],
    sourceCounts: { ...data.sourceCounts },
    returnedCounts: { ...data.returnedCounts },
    nodes: data.nodes.map((feature) => ({ ...feature })),
    pipes: data.pipes.map((feature) => ({ ...feature })),
    catchments: data.catchments.map((feature) => ({ ...feature })),
    ponding: data.ponding.map((feature) => ({ ...feature })),
  }
}
