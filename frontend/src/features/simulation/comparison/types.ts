export interface SimulationSeriesPoint {
  readonly timestamp: string
  readonly value: number | null
}

export interface AlignedSimulationPoint {
  readonly timestamp: string
  readonly timestamp_ms: number
  readonly baseline: number | null
  readonly candidate: number | null
  readonly delta: number | null
}

export type ComparisonViewMode = 'overlay' | 'split' | 'difference'
export type BetterDirection = 'higher' | 'lower' | 'target'

export interface ComparisonMetric {
  readonly key: string
  readonly label?: string | null
  readonly baseline: number | null
  readonly candidate: number | null
  readonly absolute_delta: number | null
  readonly relative_delta: number | null
  readonly unit: string | null
  readonly better_direction: BetterDirection | null
}

export interface ObservedPredictedPoint {
  readonly timestamp: string
  readonly observed: number | null
  readonly predicted: number | null
  readonly lower_bound?: number | null
  readonly upper_bound?: number | null
}

export interface SimulationComparisonResponse {
  readonly schema_version: 'simulation-comparison-v2'
  readonly compatible: boolean
  readonly network_revision_id: string
  readonly target_manifest_id: string
  readonly target_manifest_sha256: string
  readonly frame_grid: {
    readonly start_at: string
    readonly end_at: string
    readonly interval_seconds: number
    readonly frame_count: number
  }
  readonly baseline: {
    readonly simulation_id: string
    readonly published_attempt_id: string
    readonly published_attempt_no: number
    readonly label?: string | null
  }
  readonly candidate: {
    readonly simulation_id: string
    readonly published_attempt_id: string
    readonly published_attempt_no: number
    readonly label?: string | null
  }
  readonly metrics: readonly ComparisonMetric[]
  readonly reason?: string | null
}

export interface ComparisonMetricLabels {
  readonly metric: string
  readonly baseline: string
  readonly candidate: string
  readonly absoluteDelta: string
  readonly relativeDelta: string
  readonly betterDirection: string
  readonly betterDirections: {
    readonly higher: string
    readonly lower: string
    readonly target: string
  }
  readonly unit: string
  readonly notAvailable: string
}

export interface SimulationComparisonLabels {
  readonly baseline: string
  readonly candidate: string
  readonly difference: string
}

export interface ObservedPredictedLabels {
  readonly observed: string
  readonly predicted: string
  readonly uncertainty: string
  readonly issuedAt: string
}
