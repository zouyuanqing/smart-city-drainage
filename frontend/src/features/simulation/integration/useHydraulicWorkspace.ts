import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { NetworkSummary } from '../model/types'
import type { SimulationFrame, SimulationFrameIndex } from '../model/types'
import { simulationApi } from '../api'
import { listHydraulicNetworks } from './networkApi'
import type {
  CachedFrame,
  HydraulicLayer,
  MapLayerVisibility,
  NetworkMapData,
  RenderStats,
} from '../map'
import { FrameCacheClient, frameCacheKey, validateFrame } from '../map'

export interface SimulationWorkspaceState {
  networks: NetworkSummary[]
  selectedNetwork: NetworkSummary | null
  selectedNetworkId: string | null
  selectNetwork: (networkId: string) => void
  networkLoading: boolean
  networkError: string | null
  mapData: NetworkMapData | null
  mapLoading: boolean
  mapError: string | null
  frameIndex: SimulationFrameIndex | null
  currentFrame: SimulationFrame | CachedFrame | null
  frameLoading: boolean
  frameError: string | null
  selectedSimulationId: string | null
  selectSimulation: (simulationId: string | null) => void
  layerVisibility: MapLayerVisibility
  setLayerVisibility: (layer: HydraulicLayer, visible: boolean) => void
  colorMetric: 'fill_ratio' | 'velocity_mps' | 'depth_m'
  setColorMetric: (metric: 'fill_ratio' | 'velocity_mps' | 'depth_m') => void
  renderStats: RenderStats | null
  setRenderStats: (stats: RenderStats) => void
  /** P0 current-frame load. Only this path commits the visible frame. */
  loadFrame: (frameIndex: number, signal?: AbortSignal) => Promise<void>
  /** P1 neighboring-frame prefetch. It never changes the visible frame. */
  prefetchFrame: (frameIndex: number, signal?: AbortSignal) => Promise<void>
  refreshMap: () => Promise<void>
}

const INITIAL_LAYERS: MapLayerVisibility = {
  nodes: true,
  pipes: true,
  catchments: true,
  ponding: true,
}

const MAP_PAGE_SIZE = 2_000
const MAX_MAP_PAGES = 100

function readNetworkId(): string | null {
  try {
    return globalThis.localStorage?.getItem('scn_hydraulic_network_id') ?? null
  } catch {
    return null
  }
}

function mergeMapPages(pages: readonly NetworkMapData[]): NetworkMapData {
  const first = pages[0]
  if (!first) throw new Error('Map response did not contain a page')
  const collections = {
    nodes: [...first.nodes.features],
    pipes: [...first.pipes.features],
    catchments: [...first.catchments.features],
    ...(first.ponding ? { ponding: [...first.ponding.features] } : {}),
  }
  const seen = {
    nodes: new Set(collections.nodes.map((feature) => feature.id)),
    pipes: new Set(collections.pipes.map((feature) => feature.id)),
    catchments: new Set(collections.catchments.map((feature) => feature.id)),
    ...(collections.ponding
      ? { ponding: new Set(collections.ponding.map((feature) => feature.id)) }
      : {}),
  }
  for (const [index, page] of pages.entries()) {
    if (
      page.network_id !== first.network_id ||
      page.network_revision_id !== first.network_revision_id ||
      page.target_manifest_id !== first.target_manifest_id ||
      page.target_manifest_sha256 !== first.target_manifest_sha256
    ) {
      throw new Error('Map pagination returned inconsistent network identity')
    }
    if (index > 0) {
      for (const layer of ['nodes', 'pipes', 'catchments', 'ponding'] as const) {
        const collection = page[layer]
        const ids = seen[layer]
        if (!collection || !ids) continue
        for (const feature of collection.features) {
          if (ids.has(feature.id)) continue
          ids.add(feature.id)
          const target = collections[layer]
          if (target) target.push(feature)
        }
      }
    }
  }
  const last = pages[pages.length - 1]
  const truncated = new Set(last.truncated_layers)
  const complete = truncated.size === 0
  const returned_counts = {
    nodes: collections.nodes.length,
    pipes: collections.pipes.length,
    catchments: collections.catchments.length,
    ...(collections.ponding ? { ponding: collections.ponding.length } : {}),
  }
  return {
    ...last,
    nodes: { ...last.nodes, features: collections.nodes },
    pipes: { ...last.pipes, features: collections.pipes },
    catchments: { ...last.catchments, features: collections.catchments },
    ...(last.ponding && collections.ponding
      ? { ponding: { ...last.ponding, features: collections.ponding } }
      : {}),
    complete,
    truncated_layers: [...truncated].sort(),
    source_counts: { ...last.source_counts },
    returned_counts,
  }
}

