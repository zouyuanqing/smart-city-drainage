import axios, {
  AxiosHeaders,
  type AxiosInstance,
  type AxiosRequestConfig,
  type AxiosResponse,
} from 'axios'
import {
  ApiClientError,
  toApiClientError,
  withApiRetry,
  type ApiRetryOptions,
} from '../model/errors'
import {
  normalizeNetworkMapData,
  normalizeSimulationComparison,
  normalizeSimulationFrame,
  normalizeSimulationFrameIndex,
  normalizeSimulationResult,
  normalizeSimulationSubmitRequest,
  normalizeSimulationTargetManifest,
  normalizeSimulationTask,
  normalizeSimulationTaskPage,
  simulationListQueryParams,
} from '../model/normalizers'
import type {
  NetworkMapData,
  SimulationClient,
  SimulationCommandContext,
  SimulationCommandResult,
  SimulationFrame,
  SimulationFrameIndex,
  SimulationListQuery,
  SimulationResult,
  SimulationSubmitRequest,
  SimulationTargetManifest,
  SimulationTask,
  SimulationComparison,
} from '../model/types'

export interface SimulationApiClientOptions {
  /** Inject an Axios instance in tests or when the host supplies a shared client. */
  axiosInstance?: AxiosInstance
  /** Alias retained for callers that use the shorter name. */
  instance?: AxiosInstance
  baseUrl?: string
  timeoutMs?: number
  getToken?: () => string | null | undefined
  onUnauthorized?: (error: ApiClientError) => void
  retry?: ApiRetryOptions
}

export interface SimulationCommandResponse extends SimulationCommandResult {
  response: AxiosResponse<unknown>
}

export interface SimulationApiClient extends SimulationClient {
  result(simulationId: string, context?: SimulationCommandContext): Promise<SimulationResult>
  frames(simulationId: string, context?: SimulationCommandContext): Promise<SimulationFrameIndex>
  frame(
    simulationId: string,
    frameIndex: number,
    context?: SimulationCommandContext
  ): Promise<SimulationFrame>
  getResult(simulationId: string, context?: SimulationCommandContext): Promise<SimulationResult>
  getFrames(simulationId: string, context?: SimulationCommandContext): Promise<SimulationFrameIndex>
  getFrame(
    simulationId: string,
    frameIndex: number,
    context?: SimulationCommandContext
  ): Promise<SimulationFrame>
  getTargetManifest(
    networkId: string,
    query?: { revisionId?: string } | SimulationCommandContext,
    context?: SimulationCommandContext
  ): Promise<SimulationTargetManifest>
  getMapData(
    networkId: string,
    query: Record<string, string | number | boolean | undefined>,
    context?: SimulationCommandContext
  ): Promise<NetworkMapData>
  compare(
    baselineId: string,
    candidateId: string,
    query?: Record<string, string | number | boolean | undefined>,
    context?: SimulationCommandContext
  ): Promise<SimulationComparison>
}

function defaultToken(): string | null | undefined {
  try {
    return globalThis.localStorage?.getItem('scn_access_token')
  } catch {
    return undefined
  }
}

function defaultUnauthorizedHandler(error: ApiClientError): void {
  try {
    globalThis.localStorage?.removeItem('scn_access_token')
    globalThis.localStorage?.removeItem('scn_user')
    globalThis.localStorage?.removeItem('scn_user_role')
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    try {
      window.location.assign('/login')
    } catch {
      // Test environments may expose a non-navigable location object.
    }
  }
  // Keep the structured error observable even though the host is navigating.
  void error
}

function requireIdempotencyKey(value: string): string {
  if (typeof value !== 'string' || value.length < 8 || value.length > 128) {
    throw new TypeError('Idempotency-Key must contain 8..128 characters')
  }
  return value
}

function readHeader(headers: unknown, name: string): string | undefined {
  if (!headers || typeof headers !== 'object') return undefined
  const direct = (headers as { get?: (key: string) => unknown }).get?.(name)
  if (typeof direct === 'string' && direct) return direct
  const record = headers as Record<string, unknown>
  const key = Object.keys(record).find(
    (candidate) => candidate.toLowerCase() === name.toLowerCase()
  )
  return typeof key === 'string' && typeof record[key] === 'string'
    ? (record[key] as string)
    : undefined
}

function isReplayHeader(value: string | undefined): boolean {
  return value?.toLowerCase() === 'true'
}

function pathId(value: string): string {
  return encodeURIComponent(value)
}

function unwrapTask(value: unknown): unknown {
  if (typeof value !== 'object' || value === null) return value
  const record = value as Record<string, unknown>
  if (record.task && typeof record.task === 'object') return record.task
  if (record.simulation && typeof record.simulation === 'object') return record.simulation
  if (record.data && typeof record.data === 'object') return record.data
  return value
}

