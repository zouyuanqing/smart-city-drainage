import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SimulationTaskStreamEvent } from '../tasks/types'
import { isTerminalSimulationStatus } from '../model/taskState'
import {
  handleSseAuthorizationFailure,
  isSseAuthorizationFailure,
  isSseKeepaliveBlock,
} from '../realtime/authFailure'

const INITIAL_RETRY_DELAY_MS = 1_000
const MAX_RETRY_DELAY_MS = 30_000
const MAX_SEEN_EVENT_IDS = 500
const ACCEPTED_EVENT_NAMES = new Set(['simulation', 'task'])

function isForecastEventBlock(block: string): boolean {
  return block.split(/\r?\n/).some((line) => line === 'event: forecast')
}

export type SimulationStreamStatus =
  | 'disabled'
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'unauthenticated'
  | 'forbidden'

export interface ParsedSimulationStreamEvent {
  streamId: string
  streamEvent: SimulationTaskStreamEvent
}

export interface UseSimulationStreamOptions {
  networkId: string
  token?: string | null
  enabled?: boolean
  endpoint?: string | ((networkId: string) => string)
  getToken?: () => string | null | undefined
  fetchImpl?: typeof fetch
  onEvent?: (event: SimulationTaskStreamEvent) => void
  onInvalidate?: (simulationId: string) => void
  onOpen?: () => void
  onTransportLost?: () => void
  onResyncRequired?: (reason: string) => void
  onProtocolError?: (error: unknown) => void
}

export interface UseSimulationStreamResult {
  status: SimulationStreamStatus
  lastEventId: string | null
  reconnect: () => void
}

export function parseSimulationStreamBlock(
  block: string,
  expectedNetworkId?: string
): ParsedSimulationStreamEvent | null {
  let streamId = ''
  let eventName = ''
  const data: string[] = []

  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(':')) continue
    const separator = rawLine.indexOf(':')
    const field = separator === -1 ? rawLine : rawLine.slice(0, separator)
    const value = separator === -1 ? '' : rawLine.slice(separator + 1).replace(/^ /, '')
    if (field === 'id') streamId = value
    if (field === 'event') eventName = value
    if (field === 'data') data.push(value)
  }

  if (!ACCEPTED_EVENT_NAMES.has(eventName) || !streamId || data.length === 0) return null

  try {
    const value = JSON.parse(data.join('\n')) as Partial<SimulationTaskStreamEvent>
    if (
      value.schema_version !== 1 ||
      typeof value.event_id !== 'string' ||
      !value.event_id ||
      typeof value.sequence !== 'number' ||
      !Number.isSafeInteger(value.sequence) ||
      value.sequence < 0 ||
      typeof value.event_type !== 'string' ||
      !value.event_type ||
      typeof value.occurred_at !== 'string' ||
      typeof value.network_id !== 'string' ||
      !value.network_id
    ) {
      return null
    }

    if (expectedNetworkId && value.network_id !== expectedNetworkId) return null
    const candidate = value.simulation ?? value.task
    if (candidate) {
      if (
        typeof candidate !== 'object' ||
        candidate === null ||
        typeof candidate.id !== 'string' ||
        (value.simulation_id !== undefined && candidate.id !== value.simulation_id) ||
        ('network_id' in candidate &&
          candidate.network_id !== undefined &&
          candidate.network_id !== value.network_id)
      ) {
        return null
      }
    }

    return {
      streamId,
      streamEvent: {
        schema_version: 1,
        event_id: value.event_id,
        sequence: value.sequence,
        event_type: value.event_type,
        occurred_at: value.occurred_at,
        network_id: value.network_id,
        simulation_id: typeof value.simulation_id === 'string' ? value.simulation_id : undefined,
        simulation: value.simulation,
        task: value.task,
        from_sequence: typeof value.from_sequence === 'number' ? value.from_sequence : undefined,
        to_sequence: typeof value.to_sequence === 'number' ? value.to_sequence : undefined,
      },
    }
  } catch {
    return null
  }
}

function defaultEndpoint(networkId: string): string {
  return `/api/forecast/events?network_id=${encodeURIComponent(networkId)}`
}

function rememberEventId(seen: Set<string>, eventId: string): void {
  seen.add(eventId)
  if (seen.size <= MAX_SEEN_EVENT_IDS) return
  const oldest = seen.values().next().value
  if (oldest) seen.delete(oldest)
}

/**
 * Bearer-authenticated fetch/SSE transport for simulation invalidations and optional task snapshots.
 * REST polling remains the source of truth; disconnect and sequence gaps always request a REST resync.
 */
