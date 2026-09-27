import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import L from 'leaflet'
import type { NetworkMapData, SimulationFrame } from '../map'
import { HydraulicCanvasLayer, SimulationMapController, normalizeMapData } from '../map'

const NETWORK_ID = '11111111-1111-4111-8111-111111111111'
const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const MANIFEST_ID = '33333333-3333-4333-8333-333333333333'
const HASH = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const SIMULATION_ID = '55555555-5555-4555-8555-555555555555'
const ATTEMPT_ID = '77777777-7777-4777-8777-777777777777'

function featureId(namespace: number, index: number): string {
  return `${namespace.toString(16).padStart(8, '0')}-0000-4000-8000-${index
    .toString(16)
    .padStart(12, '0')}`
}

function makeMapData(nodeCount = 2, pipeCount = 2, catchmentCount = 1): NetworkMapData {
  return {
    schema_version: 'network-map-v2',
    network_id: NETWORK_ID,
    network_revision_id: REVISION_ID,
    target_manifest_id: MANIFEST_ID,
    target_manifest_sha256: HASH,
    crs: 'EPSG:4326',
    bbox: [121.3, 31.1, 121.5, 31.3],
    zoom: 15,
    complete: true,
    simplified: false,
    truncated_layers: [],
    source_counts: { nodes: nodeCount, pipes: pipeCount, catchments: catchmentCount },
    returned_counts: { nodes: nodeCount, pipes: pipeCount, catchments: catchmentCount },
    nodes: {
      type: 'FeatureCollection',
      features: Array.from({ length: nodeCount }, (_, index) => ({
        type: 'Feature' as const,
        id: featureId(1, index),
        feature_index: index,
        geometry: {
          type: 'Point' as const,
          coordinates: [121.4, 31.2] as [number, number],
        },
        properties: { feature_index: index, critical: index % 97 === 0 },
      })),
    },
    pipes: {
      type: 'FeatureCollection',
      features: Array.from({ length: pipeCount }, (_, index) => ({
        type: 'Feature' as const,
        id: featureId(2, index),
        feature_index: index,
        geometry: {
          type: 'LineString' as const,
          coordinates: [
            [121.4, 31.2],
            [121.401, 31.201],
          ] as [number, number][],
        },
        properties: { feature_index: index },
      })),
    },
    catchments: {
      type: 'FeatureCollection',
      features: Array.from({ length: catchmentCount }, (_, index) => ({
        type: 'Feature' as const,
        id: featureId(3, index),
        feature_index: index,
        geometry: {
          type: 'Polygon' as const,
          coordinates: [
            [
              [121.39, 31.19],
              [121.41, 31.19],
              [121.41, 31.21],
              [121.39, 31.21],
              [121.39, 31.19],
            ],
          ] as [number, number][][],
        },
        properties: { feature_index: index },
      })),
    },
    generated_at: '2026-09-24T10:00:00Z',
  }
}

function makeFrame(frameIndex = 0, count = 2): SimulationFrame {
  return {
    schema_version: 'hydraulic-frame-v1',
    simulation_id: SIMULATION_ID,
    network_revision_id: REVISION_ID,
    target_manifest_id: MANIFEST_ID,
    target_manifest_sha256: HASH,
    published_attempt_id: ATTEMPT_ID,
    published_attempt_no: 1,
    solver_id: 'py_mw',
    frame_index: frameIndex,
    frame_time: `2026-09-24T00:${String(frameIndex).padStart(2, '0')}:00Z`,
    nodes: {
      feature_index: Array.from({ length: count }, (_, index) => index),
      depth_m: [0.12, ...Array.from({ length: Math.max(0, count - 1) }, () => null)],
      surcharged: Array.from({ length: count }, () => false),
    },
    pipes: {
      feature_index: Array.from({ length: count }, (_, index) => index),
      fill_ratio: Array.from({ length: count }, (_, index) => (index === 0 ? 0.2 : 0.9)),
      flow_m3s: Array.from({ length: count }, (_, index) => (index === 0 ? 0.4 : 1.8)),
      surcharged: Array.from({ length: count }, () => false),
    },
    ponding: { feature_index: [0], depth_m: [0.04] },
    quality: { status: 'verified' },
  }
}

