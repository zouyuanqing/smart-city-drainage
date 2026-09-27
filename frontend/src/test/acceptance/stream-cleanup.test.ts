import { createElement } from 'react'
import { act, cleanup, render, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSSE } from '@/hooks/useSSE'
import { parseForecastSse } from '@/features/simulation/forecast/stream'
import { ForecastStreamBoundary } from '@/features/simulation/integration/ForecastStreamBoundary'
import { useForecastStreamSubscription } from '@/features/simulation/forecast/ForecastStreamProvider'
import { applyForecastEvent } from '@/features/simulation/forecast/ranking'
import type { ForecastAlert, ForecastEvent } from '@/features/simulation/forecast/types'
import { FrameCache, FrameCacheClient, FrameRequestScheduler } from '@/features/simulation/map'
import type { SimulationFrame } from '@/features/simulation/model/types'

const NETWORK_ID = '11111111-1111-4111-8111-111111111111'
const REVISION_ID = '22222222-2222-4222-8222-222222222222'
const MANIFEST_ID = '33333333-3333-4333-8333-333333333333'
const ATTEMPT_ID = '77777777-7777-4777-8777-777777777777'
const SIMULATION_ID = '55555555-5555-4555-8555-555555555555'
const HASH = 'a'.repeat(64)

function validFrame(index = 0): SimulationFrame {
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
    frame_time: `2026-09-24T00:${String(index).padStart(2, '0')}:00Z`,
    nodes: { feature_index: [0], depth_m: [0.1] },
    pipes: { feature_index: [0], fill_ratio: [0.2] },
    quality: { status: 'verified' },
  }
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  readonly url: string
  onopen: ((event: Event) => void) | null = null
  onerror: ((event: Event) => void) | null = null
  closed = false
  private readonly listeners = new Map<string, Set<EventListenerOrEventListenerObject>>()
  readonly close = vi.fn(() => {
    this.closed = true
  })

  constructor(url: string | URL) {
    this.url = String(url)
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject | null): void {
    if (!listener) return
    const bucket = this.listeners.get(type) ?? new Set()
    bucket.add(listener)
    this.listeners.set(type, bucket)
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject | null): void {
    if (listener) this.listeners.get(type)?.delete(listener)
  }

  emitOpen(): void {
    this.onopen?.(new Event('open'))
  }

  emitError(): void {
    this.onerror?.(new Event('error'))
  }

  emit(name: string, data: unknown): void {
    const event = new MessageEvent(name, { data: JSON.stringify(data) })
    for (const listener of this.listeners.get(name) ?? []) {
      if (typeof listener === 'function') listener.call(this, event)
      else listener.handleEvent(event)
    }
  }
}

function forecastBlock(
  id: number,
  sequence: number,
  phase: 'predicted' | 'observed' = 'predicted'
) {
  const eventId = `event-${sequence}`
  return [
    `id: ${id}`,
    'event: forecast',
    `data: ${JSON.stringify({
      schema_version: 1,
      event_id: eventId,
      sequence,
      event_type: phase === 'observed' ? 'alert.observed' : 'alert.created',
      occurred_at: '2026-09-24T00:01:00Z',
      network_id: NETWORK_ID,
      nowcast_run_id: 'run-1',
      simulation_id: SIMULATION_ID,
      alert: {
        id: 'alert-1',
        type: 'flood_forecast',
        status: 'open',
        phase,
        target: { type: 'node', id: 'node-1' },
        expected_at: '2026-09-24T00:30:00Z',
        lead_time_minutes: 30,
        measurements: { depth_m: 0.2 },
        confidence: 0.9,
        confidence_method: 'fixture',
        risk_score: 80,
        version: sequence,
      },
    })}`,
    '',
    '',
  ].join('\n')
}

function streamResponse(chunks: readonly string[]): Response {
  const encoder = new TextEncoder()
  let index = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: vi.fn(async () => {
          if (index >= chunks.length) return { done: true, value: undefined }
          const value = encoder.encode(chunks[index])
          index += 1
          return { done: false, value }
        }),
      }),
    },
  } as unknown as Response
}

