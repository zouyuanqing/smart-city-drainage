import { renderHook } from '@testing-library/react'
import { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useWebSocket } from './useWebSocket'

class MockWebSocket {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 3
  static instances: MockWebSocket[] = []

  readyState = MockWebSocket.CONNECTING
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onclose: (() => void) | null = null
  onerror: ((event: Event) => void) | null = null

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this)
  }

  open() {
    this.readyState = MockWebSocket.OPEN
    this.onopen?.()
  }

  send(value: string) {
    this.sent.push(value)
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }
}

describe('useWebSocket authentication', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    const values = new Map([['scn_access_token', 'browser-jwt']])
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
      clear: () => values.clear(),
    })
    vi.stubGlobal('WebSocket', MockWebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('authenticates after the native browser handshake without a query token', () => {
    const view = renderHook(() => useWebSocket({ autoReconnect: false }))
    const socket = MockWebSocket.instances[0]

    expect(socket.url).toBe('ws://localhost:3000/api/ws/control')
    expect(socket.url).not.toContain('browser-jwt')

    act(() => socket.open())

    expect(socket.sent).toEqual([JSON.stringify({ type: 'authenticate', token: 'browser-jwt' })])

    view.unmount()
  })
})
