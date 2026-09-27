import { useCallback, useEffect, useMemo, useState } from 'react'
import type { NetworkSummary } from '../model/types'
import { listHydraulicNetworks } from './networkApi'

const NETWORK_STORAGE_KEY = 'scn_hydraulic_network_id'

export interface HydraulicNetworksState {
  networks: NetworkSummary[]
  selectedNetwork: NetworkSummary | null
  selectedNetworkId: string | null
  loading: boolean
  error: string | null
  setSelectedNetworkId: (networkId: string) => void
  refresh: () => Promise<void>
}

function readSelectedNetworkId(): string | null {
  try {
    return globalThis.localStorage?.getItem(NETWORK_STORAGE_KEY) ?? null
  } catch {
    return null
  }
}

function writeSelectedNetworkId(networkId: string): void {
  try {
    globalThis.localStorage?.setItem(NETWORK_STORAGE_KEY, networkId)
  } catch {
    // Storage is optional; the in-memory selection still works for this tab.
  }
}

/** Shared network selector state for Dashboard and the simulation workbench. */
export function useHydraulicNetworks(): HydraulicNetworksState {
  const [networks, setNetworks] = useState<NetworkSummary[]>([])
  const [selectedNetworkId, setSelectedNetworkIdState] = useState<string | null>(() =>
    readSelectedNetworkId()
  )
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const page = await listHydraulicNetworks({ status: 'active', limit: 200 })
      setNetworks(page.items)
      setSelectedNetworkIdState((current) => {
        const next =
          current && page.items.some((network) => network.id === current)
            ? current
            : page.items[0]?.id ?? null
        if (next) writeSelectedNetworkId(next)
        return next
      })
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const setSelectedNetworkId = useCallback((networkId: string) => {
    setSelectedNetworkIdState(networkId)
    writeSelectedNetworkId(networkId)
  }, [])

  const selectedNetwork = useMemo(
    () => networks.find((network) => network.id === selectedNetworkId) ?? null,
    [networks, selectedNetworkId]
  )

  return {
    networks,
    selectedNetwork,
    selectedNetworkId,
    loading,
    error,
    setSelectedNetworkId,
    refresh,
  }
}
