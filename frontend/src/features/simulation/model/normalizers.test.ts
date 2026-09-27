import { describe, expect, it } from 'vitest'
import {
  normalizeNetworkMapData,
  normalizeSimulationComparison,
  normalizeSimulationFrame,
  normalizeSimulationFrameIndex,
  normalizeSimulationSubmitRequest,
  normalizeSimulationTargetManifest,
  normalizeSimulationTask,
  normalizeSimulationTaskPage,
} from './normalizers'
import {
  apiErrorFixture,
  networkMapFixture,
  simulationFrameFixture,
  simulationListFixture,
  simulationSubmitFixture,
  simulationTaskFixture,
} from '../test/fixtures'

const SECOND_SIMULATION_ID = '66666666-6666-4666-8666-666666666666'
const ATTEMPT_ID = '77777777-7777-4777-8777-777777777777'
const HASH = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const NETWORK_ID = '11111111-1111-4111-8111-111111111111'
const MANIFEST_ID = '33333333-3333-4333-8333-333333333333'
const RAINFALL_ID = '44444444-4444-4444-8444-444444444444'

describe('simulation response normalizers', () => {
  it('converts REST task progress aliases and frozen retry metadata', () => {
    const task = normalizeSimulationTask(simulationListFixture.items[1])
    expect(task).toMatchObject({
      id: SECOND_SIMULATION_ID,
      status: 'retry_wait',
      progress: 72,
      attempt: 2,
      next_attempt_at: '2026-09-24T10:02:00Z',
      retry_backoff_seconds: 60,
      last_retry_error_code: 'RESULT_PERSIST_FAILED',
    })
    expect(normalizeSimulationTask(simulationTaskFixture).progress).toBe(64)
  })

  it('fails closed for missing task/list metadata instead of inventing defaults', () => {
    expect(() =>
      normalizeSimulationTask({ ...simulationTaskFixture, created_at: undefined })
    ).toThrow(/created_at/)
    expect(() => normalizeSimulationTask({ ...simulationTaskFixture, progress: 150 })).toThrow(
      /progress/
    )
    expect(() => normalizeSimulationTask({ ...simulationTaskFixture, attempt: 4 })).toThrow(
      /attempt/
    )
    expect(() => normalizeSimulationTaskPage({ next_cursor: null })).toThrow(/items/)
    expect(() => normalizeSimulationTaskPage({ items: simulationTaskFixture })).toThrow(/items/)
  })

  it('reads list envelopes without dropping the opaque cursor', () => {
    const page = normalizeSimulationTaskPage({ data: simulationListFixture })
    expect(page.items.map(({ id }) => id)).toEqual([simulationTaskFixture.id, SECOND_SIMULATION_ID])
    expect(page.nextCursor).toBe('cursor-fixture-1')
  })

  it('normalizes a canonical frame and rejects duplicate feature indexes', () => {
    const frame = normalizeSimulationFrame(simulationFrameFixture)
    expect(frame).toMatchObject({
      frame_index: 12,
      frame_time: '2026-09-24T01:00:00Z',
      published_attempt_no: 1,
    })
    expect(frame.nodes).toEqual({
      feature_index: [0, 1],
      depth_m: [0, 0.12],
      surcharged: [false, false],
    })
    expect(() =>
      normalizeSimulationFrame({
        ...simulationFrameFixture,
        nodes: { ...simulationFrameFixture.nodes, feature_index: [1, 1] },
      })
    ).toThrow(/strictly ascending/)
  })

  it('validates dense frame indexes, time grids, checksums, and bytes', () => {
    const index = {
      schema_version: 1,
      simulation_id: simulationTaskFixture.id,
      network_revision_id: REVISION_ID,
      target_manifest_id: MANIFEST_ID,
      target_manifest_sha256: HASH,
      published_attempt_id: ATTEMPT_ID,
      published_attempt_no: 1,
      frame_interval_seconds: 300,
      frame_count: 2,
      frames: [
        {
          frame_index: 0,
          frame_time: '2026-09-24T00:00:00Z',
          etag: 'W/"0"',
          checksum: HASH,
          bytes: 10,
        },
        {
          frame_index: 1,
          frame_time: '2026-09-24T00:05:00Z',
          etag: 'W/"1"',
          checksum: HASH,
          bytes: 12,
        },
      ],
    }
    expect(normalizeSimulationFrameIndex(index)).toMatchObject({ frame_count: 2 })
    expect(() =>
      normalizeSimulationFrameIndex({
        ...index,
        frames: [index.frames[0], { ...index.frames[1], frame_index: 2 }],
      })
    ).toThrow(/dense/)
    expect(() =>
      normalizeSimulationFrameIndex({
        ...index,
        frames: [index.frames[0], { ...index.frames[1], checksum: 'bad' }],
      })
    ).toThrow(/checksum/)
  })

  it('validates map completeness, counts, tuple bbox, and target identity', () => {
    expect(normalizeNetworkMapData(networkMapFixture)).toMatchObject({
      network_id: NETWORK_ID,
      complete: true,
      returned_counts: { nodes: 2 },
    })
    expect(() => normalizeNetworkMapData({ ...networkMapFixture, complete: false })).toThrow(
      /truncated_layers/
    )
    expect(() =>
      normalizeNetworkMapData({ ...networkMapFixture, returned_counts: { nodes: 1 } })
    ).toThrow(/returned_counts/)
  })

  it('normalizes an immutable target manifest and rejects non-dense indexes', () => {
    const manifest = {
      schema_version: 1,
      network_id: NETWORK_ID,
      network_revision_id: REVISION_ID,
      target_manifest_id: MANIFEST_ID,
      target_manifest_sha256: HASH,
      target_types: ['nodes', 'pipes', 'catchments', 'pond_cells'],
      counts: { nodes: 2, pipes: 0, catchments: 0, pond_cells: 0 },
      feature_index: { nodes: [0, 1], pipes: [], catchments: [], pond_cells: [] },
    }
    expect(normalizeSimulationTargetManifest(manifest)).toMatchObject({
      target_manifest_id: MANIFEST_ID,
    })
    expect(() =>
      normalizeSimulationTargetManifest({
        ...manifest,
        feature_index: { ...manifest.feature_index, nodes: [0, 2] },
      })
    ).toThrow(/dense/)
  })

  it('fails closed for unknown status and malformed submit cross-field constraints', () => {
    expect(() => normalizeSimulationTask({ ...simulationTaskFixture, status: 'retrying' })).toThrow(
      /unsupported status/
    )
    expect(() =>
      normalizeSimulationSubmitRequest({ ...simulationSubmitFixture, start_at: 'not-a-time' })
    ).toThrow(/invalid start_at/)
    expect(() =>
      normalizeSimulationSubmitRequest({
        ...simulationSubmitFixture,
        duration_seconds: Number.NaN,
      })
    ).toThrow(/invalid duration_seconds/)
    expect(() =>
      normalizeSimulationSubmitRequest({
        ...simulationSubmitFixture,
        solver_selection: 'explicit',
        requested_solver_id: null,
      })
    ).toThrow(/requested_solver_id/)
    expect(() =>
      normalizeSimulationSubmitRequest({
        ...simulationSubmitFixture,
        duration_seconds: 301,
        frame_interval_seconds: 300,
      })
    ).toThrow(/align/)
  })

  it('requires exact comparison frame grids and published attempt identity', () => {
    const comparison = {
      schema_version: 'simulation-comparison-v2',
      compatible: true,
      network_revision_id: REVISION_ID,
      target_manifest_id: MANIFEST_ID,
      target_manifest_sha256: HASH,
      frame_grid: {
        start_at: '2026-09-24T00:00:00Z',
        end_at: '2026-09-24T00:05:00Z',
        interval_seconds: 300,
        frame_count: 2,
      },
      baseline: {
        simulation_id: simulationTaskFixture.id,
        published_attempt_id: ATTEMPT_ID,
        published_attempt_no: 1,
        rainfall_scenario_id: RAINFALL_ID,
        solver: 'py_mw',
        parameters_hash: `sha256:${HASH}`,
      },
      candidate: {
        simulation_id: SECOND_SIMULATION_ID,
        published_attempt_id: '88888888-8888-4888-8888-888888888888',
        published_attempt_no: 1,
        rainfall_scenario_id: RAINFALL_ID,
        solver: 'py_dw',
        parameters_hash: `sha256:${HASH}`,
      },
      metrics: [],
    }
    expect(normalizeSimulationComparison(comparison)).toMatchObject({ compatible: true })
    expect(() =>
      normalizeSimulationComparison({
        ...comparison,
        baseline: { ...comparison.baseline, published_attempt_id: undefined },
      })
    ).toThrow(/published_attempt_id/)
  })

  it('keeps the API error envelope as structured data', () => {
    expect(apiErrorFixture.code).toBe('UNSUPPORTED_FEATURE')
    expect(apiErrorFixture.request_id).toBe('99999999-9999-4999-8999-999999999999')
    expect(apiErrorFixture.errors[0].details).toMatchObject({ suggested_solver: 'swmm_kw' })
  })
})
