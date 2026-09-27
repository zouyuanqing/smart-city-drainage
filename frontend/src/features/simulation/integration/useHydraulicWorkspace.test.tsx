import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useHydraulicWorkspace } from './useHydraulicWorkspace'

const NETWORK_ID = '11111111-1111-4111-8111-111111111111'
const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const MANIFEST_ID = '33333333-3333-4333-8333-333333333333'
const SIMULATION_ID = '55555555-5555-4555-8555-555555555555'
const ATTEMPT_ID = '77777777-7777-4777-8777-777777777777'
const HASH = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'

const networkMock = vi.hoisted(() => ({
  list: vi.fn(async () => ({
    items: [
      {
        id: '11111111-1111-4111-8111-111111111111',
        network_revision_id: '22222222-2222-4222-8222-222222222222',
        name: 'Fixture network',
      },
    ],
    nextCursor: null,
  })),
}))
vi.mock('./networkApi', () => ({ listHydraulicNetworks: networkMock.list }))

function frame(index: number) {
  return {
    schema_version: 'hydraulic-frame-v1' as const,
    simulation_id: SIMULATION_ID,
    network_revision_id: REVISION_ID,
    target_manifest_id: MANIFEST_ID,
    target_manifest_sha256: HASH,
    published_attempt_id: ATTEMPT_ID,
    published_attempt_no: 1,
    solver_id: 'py_mw',
    frame_index: index,
    frame_time: new Date(Date.parse('2026-09-24T00:00:00Z') + index * 300_000).toISOString(),
    nodes: { feature_index: [0], depth_m: [index / 10] },
    pipes: { feature_index: [0], fill_ratio: [0.5] },
    quality: { status: 'verified' as const },
    payload_sha256: HASH,
  }
}

function manifest() {
  return {
    schema_version: 1 as const,
    simulation_id: SIMULATION_ID,
    network_revision_id: REVISION_ID,
    target_manifest_id: MANIFEST_ID,
    target_manifest_sha256: HASH,
    published_attempt_id: ATTEMPT_ID,
    published_attempt_no: 1,
    frame_interval_seconds: 300,
    frame_count: 2,
    frames: [0, 1].map((index) => ({
      frame_index: index,
      frame_time: frame(index).frame_time,
      etag: `W/"frame-${index}"`,
      checksum: HASH,
      bytes: 100,
    })),
  }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('useHydraulicWorkspace frame scheduling', () => {
  it('merges every map page before exposing a complete map', async () => {
    const feature = (index: number) => ({
      type: 'Feature' as const,
      id: `aaaaaaaa-aaaa-4aaa-8aaa-${String(index).padStart(12, '0')}`,
      geometry: { type: 'Point' as const, coordinates: [121.4, 31.2] },
      properties: { feature_index: index },
    })
    const page = (start: number, count: number, nextCursor: string | null) => ({
      schema_version: 'network-map-v2' as const,
      network_id: NETWORK_ID,
      network_revision_id: REVISION_ID,
      target_manifest_id: MANIFEST_ID,
      target_manifest_sha256: HASH,
      crs: 'EPSG:4326' as const,
      bbox: [121.3, 31.15, 121.6, 31.32] as [number, number, number, number],
      zoom: 13,
      complete: nextCursor === null,
      simplified: true,
      truncated_layers: nextCursor ? ['nodes'] : [],
      nextCursor,
      source_counts: { nodes: 2500, pipes: 0, catchments: 0 },
      returned_counts: { nodes: count, pipes: 0, catchments: 0 },
      nodes: {
        type: 'FeatureCollection' as const,
        features: Array.from({ length: count }, (_, offset) => feature(start + offset)),
      },
      pipes: { type: 'FeatureCollection' as const, features: [] },
      catchments: { type: 'FeatureCollection' as const, features: [] },
      generated_at: '2026-09-24T00:00:00Z',
    })
    const getMapData = vi.fn(async (_networkId: string, query: Record<string, unknown>) =>
      query.cursor ? page(2000, 500, null) : page(0, 2000, 'cursor-2')
    )
    const mapClient = { getMapData } as never
    const view = renderHook(() =>
      useHydraulicWorkspace({
        client: mapClient,
        autoLoadMap: true,
      })
    )
    await waitFor(() => expect(view.result.current.mapData?.nodes.features).toHaveLength(2500))
    expect(view.result.current.mapData?.complete).toBe(true)
    expect(getMapData).toHaveBeenCalled()
    expect(getMapData.mock.calls.some((call) => call[1]?.cursor === 'cursor-2')).toBe(true)
  })

  it('keeps prefetch out of the visible frame and deduplicates cached requests', async () => {
    const frameMock = vi.fn(async (_simulationId: string, index: number) => frame(index))
    const client = {
      frames: vi.fn(async () => manifest()),
      frame: frameMock,
      getMapData: vi.fn(),
    }
    const view = renderHook(() =>
      useHydraulicWorkspace({
        client: client as never,
        autoLoadMap: false,
      })
    )
    await waitFor(() => expect(view.result.current.selectedNetworkId).toBe(NETWORK_ID))

    act(() => view.result.current.selectSimulation(SIMULATION_ID))
    await act(async () => {
      await view.result.current.loadFrame(0)
    })
    expect(
      view.result.current.currentFrame && 'frameIndex' in view.result.current.currentFrame
        ? view.result.current.currentFrame.frameIndex
        : view.result.current.currentFrame?.frame_index
    ).toBe(0)

    await act(async () => {
      await Promise.all([
        view.result.current.prefetchFrame(1),
        view.result.current.prefetchFrame(0),
      ])
    })
    expect(
      view.result.current.currentFrame && 'frameIndex' in view.result.current.currentFrame
        ? view.result.current.currentFrame.frameIndex
        : view.result.current.currentFrame?.frame_index
    ).toBe(0)
    expect(frameMock).toHaveBeenCalledTimes(2)

    await act(async () => {
      await view.result.current.prefetchFrame(1)
    })
    expect(frameMock).toHaveBeenCalledTimes(2)
  })
})