function ForecastSubscriptionProbe({
  onEvent,
  onResyncRequired,
}: {
  onEvent: (event: ForecastEvent) => void
  onResyncRequired: (reason?: string) => void
}) {
  useForecastStreamSubscription({
    networkId: NETWORK_ID,
    token: 'jwt-token',
    onEvent,
    onResyncRequired,
  })
  return createElement('div', { 'data-testid': 'forecast-subscription-probe' })
}

function pendingStreamResponse(signal: AbortSignal): Response {
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: vi.fn(
          () =>
            new Promise<{ done: boolean; value?: Uint8Array }>((resolve) => {
              if (signal.aborted) resolve({ done: true })
              else signal.addEventListener('abort', () => resolve({ done: true }), { once: true })
            })
        ),
      }),
    },
  } as unknown as Response
}

beforeEach(() => {
  FakeEventSource.instances = []
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.useFakeTimers()
})

afterEach(() => {
  cleanup()
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('legacy useSSE reconnect and cleanup', () => {
  it('backs off exponentially, dispatches events, and cancels work on unmount', () => {
    const onConnected = vi.fn()
    const onDisconnected = vi.fn()
    const onError = vi.fn()
    const onSensorData = vi.fn()
    const onAlert = vi.fn()
    const view = renderHook(() =>
      useSSE({ onConnected, onDisconnected, onError, onSensorData, onAlert })
    )

    expect(FakeEventSource.instances).toHaveLength(1)
    const first = FakeEventSource.instances[0]
    act(() => first.emitOpen())
    expect(view.result.current.isConnected).toBe(true)
    expect(onConnected).toHaveBeenCalledTimes(1)

    act(() => first.emit('sensors', { readings: [{ id: 'sensor-1', value: 1 }] }))
    act(() => first.emit('alerts', { alert: { id: 'alert-1' } }))
    expect(onSensorData).toHaveBeenCalledWith([{ id: 'sensor-1', value: 1 }])
    expect(onAlert).toHaveBeenCalledWith({ id: 'alert-1' })

    act(() => first.emitError())
    expect(view.result.current.isConnected).toBe(false)
    expect(first.close).toHaveBeenCalledTimes(1)
    expect(onError).toHaveBeenCalledTimes(1)
    act(() => vi.advanceTimersByTime(999))
    expect(FakeEventSource.instances).toHaveLength(1)
    act(() => vi.advanceTimersByTime(1))
    expect(FakeEventSource.instances).toHaveLength(2)

    act(() => FakeEventSource.instances[1].emitError())
    act(() => vi.advanceTimersByTime(1_999))
    expect(FakeEventSource.instances).toHaveLength(2)
    act(() => vi.advanceTimersByTime(1))
    expect(FakeEventSource.instances).toHaveLength(3)

    act(() => FakeEventSource.instances[2].emitOpen())
    act(() => FakeEventSource.instances[2].emitError())
    act(() => vi.advanceTimersByTime(999))
    expect(FakeEventSource.instances).toHaveLength(3)
    act(() => vi.advanceTimersByTime(1))
    expect(FakeEventSource.instances).toHaveLength(4)

    view.unmount()
    act(() => vi.advanceTimersByTime(30_000))
    expect(FakeEventSource.instances).toHaveLength(4)
    expect(FakeEventSource.instances[3].close).toHaveBeenCalled()
    expect(onDisconnected).toHaveBeenCalled()
  })

  it('allows an explicit disconnect to suppress a pending reconnect', () => {
    const view = renderHook(() => useSSE())
    const source = FakeEventSource.instances[0]
    act(() => source.emitError())
    act(() => view.result.current.disconnect())
    act(() => vi.advanceTimersByTime(60_000))
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(view.result.current.isConnected).toBe(false)
  })

  it('ignores a late error callback after explicit disconnect', () => {
    const onError = vi.fn()
    const onDisconnected = vi.fn()
    const view = renderHook(() => useSSE({ onError, onDisconnected }))
    const source = FakeEventSource.instances[0]

    act(() => view.result.current.disconnect())
    expect(onDisconnected).toHaveBeenCalledTimes(1)
    act(() => source.emitError())
    act(() => vi.advanceTimersByTime(60_000))

    expect(onError).not.toHaveBeenCalled()
    expect(onDisconnected).toHaveBeenCalledTimes(1)
    expect(FakeEventSource.instances).toHaveLength(1)
  })

  it('invalidates the previous generation when reconnect is requested explicitly', () => {
    const onError = vi.fn()
    const view = renderHook(() => useSSE({ onError }))
    const first = FakeEventSource.instances[0]

    act(() => view.result.current.reconnect())
    expect(FakeEventSource.instances).toHaveLength(2)
    const second = FakeEventSource.instances[1]

    act(() => first.emitError())
    act(() => vi.advanceTimersByTime(60_000))

    expect(onError).not.toHaveBeenCalled()
    expect(FakeEventSource.instances).toHaveLength(2)
    expect(second.closed).toBe(false)
  })
})

describe('forecast stream cursor recovery and frame-resource cleanup', () => {
  it('reconnects the page-owned stream with the last event id after a gap and aborts on unmount', async () => {
    localStorage.setItem('scn_access_token', 'jwt-token')
    const signals: AbortSignal[] = []
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      const signal = init?.signal as AbortSignal
      signals.push(signal)
      if (fetchMock.mock.calls.length === 1) {
        return streamResponse([forecastBlock(1, 1) + forecastBlock(2, 3, 'observed')])
      }
      return pendingStreamResponse(signal)
    })
    const onEvent = vi.fn()
    const onResyncRequired = vi.fn()

    const view = render(
      createElement(ForecastStreamBoundary, {
        networkId: NETWORK_ID,
        fetchImpl: fetchMock as typeof fetch,
        children: createElement(ForecastSubscriptionProbe, {
          onEvent,
          onResyncRequired,
        }),
      })
    )

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(onEvent).toHaveBeenCalledTimes(2)
    expect(onResyncRequired).toHaveBeenCalledWith('sequence_gap')
    expect(onResyncRequired).toHaveBeenCalledWith('transport_lost')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000)
    })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const headers = fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer jwt-token')
    expect(headers['Last-Event-ID']).toBe('2')

    view.unmount()
    await act(async () => {
      await Promise.resolve()
    })
    expect(signals[1].aborted).toBe(true)
  })

  it('rejects malformed network events instead of committing them', () => {
    const malformed = forecastBlock(4, 4).replace(NETWORK_ID, 'other-network')
    expect(parseForecastSse(malformed, NETWORK_ID)).toBeNull()
    expect(parseForecastSse('event: forecast\\ndata: {}\\n')).toBeNull()
  })

  it('applies predicted to observed and terminal alert transitions without regressing versions', () => {
    const initial: ForecastAlert = {
      id: 'alert-1',
      type: 'flood_forecast',
      status: 'open',
      phase: 'predicted',
      target: { type: 'node', id: 'node-1' },
      expected_at: '2026-09-24T00:30:00Z',
      lead_time_minutes: 30,
      measurements: { depth_m: 0.2 },
      confidence: 0.9,
      confidence_method: 'fixture',
      risk_score: 80,
      version: 1,
    }
    const event = (
      version: number,
      eventType: ForecastEvent['event_type'],
      phase: ForecastAlert['phase'] = 'observed'
    ): ForecastEvent => ({
      schema_version: 1,
      event_id: `event-${version}`,
      sequence: version,
      event_type: eventType,
      occurred_at: '2026-09-24T00:01:00Z',
      network_id: NETWORK_ID,
      nowcast_run_id: 'run-1',
      simulation_id: SIMULATION_ID,
      alert: { ...initial, version, phase },
    })

    const observed = applyForecastEvent([initial], event(2, 'alert.observed'), NETWORK_ID)
    expect(observed[0]).toMatchObject({ phase: 'observed', version: 2 })
    expect(applyForecastEvent(observed, event(1, 'alert.observed'), NETWORK_ID)).toEqual(observed)

    const resolved = applyForecastEvent(observed, event(3, 'alert.resolved'), NETWORK_ID)
    expect(resolved[0]).toMatchObject({ status: 'resolved', version: 3 })

    const equalVersionReopen: ForecastEvent = {
      ...event(3, 'alert.updated'),
      alert: { ...resolved[0], status: 'open', version: 3 },
    }
    expect(applyForecastEvent(resolved, equalVersionReopen, NETWORK_ID)).toEqual(resolved)

    const higherVersionReopen: ForecastEvent = {
      ...event(4, 'alert.observed'),
      alert: { ...resolved[0], status: 'open', phase: 'observed', version: 4 },
    }
    expect(applyForecastEvent(resolved, higherVersionReopen, NETWORK_ID)).toEqual(resolved)

    const predictedJump = applyForecastEvent(
      observed,
      event(5, 'alert.escalated', 'predicted'),
      NETWORK_ID
    )
    expect(predictedJump).toEqual(observed)

    const expired = applyForecastEvent(
      [initial],
      event(2, 'alert.expired', 'predicted'),
      NETWORK_ID
    )
    expect(expired[0]).toMatchObject({ status: 'expired', version: 2 })
    expect(
      applyForecastEvent(
        expired,
        {
          ...event(2, 'alert.updated', 'predicted'),
          alert: { ...expired[0], status: 'open', version: 2 },
        },
        NETWORK_ID
      )
    ).toEqual(expired)
    expect(
      applyForecastEvent(
        expired,
        {
          ...event(3, 'alert.observed', 'observed'),
          alert: { ...expired[0], status: 'open', phase: 'observed', version: 3 },
        },
        NETWORK_ID
      )
    ).toEqual(expired)

    const wrongNetwork = applyForecastEvent(
      [initial],
      { ...event(2, 'alert.observed'), network_id: 'other-network' },
      NETWORK_ID
    )
    expect(wrongNetwork).toEqual([initial])
  })

  it('deduplicates in-flight frame loads and makes clear/dispose reject future work', async () => {
    const client = new FrameCacheClient(new FrameCache(1_000_000), 1)
    const loader = vi.fn(async () => validFrame())
    const first = client.getOrLoad('frame-0', loader)
    const second = client.getOrLoad('frame-0', loader)
    expect(second).toBe(first)
    await expect(Promise.all([first, second])).resolves.toHaveLength(2)
    expect(loader).toHaveBeenCalledTimes(1)
    expect(client.cache.size).toBe(1)

    client.dispose()
    expect(client.cache.sizeBytes).toBe(0)
    await expect(client.getOrLoad('frame-1', loader)).rejects.toThrow(/disposed/)
  })

  it('aborts active frame work when the client is cleared and drains queued scheduler work', async () => {
    const client = new FrameCacheClient(new FrameCache(1_000_000), 1)
    let signal: AbortSignal | undefined
    const pending = client.getOrLoad(
      'frame-pending',
      (loaderSignal) =>
        new Promise<SimulationFrame>((_resolve, reject) => {
          signal = loaderSignal
          loaderSignal?.addEventListener(
            'abort',
            () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })),
            { once: true }
          )
        })
    )
    const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(signal).toBeDefined()
    client.clear()
    await rejected
    expect(signal?.aborted).toBe(true)
    expect(client.cache.size).toBe(0)

    const scheduler = new FrameRequestScheduler(1)
    let release: (() => void) | undefined
    const active = scheduler.request(
      'active',
      () => new Promise<string>((resolve) => (release = () => resolve('active')))
    )
    const queued = scheduler.request('queued', async () => 'queued')
    const queuedRejected = expect(queued).rejects.toMatchObject({ name: 'AbortError' })
    scheduler.cancel('queued')
    await queuedRejected
    scheduler.dispose()
    release?.()
    await expect(active).resolves.toBe('active')
    await expect(scheduler.request('after-dispose', async () => 'nope')).rejects.toThrow(/disposed/)
  })
})
