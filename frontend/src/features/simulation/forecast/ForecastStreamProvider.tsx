import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { useHydraulicForecastStream, type ForecastStreamOptions } from './stream'
import { useOptionalHydraulicRealtime } from '../realtime/HydraulicRealtimeProvider'
import type { ForecastEvent } from './types'

export interface ForecastStreamController {
  connected: boolean
  subscribe: (listener: (event: ForecastEvent) => void) => () => void
  onResync: (listener: (reason: string) => void) => () => void
}

const ForecastStreamContext = createContext<ForecastStreamController | null>(null)

export interface ForecastStreamProviderProps {
  networkId: string
  token: string
  children: ReactNode
  fetchImpl?: typeof fetch
  onResyncRequired?: () => void
}

export function ForecastStreamProvider({
  networkId,
  token,
  children,
  fetchImpl,
  onResyncRequired,
}: ForecastStreamProviderProps) {
  const listenersRef = useRef(new Set<(event: ForecastEvent) => void>())
  const resyncRef = useRef(onResyncRequired)
  const shared = useOptionalHydraulicRealtime()
  resyncRef.current = onResyncRequired
  const own = useHydraulicForecastStream({
    networkId,
    token,
    fetchImpl,
    enabled: !shared,
    onEvent: (event) => listenersRef.current.forEach((listener) => listener(event)),
    onResyncRequired: () => resyncRef.current?.(),
  })
  useEffect(() => {
    if (!shared) return undefined
    return shared.subscribeForecast((event) =>
      listenersRef.current.forEach((listener) => listener(event))
    )
  }, [shared])
  useEffect(() => {
    if (!shared) return undefined
    return shared.onResync(() => resyncRef.current?.())
  }, [shared])
  const value = useMemo<ForecastStreamController>(
    () => ({
      connected: shared?.connected ?? own.connected,
      subscribe: (listener) => {
        listenersRef.current.add(listener)
        return () => listenersRef.current.delete(listener)
      },
      onResync: (listener) => {
        if (!shared) return () => undefined
        return shared.onResync(listener)
      },
    }),
    [own.connected, shared]
  )
  return <ForecastStreamContext.Provider value={value}>{children}</ForecastStreamContext.Provider>
}

export interface ForecastSubscriptionOptions {
  networkId: string
  token: string
  onEvent: (event: ForecastEvent) => void
  onResyncRequired: () => void
  fetchImpl?: typeof fetch
}

/** Subscribe to the provider connection; the fallback is only for standalone tests/hosts. */
export function useForecastStreamSubscription(options: ForecastSubscriptionOptions): {
  connected: boolean
} {
  const controller = useContext(ForecastStreamContext)
  const fallback = useHydraulicForecastStream({
    ...(options as ForecastStreamOptions),
    enabled: !controller,
  })
  useEffect(() => {
    if (!controller) return undefined
    const unsubscribeEvent = controller.subscribe(options.onEvent)
    const unsubscribeResync = controller.onResync(options.onResyncRequired)
    return () => {
      unsubscribeEvent()
      unsubscribeResync()
    }
  }, [controller, options.networkId, options.onEvent, options.onResyncRequired])
  return { connected: controller?.connected ?? fallback?.connected ?? false }
}