interface FakeMap {
  getPanes: () => { overlayPane: HTMLElement }
  getContainer: () => HTMLElement
  getSize: () => { x: number; y: number }
  getBounds: () => {
    getWest: () => number
    getSouth: () => number
    getEast: () => number
    getNorth: () => number
  }
  getZoom: () => number
  latLngToContainerPoint: (latlng: L.LatLngExpression) => L.Point
  containerPointToLatLng: (point: L.PointExpression) => L.LatLng
  on: (event: string, handler: L.LeafletEventHandlerFn) => void
  off: (event: string, handler: L.LeafletEventHandlerFn) => void
  removeLayer: (layer: L.Layer) => void
}

function makeFakeMap(): FakeMap & {
  pane: HTMLElement
  container: HTMLElement
  listeners: Map<string, Set<L.LeafletEventHandlerFn>>
} {
  const pane = document.createElement('div')
  const container = document.createElement('div')
  const listeners = new Map<string, Set<L.LeafletEventHandlerFn>>()
  const west = 121.3
  const south = 31.1
  const east = 121.5
  const north = 31.3
  const map: FakeMap & {
    pane: HTMLElement
    container: HTMLElement
    listeners: Map<string, Set<L.LeafletEventHandlerFn>>
  } = {
    pane,
    container,
    listeners,
    getPanes: () => ({ overlayPane: pane }),
    getContainer: () => container,
    getSize: () => ({ x: 800, y: 600 }),
    getBounds: () => ({
      getWest: () => west,
      getSouth: () => south,
      getEast: () => east,
      getNorth: () => north,
    }),
    getZoom: () => 15,
    latLngToContainerPoint: (latlng) => {
      const value = L.latLng(latlng)
      return L.point(
        ((value.lng - west) / (east - west)) * 800,
        (1 - (value.lat - south) / (north - south)) * 600
      )
    },
    containerPointToLatLng: (point) => {
      const value = L.point(point)
      return L.latLng(
        north - (value.y / 600) * (north - south),
        west + (value.x / 800) * (east - west)
      )
    },
    on: (event, handler) => {
      const set = listeners.get(event) ?? new Set<L.LeafletEventHandlerFn>()
      set.add(handler)
      listeners.set(event, set)
    },
    off: (event, handler) => listeners.get(event)?.delete(handler),
    removeLayer: (layer) => layer.onRemove(map as unknown as L.Map),
  }
  return map
}

