export {
  COLOR_BANDS,
  DEPTH_BANDS,
  FILL_RATIO_BANDS,
  NEUTRAL_BAND,
  VELOCITY_BANDS,
  colorForValue,
  depthToColor,
  fillRatioToColor,
  formatColorMetricValue,
  getColorBand,
  getFeatureStyle,
  metricValueForFeature,
  velocityToColor,
} from './colorScales'
export type {
  ColorBand,
  FeatureMapStyle,
  FeatureStyleLabels,
  FeatureStyleOptions,
} from './colorScales'

export {
  DEFAULT_FRAME_CACHE_BYTES,
  FrameCache,
  FrameCacheClient,
  FrameRequestScheduler,
  encodeFrame,
  fieldsHash,
  frameCacheKey,
  isCachedFrame,
  validateCachedFrame,
  validateFrame,
} from './frameCache'
export type {
  CachedFrame,
  CachedLayerData,
  CachedSeries,
  FrameRequestOptions,
  FrameRequestPriority,
  FrameValidationResult,
} from './frameCache'

export { FrameLookup, roundSixDecimals, valueFromFeatureProperties } from './frameLookup'
export { FrameDecoder, decodeFrameOnMainThread } from './frameDecoder'
export type { FrameDecoderOptions } from './frameDecoder'

export { MapDataNormalizationError, cloneMapData, normalizeMapData } from './mapData'

export {
  InvalidBBoxError,
  ViewportIndex,
  bboxContains,
  bboxHash,
  bboxIntersects,
  boundsForFeature,
  boundsForGeometry,
  countFeaturesInBBox,
  decimateFeatures,
  expandBBox,
  formatBBox,
  getLodProfile,
  isMajorPipeFeature,
  isValidBBox,
  normalizeBBox,
  parseBBox,
  selectVisibleFeatures,
} from './viewport'
export type { DecimateOptions, LodProfile } from './viewport'

export { HydraulicCanvasLayer, createHydraulicCanvasLayer } from './canvasLayer'
export type { HydraulicCanvasLayerOptions } from './canvasLayer'

export { SimulationMapController, createSimulationMapController } from './layerController'
export type { SimulationMapControllerOptions } from './layerController'

export type {
  BBox,
  BoundedMapFeature,
  CatchmentMapFeature,
  FeatureCollection,
  FeatureInteraction,
  FeaturePhase,
  FrameFields,
  HydraulicColorMetric,
  HydraulicLayer,
  LayerPayload,
  LonLat,
  MapFeature,
  MapFeatureProperties,
  MapGeometry,
  MapLabels,
  MapLayerVisibility,
  NetworkMapData,
  NodeFrameData,
  NodeMapFeature,
  NormalizedMapData,
  PipeFrameData,
  PipeMapFeature,
  PondingFrameData,
  PondingMapFeature,
  RenderStats,
  SimulationFrame,
} from './types'
export { DEFAULT_LAYER_VISIBILITY } from './types'
