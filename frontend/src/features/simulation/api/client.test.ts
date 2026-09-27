import type { AxiosInstance, AxiosRequestConfig, AxiosResponse } from 'axios'
import { describe, expect, it, vi } from 'vitest'
import {
  networkMapFixture,
  simulationFrameFixture,
  simulationSubmitFixture,
  simulationTaskFixture,
  apiErrorFixture,
} from '../test/fixtures'
import { ApiClientError } from '../model/errors'
import { createSimulationApiClient } from './client'

const NETWORK_ID = '11111111-1111-4111-8111-111111111111'
const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const MANIFEST_ID = '33333333-3333-4333-8333-333333333333'
const HASH = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const ATTEMPT_ID = '77777777-7777-4777-8777-777777777777'
const RAINFALL_ID = '44444444-4444-4444-8444-444444444444'

function response<T>(
  data: T,
  status = 200,
  headers: Record<string, string> = {}
): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    headers,
    config: {} as AxiosRequestConfig,
  } as AxiosResponse<T>
}

function rejection(status: number, data: unknown, headers: Record<string, string> = {}) {
  return {
    isAxiosError: true,
    message: `Request failed with status code ${status}`,
    response: response(data, status, headers),
    config: { headers: {} },
  }
}

const targetManifest = {
  schema_version: 1,
  network_id: NETWORK_ID,
  network_revision_id: REVISION_ID,
  target_manifest_id: MANIFEST_ID,
  target_manifest_sha256: HASH,
  target_types: ['nodes', 'pipes', 'catchments', 'pond_cells'],
  counts: { nodes: 1, pipes: 0, catchments: 0, pond_cells: 0 },
  feature_index: { nodes: [0], pipes: [], catchments: [], pond_cells: [] },
}

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
    simulation_id: '66666666-6666-4666-8666-666666666666',
    published_attempt_id: '88888888-8888-4888-8888-888888888888',
    published_attempt_no: 1,
    rainfall_scenario_id: RAINFALL_ID,
    solver: 'py_dw',
    parameters_hash: `sha256:${HASH}`,
  },
  metrics: [
    {
      key: 'flooded_node_count',
      unit: 'count',
      baseline: 2,
      candidate: 1,
      absolute_delta: -1,
      relative_delta: -0.5,
      better_direction: 'lower',
    },
  ],
}

const result = {
  schema_version: 1,
  simulation_id: simulationTaskFixture.id,
  network_revision_id: REVISION_ID,
  target_manifest_id: MANIFEST_ID,
  target_manifest_sha256: HASH,
  published_attempt_id: ATTEMPT_ID,
  published_attempt_no: 1,
  frame_count: 2,
  completed_at: '2026-09-24T01:05:00Z',
  summary: { peak_depth_m: 0.4 },
  quality: { status: 'verified' },
  metrics: { flooded_node_count: 1 },
}

const frameIndex = {
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
      etag: 'W/"frame-0"',
      checksum: HASH,
      bytes: 128,
    },
    {
      frame_index: 1,
      frame_time: '2026-09-24T00:05:00Z',
      etag: 'W/"frame-1"',
      checksum: HASH,
      bytes: 132,
    },
  ],
}

