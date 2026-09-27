import { describe, expect, it } from 'vitest'
import { FrameDecoder, decodeFrameOnMainThread } from '../map'
import type { SimulationFrame } from '../map'
import { decodeFrameLocally } from '../workers/frameDecodeProtocol'

function makeFrame(frameIndex = 0, count = 2): SimulationFrame {
  return {
    schema_version: 'hydraulic-frame-v1',
    simulation_id: '55555555-5555-4555-8555-555555555555',
    network_revision_id: '22222222-2222-4222-8222-222222222222',
    target_manifest_id: '33333333-3333-4333-8333-333333333333',
    target_manifest_sha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    published_attempt_id: '77777777-7777-4777-8777-777777777777',
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

describe('simulation map frame worker protocol', () => {
  it('decodes a frame to transferable typed arrays on the main-thread fallback', () => {
    const frame = decodeFrameLocally({
      type: 'decode',
      request_id: 'local-1',
      frame: makeFrame(2, 2),
    })
    expect(frame.frameIndex).toBe(2)
    expect(frame.pipes.fill_ratio.featureIndex).toBeInstanceOf(Uint32Array)
    expect(frame.pipes.fill_ratio.values[1]).toBeCloseTo(0.9)
    expect(frame.ponding.depth_m.flags[0]).toBe(0)
  })

  it('falls back cleanly when module workers are unavailable and disposes pending state', async () => {
    const decoder = new FrameDecoder({ useWorker: false })
    await expect(decoder.decode(makeFrame(4, 1))).resolves.toMatchObject({ frameIndex: 4 })
    expect(decoder.pendingCount).toBe(0)
    decoder.dispose()
  })

  it('keeps the direct encoder available for non-browser callers', () => {
    const encoded = decodeFrameOnMainThread(makeFrame(0, 1))
    expect(encoded.bytes).toBeGreaterThan(0)
    expect(encoded.nodes.depth_m.values[0]).toBeCloseTo(0.12)
  })
})
