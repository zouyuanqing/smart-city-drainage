import { useMemo } from 'react'
import type {
  AlignedSimulationPoint,
  ComparisonViewMode,
  SimulationComparisonLabels,
} from '../comparison'
import { buildSimulationComparisonOption } from './chartOptions'
import { useEChart } from './useEChart'
import { useReducedMotion } from './useReducedMotion'

export interface SimulationComparisonChartProps {
  readonly data: readonly AlignedSimulationPoint[]
  readonly labels: SimulationComparisonLabels
  readonly mode?: ComparisonViewMode
  readonly comparison?: {
    readonly compatible: boolean
    readonly reason?: string | null
  }
  readonly reducedMotion?: boolean
  readonly ariaLabel: string
  readonly className?: string
  readonly height?: number
}

export function SimulationComparisonChart({
  data,
  labels,
  mode = 'overlay',
  comparison,
  reducedMotion,
  ariaLabel,
  className,
  height = 360,
}: SimulationComparisonChartProps) {
  const motionReduced = useReducedMotion(reducedMotion)
  const option = useMemo(
    () => buildSimulationComparisonOption(data, { labels, mode, reducedMotion: motionReduced }),
    [data, labels, mode, motionReduced]
  )
  const containerRef = useEChart(option)
  const incompatible = comparison?.compatible === false

  if (incompatible && mode === 'difference') {
    return (
      <div role="alert" data-testid="comparison-incompatible" className={className}>
        {comparison?.reason ?? 'Comparison is incompatible; difference mode is disabled.'}
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      aria-label={ariaLabel}
      className={className}
      role="img"
      style={{ height, minHeight: 240, width: '100%' }}
    />
  )
}

export default SimulationComparisonChart
