import type {
  BoundedMapFeature,
  HydraulicColorMetric,
  HydraulicLayer,
  MapFeature,
  MapFeatureProperties,
} from './types'
import { FrameLookup, valueFromFeatureProperties } from './frameLookup'

export interface ColorBand {
  id: string
  min: number
  max: number
  color: string
  label: string
  /** A non-colour cue required by the map contract. */
  dash?: readonly number[]
  symbol?: 'circle' | 'line' | 'polygon' | 'triangle'
}

export const FILL_RATIO_BANDS: readonly ColorBand[] = Object.freeze([
  { id: 'dry', min: 0, max: 0.25, color: '#2b8cbe', label: '低充满度', symbol: 'line' },
  { id: 'low', min: 0.25, max: 0.5, color: '#41b6c4', label: '中低充满度', symbol: 'line' },
  {
    id: 'medium',
    min: 0.5,
    max: 0.75,
    color: '#fdae61',
    label: '中高充满度',
    dash: [6, 3],
    symbol: 'line',
  },
  {
    id: 'high',
    min: 0.75,
    max: 1,
    color: '#f46d43',
    label: '高充满度',
    dash: [3, 3],
    symbol: 'triangle',
  },
  {
    id: 'surcharge',
    min: 1,
    max: Number.POSITIVE_INFINITY,
    color: '#d73027',
    label: '满流/超载',
    dash: [2, 2],
    symbol: 'triangle',
  },
])

export const VELOCITY_BANDS: readonly ColorBand[] = Object.freeze([
  { id: 'slow', min: 0, max: 0.5, color: '#4575b4', label: '低速', symbol: 'line' },
  { id: 'steady', min: 0.5, max: 1.5, color: '#74add1', label: '稳定流速', symbol: 'line' },
  {
    id: 'fast',
    min: 1.5,
    max: 3,
    color: '#fee090',
    label: '较高流速',
    dash: [6, 3],
    symbol: 'line',
  },
  {
    id: 'surge',
    min: 3,
    max: Number.POSITIVE_INFINITY,
    color: '#d73027',
    label: '急流',
    dash: [2, 2],
    symbol: 'triangle',
  },
])

export const DEPTH_BANDS: readonly ColorBand[] = Object.freeze([
  { id: 'surface-dry', min: 0, max: 0.02, color: '#f7fbff', label: '无积水', symbol: 'polygon' },
  {
    id: 'surface-low',
    min: 0.02,
    max: 0.1,
    color: '#c6dbef',
    label: '轻微积水',
    symbol: 'polygon',
  },
  {
    id: 'surface-medium',
    min: 0.1,
    max: 0.3,
    color: '#6baed6',
    label: '中度积水',
    symbol: 'polygon',
  },
  {
    id: 'surface-high',
    min: 0.3,
    max: 0.6,
    color: '#2171b5',
    label: '较深积水',
    symbol: 'polygon',
  },
  {
    id: 'surface-critical',
    min: 0.6,
    max: Number.POSITIVE_INFINITY,
    color: '#cb181d',
    label: '危险积水',
    symbol: 'triangle',
  },
])

export const COLOR_BANDS: Readonly<Record<HydraulicColorMetric, readonly ColorBand[]>> =
  Object.freeze({
    fill_ratio: FILL_RATIO_BANDS,
    velocity_mps: VELOCITY_BANDS,
    depth_m: DEPTH_BANDS,
  })

export const NEUTRAL_BAND: ColorBand = Object.freeze({
  id: 'no-value',
  min: Number.NEGATIVE_INFINITY,
  max: Number.POSITIVE_INFINITY,
  color: '#7f8c8d',
  label: '无数据',
  symbol: 'line',
})

export interface FeatureMapStyle {
  metric: HydraulicColorMetric
  value: number | null
  band: ColorBand
  color: string
  fillColor: string
  strokeColor: string
  lineWidth: number
  radius: number
  dash: readonly number[]
  symbol: ColorBand['symbol']
  critical: boolean
  phase: 'predicted' | 'observed' | null
  label: string
  accessibleLabel: string
}

export interface FeatureStyleLabels {
  noData: string
  predicted: string
  observed: string
}

/** Shared dash patterns: the declared `readonly` type forbids consumer mutation. */
const PREDICTED_DASH: readonly number[] = Object.freeze([7, 4])
const NO_DASH: readonly number[] = Object.freeze([])

