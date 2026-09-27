import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useTranslation } from 'react-i18next'
import { createSimulationMapController, frameCacheKey } from '../map'
import type {
  HydraulicLayer,
  MapLayerVisibility,
  NetworkMapData,
  RenderStats,
  SimulationFrame,
  CachedFrame,
} from '../map'

const DEFAULT_TILE_URL = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'

export interface HydraulicMapPanelProps {
  networkId: string
  networkRevisionId: string
  mapData: NetworkMapData | null
  frame?: SimulationFrame | CachedFrame | null
  frameCacheKey?: string | null
  frameChecksum?: string | null
  /** Locally derived A/B delta frames are not server-payload frames. */
  derivedFrame?: boolean
  colorMetric?: 'fill_ratio' | 'velocity_mps' | 'depth_m'
  layerVisibility?: Partial<MapLayerVisibility>
  onStats?: (stats: RenderStats) => void
  onFeatureClick?: (featureId: string, layer: HydraulicLayer) => void
  onFrameError?: (errors: readonly string[]) => void
  className?: string
}

/** One imperative Leaflet/Canvas owner; no per-feature React markers are created. */
export function HydraulicMapPanel({
  networkId,
  networkRevisionId,
  mapData,
  frame = null,
  frameCacheKey: frameCacheKeyProp = null,
  frameChecksum = null,
  derivedFrame = false,
  colorMetric = 'fill_ratio',
  layerVisibility,
  onStats,
  onFeatureClick,
  onFrameError,
  className = '',
}: HydraulicMapPanelProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<L.Map | null>(null)
  const controllerRef = useRef<ReturnType<typeof createSimulationMapController> | null>(null)
  const callbacksRef = useRef({ onStats, onFeatureClick, onFrameError })
  const { t } = useTranslation()
  callbacksRef.current = { onStats, onFeatureClick, onFrameError }

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return undefined
    const map = L.map(containerRef.current, {
      center: [31.23, 121.47],
      zoom: 13,
      zoomControl: true,
      preferCanvas: true,
    })
    L.tileLayer(DEFAULT_TILE_URL, {
      attribution: '&copy; OpenStreetMap &copy; CARTO',
      maxZoom: 19,
    }).addTo(map)
    const controller = createSimulationMapController({
      colorMetric,
      labels: {
        canvasLabel: t('map.canvasLabel'),
        layerLabel: (layer) => t(`map.layers.${layer}`),
        metricLabel: (metric) => t(`map.metrics.${metric}`),
        idLabel: t('map.featureId'),
        noDataLabel: t('map.noData'),
        predictedLabel: t('map.predicted'),
        observedLabel: t('map.observed'),
      },
      onRenderStats: (stats) => callbacksRef.current.onStats?.(stats),
      onFeatureClick: (interaction) =>
        callbacksRef.current.onFeatureClick?.(interaction.feature.id, interaction.layer),
      onFrameError: (errors) => callbacksRef.current.onFrameError?.(errors),
      clearFrameCacheOnRemove: true,
    })
    controller.mount(map)
    mapRef.current = map
    controllerRef.current = controller
    const resize = () => map.invalidateSize({ pan: false })
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      controller.dispose()
      map.remove()
      controllerRef.current = null
      mapRef.current = null
    }
  }, [colorMetric, t])

  useEffect(() => {
    if (!mapData || !controllerRef.current) return
    controllerRef.current.setData(mapData)
  }, [mapData])

  useEffect(() => {
    const controller = controllerRef.current
    if (!controller) return
    if (layerVisibility) controller.setLayerVisibility(layerVisibility)
  }, [layerVisibility])

  useEffect(() => {
    controllerRef.current?.setColorMetric(colorMetric)
  }, [colorMetric])

  useEffect(() => {
    const controller = controllerRef.current
    if (!controller) return
    if (!frame) {
      controller.setFrame(null)
      return
    }
    const simulationId = 'simulation_id' in frame ? frame.simulation_id : frame.simulationId
    const frameIndex = 'frame_index' in frame ? frame.frame_index : frame.frameIndex
    const revisionId =
      'network_revision_id' in frame ? frame.network_revision_id : frame.networkRevisionId
    const effectiveChecksum =
      frameChecksum ??
      ('checksum' in frame
        ? frame.checksum
        : 'payload_sha256' in frame
          ? frame.payload_sha256
          : null)
    if (!derivedFrame && !effectiveChecksum) {
      callbacksRef.current.onFrameError?.(['frame checksum is required before rendering'])
      return
    }
    const canonicalKey = frameCacheKey(simulationId, frameIndex, undefined, revisionId)
    void controller
      .loadFrame(
        frame,
        frameCacheKeyProp ?? canonicalKey,
        undefined,
        derivedFrame ? undefined : effectiveChecksum ?? undefined
      )
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error)
        callbacksRef.current.onFrameError?.([message])
      })
  }, [derivedFrame, frame, frameCacheKeyProp, frameChecksum, networkId, networkRevisionId])

  return (
    <div
      ref={containerRef}
      className={`map-container ${className}`.trim()}
      style={{ width: '100%', height: '100%', minHeight: 320 }}
      data-testid="hydraulic-map-panel"
      data-network-id={networkId}
      data-network-revision-id={networkRevisionId}
    />
  )
}

export default HydraulicMapPanel
