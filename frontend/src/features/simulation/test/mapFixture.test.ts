import { describe, expect, it } from 'vitest'
import type { NetworkMapData } from '../map'
import { ViewportIndex, normalizeMapData } from '../map'

function featureId(namespace: number, index: number): string {
  return `${namespace.toString(16).padStart(8, '0')}-0000-4000-8000-${index
    .toString(16)
    .padStart(12, '0')}`
}

function targetFixture(): NetworkMapData {
  return {
    schema_version: 'network-map-v2',
    network_id: '11111111-1111-4111-8111-111111111111',
    network_revision_id: '22222222-2222-4222-8222-222222222222',
    target_manifest_id: '33333333-3333-4333-8333-333333333333',
    target_manifest_sha256: 'b'.repeat(64),
    crs: 'EPSG:4326',
    bbox: [121, 31, 121.2, 31.1],
    zoom: 13,
    complete: true,
    simplified: false,
    truncated_layers: [],
    source_counts: { nodes: 5_000, pipes: 10_000, catchments: 1_000 },
    returned_counts: { nodes: 5_000, pipes: 10_000, catchments: 1_000 },
    nodes: {
      type: 'FeatureCollection',
      features: Array.from({ length: 5_000 }, (_, index) => ({
        type: 'Feature' as const,
        id: featureId(1, index),
        feature_index: index,
        geometry: {
          type: 'Point' as const,
          coordinates: [121 + (index % 100) * 0.001, 31 + Math.floor(index / 100) * 0.001] as [
            number,
            number,
          ],
        },
        properties: { feature_index: index },
      })),
    },
    pipes: {
      type: 'FeatureCollection',
      features: Array.from({ length: 10_000 }, (_, index) => ({
        type: 'Feature' as const,
        id: featureId(2, index),
        feature_index: index,
        geometry: {
          type: 'LineString' as const,
          coordinates: [
            [121 + (index % 100) * 0.001, 31 + Math.floor(index / 100) * 0.001],
            [121 + ((index % 100) + 1) * 0.001, 31 + Math.floor(index / 100) * 0.001],
          ] as [number, number][],
        },
        properties: { feature_index: index },
      })),
    },
    catchments: {
      type: 'FeatureCollection',
      features: Array.from({ length: 1_000 }, (_, index) => {
        const west = 121 + (index % 50) * 0.002
        const south = 31 + Math.floor(index / 50) * 0.002
        return {
          type: 'Feature' as const,
          id: featureId(3, index),
          feature_index: index,
          geometry: {
            type: 'Polygon' as const,
            coordinates: [
              [
                [west, south],
                [west + 0.0015, south],
                [west + 0.0015, south + 0.0015],
                [west, south + 0.0015],
                [west, south],
              ],
            ] as [number, number][][],
          },
          properties: { feature_index: index },
        }
      }),
    },
    generated_at: '2026-09-24T10:00:00Z',
  }
}

describe('target 5k/10k/1k hydraulic fixture', () => {
  it('normalizes all layers and builds a bounded viewport index without DOM features', () => {
    const normalized = normalizeMapData(targetFixture())
    expect(normalized.nodes).toHaveLength(5_000)
    expect(normalized.pipes).toHaveLength(10_000)
    expect(normalized.catchments).toHaveLength(1_000)

    const pipeIndex = new ViewportIndex(normalized.pipes, (feature) => feature.bounds, 0.01)
    const visible = pipeIndex.query({ west: 121.02, south: 31.02, east: 121.05, north: 31.05 })
    expect(pipeIndex.size).toBe(10_000)
    expect(visible.length).toBeLessThan(10_000)
    expect(visible.every((feature) => feature.bounds.east >= 121.02)).toBe(true)
  })
})
