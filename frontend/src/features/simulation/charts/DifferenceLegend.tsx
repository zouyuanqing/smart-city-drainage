import { useMemo } from 'react'
import {
  differenceScaleCss,
  formatMetricValue,
  maxAbsoluteDelta,
  type AlignedSimulationPoint,
} from '../comparison'

export interface DifferenceLegendLabels {
  readonly negative: string
  readonly zero: string
  readonly positive: string
}

export interface DifferenceLegendProps {
  readonly data: readonly AlignedSimulationPoint[]
  readonly labels: DifferenceLegendLabels
  readonly ariaLabel: string
  readonly locale?: string
}

export function DifferenceLegend({
  data,
  labels,
  ariaLabel,
  locale = 'en-US',
}: DifferenceLegendProps) {
  const domain = useMemo(() => {
    const value = maxAbsoluteDelta(data)
    return Object.is(value, -0) ? 0 : value
  }, [data])
  const gradient = useMemo(() => differenceScaleCss(domain), [domain])

  return (
    <div aria-label={ariaLabel} role="img" style={{ display: 'grid', gap: 6, minWidth: 220 }}>
      <div
        aria-hidden="true"
        style={{ background: gradient, borderRadius: 999, height: 10, width: '100%' }}
      />
      <div
        style={{
          color: '#475569',
          display: 'flex',
          fontSize: 12,
          justifyContent: 'space-between',
        }}
      >
        <span>
          {labels.negative} {formatMetricValue(-domain, locale)}
        </span>
        <span>{labels.zero}</span>
        <span>
          {labels.positive} {formatMetricValue(domain, locale)}
        </span>
      </div>
    </div>
  )
}

export default DifferenceLegend
