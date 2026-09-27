import { act } from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { parseSimulationStreamBlock, useSimulationStream } from '../hooks/useSimulationStream'

function eventBlock(id: number, eventId: string, sequence: number, simulationId: string): string {
  return [
    `id: ${id}`,
    'event: simulation',
    `data: ${JSON.stringify({
      schema_version: 1,
      event_id: eventId,
      sequence,
      event_type: 'simulation.progress',
      occurred_at: '2026-09-24T00:01:00Z',
      network_id: 'network-1',
      simulation_id: simulationId,
      simulation: {
        id: simulationId,
        status: 'running',
        progress: sequence * 10,
        attempt: 1,
        max_attempts: 3,
        network_id: 'network-1',
        created_at: '2026-09-24T00:00:00Z',
        updated_at: '2026-09-24T00:01:00Z',
      },
    })}`,
    '',
    '',
  ].join('\n')
}

function finiteStream(chunks: string[]): Response {
  const encoder = new TextEncoder()
  let index = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: vi.fn(async () => {
          if (index < chunks.length) {
            const value = encoder.encode(chunks[index])
            index += 1
            return { done: false, value }
          }
          return { done: true, value: undefined }
        }),
      }),
    },
  } as unknown as Response
}

function pendingStream(): Response {
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({ read: vi.fn(() => new Promise(() => undefined)) }),
    },
  } as unknown as Response
}

describe('simulation SSE transport', () => {
  it('parses only the versioned stream envelope', () => {
    const parsed = parseSimulationStreamBlock(eventBlock(7, 'event-7', 7, 'simulation-1'))
    expect(parsed).toMatchObject({
      streamId: '7',
      streamEvent: {
        schema_version: 1,
        event_id: 'event-7',
        sequence: 7,
        simulation_id: 'simulation-1',
      },
    })

    expect(
      parseSimulationStreamBlock(
        eventBlock(8, 'bad-event', 8, 'simulation-1').replace(
          '"schema_version":1',
          '"schema_version":2'
        )
      )
    ).toBeNull()
  })

  it('requests REST resync on a gap/transport loss and reconnects with cursor and Bearer auth', async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit): Promise<Response> => pendingStream()
    )
    fetchMock
      .mockResolvedValueOnce(
        finiteStream([
          eventBlock(10, 'event-10', 10, 'simulation-1') +
            eventBlock(12, 'event-12', 12, 'simulation-1'),
        ])
      )
      .mockResolvedValueOnce(pendingStream())
    const onEvent = vi.fn()
    const onInvalidate = vi.fn()
    const onResyncRequired = vi.fn()
    const onTransportLost = vi.fn()

    const view = renderHook(() =>
      useSimulationStream({
        networkId: 'network-1',
        token: 'jwt-token',
        endpoint: '/api/forecast/events?network_id=network-1',
        fetchImpl: fetchMock as typeof fetch,
        onEvent,
        onInvalidate,
        onResyncRequired,
        onTransportLost,
      })
    )

    await waitFor(() => expect(onTransportLost).toHaveBeenCalledTimes(1))
    expect(onResyncRequired).toHaveBeenCalledWith('sequence_gap')
    expect(onEvent).toHaveBeenCalledTimes(2)
    expect(onInvalidate).toHaveBeenCalledTimes(2)

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2), { timeout: 2_000 })
    const secondHeaders = fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>
    expect(secondHeaders.Authorization).toBe('Bearer jwt-token')
    expect(secondHeaders['Last-Event-ID']).toBe('12')
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/forecast/events?network_id=network-1')

    await act(async () => view.unmount())
  })
})
