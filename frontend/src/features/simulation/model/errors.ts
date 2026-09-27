import type { ApiFieldError, SimulationErrorCode } from './types'

export interface ApiErrorEnvelope {
  detail?: string
  code?: SimulationErrorCode
  request_id?: string
  errors?: ApiFieldError[]
  type?: string
}

export interface ApiClientErrorInit {
  status: number
  code: SimulationErrorCode
  detail: string
  errors?: ApiFieldError[]
  requestId?: string
  retryAfterSeconds?: number
  cause?: unknown
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined
}

function numberValue(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function headerValue(headers: unknown, name: string): string | undefined {
  if (!headers || typeof headers !== 'object') return undefined
  const direct = (headers as { get?: (key: string) => unknown }).get?.(name)
  const fromGet = stringValue(direct)
  if (fromGet) return fromGet

  const record = headers as Record<string, unknown>
  const key = Object.keys(record).find(
    (candidate) => candidate.toLowerCase() === name.toLowerCase()
  )
  return key ? stringValue(record[key]) : undefined
}

/** Normalize both the hydraulic envelope and FastAPI's legacy `loc/msg` shape. */
export function normalizeApiFieldError(value: unknown, fallbackRetryable = false): ApiFieldError {
  if (!isRecord(value)) {
    return { message: 'Request validation failed', retryable: fallbackRetryable }
  }

  const field = stringValue(value.field)
  const loc = Array.isArray(value.loc)
    ? value.loc.filter(
        (item): item is string | number => typeof item === 'string' || typeof item === 'number'
      )
    : undefined
  const message =
    stringValue(value.message) ?? stringValue(value.msg) ?? 'Request validation failed'
  const code = stringValue(value.code)
  const retryable = typeof value.retryable === 'boolean' ? value.retryable : fallbackRetryable
  const details = isRecord(value.details) ? value.details : undefined

  return {
    ...(field ? { field } : {}),
    ...(loc ? { loc } : {}),
    message,
    ...(code ? { code } : {}),
    retryable,
    ...(details ? { details } : {}),
  }
}

export function normalizeApiErrorEnvelope(
  value: unknown,
  fallbackStatus = 0
): ApiErrorEnvelope & { status: number } {
  if (!isRecord(value)) {
    return { status: fallbackStatus, detail: undefined, errors: [] }
  }
  const data = isRecord(value.data) ? value.data : value
  return {
    status: numberValue(data.status) ?? fallbackStatus,
    detail: stringValue(data.detail),
    code: stringValue(data.code) as SimulationErrorCode | undefined,
    request_id: stringValue(data.request_id),
    errors: Array.isArray(data.errors)
      ? data.errors.map((item) => normalizeApiFieldError(item))
      : [],
    ...(stringValue(data.type) ? { type: stringValue(data.type) } : {}),
  }
}

/**
 * Structured transport error used by the hydraulic client.  In particular,
 * this does not collapse the response to `new Error(detail)`, because the
 * task UI needs the code, field errors, request id, and rate-limit delay.
 */
export class ApiClientError extends Error {
  readonly status: number
  readonly code: SimulationErrorCode
  readonly detail: string
  readonly errors: ApiFieldError[]
  readonly requestId?: string
  readonly retryAfterSeconds?: number
  readonly cause?: unknown

  constructor(init: ApiClientErrorInit) {
    super(init.detail)
    this.name = 'ApiClientError'
    this.status = init.status
    this.code = init.code
    this.detail = init.detail
    this.errors = init.errors ?? []
    this.requestId = init.requestId
    this.retryAfterSeconds = init.retryAfterSeconds
    this.cause = init.cause
  }

  get isNetworkError(): boolean {
    return this.status === 0
  }

  get isRetryable(): boolean {
    return isRetryableApiError(this)
  }

  toJSON(): Record<string, unknown> {
    return {
      name: this.name,
      status: this.status,
      code: this.code,
      detail: this.detail,
      errors: this.errors,
      ...(this.requestId ? { request_id: this.requestId } : {}),
      ...(this.retryAfterSeconds === undefined
        ? {}
        : { retry_after_seconds: this.retryAfterSeconds }),
    }
  }
}

export function isApiClientError(value: unknown): value is ApiClientError {
  return value instanceof ApiClientError
}

export function parseRetryAfter(value: unknown, now = Date.now()): number | undefined {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
    return Math.ceil(value)
  }
  const text = stringValue(value)
  if (!text) return undefined
  const seconds = Number(text)
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds)
  const timestamp = Date.parse(text)
  return Number.isFinite(timestamp) ? Math.max(0, Math.ceil((timestamp - now) / 1_000)) : undefined
}

function statusFromUnknown(error: unknown): number {
  if (!isRecord(error)) return 0
  if (typeof error.status === 'number' && Number.isFinite(error.status)) return error.status
  const response = isRecord(error.response) ? error.response : undefined
  return typeof response?.status === 'number' ? response.status : 0
}

function responseFromUnknown(error: unknown): Record<string, unknown> | undefined {
  if (!isRecord(error) || !isRecord(error.response)) return undefined
  return error.response
}

const STATUS_CODE_FALLBACKS: Readonly<Record<number, SimulationErrorCode>> = {
  400: 'MALFORMED_REQUEST',
  401: 'AUTH_REQUIRED',
  403: 'FORBIDDEN',
  404: 'NOT_FOUND',
  409: 'CONFLICT',
  410: 'RESULT_EXPIRED',
  413: 'INPUT_TOO_LARGE',
  415: 'UNSUPPORTED_IMPORT_FORMAT',
  422: 'VALIDATION_ERROR',
  429: 'RATE_LIMITED',
  503: 'DEPENDENCY_NOT_READY',
  504: 'SIMULATION_TIMEOUT',
}

