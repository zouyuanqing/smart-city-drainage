export { TimeAlignmentError, alignSimulationSeries } from './timeAlignment'
export {
  DIFFERENCE_SCALE,
  differenceColorFor,
  differenceScaleCss,
  maxAbsoluteDelta,
} from './differenceScale'
export { formatMetricValue, formatRelativeDelta, formatSignedMetricValue } from './metrics'
export type {
  AlignedSimulationPoint,
  BetterDirection,
  ComparisonMetric,
  ComparisonMetricLabels,
  ComparisonViewMode,
  ObservedPredictedLabels,
  ObservedPredictedPoint,
  SimulationComparisonLabels,
  SimulationComparisonResponse,
  SimulationSeriesPoint,
} from './types'
