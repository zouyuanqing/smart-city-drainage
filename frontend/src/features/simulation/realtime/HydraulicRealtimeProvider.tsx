import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { parseForecastSse } from '../forecast/stream'
import {
  parseSimulationStreamBlock,
  type SimulationStreamStatus,
} from '../hooks/useSimulationStream'
import type { SimulationTaskStreamEvent } from '../tasks/types'
import type { ForecastEvent } from '../forecast/types'
import {
  handleSseAuthorizationFailure,
  isSseAuthorizationFailure,
  isSseKeepaliveBlock,
} from './authFailure'

const MAX_SEEN_EVENT_IDS = 500
const MAX_RETRY_DELAY_MS = 30_000

export interface HydraulicRealtimeController {
  status: SimulationStreamStatus
  connected: boolean
  lastEventId: string | null
  reconnect: () => void
  subscribeForecast: (listener: (event: ForecastEvent) => void) => () => void
  subscribeTask: (listener: (event: SimulationTaskStreamEvent) => void) => () => void
  onResync: (listener: (reason: string) => void) => () => void
}

const HydraulicRealtimeContext = createContext<HydraulicRealtimeController | null>(null)

export interface HydraulicRealtimeProviderProps {
  networkId: string
  token: string
  children: ReactNode
  fetchImpl?: typeof fetch
}

function rememberEventId(seen: Set<string>, eventId: string): void {
  seen.add(eventId)
  if (seen.size <= MAX_SEEN_EVENT_IDS) return
  const oldest = seen.values().next().value
  if (oldest) seen.delete(oldest)
}

