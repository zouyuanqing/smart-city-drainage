import { describe, expect, it, vi } from 'vitest'
import { ApiClientError, isRetryableApiError, toApiClientError, withApiRetry } from './errors'

describe('simulation API error and retry model', () => {
  it('keeps field errors, request id, and Retry-After on the typed error', () => {
    const error = toApiClientError({
      isAxiosError: true,
      response: {
        status: 429,
        data: {
          detail: 'rate limited',
          code: 'RATE_LIMITED',
          request_id: 'request-1',
          errors: [{ loc: ['query', 'limit'], msg: 'must be <= 200' }],
        },
        headers: { 'Retry-After': '3' },
      },
    })

    expect(error).toBeInstanceOf(ApiClientError)
    expect(error).toMatchObject({
      status: 429,
      code: 'RATE_LIMITED',
      detail: 'rate limited',
      requestId: 'request-1',
      retryAfterSeconds: 3,
    })
    expect(error.errors[0]).toMatchObject({
      loc: ['query', 'limit'],
      message: 'must be <= 200',
    })
  })

  it('uses stable status fallbacks when a legacy server omits code', () => {
    const errorFor = (status: number) =>
      toApiClientError({
        isAxiosError: true,
        response: { status, data: { detail: 'legacy error' }, headers: {} },
      })

    expect(errorFor(401).code).toBe('AUTH_REQUIRED')
    expect(errorFor(403).code).toBe('FORBIDDEN')
    expect(errorFor(422).code).toBe('VALIDATION_ERROR')
    expect(errorFor(409).code).toBe('CONFLICT')
    expect(errorFor(410).code).toBe('RESULT_EXPIRED')
    expect(errorFor(429).code).toBe('RATE_LIMITED')
    expect(errorFor(503).code).toBe('DEPENDENCY_NOT_READY')
    expect(errorFor(504).code).toBe('SIMULATION_TIMEOUT')
  })

  it('retries only transport/network failures and honors an injected backoff', async () => {
    const sleep = vi.fn(async () => undefined)
    const operation = vi
      .fn()
      .mockRejectedValueOnce(toApiClientError({ isAxiosError: true, code: 'ERR_NETWORK' }))
      .mockResolvedValue('ok')

    await expect(withApiRetry(operation, { sleep, delaysMs: [1] })).resolves.toBe('ok')
    expect(operation).toHaveBeenCalledTimes(2)
    expect(sleep).toHaveBeenCalledWith(1, undefined)
    expect(isRetryableApiError(new Error('programming error'))).toBe(false)
    expect(
      isRetryableApiError(
        new ApiClientError({ status: 409, code: 'IDEMPOTENCY_KEY_REUSED', detail: 'conflict' })
      )
    ).toBe(false)
    expect(
      isRetryableApiError(
        new ApiClientError({ status: 503, code: 'SOLVER_UNAVAILABLE', detail: 'unavailable' })
      )
    ).toBe(false)
    expect(
      isRetryableApiError(
        new ApiClientError({ status: 503, code: 'DEPENDENCY_NOT_READY', detail: 'busy' })
      )
    ).toBe(true)
  })
})