function canonicalCodeForStatus(status: number): SimulationErrorCode {
  if (status === 0) return 'NETWORK_ERROR'
  return STATUS_CODE_FALLBACKS[status] ?? 'HTTP_ERROR'
}

/** Convert an Axios rejection (or a test double shaped like one) without losing metadata. */
export function toApiClientError(
  error: unknown,
  fallbackMessage = 'API request failed'
): ApiClientError {
  if (error instanceof ApiClientError) return error

  const response = responseFromUnknown(error)
  const status = statusFromUnknown(error)
  const body = response?.data
  const envelope = normalizeApiErrorEnvelope(body, status)
  const detail =
    envelope.detail ??
    stringValue(isRecord(error) ? error.message : undefined) ??
    (status > 0 ? `API request failed with status ${status}` : fallbackMessage)
  const requestConfig = isRecord(error) && isRecord(error.config) ? error.config : undefined
  const requestId =
    envelope.request_id ??
    headerValue(response?.headers, 'X-Request-ID') ??
    headerValue(requestConfig?.headers, 'X-Request-ID')
  const retryAfterSeconds = parseRetryAfter(headerValue(response?.headers, 'Retry-After'))
  const code =
    envelope.code ??
    (stringValue(isRecord(error) ? error.code : undefined) as SimulationErrorCode | undefined) ??
    canonicalCodeForStatus(status)

  return new ApiClientError({
    status,
    code,
    detail,
    errors: envelope.errors,
    requestId,
    retryAfterSeconds,
    cause: error,
  })
}

export const normalizeApiError = toApiClientError
export const createApiClientError = (init: ApiClientErrorInit): ApiClientError =>
  new ApiClientError(init)

const RETRYABLE_CODES = new Set(['RATE_LIMITED', 'DEPENDENCY_NOT_READY', 'TRANSIENT_ERROR'])

/** Transport-level retry is intentionally narrower than a task's server-side retry policy. */
export function isRetryableApiError(error: unknown): boolean {
  const apiError = error instanceof ApiClientError ? error : toApiClientError(error)
  if (apiError.status === 0) {
    const cause = apiError.cause
    return Boolean(
      isRecord(cause) &&
        (cause.isAxiosError === true ||
          cause.code === 'ERR_NETWORK' ||
          cause.name === 'NetworkError')
    )
  }
  if (apiError.status === 429) return true
  if (apiError.status === 408 || apiError.status === 425) return true
  // A 5xx status is not sufficient evidence: deployment/policy/solver failures
  // are explicitly non-retryable unless the server supplied a transient code.
  return RETRYABLE_CODES.has(apiError.code)
}

export const DEFAULT_API_RETRY_DELAYS_MS = [10_000, 60_000] as const

export interface ApiRetryOptions {
  maxRetries?: number
  delaysMs?: readonly number[]
  shouldRetry?: (error: ApiClientError, retryNumber: number) => boolean
  sleep?: (delayMs: number, signal?: AbortSignal) => Promise<void>
  signal?: AbortSignal
}

function waitForRetry(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(signal.reason ?? new Error('Request aborted'))
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      clearTimeout(timer)
      reject(signal?.reason ?? new Error('Request aborted'))
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, delayMs)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

export async function withApiRetry<T>(
  operation: (retryNumber: number) => Promise<T>,
  options: ApiRetryOptions = {}
): Promise<T> {
  const maxRetries = Math.max(0, Math.trunc(options.maxRetries ?? 2))
  const delays = options.delaysMs ?? DEFAULT_API_RETRY_DELAYS_MS
  const sleep = options.sleep ?? waitForRetry
  let retryNumber = 0

  for (;;) {
    try {
      return await operation(retryNumber)
    } catch (error) {
      const apiError = toApiClientError(error)
      const canRetry =
        retryNumber < maxRetries &&
        !options.signal?.aborted &&
        (options.shouldRetry?.(apiError, retryNumber) ?? isRetryableApiError(apiError))
      if (!canRetry) throw apiError
      const configuredDelay = delays[Math.min(retryNumber, delays.length - 1)] ?? 0
      const delay =
        apiError.retryAfterSeconds === undefined
          ? configuredDelay
          : Math.max(0, apiError.retryAfterSeconds * 1_000)
      await sleep(delay, options.signal)
      retryNumber += 1
    }
  }
}

export function getApiErrorMessage(error: unknown, fallback = 'API request failed'): string {
  if (error instanceof ApiClientError) return error.detail || error.code
  if (error instanceof Error && error.message) return error.message
  return fallback
}

export function toStoreError(error: unknown): {
  status: number
  code: SimulationErrorCode
  message: string
  requestId?: string
  retryAfterSeconds?: number
  retryable: boolean
} {
  const apiError = toApiClientError(error)
  return {
    status: apiError.status,
    code: apiError.code,
    message: getApiErrorMessage(apiError),
    ...(apiError.requestId ? { requestId: apiError.requestId } : {}),
    ...(apiError.retryAfterSeconds === undefined
      ? {}
      : { retryAfterSeconds: apiError.retryAfterSeconds }),
    retryable: apiError.isRetryable,
  }
}
