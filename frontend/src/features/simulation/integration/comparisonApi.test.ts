import { describe, expect, it, vi } from 'vitest'
import { loadComparisonSeries } from './comparisonApi'

const REVISION = '22222222-2222-4222-8222-222222222222'
const MANIFEST = '33333333-3333-4333-8333-333333333333'
const HASH = 'a'.repeat(64)
const BASELINE = '55555555-5555-4555-8555-555555555555'
const CANDIDATE = '66666666-6666-4666-8666-666666666666'
const ATTEMPT = '77777777-7777-4777-8777-777777777777'

function index(simulationId: string) {
  return {
    schema_version: 1 as const,
    simulation_id: simulationId,
    network_revision_id: REVISION,
    target_manifest_id: MANIFEST,
    target_manifest_sha256: HASH,
    published_attempt_id: ATTEMPT,
    published_attempt_no: 1,
    frame_interval_seconds: 300,
    frame_count: 2,
    frames: [0, 1].map((frameIndex) => ({
      frame_index: frameIndex,
      frame_time: new Date(Date.parse('2026-09-24T00:00:00Z') + frameIndex * 300_000).toISOString(),
      etag: `etag-${frameIndex}`,
      checksum: HASH,
      bytes: 10,
    })),
  }
}

function frame(simulationId: string, frameIndex: number, depth: number) {
  return {
    schema_version: 'hydraulic-frame-v1' as const,
    simulation_id: simulationId,
    network_revision_id: REVISION,
    target_manifest_id: MANIFEST,
    target_manifest_sha256: HASH,
    published_attempt_id: ATTEMPT,
    published_attempt_no: 1,
    solver_id: 'py_mw',
    frame_index: frameIndex,
    frame_time: new Date(Date.parse('2026-09-24T00:00:00Z') + frameIndex * 300_000).toISOString(),
    nodes: { feature_index: [0], depth_m: [depth] },
    quality: { status: 'verified' as const },
    payload_sha256: HASH,
  }
}

describe('loadComparisonSeries', () => {
  it('builds a real aligned time series from both published frame grids', async () => {
    const client = {
      frames: vi.fn(async (simulationId: string) => index(simulationId)),
      frame: vi.fn(async (simulationId: string, frameIndex: number) =>
        frame(
          simulationId,
          frameIndex,
          simulationId === BASELINE ? frameIndex / 10 : frameIndex / 5
        )
      ),
    }
    const result = await loadComparisonSeries(
      BASELINE,
      CANDIDATE,
      'max_depth_m',
      undefined,
      client as never
    )
    expect(result).toHaveLength(2)
    expect(result[0]).toMatchObject({ baseline: 0, candidate: 0, delta: 0 })
    expect(result[1]).toMatchObject({ baseline: 0.1, candidate: 0.2 })
    expect(result[1]?.delta).toBeCloseTo(0.1)
  })
})