export function useHydraulicWorkspace(
  options: { client?: typeof simulationApi; autoLoadMap?: boolean } = {}
): SimulationWorkspaceState {
  const client = options.client ?? simulationApi
  const autoLoadMap = options.autoLoadMap ?? true
  const [networks, setNetworks] = useState<NetworkSummary[]>([])
  const [selectedNetworkId, setSelectedNetworkId] = useState<string | null>(() => readNetworkId())
  const [networkLoading, setNetworkLoading] = useState(true)
  const [networkError, setNetworkError] = useState<string | null>(null)
  const [mapData, setMapData] = useState<NetworkMapData | null>(null)
  const [mapLoading, setMapLoading] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const [frameIndex, setFrameIndex] = useState<SimulationFrameIndex | null>(null)
  const [currentFrame, setCurrentFrame] = useState<SimulationFrame | CachedFrame | null>(null)
  const [frameLoading, setFrameLoading] = useState(false)
  const [frameError, setFrameError] = useState<string | null>(null)
  const [selectedSimulationId, setSelectedSimulationId] = useState<string | null>(null)
  const [layerVisibility, setLayerVisibilityState] = useState<MapLayerVisibility>(INITIAL_LAYERS)
  const [colorMetric, setColorMetric] = useState<'fill_ratio' | 'velocity_mps' | 'depth_m'>(
    'fill_ratio'
  )
  const [renderStats, setRenderStats] = useState<RenderStats | null>(null)
  const mapAbortRef = useRef<AbortController | null>(null)
  const currentFrameAbortRef = useRef<AbortController | null>(null)
  const frameIndexRef = useRef<SimulationFrameIndex | null>(null)
  const frameIndexRequestRef = useRef<{
    simulationId: string
    promise: Promise<SimulationFrameIndex>
  } | null>(null)
  const frameClientRef = useRef<FrameCacheClient | null>(null)
  if (!frameClientRef.current) frameClientRef.current = new FrameCacheClient()
  const frameClient = frameClientRef.current

  const selectedNetwork = useMemo(
    () => networks.find((network) => network.id === selectedNetworkId) ?? null,
    [networks, selectedNetworkId]
  )

  useEffect(() => {
    let active = true
    setNetworkLoading(true)
    setNetworkError(null)
    void listHydraulicNetworks({ status: 'active', limit: 200 })
      .then((page) => {
        if (!active) return
        setNetworks(page.items)
        setSelectedNetworkId((current) => {
          const next =
            current && page.items.some((network) => network.id === current)
              ? current
              : page.items[0]?.id ?? null
          try {
            if (next) globalThis.localStorage?.setItem('scn_hydraulic_network_id', next)
          } catch {
            // Ignore unavailable storage.
          }
          return next
        })
      })
      .catch((error: unknown) => {
        if (active) setNetworkError(error instanceof Error ? error.message : String(error))
      })
      .finally(() => {
        if (active) setNetworkLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  const refreshMap = useCallback(async () => {
    if (!selectedNetwork) {
      setMapData(null)
      return
    }
    mapAbortRef.current?.abort()
    const controller = new AbortController()
    mapAbortRef.current = controller
    setMapLoading(true)
    setMapError(null)
    try {
      const pages: NetworkMapData[] = []
      let cursor: string | undefined
      let pageCount = 0
      do {
        const page = await client.getMapData(
          selectedNetwork.id,
          {
            revision_id: selectedNetwork.network_revision_id,
            zoom: 13,
            limit: MAP_PAGE_SIZE,
            simplify: true,
            layers: 'nodes,pipes,catchments,ponding',
            ...(cursor ? { cursor } : {}),
          },
          { signal: controller.signal }
        )
        if (controller.signal.aborted) return
        pages.push(page)
        cursor = page.nextCursor ?? undefined
        pageCount += 1
        if (cursor && pageCount >= MAX_MAP_PAGES) {
          throw new Error('Map pagination exceeded the browser safety budget')
        }
      } while (cursor)
      if (!controller.signal.aborted) setMapData(mergeMapPages(pages))
    } catch (error) {
      if (!controller.signal.aborted) {
        setMapError(error instanceof Error ? error.message : String(error))
      }
    } finally {
      if (!controller.signal.aborted) setMapLoading(false)
    }
  }, [client, selectedNetwork])

  useEffect(() => {
    if (!autoLoadMap) return
    void refreshMap()
    return () => mapAbortRef.current?.abort()
  }, [autoLoadMap, refreshMap])

  const resetFrames = useCallback(() => {
    currentFrameAbortRef.current?.abort()
    currentFrameAbortRef.current = null
    frameIndexRef.current = null
    frameIndexRequestRef.current = null
    frameClient.clear()
    setFrameIndex(null)
    setCurrentFrame(null)
    setFrameError(null)
    setFrameLoading(false)
  }, [frameClient])

  const selectNetwork = useCallback(
    (networkId: string) => {
      mapAbortRef.current?.abort()
      setSelectedNetworkId(networkId)
      setSelectedSimulationId(null)
      resetFrames()
      setMapData(null)
      try {
        globalThis.localStorage?.setItem('scn_hydraulic_network_id', networkId)
      } catch {
        // Ignore unavailable storage.
      }
    },
    [resetFrames]
  )

  const selectSimulation = useCallback(
    (simulationId: string | null) => {
      if (simulationId === selectedSimulationId) return
      setSelectedSimulationId(simulationId)
      resetFrames()
    },
    [resetFrames, selectedSimulationId]
  )

  const ensureFrameIndex = useCallback(
    (simulationId: string): Promise<SimulationFrameIndex> => {
      const existing = frameIndexRequestRef.current
      if (existing?.simulationId === simulationId) return existing.promise
      if (frameIndexRef.current?.simulation_id === simulationId) {
        return Promise.resolve(frameIndexRef.current)
      }
      const promise = client.frames(simulationId).then((manifest) => {
        if (selectedSimulationId !== simulationId) {
          throw new DOMException('Simulation selection changed', 'AbortError')
        }
        frameIndexRef.current = manifest
        frameIndexRequestRef.current = null
        setFrameIndex(manifest)
        return manifest
      })
      frameIndexRequestRef.current = { simulationId, promise }
      return promise
    },
    [client, selectedSimulationId]
  )

  const loadFrameInternal = useCallback(
    async (
      index: number,
      priority: 'current' | 'next' | 'previous',
      externalSignal?: AbortSignal,
      commitVisibleFrame = true
    ) => {
      if (!selectedSimulationId) return
      if (!Number.isSafeInteger(index) || index < 0) {
        throw new RangeError('Frame index must be a non-negative integer')
      }
      const controller = new AbortController()
      const forwardAbort = () => controller.abort(externalSignal?.reason)
      if (externalSignal?.aborted) controller.abort(externalSignal.reason)
      else externalSignal?.addEventListener('abort', forwardAbort, { once: true })
      if (commitVisibleFrame) {
        currentFrameAbortRef.current?.abort()
        currentFrameAbortRef.current = controller
        setFrameLoading(true)
        setFrameError(null)
      }
      try {
        const manifest = await ensureFrameIndex(selectedSimulationId)
        if (controller.signal.aborted) return
        const entry = manifest.frames[index]
        if (!entry) throw new RangeError(`Frame index ${index} is outside the published grid`)
        const key = frameCacheKey(
          selectedSimulationId,
          index,
          undefined,
          manifest.network_revision_id
        )
        const cached = await frameClient.getOrLoad(
          key,
          async (loaderSignal) => {
            const frame = await client.frame(selectedSimulationId, index, {
              signal: loaderSignal,
            })
            const validation = validateFrame(frame, entry.checksum)
            if (!validation.valid) {
              throw new Error(`Frame checksum validation failed: ${validation.errors.join('; ')}`)
            }
            return frame
          },
          { priority, signal: controller.signal }
        )
        if (commitVisibleFrame && !controller.signal.aborted) {
          setFrameIndex(manifest)
          setCurrentFrame(cached)
        }
      } finally {
        externalSignal?.removeEventListener('abort', forwardAbort)
        if (commitVisibleFrame && currentFrameAbortRef.current === controller) {
          currentFrameAbortRef.current = null
          setFrameLoading(false)
        }
      }
    },
    [client, ensureFrameIndex, frameClient, selectedSimulationId]
  )

  const loadFrame = useCallback(
    async (index: number, signal?: AbortSignal) => {
      try {
        await loadFrameInternal(index, 'current', signal, true)
      } catch (error) {
        if (!signal?.aborted) setFrameError(error instanceof Error ? error.message : String(error))
      }
    },
    [loadFrameInternal]
  )

  const prefetchFrame = useCallback(
    async (index: number, signal?: AbortSignal) => {
      const currentIndex = currentFrame
        ? 'frame_index' in currentFrame
          ? currentFrame.frame_index
          : currentFrame.frameIndex
        : 0
      await loadFrameInternal(index, index >= currentIndex ? 'next' : 'previous', signal, false)
    },
    [currentFrame, loadFrameInternal]
  )

  useEffect(() => {
    return () => {
      currentFrameAbortRef.current?.abort()
      frameClient.dispose()
    }
  }, [frameClient])

  const setLayerVisibility = useCallback((layer: HydraulicLayer, visible: boolean) => {
    setLayerVisibilityState((current) => ({ ...current, [layer]: visible }))
  }, [])

  return {
    networks,
    selectedNetwork,
    selectedNetworkId,
    selectNetwork,
    networkLoading,
    networkError,
    mapData,
    mapLoading,
    mapError,
    frameIndex,
    currentFrame,
    frameLoading,
    frameError,
    selectedSimulationId,
    selectSimulation,
    layerVisibility,
    setLayerVisibility,
    colorMetric,
    setColorMetric,
    renderStats,
    setRenderStats,
    loadFrame,
    prefetchFrame,
    refreshMap,
  }
}