describe('simulation Axios client', () => {
  it('injects bearer auth into list, maps envelopes, and guards idempotent submit', async () => {
    const request = vi.fn(async (config: AxiosRequestConfig) => {
      if (config.url?.startsWith('/simulations?')) {
        return response({ items: [simulationTaskFixture], next_cursor: 'next-1' })
      }
      return response(simulationTaskFixture, 202, { 'Idempotency-Replayed': 'true' })
    })
    const client = createSimulationApiClient({
      instance: { request } as unknown as AxiosInstance,
      getToken: () => 'fixture-token',
    })

    const page = await client.list({ networkId: NETWORK_ID, status: 'running' })
    expect(page.nextCursor).toBe('next-1')
    const listConfig = request.mock.calls[0][0] as AxiosRequestConfig
    expect(listConfig.url).toBe(`/simulations?status=running&network_id=${NETWORK_ID}&limit=50`)
    expect((listConfig.headers as { Authorization?: string }).Authorization).toBe(
      'Bearer fixture-token'
    )

    await expect(
      client.submit({ ...simulationSubmitFixture, network_id: 'not-a-uuid' }, 'short')
    ).rejects.toThrow(TypeError)
    expect(request).toHaveBeenCalledTimes(1)

    const command = await client.submit(simulationSubmitFixture, 'simulation-key-1')
    const submitConfig = request.mock.calls[1][0] as AxiosRequestConfig
    expect((submitConfig.headers as { 'Idempotency-Key'?: string })['Idempotency-Key']).toBe(
      'simulation-key-1'
    )
    expect(command.idempotencyReplayed).toBe(true)
    expect(command.task.id).toBe(simulationTaskFixture.id)
  })

  it('uses the canonical network target-manifest/map/result/frame/compare paths and normalizers', async () => {
    const request = vi.fn(async (config: AxiosRequestConfig) => {
      if (config.url?.includes('/target-manifest')) return response(targetManifest)
      if (config.url?.includes('/map-data')) return response(networkMapFixture)
      if (config.url?.endsWith('/result')) return response(result)
      if (config.url?.endsWith('/frames')) return response(frameIndex)
      if (config.url?.includes('/frames/')) return response(simulationFrameFixture)
      if (config.url?.includes('/compare/')) return response(comparison)
      return response(simulationTaskFixture)
    })
    const client = createSimulationApiClient({
      instance: { request } as unknown as AxiosInstance,
    })

    await expect(
      client.getTargetManifest(NETWORK_ID, { revisionId: REVISION_ID })
    ).resolves.toMatchObject({
      network_revision_id: REVISION_ID,
      target_manifest_id: MANIFEST_ID,
    })
    await expect(
      client.getMapData(NETWORK_ID, { zoom: 13, bbox: '1,2,3,4' })
    ).resolves.toMatchObject({
      complete: true,
    })
    await expect(client.getResult(simulationTaskFixture.id)).resolves.toMatchObject({
      published_attempt_no: 1,
    })
    await expect(client.getFrames(simulationTaskFixture.id)).resolves.toMatchObject({
      frame_count: 2,
    })
    await expect(client.getFrame(simulationTaskFixture.id, 12)).resolves.toMatchObject({
      frame_time: '2026-09-24T01:00:00Z',
    })
    await expect(
      client.compare(simulationTaskFixture.id, '66666666-6666-4666-8666-666666666666')
    ).resolves.toMatchObject({ compatible: true })

    const urls = request.mock.calls.map(([config]) => (config as AxiosRequestConfig).url)
    expect(urls).toEqual([
      `/networks/${NETWORK_ID}/target-manifest?revision_id=${REVISION_ID}`,
      `/networks/${NETWORK_ID}/map-data?zoom=13&bbox=1%2C2%2C3%2C4`,
      `/simulations/${simulationTaskFixture.id}/result`,
      `/simulations/${simulationTaskFixture.id}/frames`,
      `/simulations/${simulationTaskFixture.id}/frames/12`,
      `/simulations/${simulationTaskFixture.id}/compare/66666666-6666-4666-8666-666666666666`,
    ])
  })

  it('rejects partial target/map/comparison responses instead of casting them', async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(response({ ...targetManifest, target_manifest_sha256: 'bad' }))
      .mockResolvedValueOnce(response({ ...networkMapFixture, complete: false }))
      .mockResolvedValueOnce(
        response({ ...comparison, frame_grid: { ...comparison.frame_grid, interval_seconds: 60 } })
      )
    const client = createSimulationApiClient({
      instance: { request } as unknown as AxiosInstance,
    })

    await expect(client.getTargetManifest(NETWORK_ID)).rejects.toThrow(/target_manifest_sha256/)
    await expect(client.getMapData(NETWORK_ID, { zoom: 13, bbox: '1,2,3,4' })).rejects.toThrow(
      /truncated_layers/
    )
    await expect(
      client.compare(simulationTaskFixture.id, '66666666-6666-4666-8666-666666666666')
    ).rejects.toThrow(/frame grid/)
  })

  it('converts a structured API rejection instead of flattening it', async () => {
    const request = vi.fn(async () => {
      throw rejection(422, apiErrorFixture, {
        'X-Request-ID': '99999999-9999-4999-8999-999999999999',
      })
    })
    const client = createSimulationApiClient({
      instance: { request } as unknown as AxiosInstance,
    })

    await expect(client.get('99999999-9999-4999-8999-999999999999')).rejects.toMatchObject({
      status: 422,
      code: 'UNSUPPORTED_FEATURE',
      requestId: '99999999-9999-4999-8999-999999999999',
      detail: 'simulation input contains unsupported features',
    })
    await expect(client.get('99999999-9999-4999-8999-999999999999')).rejects.toBeInstanceOf(
      ApiClientError
    )
  })

  it('invokes the shared unauthorized boundary for a default client', async () => {
    const onUnauthorized = vi.fn()
    const request = vi.fn(async () => response(simulationTaskFixture, 401))
    const client = createSimulationApiClient({
      instance: { request } as unknown as AxiosInstance,
      onUnauthorized,
    })

    await expect(client.get('99999999-9999-4999-8999-999999999999')).resolves.toBeDefined()
    expect(onUnauthorized).toHaveBeenCalledWith(
      expect.objectContaining({ status: 401, code: 'AUTH_REQUIRED' })
    )
  })

  it('uses general and frame-specific retry policies without retrying business 5xx codes', async () => {
    const sleeps: number[] = []
    const request = vi
      .fn()
      .mockRejectedValueOnce(
        rejection(429, { detail: 'rate limited', code: 'RATE_LIMITED' }, { 'Retry-After': '2' })
      )
      .mockRejectedValueOnce(rejection(503, { detail: 'busy', code: 'DEPENDENCY_NOT_READY' }))
      .mockResolvedValueOnce(response(simulationTaskFixture))
    const client = createSimulationApiClient({
      instance: { request } as unknown as AxiosInstance,
      retry: {
        sleep: async (delay) => {
          sleeps.push(delay)
        },
      },
    })

    await expect(client.get(simulationTaskFixture.id)).resolves.toMatchObject({
      id: simulationTaskFixture.id,
    })
    expect(sleeps).toEqual([2_000, 60_000])

    const frameSleeps: number[] = []
    const frameRequest = vi
      .fn()
      .mockRejectedValueOnce(rejection(503, { detail: 'busy', code: 'DEPENDENCY_NOT_READY' }))
      .mockRejectedValueOnce(rejection(503, { detail: 'busy', code: 'DEPENDENCY_NOT_READY' }))
      .mockResolvedValueOnce(response(simulationFrameFixture))
    const frameClient = createSimulationApiClient({
      instance: { request: frameRequest } as unknown as AxiosInstance,
      retry: {
        sleep: async (delay) => {
          frameSleeps.push(delay)
        },
      },
    })
    await frameClient.getFrame(simulationTaskFixture.id, 12)
    expect(frameSleeps).toEqual([500, 1_000])

    const nonRetryable = vi.fn(async () => {
      throw rejection(503, { detail: 'solver missing', code: 'SOLVER_UNAVAILABLE' })
    })
    const nonRetryableClient = createSimulationApiClient({
      instance: { request: nonRetryable } as unknown as AxiosInstance,
    })
    await expect(nonRetryableClient.get(simulationTaskFixture.id)).rejects.toMatchObject({
      code: 'SOLVER_UNAVAILABLE',
    })
    expect(nonRetryable).toHaveBeenCalledTimes(1)
  })
})