function HydraulicRealtimeProviderImpl({
  networkId,
  token,
  children,
  fetchImpl,
}: HydraulicRealtimeProviderProps) {
  const [status, setStatus] = useState<SimulationStreamStatus>(
    networkId ? 'connecting' : 'disabled'
  )
  const [lastEventId, setLastEventId] = useState<string | null>(null)
  const forecastListenersRef = useRef(new Set<(event: ForecastEvent) => void>())
  const taskListenersRef = useRef(new Set<(event: SimulationTaskStreamEvent) => void>())
  const resyncListenersRef = useRef(new Set<(reason: string) => void>())
  const reconnectRef = useRef<() => void>(() => undefined)

  const subscribeForecast = useCallback((listener: (event: ForecastEvent) => void) => {
    forecastListenersRef.current.add(listener)
    return () => forecastListenersRef.current.delete(listener)
  }, [])
  const subscribeTask = useCallback((listener: (event: SimulationTaskStreamEvent) => void) => {
    taskListenersRef.current.add(listener)
    return () => taskListenersRef.current.delete(listener)
  }, [])
  const onResync = useCallback((listener: (reason: string) => void) => {
    resyncListenersRef.current.add(listener)
    return () => resyncListenersRef.current.delete(listener)
  }, [])

  useEffect(() => {
    if (!networkId || !token) {
      setStatus(token ? 'disabled' : 'unauthenticated')
      return undefined
    }

    let stopped = false
    let terminalAuthFailure = false
    let retryCount = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    let previousSequence: number | undefined
    let cursor: string | null = null
    const seenEventIds = new Set<string>()
    const forecastListeners = forecastListenersRef.current
    const taskListeners = taskListenersRef.current
    const resyncListeners = resyncListenersRef.current
    const request = fetchImpl ?? globalThis.fetch.bind(globalThis)

    const notifyResync = (reason: string) => {
      resyncListeners.forEach((listener) => listener(reason))
    }
    const schedule = (delay: number) => {
      if (stopped || timer) return
      timer = setTimeout(() => {
        timer = undefined
        void connect()
      }, delay)
    }
    const process = (block: string) => {
      if (isSseKeepaliveBlock(block)) return
      const forecast = parseForecastSse(block, networkId)
      const task = forecast ? null : parseSimulationStreamBlock(block, networkId)
      if (!forecast && !task) {
        notifyResync('invalid_event')
        return
      }
      const sequence =
        forecast?.event?.sequence ?? task?.streamEvent.sequence ?? forecast?.resync?.from_sequence
      if (sequence !== undefined) {
        if (previousSequence !== undefined && sequence <= previousSequence) return
        if (previousSequence !== undefined && sequence > previousSequence + 1) {
          notifyResync('sequence_gap')
        }
        previousSequence = sequence
      }
      const eventId = forecast?.eventId ?? task?.streamId
      const identity = forecast?.event?.event_id ?? task?.streamEvent.event_id ?? eventId
      if (!eventId || !identity || seenEventIds.has(identity)) return
      rememberEventId(seenEventIds, identity)
      cursor = eventId
      setLastEventId(eventId)
      if (forecast?.resync) {
        notifyResync('server_resync_required')
        return
      }
      if (forecast?.event) {
        forecastListeners.forEach((listener) => listener(forecast.event!))
      } else if (task) {
        taskListeners.forEach((listener) => listener(task.streamEvent))
      }
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
      const headers: Record<string, string> = {
        Accept: 'text/event-stream',
        Authorization: `Bearer ${token}`,
      }
      if (cursor) headers['Last-Event-ID'] = cursor
      try {
        const response = await request(
          `/api/forecast/events?network_id=${encodeURIComponent(networkId)}`,
          { headers, signal: connection.signal }
        )
        if (isSseAuthorizationFailure(response.status)) {
          terminalAuthFailure = true
          setStatus(response.status === 401 ? 'unauthenticated' : 'forbidden')
          if (response.status === 401) handleSseAuthorizationFailure(401)
          return
        }
        if (response.status === 409) {
          const body = (await response.json().catch(() => null)) as { code?: string } | null
          cursor = null
          previousSequence = undefined
          seenEventIds.clear()
          setLastEventId(null)
          notifyResync(body?.code ?? 'SSE_CURSOR_EXPIRED')
          schedule(1_000)
          return
        }
        if (!response.ok || !response.body) throw new Error(`stream failed: ${response.status}`)
        retryCount = 0
        setStatus('connected')
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder
            .decode(value, { stream: true })
            .replace(/\r\n/g, '\n')
            .replace(/\r/g, '\n')
          const blocks = buffer.split('\n\n')
          buffer = blocks.pop() ?? ''
          blocks.forEach(process)
        }
        if (buffer.trim()) process(buffer)
        throw new Error('stream closed')
      } catch {
        if (stopped || connection.signal.aborted || controller !== connection) return
        setStatus('reconnecting')
        notifyResync('transport_lost')
        schedule(Math.min(1_000 * 2 ** retryCount++, MAX_RETRY_DELAY_MS))
      }
    }

    reconnectRef.current = () => {
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
      reconnectRef.current = () => undefined
      forecastListeners.clear()
      taskListeners.clear()
      resyncListeners.clear()
    }
  }, [fetchImpl, networkId, token])

  const value = useMemo<HydraulicRealtimeController>(
    () => ({
      status,
      connected: status === 'connected',
      lastEventId,
      reconnect: () => reconnectRef.current(),
      subscribeForecast,
      subscribeTask,
      onResync,
    }),
    [lastEventId, onResync, status, subscribeForecast, subscribeTask]
  )

  return (
    <HydraulicRealtimeContext.Provider value={value}>{children}</HydraulicRealtimeContext.Provider>
  )
}

export function HydraulicRealtimeProvider(props: HydraulicRealtimeProviderProps) {
  const parent = useContext(HydraulicRealtimeContext)
  if (parent) return <>{props.children}</>
  return <HydraulicRealtimeProviderImpl {...props} />
}

export function useHydraulicRealtime(): HydraulicRealtimeController {
  const controller = useContext(HydraulicRealtimeContext)
  if (!controller)
    throw new Error('useHydraulicRealtime must be used inside HydraulicRealtimeProvider')
  return controller
}

export function useOptionalHydraulicRealtime(): HydraulicRealtimeController | null {
  return useContext(HydraulicRealtimeContext)
}

export { HydraulicRealtimeContext }
