import { useMemo } from 'react'
import type { ObservedPredictedLabels, ObservedPredictedPoint } from '../comparison'
import { buildObservedPredictedOption } from './chartOptions'
import { useEChart } from './useEChart'
import { useReducedMotion } from './useReducedMotion'

export interface ObservedPredictedChartProps {
  readonly data: readonly ObservedPredictedPoint[]
  readonly labels: ObservedPredictedLabels
  readonly issueTime?: string | null
  readonly reducedMotion?: boolean
  readonly ariaLabel: string
  readonly className?: string
  readonly height?: number
}

export function ObservedPredictedChart({
  data,
  labels,
  issueTime,
  reducedMotion,
  ariaLabel,
  className,
  height = 360,
}: ObservedPredictedChartProps) {
  const motionReduced = useReducedMotion(reducedMotion)
  const option = useMemo(
    () => buildObservedPredictedOption(data, { issueTime, labels, reducedMotion: motionReduced }),
    [data, issueTime, labels, motionReduced]
  )
  const containerRef = useEChart(option)

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

export default ObservedPredictedChart
