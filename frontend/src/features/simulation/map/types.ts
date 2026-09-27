import type {
  NetworkMapData as CanonicalNetworkMapData,
  SimulationFrame as CanonicalSimulationFrame,
  SimulationFrameQuality,
  SimulationNodeFrameValues,
  SimulationPipeFrameValues,
  SimulationPondingFrameValues,
} from '../model/types'

export type LonLat = readonly [number, number]
export type PolygonRing = readonly LonLat[]
export type PolygonCoordinates = readonly PolygonRing[]
export type MultiPolygonCoordinates = readonly PolygonCoordinates[]

export interface PointGeometry {
  type: 'Point'
  coordinates: LonLat
}

export interface LineStringGeometry {
  type: 'LineString'
  coordinates: readonly LonLat[]
}

export interface PolygonGeometry {
  type: 'Polygon'
  coordinates: PolygonCoordinates
}

export interface MultiPolygonGeometry {
  type: 'MultiPolygon'
  coordinates: MultiPolygonCoordinates
}

export type MapGeometry =
  | PointGeometry
  | LineStringGeometry
  | PolygonGeometry
  | MultiPolygonGeometry

/** Internal renderer bounds. Wire bbox remains the canonical tuple in model/types. */
export interface BBox {
  west: number
  south: number
  east: number
  north: number
}

export type HydraulicLayer = 'nodes' | 'pipes' | 'catchments' | 'ponding'
export type HydraulicColorMetric = 'fill_ratio' | 'velocity_mps' | 'depth_m'
export type FeaturePhase = 'predicted' | 'observed'

export interface MapFeatureProperties {
  feature_index: number
  stable_key?: string
  name?: string
  critical?: boolean
  major?: boolean
  phase?: FeaturePhase
  [key: string]: unknown
}

export interface MapFeature<TGeometry extends MapGeometry = MapGeometry> {
  type: 'Feature'
  id: string
  feature_index: number
  geometry: TGeometry
  properties: MapFeatureProperties
}

export type NodeMapFeature = MapFeature<PointGeometry>
export type PipeMapFeature = MapFeature<LineStringGeometry>
export type CatchmentMapFeature = MapFeature<PolygonGeometry | MultiPolygonGeometry>
export type PondingMapFeature = MapFeature<PolygonGeometry | MultiPolygonGeometry>

export interface FeatureCollection<TFeature extends MapFeature = MapFeature> {
  type: 'FeatureCollection'
  features: readonly TFeature[]
}

export type LayerPayload<TFeature extends MapFeature> =
  | readonly TFeature[]
  | FeatureCollection<TFeature>

/** Canonical API map data is directly accepted by the map boundary. */
export type NetworkMapData = CanonicalNetworkMapData

export type BoundedMapFeature<TFeature extends MapFeature = MapFeature> = TFeature & {
  bounds: BBox
}

export interface NormalizedMapData {
  schemaVersion: 'network-map-v2'
  networkId: string
  networkRevisionId: string
  targetManifestId: string
  targetManifestSha256: string
  crs: 'EPSG:4326'
  bbox: BBox
  zoom: number
  complete: boolean
  simplified: boolean
  truncatedLayers: readonly HydraulicLayer[]
  sourceCounts: Readonly<Record<string, number>>
  returnedCounts: Readonly<Record<string, number>>
  nodes: readonly BoundedMapFeature<NodeMapFeature>[]
  pipes: readonly BoundedMapFeature<PipeMapFeature>[]
  catchments: readonly BoundedMapFeature<CatchmentMapFeature>[]
  ponding: readonly BoundedMapFeature<PondingMapFeature>[]
}

export type NullableNumber = number | null
export type NodeFrameData = SimulationNodeFrameValues
export type PipeFrameData = SimulationPipeFrameValues
export type PondingFrameData = SimulationPondingFrameValues
export type SimulationFrame = CanonicalSimulationFrame
export type { SimulationFrameQuality }

/** Canonical frame fields requested by the shared cache/worker. */
export interface FrameFields {
  nodes?: readonly ('depth_m' | 'fill_ratio' | 'surcharged')[]
  pipes?: readonly ('fill_ratio' | 'flow_m3s' | 'depth_m' | 'surcharged')[]
  ponding?: readonly ('depth_m' | 'flow_m3s')[]
}

export type MapLayerVisibility = Record<HydraulicLayer, boolean>

export interface MapLabels {
  canvasLabel: string
  layerLabel: (layer: HydraulicLayer) => string
  metricLabel: (metric: HydraulicColorMetric) => string
  idLabel: string
  noDataLabel: string
  predictedLabel: string
  observedLabel: string
}

export interface FeatureInteraction {
  layer: HydraulicLayer
  feature: BoundedMapFeature
  latlng: { lat: number; lng: number }
  frame: SimulationFrame | import('./frameCache').CachedFrame | null
  metric: HydraulicColorMetric
  value: number | null
  style: import('./colorScales').FeatureMapStyle
}

export interface RenderStats {
  zoom: number
  visible: Record<HydraulicLayer, number>
  drawn: Record<HydraulicLayer, number>
  indexed: Record<HydraulicLayer, number>
  decimated: Record<HydraulicLayer, number>
  frameCacheKey: string | null
  renderMs: number
}

export const DEFAULT_LAYER_VISIBILITY: Readonly<MapLayerVisibility> = Object.freeze({
  nodes: true,
  pipes: true,
  catchments: true,
  ponding: true,
})
