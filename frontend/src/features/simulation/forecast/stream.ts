import { useEffect, useRef, useState } from 'react'
import { applyForecastEvent } from './ranking'
import {
  handleSseAuthorizationFailure,
  isSseAuthorizationFailure,
  isSseKeepaliveBlock,
} from '../realtime/authFailure'
import type { ForecastAlert, ForecastEvent } from './types'

const MAX_RETRY_DELAY_MS = 30_000
const MAX_SEEN_EVENT_IDS = 500
const UTC_RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/
const EVENT_TYPES = new Set([
  'alert.created',
  'alert.updated',
  'alert.escalated',
  'alert.observed',
  'alert.resolved',
  'alert.expired',
])

export interface ForecastResyncControl {
  eventId: string
  from_sequence?: number
  to_sequence?: number
}

export type ParsedForecastSse =
  | { eventId: string; event: ForecastEvent; resync?: undefined }
  | { eventId: string; resync: ForecastResyncControl; event?: undefined }

function validTimestamp(value: unknown): value is string {
  return (
    typeof value === 'string' && UTC_RFC3339_RE.test(value) && Number.isFinite(Date.parse(value))
  )
}

function validFiniteNumber(value: unknown, min = -Infinity, max = Infinity): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max
}

function validAlert(value: unknown): value is ForecastAlert {
  if (typeof value !== 'object' || value === null) return false
  const alert = value as Record<string, unknown>
  const target = alert.target
  return (
    typeof alert.id === 'string' &&
    alert.id.length > 0 &&
    (alert.type === 'flood_forecast' || alert.type === 'pipe_surcharge') &&
    (alert.status === 'open' ||
      alert.status === 'acknowledged' ||
      alert.status === 'resolved' ||
      alert.status === 'expired') &&
    (alert.phase === 'predicted' || alert.phase === 'observed') &&
    typeof target === 'object' &&
    target !== null &&
    ((target as Record<string, unknown>).type === 'node' ||
      (target as Record<string, unknown>).type === 'pipe_segment') &&
    typeof (target as Record<string, unknown>).id === 'string' &&
    validTimestamp(alert.expected_at) &&
    validFiniteNumber(alert.lead_time_minutes, 0, Number.MAX_SAFE_INTEGER) &&
    typeof alert.measurements === 'object' &&
    alert.measurements !== null &&
    Object.values(alert.measurements as Record<string, unknown>).every(
      (entry) => entry === null || validFiniteNumber(entry)
    ) &&
    validFiniteNumber(alert.confidence, 0, 1) &&
    typeof alert.confidence_method === 'string' &&
    alert.confidence_method.length > 0 &&
    validFiniteNumber(alert.risk_score, 0, 100) &&
    validFiniteNumber(alert.version, 0, Number.MAX_SAFE_INTEGER)
  )
}

export function parseForecastSse(
  block: string,
  expectedNetworkId?: string
): ParsedForecastSse | null {
  let eventId = ''
  let eventName = ''
  const data: string[] = []
  for (const rawLine of block.split(/\r?\n/)) {
    if (!rawLine || rawLine.startsWith(':')) continue
    const separator = rawLine.indexOf(':')
    const field = separator === -1 ? rawLine : rawLine.slice(0, separator)
    const value = separator === -1 ? '' : rawLine.slice(separator + 1).replace(/^ /, '')
    if (field === 'id') eventId = value
    if (field === 'event') eventName = value
    if (field === 'data') data.push(value)
  }
  if (eventName !== 'forecast' || !eventId || data.length === 0) return null

  try {
    const raw = JSON.parse(data.join('\n')) as Record<string, unknown>
    if (
      raw.schema_version !== 1 ||
      typeof raw.event_id !== 'string' ||
      !raw.event_id ||
      !validFiniteNumber(raw.sequence, 0, Number.MAX_SAFE_INTEGER) ||
      typeof raw.event_type !== 'string' ||
      !validTimestamp(raw.occurred_at) ||
      typeof raw.network_id !== 'string' ||
      !raw.network_id ||
      (expectedNetworkId !== undefined && raw.network_id !== expectedNetworkId) ||
      typeof raw.nowcast_run_id !== 'string' ||
      typeof raw.simulation_id !== 'string'
    ) {
      return null
    }

    if (raw.event_type === 'forecast.resync_required') {
      const from = raw.from_sequence
      const to = raw.to_sequence
      if (from !== undefined && !validFiniteNumber(from, 0, Number.MAX_SAFE_INTEGER)) return null
      if (to !== undefined && !validFiniteNumber(to, 0, Number.MAX_SAFE_INTEGER)) return null
      return {
        eventId,
        resync: {
          eventId: raw.event_id,
          ...(from === undefined ? {} : { from_sequence: from }),
          ...(to === undefined ? {} : { to_sequence: to }),
        },
      }
    }

    if (!EVENT_TYPES.has(raw.event_type) || !validAlert(raw.alert)) return null
    return {
      eventId,
      event: raw as unknown as ForecastEvent,
    }
  } catch {
    return null
  }
}

