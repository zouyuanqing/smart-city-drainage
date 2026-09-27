import { describe, expect, it } from 'vitest'
import type {
  CatchmentMapFeature,
  NetworkMapData,
  NodeMapFeature,
  PipeMapFeature,
  SimulationFrame,
} from '../map'

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

export function makeNode(index: number, longitude = 121.4, latitude = 31.2): NodeMapFeature {
  return {
    type: 'Feature',
    id: featureId(1, index),
    feature_index: index,
    geometry: { type: 'Point', coordinates: [longitude, latitude] },
    properties: {
      feature_index: index,
      stable_key: `N-${index}`,
      critical: index % 97 === 0,
    },
  }
}

export function makePipe(index: number, longitude = 121.4, latitude = 31.2): PipeMapFeature {
  return {
    type: 'Feature',
    id: featureId(2, index),
    feature_index: index,
    geometry: {
      type: 'LineString',
      coordinates: [
        [longitude, latitude],
        [longitude + 0.001, latitude + 0.001],
      ],
    },
    properties: {
      feature_index: index,
      stable_key: `P-${index}`,
      major: index % 10 === 0,
    },
  }
}

export function makeCatchment(index: number): CatchmentMapFeature {
  const west = 121.3 + (index % 10) * 0.01
  const south = 31.1 + Math.floor(index / 10) * 0.01
  return {
    type: 'Feature',
    id: featureId(3, index),
    feature_index: index,
    geometry: {
      type: 'Polygon',
      coordinates: [
        [
          [west, south],
          [west + 0.008, south],
          [west + 0.008, south + 0.008],
          [west, south + 0.008],
          [west, south],
        ],
      ],
    },
    properties: { feature_index: index, stable_key: `C-${index}` },
  }
}

export function makeMapData(nodeCount = 2, pipeCount = 2, catchmentCount = 1): NetworkMapData {
  return {
    schema_version: 'network-map-v2',
    network_id: NETWORK_ID,
    network_revision_id: REVISION_ID,
    target_manifest_id: MANIFEST_ID,
    target_manifest_sha256: HASH,
    crs: 'EPSG:4326',
    bbox: [121.29, 31.09, 121.42, 31.32],
    zoom: 13,
    complete: true,
    simplified: false,
    truncated_layers: [],
    source_counts: { nodes: nodeCount, pipes: pipeCount, catchments: catchmentCount },
    returned_counts: { nodes: nodeCount, pipes: pipeCount, catchments: catchmentCount },
    nodes: {
      type: 'FeatureCollection',
      features: Array.from({ length: nodeCount }, (_, index) => makeNode(index)),
    },
    pipes: {
      type: 'FeatureCollection',
      features: Array.from({ length: pipeCount }, (_, index) => makePipe(index)),
    },
    catchments: {
      type: 'FeatureCollection',
      features: Array.from({ length: catchmentCount }, (_, index) => makeCatchment(index)),
    },
    generated_at: '2026-09-24T10:00:00Z',
  }
}