const DEFAULT_STYLE_LABELS: FeatureStyleLabels = Object.freeze({
  noData: 'No data',
  predicted: 'Predicted',
  observed: 'Observed',
})

type NumericField = 'depth_m' | 'fill_ratio' | 'flow_m3s'

export interface FeatureStyleOptions {
  defaultMetric?: HydraulicColorMetric
  metric?: HydraulicColorMetric
  selected?: boolean
  critical?: boolean
  labels?: FeatureStyleLabels
}

function normalizeMetricValue(value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return null
  return value
}

/**
 * Band tables are frozen module constants, so their ascending order can be
 * computed once per table instead of re-allocating and re-sorting a copy on
 * every feature style lookup (the render loop calls this once per drawn
 * feature per frame). The returned arrays are never mutated.
 */
const ORDERED_BANDS = new WeakMap<readonly ColorBand[], readonly ColorBand[]>()

function orderedBands(bands: readonly ColorBand[]): readonly ColorBand[] {
  const cached = ORDERED_BANDS.get(bands)
  if (cached) return cached
  const ordered = [...bands].sort((left, right) => left.min - right.min)
  ORDERED_BANDS.set(bands, ordered)
  return ordered
}

export function getColorBand(
  metric: HydraulicColorMetric,
  value: number | null | undefined,
  bands: readonly ColorBand[] = COLOR_BANDS[metric]
): ColorBand {
  if (typeof value !== 'number' || !Number.isFinite(value)) return NEUTRAL_BAND
  const ordered = orderedBands(bands)
  for (let index = 0; index < ordered.length; index += 1) {
    const band = ordered[index]
    const isLast = index === ordered.length - 1
    if (value >= band.min && (isLast || value < band.max)) return band
  }
  return ordered[ordered.length - 1] ?? NEUTRAL_BAND
}

export function colorForValue(
  metric: HydraulicColorMetric,
  value: number | null | undefined,
  bands: readonly ColorBand[] = COLOR_BANDS[metric]
): string {
  return getColorBand(metric, value, bands).color
}

export const fillRatioToColor = (value: number | null | undefined): string =>
  colorForValue('fill_ratio', value)
export const depthToColor = (value: number | null | undefined): string =>
  colorForValue('depth_m', value)
export const velocityToColor = (value: number | null | undefined): string =>
  colorForValue('velocity_mps', value)

/**
 * Property fallbacks, one frozen array per metric.
 *
 * The render loop resolves one of these per drawn feature per frame (hundreds at
 * the contracted scale) and the lookup never mutates the array, so allocating a
 * fresh `[metric]` on every call was pure per-feature garbage.
 */
const METRIC_FIELDS: Readonly<Record<HydraulicColorMetric, readonly NumericField[]>> =
  Object.freeze({
    fill_ratio: Object.freeze(['fill_ratio'] as const),
    velocity_mps: Object.freeze(['flow_m3s'] as const),
    depth_m: Object.freeze(['depth_m'] as const),
  })

export function metricValueForFeature(
  feature: BoundedMapFeature,
  layer: HydraulicLayer,
  lookup: FrameLookup,
  metric: HydraulicColorMetric
): number | null {
  const value = lookup.value(layer, feature.feature_index, metric)
  if (value !== null) return value
  return valueFromFeatureProperties(feature, METRIC_FIELDS[metric])
}

function featureLabel(feature: MapFeature, properties: MapFeatureProperties | undefined): string {
  const candidate = properties?.name ?? properties?.stable_key ?? feature.id
  return typeof candidate === 'string' && candidate.length > 0 ? candidate : feature.id
}

function phaseForFeature(feature: MapFeature): 'predicted' | 'observed' | null {
  const propertyPhase = feature.properties?.phase
  return propertyPhase === 'predicted' || propertyPhase === 'observed' ? propertyPhase : null
}

/**
 * Feature style value object.
 *
 * Every field the renderer consumes is a plain eagerly-assigned property, so a
 * stable hidden class keeps construction cheap. `accessibleLabel` is an
 * assistive-technology string that the render loop never reads (one style is
 * built per visible feature per frame), so it is a prototype getter that formats
 * at most once per instance instead of running a `toFixed` + `filter` + `join`
 * for every feature of every frame.
 */