export interface ForecastStreamOptions {
  networkId: string
  token: string
  onEvent: (event: ForecastEvent) => void
  onResyncRequired: (reason?: string) => void
  onConnectionChange?: (connected: boolean) => void
  onProtocolError?: (error: unknown) => void
  enabled?: boolean
  fetchImpl?: typeof fetch
}

function rememberEventId(seen: Set<string>, eventId: string): void {
  seen.add(eventId)
  if (seen.size <= MAX_SEEN_EVENT_IDS) return
  const oldest = seen.values().next().value
  if (oldest) seen.delete(oldest)
}

/** A single bearer-authenticated fetch stream with cursor/gap recovery. */
export function useHydraulicForecastStream(options: ForecastStreamOptions): { connected: boolean } {
  const callbacks = useRef(options)
  callbacks.current = options
  const enabled = options.enabled !== false && Boolean(options.networkId)
  const [connected, setConnected] = useState(false)
  const lastEventId = useRef<string | null>(null)

  useEffect(() => {
    if (!enabled) {
      setConnected(false)
      return undefined
    }
    let stopped = false
    let retryCount = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    let activeController: AbortController | undefined
    let previousSequence: number | undefined
    const seenEventIds = new Set<string>()
    const fetchImpl = callbacks.current.fetchImpl ?? globalThis.fetch.bind(globalThis)

    const schedule = (delay: number) => {
      if (stopped || timer) return
      timer = setTimeout(() => {
        timer = undefined
        void connect()
      }, delay)
    }

    const process = (block: string) => {
      if (isSseKeepaliveBlock(block)) return
      const parsed = parseForecastSse(block, callbacks.current.networkId)
      if (!parsed) {
        callbacks.current.onResyncRequired('invalid_event')
        callbacks.current.onProtocolError?.(new Error('Invalid forecast SSE event'))
        return
      }
      const sequence = parsed.event?.sequence ?? parsed.resync?.from_sequence
      if (sequence !== undefined) {
        if (previousSequence !== undefined && sequence <= previousSequence) return
        if (previousSequence !== undefined && sequence > previousSequence + 1) {
          callbacks.current.onResyncRequired('sequence_gap')
        }
        previousSequence = sequence
      }
      const identity = parsed.event?.event_id ?? parsed.resync?.eventId ?? parsed.eventId
      if (seenEventIds.has(identity)) return
      rememberEventId(seenEventIds, identity)
      lastEventId.current = parsed.eventId
      if (parsed.resync) {
        callbacks.current.onResyncRequired('server_resync_required')
      } else if (parsed.event) {
        callbacks.current.onEvent(parsed.event)
      }
    }

    const connect = async () => {
      if (stopped) return
      const connection = new AbortController()
      activeController = connection
      const headers: HeadersInit = {
        Authorization: `Bearer ${callbacks.current.token}`,
        Accept: 'text/event-stream',
      }
      if (lastEventId.current) headers['Last-Event-ID'] = lastEventId.current
      try {
        const response = await fetchImpl(
          `/api/forecast/events?network_id=${encodeURIComponent(callbacks.current.networkId)}`,
          { headers, signal: connection.signal }
        )
        if (isSseAuthorizationFailure(response.status)) {
          setConnected(false)
          callbacks.current.onConnectionChange?.(false)
          if (response.status === 401) handleSseAuthorizationFailure(401)
          callbacks.current.onProtocolError?.(
            new Error(
              response.status === 401
                ? 'Forecast stream authentication expired'
                : 'Forecast stream is forbidden'
            )
          )
          return
        }
        if (response.status === 409) {
          const body = (await response.json().catch(() => null)) as { code?: string } | null
          lastEventId.current = null
          previousSequence = undefined
          seenEventIds.clear()
          callbacks.current.onResyncRequired(body?.code ?? 'SSE_CURSOR_EXPIRED')
          schedule(1000)
          return
        }
        if (!response.ok || !response.body) {
          throw new Error(`forecast stream failed: ${response.status}`)
        }
        retryCount = 0
        setConnected(true)
        callbacks.current.onConnectionChange?.(true)
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() ?? ''
          for (const block of blocks) process(block)
        }
        if (buffer.trim()) process(buffer)
        throw new Error('forecast stream closed')
      } catch {
        if (stopped || connection.signal.aborted || activeController !== connection) return
        setConnected(false)
        callbacks.current.onConnectionChange?.(false)
        callbacks.current.onResyncRequired('transport_lost')
        schedule(Math.min(1000 * 2 ** retryCount++, MAX_RETRY_DELAY_MS))
      }
    }

    void connect()
    return () => {
      stopped = true
      activeController?.abort()
      if (timer) clearTimeout(timer)
      lastEventId.current = null
      setConnected(false)
    }
  }, [enabled, options.networkId, options.token])

  return { connected }
}

export function useForecastAlerts(
  initialAlerts: readonly ForecastAlert[],
  networkId: string,
  token: string
) {
  const [alerts, setAlerts] = useState<ForecastAlert[]>([...initialAlerts])
  useHydraulicForecastStream({
    networkId,
    token,
    onEvent: (event) => setAlerts((current) => applyForecastEvent(current, event, networkId)),
    onResyncRequired: () => undefined,
  })
  return alerts
}