export function makeFrame(frameIndex = 0, count = 2): SimulationFrame {
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
      depth_m: Array.from({ length: count }, (_, index) => (index === 0 ? 0.12 : null)),
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

import {
  BBox,
  FrameCache,
  FrameLookup,
  FrameRequestScheduler,
  ViewportIndex,
  bboxIntersects,
  colorForValue,
  decimateFeatures,
  encodeFrame,
  frameCacheKey,
  getColorBand,
  getFeatureStyle,
  getLodProfile,
  normalizeBBox,
  normalizeMapData,
  parseBBox,
  roundSixDecimals,
} from '../map'

describe('map core: bbox, culling and deterministic LOD', () => {
  it('parses finite bboxes and rejects invalid ranges', () => {
    expect(parseBBox('121.3,31.1,121.5,31.3')).toEqual({
      west: 121.3,
      south: 31.1,
      east: 121.5,
      north: 31.3,
    })
    expect(() => parseBBox('121.5,31.1,121.3,31.3')).toThrow()
    expect(() => normalizeBBox({ west: 0, south: 0, east: Number.NaN, north: 1 })).toThrow()
  })

  it('culls a 10k feature fixture through the viewport index without reindexing feature order', () => {
    const features = Array.from({ length: 10_000 }, (_, index) =>
      makeNode(index, 121 + (index % 100) * 0.01, 31 + Math.floor(index / 100) * 0.01)
    )
    const normalized = normalizeMapData({
      ...makeMapData(),
      source_counts: { ...makeMapData().source_counts, nodes: 10_000 },
      returned_counts: { ...makeMapData().returned_counts, nodes: 10_000 },
      nodes: { type: 'FeatureCollection', features },
    })
    const index = new ViewportIndex(normalized.nodes, (feature) => feature.bounds, 0.05)
    const bbox: BBox = { west: 121.0, south: 31.0, east: 121.2, north: 31.2 }
    const visible = index.query(bbox)
    expect(index.size).toBe(10_000)
    expect(visible.length).toBeGreaterThan(0)
    expect(visible.every((feature) => bboxIntersects(bbox, feature.bounds))).toBe(true)
    expect(visible.map((feature) => feature.feature_index)).toEqual(
      [...visible.map((feature) => feature.feature_index)].sort((a, b) => a - b)
    )
  })

  it('de-duplicates features that span several index cells across repeated queries', () => {
    // A cell size of 0.05 with features 0.004 apart puts several nodes in one
    // cell and a query wide enough to cover a whole column reaches each of them
    // from more than one cell, which is exactly the repeated-candidate case the
    // stamped de-duplication marker in ViewportIndex.query has to collapse.
    const features = Array.from({ length: 200 }, (_, index) =>
      makeNode(index, 121 + (index % 20) * 0.004, 31 + Math.floor(index / 20) * 0.004)
    )
    const normalized = normalizeMapData({
      ...makeMapData(),
      source_counts: { ...makeMapData().source_counts, nodes: 200 },
      returned_counts: { ...makeMapData().returned_counts, nodes: 200 },
      nodes: { type: 'FeatureCollection', features },
    })
    const index = new ViewportIndex(normalized.nodes, (feature) => feature.bounds, 0.05)
    const bbox: BBox = { west: 121.0, south: 31.0, east: 121.1, north: 31.05 }
    const first = index.query(bbox).map((feature) => feature.feature_index)
    expect(first.length).toBeGreaterThan(0)
    expect(new Set(first).size).toBe(first.length)
    // The marker is stamped per query, so an interleaved different query must not
    // suppress candidates of the next one.
    index.query({ west: 121.05, south: 31.01, east: 121.09, north: 31.02 })
    const second = index.query(bbox).map((feature) => feature.feature_index)
    expect(second).toEqual(first)
    const narrower = index
      .query({ west: 121.0, south: 31.0, east: 121.01, north: 31.05 })
      .map((feature) => feature.feature_index)
    expect(narrower.every((value) => first.includes(value))).toBe(true)
    expect(narrower.length).toBeLessThan(first.length)
  })

  it('keeps critical features and stable feature indexes when decimating', () => {
    const features = Array.from({ length: 100 }, (_, index) => makeNode(index))
    features[73].properties = { feature_index: 73, critical: true }
    const selected = decimateFeatures(features, {
      maxFeatures: 10,
      preserve: (feature) => feature.properties?.critical === true,
    })
    expect(selected).toHaveLength(10)
    expect(selected.some((feature) => feature.feature_index === 73)).toBe(true)
    expect(selected.every((feature) => features.includes(feature))).toBe(true)
  })

  it('uses the frozen zoom bands for nodes, labels and feature budgets', () => {
    expect(getLodProfile(10).showNodes).toBe(false)
    expect(getLodProfile(14).showNodes).toBe(true)
    expect(getLodProfile(16).showCriticalLabels).toBe(true)
    expect(getLodProfile(16).maxFeaturesPerLayer).toBeGreaterThanOrEqual(10_000)
  })
})

describe('map core: colour and frame cache', () => {
  it('maps fill ratio, velocity, depth and missing values to distinct styles', () => {
    expect(getColorBand('fill_ratio', 0.1).id).toBe('dry')
    expect(getColorBand('fill_ratio', 0.9).id).toBe('high')
    expect(colorForValue('fill_ratio', null)).toBe('#7f8c8d')
    expect(colorForValue('velocity_mps', 2)).not.toBe(colorForValue('velocity_mps', 0.1))
    expect(colorForValue('depth_m', 0.7)).toBe('#cb181d')
  })

  it('uses frame values by immutable feature index and retains typed arrays in the LRU', () => {
    const data = normalizeMapData(makeMapData())
    const frame = makeFrame(3, 2)
    const lookup = new FrameLookup(frame)
    expect(lookup.value('pipes', 0, 'fill_ratio')).toBeCloseTo(0.2)
    expect(lookup.value('pipes', 1, 'fill_ratio')).toBeCloseTo(0.9)
    expect(lookup.value('nodes', 1, 'depth_m')).toBeNull()
    const style = getFeatureStyle(data.pipes[0], 'pipes', lookup, { metric: 'fill_ratio' })
    expect(style.value).toBe(0.2)
    expect(style.dash.length).toBeGreaterThanOrEqual(0)

    const key = frameCacheKey('sim-1', 3, undefined, 'revision-1')
    const encoded = encodeFrame(key, frame)
    const cache = new FrameCache(encoded.bytes)
    cache.put(key, frame)
    expect(cache.get(key)?.pipes.fill_ratio.values[0]).toBeCloseTo(0.2)
    expect(cache.get(key)?.pipes.fill_ratio.featureIndex).toBeInstanceOf(Uint32Array)
    expect(cache.sizeBytes).toBe(encoded.bytes)
  })

  it('rounds frame values to six decimals identically to the string form it replaces', () => {
    // roundSixDecimals is an arithmetic rewrite of Number(v.toFixed(6)) on the
    // hot lookup path. This pins the equivalence it relies on.
    expect(roundSixDecimals(0)).toBe(Number((0).toFixed(6)))
    expect(roundSixDecimals(1)).toBe(1)
    expect(roundSixDecimals(0.2)).toBe(Number((0.2).toFixed(6)))
    expect(roundSixDecimals(0.20000000298023224)).toBe(Number((0.20000000298023224).toFixed(6)))
    // every Float32 value in the fill_ratio / depth / flow domains
    const typed = new Float32Array(200_000)
    for (let index = 0; index < typed.length; index += 1) {
      typed[index] = (index % 100_000) / 100_000
    }
    for (const value of typed) {
      const expected = Number(value.toFixed(6))
      if (roundSixDecimals(value) !== expected) {
        throw new Error(`roundSixDecimals(${value}) !== Number(value.toFixed(6)) = ${expected}`)
      }
    }
    // out-of-range inputs must fall back rather than lose precision
    expect(roundSixDecimals(1e12)).toBe(Number((1e12).toFixed(6)))
    expect(roundSixDecimals(-0.5)).toBe(Number((-0.5).toFixed(6)))
    expect(roundSixDecimals(Number.NaN)).toBeNaN()
  })

  it('evicts unpinned LRU entries and refuses to evict pinned capacity', () => {
    const frame = makeFrame(1, 1)
    const first = encodeFrame('first', frame)
    const cache = new FrameCache(first.bytes + 1)
    cache.put('first', frame)
    cache.put('second', frame)
    expect(cache.has('first')).toBe(false)
    expect(cache.has('second')).toBe(true)
    cache.clear()
    cache.put('pinned', frame)
    cache.pin('pinned')
    const second = encodeFrame('second', frame)
    expect(() => cache.put('second', frame)).toThrow()
    cache.unpin('pinned')
    expect(cache.delete('pinned')).toBe(true)
    expect(second.bytes).toBeGreaterThan(0)
  })

  it('limits concurrent frame requests and prioritises the current frame', async () => {
    const scheduler = new FrameRequestScheduler(2)
    const started: string[] = []
    const resolvers = new Map<string, () => void>()
    const loader = (key: string) => {
      started.push(key)
      return new Promise<string>((resolve) => resolvers.set(key, () => resolve(key)))
    }
    const first = scheduler.request('first', () => loader('first'), { priority: 'previous' })
    const second = scheduler.request('second', () => loader('second'), { priority: 'next' })
    const current = scheduler.request('current', () => loader('current'), { priority: 'current' })
    expect(started).toEqual(['first', 'second'])
    resolvers.get('first')?.()
    resolvers.get('second')?.()
    await Promise.resolve()
    expect(started).toEqual(['first', 'second', 'current'])
    resolvers.get('current')?.()
    await expect(Promise.all([first, second, current])).resolves.toEqual([
      'first',
      'second',
      'current',
    ])
    scheduler.dispose()
  })
})