function responseHasReplayFlag(body: unknown, response: AxiosResponse<unknown>): boolean {
  const record =
    typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : undefined
  return (
    isReplayHeader(readHeader(response.headers, 'Idempotency-Replayed')) ||
    record?.idempotency_replayed === true ||
    record?.idempotencyReplayed === true
  )
}

function queryString(params: Record<string, string>): string {
  const query = new URLSearchParams(params).toString()
  return query ? `?${query}` : ''
}

export function createSimulationAxiosInstance(
  options: SimulationApiClientOptions = {}
): AxiosInstance {
  return axios.create({
    baseURL: options.baseUrl ?? '/api',
    timeout: options.timeoutMs ?? 30_000,
    headers: { Accept: 'application/json' },
  })
}

function withAuthAndContentType(
  config: AxiosRequestConfig,
  context: SimulationCommandContext,
  getToken: () => string | null | undefined
): AxiosRequestConfig {
  const headers = new AxiosHeaders()
  for (const [key, value] of Object.entries(config.headers ?? {})) {
    if (value !== undefined && value !== null) headers.set(key, String(value))
  }
  headers.set('Accept', 'application/json')
  if (config.data !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const token = context.token ?? getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return { ...config, headers }
}

export function createSimulationApiClient(
  options: SimulationApiClientOptions = {}
): SimulationApiClient {
  const instance =
    options.axiosInstance ?? options.instance ?? createSimulationAxiosInstance(options)
  const getToken = options.getToken ?? defaultToken
  const retryOptions = options.retry

  async function request<T>(
    config: AxiosRequestConfig,
    context: SimulationCommandContext = {},
    retry: ApiRetryOptions | false = false
  ): Promise<AxiosResponse<T>> {
    const requestConfig = withAuthAndContentType(config, context, getToken)
    const onUnauthorized = options.onUnauthorized ?? defaultUnauthorizedHandler
    try {
      const response = retry
        ? await withApiRetry(() => instance.request<T>(requestConfig), {
            ...retryOptions,
            ...retry,
            maxRetries: retry.maxRetries ?? retryOptions?.maxRetries ?? 2,
            signal: context.signal ?? retry.signal ?? retryOptions?.signal,
          })
        : await instance.request<T>(requestConfig)
      if (response.status === 401) {
        onUnauthorized(
          toApiClientError({ response, message: response.statusText || 'Unauthorized' })
        )
      }
      return response
    } catch (error) {
      if (axios.isCancel(error) || context.signal?.aborted) throw error
      const apiError = toApiClientError(error)
      if (apiError.status === 401) onUnauthorized(apiError)
      throw apiError
    }
  }

  async function list(
    query: SimulationListQuery = {},
    context: SimulationCommandContext = {}
  ): Promise<ReturnType<typeof normalizeSimulationTaskPage>> {
    const signal = query.signal ?? context.signal
    const response = await request<unknown>(
      {
        method: 'GET',
        url: `/simulations${queryString(simulationListQueryParams(query))}`,
        signal,
      },
      { ...context, signal },
      {}
    )
    return normalizeSimulationTaskPage(response.data)
  }

  async function get(
    simulationId: string,
    context: SimulationCommandContext = {}
  ): Promise<SimulationTask> {
    const response = await request<unknown>(
      { method: 'GET', url: `/simulations/${pathId(simulationId)}`, signal: context.signal },
      context,
      {}
    )
    return normalizeSimulationTask(unwrapTask(response.data))
  }

  async function submit(
    simulationRequest: SimulationSubmitRequest,
    idempotencyKey: string,
    context: SimulationCommandContext = {}
  ): Promise<SimulationCommandResult> {
    const normalizedRequest = normalizeSimulationSubmitRequest(simulationRequest)
    const normalizedIdempotencyKey = requireIdempotencyKey(idempotencyKey)
    const response = await request<unknown>(
      {
        method: 'POST',
        url: '/simulations',
        headers: { 'Idempotency-Key': normalizedIdempotencyKey },
        data: normalizedRequest,
        signal: context.signal,
      },
      context
    )
    const body = unwrapTask(response.data)
    let task: SimulationTask
    try {
      task = normalizeSimulationTask(body)
    } catch {
      const taskId =
        typeof body === 'object' && body !== null
          ? (body as { id?: unknown; simulation_id?: unknown }).id ??
            (body as { simulation_id?: unknown }).simulation_id
          : undefined
      if (typeof taskId !== 'string' || !taskId) {
        throw new TypeError('Simulation submit response is missing a task id')
      }
      const fallback = await get(taskId, context)
      return { task: fallback, idempotencyReplayed: responseHasReplayFlag(response.data, response) }
    }
    return { task, idempotencyReplayed: responseHasReplayFlag(response.data, response) }
  }

  async function cancel(
    simulationId: string,
    idempotencyKey: string,
    context: SimulationCommandContext = {}
  ): Promise<SimulationCommandResult> {
    const normalizedIdempotencyKey = requireIdempotencyKey(idempotencyKey)
    const response = await request<unknown>(
      {
        method: 'POST',
        url: `/simulations/${pathId(simulationId)}/cancel`,
        headers: { 'Idempotency-Key': normalizedIdempotencyKey },
        signal: context.signal,
      },
      context
    )
    const body = unwrapTask(response.data)
    try {
      return {
        task: normalizeSimulationTask(body),
        idempotencyReplayed: responseHasReplayFlag(response.data, response),
      }
    } catch {
      return {
        task: await get(simulationId, context),
        idempotencyReplayed: responseHasReplayFlag(response.data, response),
      }
    }
  }

  async function getResult(
    simulationId: string,
    context: SimulationCommandContext = {}
  ): Promise<SimulationResult> {
    const response = await request<unknown>(
      { method: 'GET', url: `/simulations/${pathId(simulationId)}/result`, signal: context.signal },
      context,
      {}
    )
    return normalizeSimulationResult(response.data)
  }

  async function getFrames(
    simulationId: string,
    context: SimulationCommandContext = {}
  ): Promise<SimulationFrameIndex> {
    const response = await request<unknown>(
      { method: 'GET', url: `/simulations/${pathId(simulationId)}/frames`, signal: context.signal },
      context,
      {}
    )
    return normalizeSimulationFrameIndex(response.data)
  }

  async function getFrame(
    simulationId: string,
    frameIndex: number,
    context: SimulationCommandContext = {}
  ): Promise<SimulationFrame> {
    const response = await request<unknown>(
      {
        method: 'GET',
        url: `/simulations/${pathId(simulationId)}/frames/${frameIndex}`,
        signal: context.signal,
      },
      context,
      {
        delaysMs: [500, 1_000, 2_000],
        maxRetries: 2,
      }
    )
    return normalizeSimulationFrame(response.data)
  }

  async function getTargetManifest(
    networkId: string,
    queryOrContext: { revisionId?: string } | SimulationCommandContext = {},
    context: SimulationCommandContext = {}
  ): Promise<SimulationTargetManifest> {
    const revisionId = 'revisionId' in queryOrContext ? queryOrContext.revisionId : undefined
    const effectiveContext: SimulationCommandContext =
      'revisionId' in queryOrContext ? context : (queryOrContext as SimulationCommandContext)
    const response = await request<unknown>(
      {
        method: 'GET',
        url: `/networks/${pathId(networkId)}/target-manifest${queryString(
          revisionId ? { revision_id: revisionId } : {}
        )}`,
        signal: effectiveContext.signal,
      },
      effectiveContext,
      {}
    )
    return normalizeSimulationTargetManifest(response.data)
  }

  async function getMapData(
    networkId: string,
    query: Record<string, string | number | boolean | undefined>,
    context: SimulationCommandContext = {}
  ): Promise<NetworkMapData> {
    const response = await request<unknown>(
      {
        method: 'GET',
        url: `/networks/${pathId(networkId)}/map-data${queryString(
          Object.fromEntries(
            Object.entries(query).filter(([, value]) => value !== undefined)
          ) as Record<string, string>
        )}`,
        signal: context.signal,
      },
      context,
      {}
    )
    return normalizeNetworkMapData(response.data)
  }

  async function compare(
    baselineId: string,
    candidateId: string,
    query: Record<string, string | number | boolean | undefined> = {},
    context: SimulationCommandContext = {}
  ): Promise<SimulationComparison> {
    const response = await request<unknown>(
      {
        method: 'GET',
        url: `/simulations/${pathId(baselineId)}/compare/${pathId(candidateId)}${queryString(
          Object.fromEntries(
            Object.entries(query).filter(([, value]) => value !== undefined)
          ) as Record<string, string>
        )}`,
        signal: context.signal,
      },
      context,
      {}
    )
    return normalizeSimulationComparison(response.data)
  }

  return {
    list,
    get,
    submit,
    cancel,
    result: getResult,
    frames: getFrames,
    frame: getFrame,
    getResult,
    getFrames,
    getFrame,
    getTargetManifest,
    getMapData,
    compare,
  }
}

/** Alias used by the integration layer when the client covers all hydraulic endpoints. */
export const createHydraulicApiClient = createSimulationApiClient

export const simulationAxios = createSimulationAxiosInstance()
export const simulationApi = createSimulationApiClient({ axiosInstance: simulationAxios })

export const defaultSimulationApi = simulationApi
export const defaultSimulationApiClient = simulationApi
export const simulationAPI = simulationApi
export const hydraulicApi = simulationApi

export function getSimulationApiErrorMessage(error: unknown): string {
  return error instanceof ApiClientError
    ? error.detail || error.code
    : toApiClientError(error).detail
}

export const getSimulationTaskErrorMessage = getSimulationApiErrorMessage
