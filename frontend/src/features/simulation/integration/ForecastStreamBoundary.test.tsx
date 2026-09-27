import { useEffect } from 'react'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ForecastStreamBoundary } from './ForecastStreamBoundary'
import { useHydraulicRealtime } from '../realtime/HydraulicRealtimeProvider'
import { useForecastStreamSubscription } from '../forecast/ForecastStreamProvider'

function storage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    clear: () => values.clear(),
    key: (index: number) => [...values.keys()][index] ?? null,
    get length() {
      return values.size
    },
  }
}

function pendingResponse(chunks: string[] = []): Response {
  const encoder = new TextEncoder()
  let index = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: vi.fn(async () => {
          if (index < chunks.length) {
            const value = encoder.encode(chunks[index++])
            return { done: false, value }
          }
          return new Promise<never>(() => undefined)
        }),
      }),
    },
  } as unknown as Response
}

function Consumer({ onResync }: { onResync?: (reason: string) => void }) {
  const realtime = useHydraulicRealtime()
  useForecastStreamSubscription({
    networkId: 'network-1',
    token: 'jwt-token',
    onEvent: () => undefined,
    onResyncRequired: () => undefined,
  })
  useEffect(() => {
    if (!onResync) return undefined
    return realtime.onResync(onResync)
  }, [onResync, realtime])
  return <div data-testid="consumer">{realtime.status}</div>
}

afterEach(() => {
  cleanup()
  window.history.replaceState({}, '', '/')
  vi.unstubAllGlobals()
})

describe('ForecastStreamBoundary', () => {
  it('keeps one authenticated fetch-SSE owner for forecast and task consumers', async () => {
    const values = storage()
    values.setItem('scn_access_token', 'jwt-token')
    vi.stubGlobal('localStorage', values)
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      pendingResponse()
    )
    vi.stubGlobal('fetch', fetchMock)

    render(
      <ForecastStreamBoundary networkId="network-1">
        <Consumer />
      </ForecastStreamBoundary>
    )

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/forecast/events?network_id=network-1')
    expect((fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>).Authorization).toBe(
      'Bearer jwt-token'
    )
  })

  it('ignores comment heartbeats without requesting a REST resync', async () => {
    const values = storage()
    values.setItem('scn_access_token', 'jwt-token')
    vi.stubGlobal('localStorage', values)
    const fetchMock = vi.fn(async () => pendingResponse([': heartbeat\n\n']))
    vi.stubGlobal('fetch', fetchMock)
    const onResync = vi.fn()

    render(
      <ForecastStreamBoundary networkId="network-1">
        <Consumer onResync={onResync} />
      </ForecastStreamBoundary>
    )

    await waitFor(() => expect(screen.getByTestId('consumer')).toHaveTextContent('connecting'))
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10))
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(onResync).not.toHaveBeenCalled()
  })
})

describe('HydraulicRealtimeProvider authorization terminality', () => {
  it('clears a rejected token and does not reconnect on 401', async () => {
    const values = storage()
    values.setItem('scn_access_token', 'bad-token')
    vi.stubGlobal('localStorage', values)
    const fetchMock = vi.fn(async () => ({ status: 401, ok: false, body: null }) as Response)
    vi.stubGlobal('fetch', fetchMock)
    window.history.replaceState({}, '', '/login')

    render(
      <ForecastStreamBoundary networkId="network-1">
        <Consumer />
      </ForecastStreamBoundary>
    )

    await waitFor(() => expect(screen.getByTestId('consumer')).toHaveTextContent('unauthenticated'))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(values.getItem('scn_access_token')).toBeNull()
  })

  it('stops on 403 without clearing the authenticated role', async () => {
    const values = storage()
    values.setItem('scn_access_token', 'role-token')
    values.setItem('scn_user_role', 'operator')
    vi.stubGlobal('localStorage', values)
    const fetchMock = vi.fn(async () => ({ status: 403, ok: false, body: null }) as Response)
    vi.stubGlobal('fetch', fetchMock)

    render(
      <ForecastStreamBoundary networkId="network-1">
        <Consumer />
      </ForecastStreamBoundary>
    )

    await waitFor(() => expect(screen.getByTestId('consumer')).toHaveTextContent('forbidden'))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(values.getItem('scn_access_token')).toBe('role-token')
  })
})
