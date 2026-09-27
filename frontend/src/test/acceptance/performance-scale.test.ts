import { describe, expect, it } from 'vitest'
import {
  FrameCache,
  ViewportIndex,
  encodeFrame,
  getLodProfile,
  type BBox,
  type MapFeature,
} from '@/features/simulation/map'
import type { SimulationFrame } from '@/features/simulation/model/types'

const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const MANIFEST_ID = '33333333-3333-4333-8333-333333333333'
const ATTEMPT_ID = '77777777-7777-4777-8777-777777777777'
const SIMULATION_ID = '55555555-5555-4555-8555-555555555555'
const HASH = 'a'.repeat(64)

function feature(index: number): MapFeature {
  const longitude = 121 + (index % 100) * 0.001
  const latitude = 31 + Math.floor(index / 100) * 0.001
  return {
    type: 'Feature',
    id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, '0')}`,
    feature_index: index,
    geometry: { type: 'Point', coordinates: [longitude, latitude] },
    properties: { feature_index: index, critical: index === 9_973 },
  }
}

function bounds(item: MapFeature): BBox {
  if (item.geometry.type !== 'Point') throw new Error('scale fixture must contain points')
  const [longitude, latitude] = item.geometry.coordinates
  return { west: longitude, south: latitude, east: longitude, north: latitude }
}

function frame(index: number): SimulationFrame {
  return {
    schema_version: 'hydraulic-frame-v1',
    simulation_id: SIMULATION_ID,
    network_revision_id: REVISION_ID,
    target_manifest_id: MANIFEST_ID,
    target_manifest_sha256: HASH,
    published_attempt_id: ATTEMPT_ID,
    published_attempt_no: 1,
    solver_id: 'py_mw',
    frame_index: index,
    frame_time: new Date(Date.UTC(2026, 8, 24, 0, 0) + index * 300_000).toISOString(),
    nodes: { feature_index: [0], depth_m: [0.2] },
    pipes: { feature_index: [0], fill_ratio: [0.4] },
    quality: { status: 'verified' },
  }
}

describe('5k/10k/1k scale and bounded-cache acceptance', () => {
  it('queries a 10k-feature index by viewport and keeps the critical feature index stable', () => {
    const features = Array.from({ length: 10_000 }, (_, index) => feature(index))
    const index = new ViewportIndex(features, bounds, 0.01)
    const visible = index.query({ west: 121.01, south: 31.01, east: 121.05, north: 31.05 })

    expect(index.size).toBe(10_000)
    expect(visible.length).toBeGreaterThan(0)
    expect(visible.length).toBeLessThan(10_000)
    expect(visible.map((item) => item.properties?.feature_index)).toEqual(
      [...visible.map((item) => item.properties?.feature_index)].sort(
        (left, right) => Number(left) - Number(right)
      )
    )
    expect(visible.every((item) => bounds(item).east >= 121.01)).toBe(true)
    expect(getLodProfile(10).showNodes).toBe(false)
    expect(getLodProfile(16).maxFeaturesPerLayer).toBeGreaterThanOrEqual(10_000)
  })

  it('accounts for typed series, metadata and geometry and evicts by total bytes', () => {
    const first = encodeFrame('frame-0', frame(0))
    const second = encodeFrame('frame-1', frame(1))
    const arrayBytes =
      first.nodes.depth_m.featureIndex.byteLength +
      first.nodes.depth_m.values.byteLength +
      first.nodes.depth_m.flags.byteLength +
      first.pipes.fill_ratio.featureIndex.byteLength +
      first.pipes.fill_ratio.values.byteLength +
      first.pipes.fill_ratio.flags.byteLength
    expect(first.bytes).toBeGreaterThan(arrayBytes)

    const cache = new FrameCache(first.bytes + second.bytes - 1)
    cache.setGeometryBytes(64)
    cache.put(first.key, first)
    cache.put(second.key, second)

    expect(cache.sizeBytes).toBeLessThanOrEqual(cache.maxBytes)
    expect(cache.staticGeometryBytes).toBe(64)
    expect(cache.get(first.key)).toBeUndefined()
    expect(cache.get(second.key)?.pipes.fill_ratio.values[0]).toBeCloseTo(0.4)

    const pinned = new FrameCache(first.bytes + 1)
    pinned.put(first.key, first)
    pinned.pin(first.key)
    expect(() => pinned.put(second.key, second)).toThrow(/capacity/)
    expect(pinned.get(first.key)).toBeDefined()
    pinned.unpin(first.key)
    pinned.put(second.key, second)
    expect(pinned.get(first.key)).toBeUndefined()

    pinned.destroy()
    expect(pinned.sizeBytes).toBe(0)
    expect(pinned.size).toBe(0)
  })

  it('retains all 289 published frames inside the frozen cache budget', () => {
    const cache = new FrameCache(256 * 1024 * 1024)
    cache.setGeometryBytes(128)
    for (let index = 0; index < 289; index += 1) {
      cache.put(`frame-${index}`, encodeFrame(`frame-${index}`, frame(index)))
    }

    expect(cache.size).toBe(289)
    expect(cache.staticGeometryBytes).toBe(128)
    expect(cache.sizeBytes).toBeLessThanOrEqual(cache.maxBytes)
    expect(cache.get('frame-288')?.frameIndex).toBe(288)
  })
})