const contextStub = {
  setTransform: vi.fn(),
  clearRect: vi.fn(),
  save: vi.fn(),
  restore: vi.fn(),
  beginPath: vi.fn(),
  closePath: vi.fn(),
  moveTo: vi.fn(),
  lineTo: vi.fn(),
  arc: vi.fn(),
  fill: vi.fn(),
  stroke: vi.fn(),
  fillText: vi.fn(),
  strokeText: vi.fn(),
  fillStyle: '',
  strokeStyle: '',
  lineWidth: 1,
  lineCap: 'round',
  lineJoin: 'round',
  globalAlpha: 1,
  setLineDash: vi.fn(),
  font: '',
}

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
    () => contextStub as unknown as CanvasRenderingContext2D
  )
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('imperative hydraulic Canvas layer', () => {
  it('reuses one layer for data/frame commands and keeps the cache bounded', () => {
    const controller = new SimulationMapController({ ownFrameCache: true })
    const layer = controller.layer
    controller.setData(makeMapData(20, 100, 4))
    controller.setFrame(makeFrame(0, 20), 'frame-key')
    controller.setLayerVisibility({ nodes: false })
    expect(controller.layer).toBe(layer)
    expect(controller.layer.getLayerVisibility().nodes).toBe(false)
    expect(controller.frameCache?.has('frame-key')).toBe(true)
    controller.dispose()
    expect(controller.isDisposed).toBe(true)
    expect(layer.isDestroyed).toBe(true)
    expect(controller.frameCache?.size).toBe(0)
  })

  it('renders a bounded visible subset and removes canvas/listeners on unmount', () => {
    const fake = makeFakeMap()
    const data: NetworkMapData = makeMapData(100, 10_000, 20)
    const layer = new HydraulicCanvasLayer({ maxFeaturesPerLayer: 500 })
    layer.onAdd(fake as unknown as L.Map)
    layer.setData(data)
    const stats = layer.renderNow()
    expect(stats.indexed.pipes).toBe(10_000)
    expect(stats.visible.pipes).toBeLessThanOrEqual(500)
    expect(stats.decimated.pipes).toBeGreaterThan(0)
    expect(fake.pane.querySelectorAll('canvas')).toHaveLength(1)
    expect(fake.container.querySelector('[role="tooltip"]')).not.toBeNull()
    expect(fake.listeners.get('moveend')?.size).toBe(1)

    layer.onRemove(fake as unknown as L.Map)
    expect(fake.pane.querySelectorAll('canvas')).toHaveLength(0)
    expect(fake.container.querySelector('[role="tooltip"]')).toBeNull()
    expect(fake.listeners.get('moveend')?.size).toBe(0)
  })

  it('degrades stroke detail only while a layer is dense or the LOD is not a detail view', () => {
    const dense = makeFakeMap()
    const denseLayer = new HydraulicCanvasLayer({ maxFeaturesPerLayer: 5_000 })
    denseLayer.onAdd(dense as unknown as L.Map)
    denseLayer.setData(makeMapData(10, 1_000, 1))
    contextStub.setLineDash.mockClear()
    const denseStats = denseLayer.renderNow()
    // The fake map reports zoom 15, which is a detail view, but 1 000 pipes are
    // still on screen, so the stroke budget applies to this pass.
    expect(denseStats.visible.pipes).toBeGreaterThan(512)
    expect(contextStub.lineCap).toBe('butt')
    expect(contextStub.lineJoin).toBe('miter')
    expect(contextStub.setLineDash).toHaveBeenLastCalledWith([])
    denseLayer.onRemove(dense as unknown as L.Map)

    const sparse = makeFakeMap()
    const sparseLayer = new HydraulicCanvasLayer()
    sparseLayer.onAdd(sparse as unknown as L.Map)
    sparseLayer.setData(makeMapData(2, 3, 1))
    sparseLayer.renderNow()
    // A sparse layer keeps the resolved stroke detail; a solid colour band still
    // legitimately asks the context for an empty dash, so the cap style is the
    // signal that the detail budget did not engage.
    expect(contextStub.lineCap).toBe('round')
    expect(contextStub.lineJoin).toBe('round')
    sparseLayer.onRemove(sparse as unknown as L.Map)
  })

  it('supports feature hit testing and a single reusable tooltip callback', () => {
    const fake = makeFakeMap()
    const clicks: string[] = []
    const hovers: string[] = []
    const layer = new HydraulicCanvasLayer({
      onFeatureClick: (event) => clicks.push(event.feature.id),
      onFeatureHover: (event) => hovers.push(event?.feature.id ?? 'none'),
    })
    layer.onAdd(fake as unknown as L.Map)
    layer.setData(makeMapData(2, 1, 1))
    layer.renderNow()
    const hit = layer.hitTest([31.2, 121.4], 8)
    expect(hit?.feature.id).toBe(featureId(1, 0))
    expect(hit?.value).toBeNull()
    const canvas = fake.pane.querySelector('canvas') as HTMLCanvasElement
    canvas.dispatchEvent(new MouseEvent('click', { clientX: 400, clientY: 300, bubbles: true }))
    canvas.dispatchEvent(new MouseEvent('mouseleave', { bubbles: true }))
    expect(clicks).toContain(featureId(1, 0))
    expect(hovers).toContain('none')
    layer.onRemove(fake as unknown as L.Map)
  })

  it('keeps the last valid frame when a malformed frame is supplied', () => {
    const errors: string[][] = []
    const layer = new HydraulicCanvasLayer({ onFrameError: (value) => errors.push([...value]) })
    const valid: SimulationFrame = makeFrame(1, 1)
    layer.setFrame(valid)
    layer.setFrame({ ...valid, frame_index: -1 })
    expect(layer.getFrame()).toMatchObject({ frameIndex: 1, frameTime: valid.frame_time })
    expect(errors[0]?.length).toBeGreaterThan(0)
  })

  it('draws a node as one opaque disc when fill and stroke share a colour', () => {
    // makeMapData marks index 0 of every layer critical, so a 3-node data set
    // yields exactly one non-critical node: for it fillColor === strokeColor and
    // the disc of radius r + lineWidth/2 must be produced by a single fill with
    // no stroke, because that is what fill(r) + stroke(r) paints when both
    // paints are fully opaque and identical.
    const fake = makeFakeMap()
    const layer = new HydraulicCanvasLayer()
    layer.onAdd(fake as unknown as L.Map)
    layer.setData(makeMapData(3, 0, 0))
    contextStub.fill.mockClear()
    contextStub.stroke.mockClear()
    contextStub.arc.mockClear()
    const stats = layer.renderNow()
    expect(stats.drawn.nodes).toBe(3)
    // One node paints fill+stroke (critical), two paint a single fill.
    expect(contextStub.stroke).toHaveBeenCalledTimes(1)
    expect(contextStub.fill).toHaveBeenCalledTimes(3)
    // The critical node keeps radius 5 and is stroked; each non-critical node is
    // a single fill of radius 3.5 + 1.5/2.
    const radii = contextStub.arc.mock.calls.map((call) => call[2])
    expect(radii).toContain(5)
    expect(radii).toEqual([5, 4.25, 4.25])
    layer.onRemove(fake as unknown as L.Map)
  })

  it('reuses one cached unit circle for node glyphs when Path2D exists', () => {
    // jsdom has no Path2D, so the production code takes its `arc` fallback.
    // Stubbing Path2D exercises the transform path the real browser takes and
    // pins the invariants that make it equivalent: one fill per non-critical
    // node, no per-node arc, and the base transform restored afterwards.
    const constructed: unknown[] = []
    class FakePath2D {
      arc(): void {}
      constructor() {
        constructed.push(this)
      }
    }
    vi.stubGlobal('Path2D', FakePath2D)
    try {
      const fake = makeFakeMap()
      const layer = new HydraulicCanvasLayer()
      layer.onAdd(fake as unknown as L.Map)
      layer.setData(makeMapData(3, 0, 0))
      contextStub.fill.mockClear()
      contextStub.arc.mockClear()
      contextStub.setTransform.mockClear()
      const stats = layer.renderNow()
      expect(stats.drawn.nodes).toBe(3)
      // Cached once for the whole process, not once per node.
      expect(constructed).toHaveLength(1)
      expect(contextStub.arc).not.toHaveBeenCalled()
      expect(contextStub.fill).toHaveBeenCalledTimes(3)
      expect(contextStub.fill.mock.calls.every((call) => call[0] === constructed[0])).toBe(true)
      // Every glyph transform is immediately followed by the base transform.
      expect(contextStub.setTransform).toHaveBeenLastCalledWith(1, 0, 0, 1, 0, 0)
      layer.onRemove(fake as unknown as L.Map)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('normalizes API FeatureCollections while preserving manifest indexes', () => {
    const source = makeMapData(1, 1, 1)
    const normalized = normalizeMapData(source)
    expect(normalized.nodes).toHaveLength(1)
    expect(normalized.nodes[0].feature_index).toBe(0)
    expect(normalized.nodes[0].bounds).toEqual({
      west: 121.4,
      south: 31.2,
      east: 121.4,
      north: 31.2,
    })
  })
})
