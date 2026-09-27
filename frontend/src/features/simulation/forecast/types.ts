export type ForecastDataState = 'ready' | 'stale' | 'not_ready'
export type ForecastPhase = 'predicted' | 'observed'
export type ForecastAlertStatus = 'open' | 'acknowledged' | 'resolved' | 'expired'

export interface ForecastAlert {
  id: string
  type: 'flood_forecast' | 'pipe_surcharge'
  status: ForecastAlertStatus
  phase: ForecastPhase
  target: { type: 'node' | 'pipe_segment'; id: string }
  expected_at: string
  lead_time_minutes: number
  measurements: Record<string, number | null>
  confidence: number
  confidence_method: string
  risk_score: number
  version: number
}

export interface ForecastSnapshot {
  schema_version: 1
  data_state: ForecastDataState
  network_id: string
  nowcast_run: {
    id: string
    scheduled_for: string
    forecast_origin_at: string
    issued_at: string
    published_at: string
    simulation_id: string
  } | null
  alerts: ForecastAlert[]
  age_seconds: number
  server_time: string
}

export interface ForecastRiskItem extends ForecastAlert {
  score_components: { confidence: number; severity: number; urgency: number; impact: number }
  policy_version: string
}

export interface ForecastRiskRankingResponse {
  schema_version: 1
  data_state: ForecastDataState
  network_id: string
  nowcast_run_id: string | null
  risk_policy_version: string
  items: ForecastRiskItem[]
  server_time: string
}

export type ForecastEventType =
  | 'alert.created'
  | 'alert.updated'
  | 'alert.escalated'
  | 'alert.observed'
  | 'alert.resolved'
  | 'alert.expired'

export interface ForecastEvent {
  schema_version: 1
  event_id: string
  sequence: number
  event_type: ForecastEventType
  occurred_at: string
  network_id: string
  nowcast_run_id: string
  simulation_id: string
  alert: ForecastAlert
}
