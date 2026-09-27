import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { createSimulationMapController } from '@/features/simulation/map/layerController'
import { encodeFrame, frameCacheKey, FrameCache } from '@/features/simulation/map/frameCache'
import type { NetworkMapData, SimulationFrame } from '@/features/simulation/model/types'

const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const SIMULATION_ID = '55555555-5555-4555-8555-555555555555'
const FRAME_COUNT = 289
const OBJECT_COUNT = 16_000

function percentile(values: readonly number[], quantile: number): number {
  if (values.length === 0) return Number.NaN
  const ordered = [...values].sort((left, right) => left - right)
  return ordered[Math.max(0, Math.ceil(ordered.length * quantile) - 1)]
}

function nextFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()))
}

async function timedFetch<T>(url: string, samples: number[]): Promise<T> {
  const started = performance.now()
  const response = await fetch(url, { cache: 'no-store' })
  if (!response.ok) throw new Error(`fixture API failed: ${response.status} ${url}`)
  const value = (await response.json()) as T
  samples.push(performance.now() - started)
  return value
}

async function runHarness(durationSeconds: number, fixture: { maxCacheBytes: number }) {
  const mapElement = document.getElementById('map')
  if (!mapElement) throw new Error('performance map container is missing')

  const mapApiSamples: number[] = []
  const frameApiSamples: number[] = []
  const mapData = await timedFetch<NetworkMapData>('/api/map-data', mapApiSamples)
  const map = L.map(mapElement, {
    zoomControl: false,
    preferCanvas: true,
    attributionControl: false,
  }).fitBounds([
    [31.15, 121.3],
    [31.32, 121.6],
  ])
  const controller = createSimulationMapController({
    viewportCellSize: 0.01,
  })
  controller.mount(map)
  controller.setData(mapData)
  const cache: FrameCache = controller.frameCache ?? new FrameCache(fixture.maxCacheBytes)
  if (controller.frameCache !== cache) throw new Error('production FrameCache was not created')

  const keys: string[] = []
  for (let frameIndex = 0; frameIndex < FRAME_COUNT; frameIndex += 1) {
    const frame = await timedFetch<SimulationFrame>(
      `/api/simulations/${SIMULATION_ID}/frames/${frameIndex}`,
      frameApiSamples
    )
    const key = frameCacheKey(SIMULATION_ID, frameIndex, undefined, REVISION_ID)
    cache.put(key, encodeFrame(key, frame))
    keys.push(key)
  }
  if (cache.size !== FRAME_COUNT) throw new Error('production FrameCache did not retain 289 frames')
  controller.setFrame(cache.get(keys[0]) ?? null, keys[0])
  controller.layer.renderNow()
  await nextFrame()

  const longTasks: number[] = []
  const observer =
    typeof PerformanceObserver === 'function'
      ? new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) longTasks.push(entry.duration)
        })
      : null
  try {
    observer?.observe({ type: 'longtask', buffered: false })
  } catch {
    observer?.disconnect()
  }

  const renderSamples: number[] = []
  const rafSamples: number[] = []
  const visibleCounts: number[] = []
  let featureOperations = 0
  let cachePeakBytes = cache.sizeBytes
  let lastPlaybackAt = performance.now()
  let frameCursor = 0
  const workloadStarted = performance.now()
  const workloadDeadline = workloadStarted + durationSeconds * 1_000
  let layerMask = 0b1111

  while (performance.now() < workloadDeadline) {
    const rafStarted = performance.now()
    const elapsedSeconds = (rafStarted - workloadStarted) / 1_000
    const phase = elapsedSeconds * Math.PI * 2
    const zoom = 12.5 + 2.5 * (0.5 + 0.5 * Math.sin(phase * 2.3))
    const center: L.LatLngExpression = [
      31.235 + 0.055 * Math.sin(phase),
      121.45 + 0.11 * Math.cos(phase * 0.8),
    ]
    map.setView(center, zoom, { animate: false })

    if (rafStarted - lastPlaybackAt >= 250) {
      frameCursor = (frameCursor + 1) % FRAME_COUNT
      lastPlaybackAt = rafStarted
      const key = keys[frameCursor]
      const frame = cache.get(key)
      if (!frame) throw new Error(`FrameCache lost ${key}`)
      controller.setFrame(frame, key)
    }
    if (Math.round(elapsedSeconds * 60) % 30 === 0) {
      layerMask = (layerMask + 1) & 0b1111
      controller.setLayerVisibility({
        nodes: (layerMask & 0b0001) !== 0,
        pipes: (layerMask & 0b0010) !== 0,
        catchments: (layerMask & 0b0100) !== 0,
        ponding: (layerMask & 0b1000) !== 0,
      })
    }
    if (Math.round(elapsedSeconds * 60) % 17 === 0) {
      const featureIndex = Math.round(elapsedSeconds * 997) % OBJECT_COUNT
      const feature = mapData.nodes.features[featureIndex % mapData.nodes.features.length]
      controller.setSelectedFeatures(feature ? [feature.id] : [])
    }

    const renderStarted = performance.now()
    const stats = controller.layer.renderNow()
    renderSamples.push(performance.now() - renderStarted)
    const visible =
      stats.visible.nodes + stats.visible.pipes + stats.visible.catchments + stats.visible.ponding
    visibleCounts.push(visible)
    featureOperations += visible
    cachePeakBytes = Math.max(cachePeakBytes, cache.sizeBytes)

    await nextFrame()
    rafSamples.push(performance.now() - rafStarted)
  }
  const actualExecutionSeconds = (performance.now() - workloadStarted) / 1_000
  observer?.disconnect()

  const canvas = controller.layer.getCanvasElement()
  const canvasContext = canvas?.getContext('2d') ?? null
  const lastStats = controller.layer.getRenderStats()
  const averageVisible =
    visibleCounts.reduce((total, value) => total + value, 0) / Math.max(1, visibleCounts.length)
  const apiSamples = [...mapApiSamples, ...frameApiSamples]
  const result = {
    schema: 'hydraulic-frontend-performance-browser-result-v1',
    actual_execution_seconds: actualExecutionSeconds,
    requested_duration_seconds: durationSeconds,
    presented_fps: rafSamples.length / actualExecutionSeconds,
    request_animation_frame_p95_ms: percentile(rafSamples, 0.95),
    request_animation_frame_max_ms: Math.max(...rafSamples),
    render_p95_ms: percentile(renderSamples, 0.95),
    long_task_count_above_200ms: longTasks.filter((duration) => duration > 200).length,
    long_task_max_ms: longTasks.length > 0 ? Math.max(...longTasks) : 0,
    map_api_p95_ms: percentile(mapApiSamples, 0.95),
    frame_api_p95_ms: percentile(frameApiSamples, 0.95),
    api_p95_ms: percentile(apiSamples, 0.95),
    feature_operations_per_second: featureOperations / actualExecutionSeconds,
    feature_operations: featureOperations,
    average_visible_features: averageVisible,
    visible_fraction: averageVisible / OBJECT_COUNT,
    render_sample_count: renderSamples.length,
    raf_sample_count: rafSamples.length,
    cache_peak_bytes: cachePeakBytes,
    cache_final_bytes: cache.sizeBytes,
    cache_frame_count: cache.size,
    static_geometry_bytes: cache.staticGeometryBytes,
    canvas: {
      present: Boolean(canvas),
      context_present: Boolean(canvasContext),
      width: canvas?.width ?? 0,
      height: canvas?.height ?? 0,
    },
    culling: {
      visible: lastStats.visible,
      drawn: lastStats.drawn,
      indexed: lastStats.indexed,
      decimated: lastStats.decimated,
    },
    lod: {
      zoom: lastStats.zoom,
      show_nodes: lastStats.zoom >= 14,
      show_pipes: true,
      show_catchments: true,
      show_ponding: true,
    },
    production_modules: [
      '@/features/simulation/map/layerController',
      '@/features/simulation/map/frameCache',
      '@/features/simulation/map/viewport',
    ],
  }
  controller.dispose()
  map.remove()
  return result
}

declare global {
  interface Window {
    __HYDRAULIC_TWIN_PERF__?: Promise<Record<string, unknown>>
  }
}

const params = new URLSearchParams(window.location.search)
const durationSeconds = Number(params.get('durationSeconds') ?? '60')
const maxCacheBytes = Number(params.get('maxCacheBytes') ?? String(256 * 1024 * 1024))
window.__HYDRAULIC_TWIN_PERF__ = runHarness(durationSeconds, { maxCacheBytes })
