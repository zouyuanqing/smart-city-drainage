import type { BetterDirection, ComparisonMetric, ComparisonMetricLabels } from '../comparison'
import { formatMetricValue, formatRelativeDelta, formatSignedMetricValue } from '../comparison'

function betterDirectionLabel(
  direction: BetterDirection | null,
  labels: ComparisonMetricLabels
): string {
  if (direction === null) return labels.notAvailable
  return labels.betterDirections[direction]
}

export interface ComparisonMetricsTableProps {
  readonly metrics: readonly ComparisonMetric[]
  readonly labels: ComparisonMetricLabels
  readonly locale?: string
  readonly ariaLabel: string
}

export function ComparisonMetricsTable({
  metrics,
  labels,
  locale = 'en-US',
  ariaLabel,
}: ComparisonMetricsTableProps) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table
        aria-label={ariaLabel}
        data-testid="comparison-metrics"
        style={{ borderCollapse: 'collapse', width: '100%' }}
      >
        <thead>
          <tr>
            <th scope="col">{labels.metric}</th>
            <th scope="col">{labels.baseline}</th>
            <th scope="col">{labels.candidate}</th>
            <th scope="col">{labels.absoluteDelta}</th>
            <th scope="col">{labels.relativeDelta}</th>
            <th scope="col">{labels.betterDirection}</th>
            <th scope="col">{labels.unit}</th>
          </tr>
        </thead>
        <tbody>
          {metrics.length === 0 ? (
            <tr>
              <td colSpan={7}>{labels.notAvailable}</td>
            </tr>
          ) : (
            metrics.map((metric) => (
              <tr key={metric.key}>
                <th scope="row">{metric.label ?? metric.key}</th>
                <td>{formatMetricValue(metric.baseline, locale, 6, labels.notAvailable)}</td>
                <td>{formatMetricValue(metric.candidate, locale, 6, labels.notAvailable)}</td>
                <td>
                  {formatSignedMetricValue(metric.absolute_delta, locale, 6, labels.notAvailable)}
                </td>
                <td>{formatRelativeDelta(metric.relative_delta, locale, labels.notAvailable)}</td>
                <td>{betterDirectionLabel(metric.better_direction, labels)}</td>
                <td>{metric.unit ?? labels.notAvailable}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}

export default ComparisonMetricsTable