export function useSimulationStream(
  options: UseSimulationStreamOptions
): UseSimulationStreamResult {
  const { endpoint: endpointOption, networkId } = options
  const enabled = options.enabled !== false && Boolean(networkId)
  const endpoint = useMemo(() => {
    if (typeof endpointOption === 'function') return endpointOption(networkId)
    return endpointOption ?? defaultEndpoint(networkId)
  }, [endpointOption, networkId])
  const callbacks = useRef(options)
  callbacks.current = options
  const connectRef = useRef<() => void>(() => undefined)
  const [status, setStatus] = useState<SimulationStreamStatus>(enabled ? 'idle' : 'disabled')
  const [lastEventId, setLastEventId] = useState<string | null>(null)

  const reconnect = useCallback(() => {
    if (!enabled) return
    connectRef.current()
  }, [enabled])

  useEffect(() => {
    if (!enabled) {
      setStatus('disabled')
      return
    }

    let stopped = false
    let terminalAuthFailure = false
    let retryCount = 0
    let wasConnected = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    let lastSequence: number | undefined
    let cursor: string | null = null
    const seenEventIds = new Set<string>()
    const fetchImpl = callbacks.current.fetchImpl ?? globalThis.fetch.bind(globalThis)

    const scheduleReconnect = (delay: number) => {
      if (stopped || timer) return
      timer = setTimeout(() => {
        timer = undefined
        void connect()
      }, delay)
    }

    const processBlock = (block: string) => {
      if (isSseKeepaliveBlock(block) || isForecastEventBlock(block)) return
      const parsed = parseSimulationStreamBlock(block, networkId)
      if (!parsed) {
        callbacks.current.onResyncRequired?.('invalid_event')
        callbacks.current.onProtocolError?.(new Error('Invalid simulation stream event'))
        return
      }

      const { streamId, streamEvent } = parsed
      if (seenEventIds.has(streamEvent.event_id)) return

      if (lastSequence !== undefined && streamEvent.sequence <= lastSequence) {
        callbacks.current.onResyncRequired?.('sequence_regression')
        return
      }
      if (lastSequence !== undefined && streamEvent.sequence > lastSequence + 1) {
        callbacks.current.onResyncRequired?.('sequence_gap')
      }

      lastSequence = streamEvent.sequence
      cursor = streamId
      setLastEventId(streamId)
      rememberEventId(seenEventIds, streamEvent.event_id)

      if (streamEvent.event_type === 'forecast.resync_required') {
        callbacks.current.onResyncRequired?.('server_resync_required')
      }
      const taskStatus = streamEvent.simulation?.status ?? streamEvent.task?.status
      const simulationId = streamEvent.simulation_id ?? streamEvent.simulation?.id
      if (taskStatus && isTerminalSimulationStatus(taskStatus)) {
        if (simulationId) callbacks.current.onInvalidate?.(simulationId)
        return
      }
      callbacks.current.onEvent?.(streamEvent)
      if (simulationId) callbacks.current.onInvalidate?.(simulationId)
    }

    const connect = async () => {
      if (stopped || terminalAuthFailure) return
      if (timer) {
        clearTimeout(timer)
        timer = undefined
      }
      controller?.abort()
      const connection = new AbortController()
      controller = connection
      setStatus(retryCount > 0 ? 'reconnecting' : 'connecting')

      const token = callbacks.current.token ?? callbacks.current.getToken?.()
      if (!token) {
        setStatus('unauthenticated')
        return
      }

      const headers: Record<string, string> = {
        Accept: 'text/event-stream',
        Authorization: `Bearer ${token}`,
      }
      if (cursor) headers['Last-Event-ID'] = cursor

      try {
        const response = await fetchImpl(endpoint, {
          method: 'GET',
          headers,
          signal: connection.signal,
        })

        if (isSseAuthorizationFailure(response.status)) {
          terminalAuthFailure = true
          setStatus(response.status === 401 ? 'unauthenticated' : 'forbidden')
          if (response.status === 401) handleSseAuthorizationFailure(401)
          callbacks.current.onProtocolError?.(
            new Error(
              response.status === 401
                ? 'Simulation stream authentication expired'
                : 'Simulation stream is forbidden'
            )
          )
          return
        }
        if (response.status === 409) {
          const body = (await response.json().catch(() => null)) as { code?: string } | null
          cursor = null
          lastSequence = undefined
          seenEventIds.clear()
          setLastEventId(null)
          callbacks.current.onResyncRequired?.(body?.code ?? 'SSE_CURSOR_EXPIRED')
          scheduleReconnect(INITIAL_RETRY_DELAY_MS)
          return
        }
        if (!response.ok || !response.body) {
          throw new Error(`Simulation stream failed: ${response.status}`)
        }

        retryCount = 0
        wasConnected = true
        setStatus('connected')
        callbacks.current.onOpen?.()

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() ?? ''
          for (const block of blocks) processBlock(block)
        }
        if (buffer.trim()) processBlock(buffer)
        throw new Error('Simulation stream closed')
      } catch (error) {
        if (stopped || connection.signal.aborted || controller !== connection) return
        if (wasConnected) callbacks.current.onTransportLost?.()
        wasConnected = false
        setStatus('reconnecting')
        callbacks.current.onProtocolError?.(error)
        const delay = Math.min(INITIAL_RETRY_DELAY_MS * 2 ** retryCount, MAX_RETRY_DELAY_MS)
        retryCount += 1
        scheduleReconnect(delay)
      }
    }

    connectRef.current = () => {
      if (terminalAuthFailure) return
      retryCount = 0
      void connect()
    }
    setLastEventId(null)
    void connect()

    return () => {
      stopped = true
      controller?.abort()
      if (timer) clearTimeout(timer)
      connectRef.current = () => undefined
    }
  }, [enabled, endpoint, networkId, options.token])

  return { status, lastEventId, reconnect }
}
