import { createContext, useContext, useMemo, type ReactNode } from 'react'
import type { ForecastEvent } from '../forecast/types'
import type { SimulationTaskStreamEvent } from '../tasks/types'
import {
  useHydraulicRealtime,
  type HydraulicRealtimeController,
} from '../realtime/HydraulicRealtimeProvider'

const WorkspaceContext = createContext<HydraulicRealtimeController | null>(null)

export function HydraulicWorkspaceControllerProvider({ children }: { children: ReactNode }) {
  const controller = useHydraulicRealtime()
  const value = useMemo<HydraulicRealtimeController>(() => controller, [controller])
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useHydraulicWorkspaceController(): HydraulicRealtimeController {
  const controller = useContext(WorkspaceContext)
  if (!controller) {
    throw new Error('useHydraulicWorkspaceController must be used inside HydraulicRealtimeProvider')
  }
  return controller
}

export type { ForecastEvent, SimulationTaskStreamEvent }