class FeatureStyle implements FeatureMapStyle {
  readonly metric: HydraulicColorMetric
  readonly value: number | null
  readonly band: ColorBand
  readonly color: string
  readonly fillColor: string
  readonly strokeColor: string
  readonly lineWidth: number
  readonly radius: number
  readonly dash: readonly number[]
  readonly symbol: ColorBand['symbol']
  readonly critical: boolean
  readonly phase: 'predicted' | 'observed' | null
  readonly label: string
  private readonly styleLabels: FeatureStyleLabels
  private readonly surcharged: boolean
  private accessibleLabelText: string | null = null

  constructor(fields: {
    metric: HydraulicColorMetric
    value: number | null
    band: ColorBand
    color: string
    fillColor: string
    strokeColor: string
    lineWidth: number
    radius: number
    dash: readonly number[]
    symbol: ColorBand['symbol']
    critical: boolean
    phase: 'predicted' | 'observed' | null
    label: string
    styleLabels: FeatureStyleLabels
    surcharged: boolean
  }) {
    this.metric = fields.metric
    this.value = fields.value
    this.band = fields.band
    this.color = fields.color
    this.fillColor = fields.fillColor
    this.strokeColor = fields.strokeColor
    this.lineWidth = fields.lineWidth
    this.radius = fields.radius
    this.dash = fields.dash
    this.symbol = fields.symbol
    this.critical = fields.critical
    this.phase = fields.phase
    this.label = fields.label
    this.styleLabels = fields.styleLabels
    this.surcharged = fields.surcharged
  }

  get accessibleLabel(): string {
    if (this.accessibleLabelText === null) {
      const { value, metric } = this
      const suffix =
        value === null
          ? this.styleLabels.noData
          : `${value.toFixed(2)}${metric === 'fill_ratio' ? '' : metric === 'velocity_mps' ? ' m/s' : ' m'}`
      const phaseLabel =
        this.phase === 'predicted'
          ? this.styleLabels.predicted
          : this.phase === 'observed'
            ? this.styleLabels.observed
            : null
      this.accessibleLabelText = [
        this.label,
        suffix,
        this.surcharged ? 'surcharge' : null,
        phaseLabel,
      ]
        .filter(Boolean)
        .join(', ')
    }
    return this.accessibleLabelText
  }
}

export function getFeatureStyle(
  feature: BoundedMapFeature,
  layer: HydraulicLayer,
  lookup: FrameLookup,
  options: FeatureStyleOptions = {}
): FeatureMapStyle {
  const metric =
    options.metric ?? options.defaultMetric ?? (layer === 'pipes' ? 'fill_ratio' : 'depth_m')
  const rawValue = metricValueForFeature(feature, layer, lookup, metric)
  const value = normalizeMetricValue(rawValue)
  const surcharged = layer === 'pipes' && lookup.isSurcharged(layer, feature.feature_index)
  const band = surcharged ? getColorBand('fill_ratio', 1.01) : getColorBand(metric, value)
  const critical = options.critical ?? feature.properties?.critical === true
  const phase = phaseForFeature(feature)
  const symbol = layer === 'nodes' ? 'circle' : layer === 'pipes' ? 'line' : 'polygon'
  const dash = phase === 'predicted' ? PREDICTED_DASH : band.dash ?? NO_DASH
  return new FeatureStyle({
    metric,
    value,
    band,
    color: band.color,
    fillColor: band.color,
    strokeColor: critical ? '#ffffff' : band.color,
    lineWidth: options.selected ? 3 : critical ? 2.5 : surcharged ? 3 : 1.5,
    radius: options.selected ? 7 : critical ? 5 : 3.5,
    dash,
    symbol,
    critical,
    phase,
    label: featureLabel(feature, feature.properties),
    styleLabels: options.labels ?? DEFAULT_STYLE_LABELS,
    surcharged,
  })
}

export function formatColorMetricValue(
  metric: HydraulicColorMetric,
  value: number | null | undefined,
  noDataLabel = 'No data'
): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return noDataLabel
  if (metric === 'fill_ratio') return `${(value * 100).toFixed(1)}%`
  if (metric === 'velocity_mps') return `${value.toFixed(2)} m/s`
  return `${value.toFixed(2)} m`
}
