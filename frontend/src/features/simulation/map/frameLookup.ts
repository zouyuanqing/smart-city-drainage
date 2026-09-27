import type { CachedFrame, CachedLayerData, CachedSeries } from './frameCache'
import { encodeFrame, isCachedFrame } from './frameCache'
import type { HydraulicColorMetric, HydraulicLayer, NullableNumber, SimulationFrame } from './types'

type NumericField = 'depth_m' | 'fill_ratio' | 'flow_m3s'
type BooleanField = 'surcharged'

/**
 * `Number(value.toFixed(6))` formats a decimal string and re-parses it on every
 * lookup, and the renderer performs two lookups per drawn feature per frame.
 *
 * For a non-negative value below 1e9 the arithmetic form below is bit-identical:
 * the argument is a Float32Array element, so its exact value carries at most a
 * 24-bit significand, and 1e6 is exactly 2^6 * 15625, so `value * 1e6` needs at
 * most 24 + 14 = 38 bits and is therefore exact in a double (1e9 * 1e6 = 1e15 is
 * also well inside 2^53). `Math.round` of that exact product is the same
 * half-up rounding to six decimals that `toFixed` applies to non-negative values,
 * and the final division by 1e6 is correctly rounded to the very same double the
 * string parse produces. Anything outside the guarded range falls back.
 */
const MAX_EXACT_SIX_DECIMAL_SCALING = 1e9

export function roundSixDecimals(value: number): number {
  if (!(value >= 0) || value >= MAX_EXACT_SIX_DECIMAL_SCALING) return Number(value.toFixed(6))
  return Math.round(value * 1e6) / 1e6
}

function seriesValue(series: CachedSeries | undefined, featureIndex: number): NullableNumber {
  if (!series) return null
  let low = 0
  let high = series.featureIndex.length - 1
  while (low <= high) {
    const middle = (low + high) >>> 1
    const candidate = series.featureIndex[middle]
    if (candidate === featureIndex) {
      return series.flags[middle] === 1 ? null : roundSixDecimals(series.values[middle])
    }
    if (candidate < featureIndex) low = middle + 1
    else high = middle - 1
  }
  return null
}

/** Fast binary-search lookup over worker/cache typed arrays. */
export class FrameLookup {
  readonly frame: SimulationFrame | CachedFrame | null
  private readonly cached: CachedFrame | null
  /**
   * Memoised `(layer, field) -> series` resolution. The renderer performs two
   * lookups per drawn feature per frame against the same handful of series, and
   * re-deriving the canonical layer name and field alias on each one is a
   * repeated string switch in the hottest loop in the layer.
   */
  private lastLayer: HydraulicLayer | null = null
  private lastField: NumericField | BooleanField | null = null
  private lastSeries: CachedSeries | undefined

  constructor(frame: SimulationFrame | CachedFrame | null) {
    this.cached =
      frame === null ? null : isCachedFrame(frame) ? frame : encodeFrame('frame-lookup', frame)
    this.frame = this.cached
  }

  private field(
    layer: HydraulicLayer,
    field: NumericField | BooleanField
  ): CachedSeries | undefined {
    if (this.lastLayer === layer && this.lastField === field) return this.lastSeries
    if (!this.cached) return undefined
    const canonicalLayer: 'nodes' | 'pipes' | 'ponding' =
      layer === 'nodes' ? 'nodes' : layer === 'pipes' ? 'pipes' : 'ponding'
    const series = (this.cached[canonicalLayer] as CachedLayerData)[field]
    this.lastLayer = layer
    this.lastField = field
    this.lastSeries = series
    return series
  }

  value(
    layer: HydraulicLayer,
    featureIndex: number,
    field: HydraulicColorMetric | BooleanField
  ): NullableNumber {
    const canonicalField: NumericField | BooleanField =
      field === 'velocity_mps' ? 'flow_m3s' : field
    return seriesValue(this.field(layer, canonicalField), featureIndex)
  }

  isSurcharged(layer: HydraulicLayer, featureIndex: number): boolean {
    return this.value(layer, featureIndex, 'surcharged') === 1
  }

  has(layer: HydraulicLayer, field: HydraulicColorMetric | BooleanField): boolean {
    const canonicalField: NumericField | BooleanField =
      field === 'velocity_mps' ? 'flow_m3s' : field
    return this.field(layer, canonicalField) !== undefined
  }
}

export function valueFromFeatureProperties(
  feature: { properties?: Record<string, unknown> },
  fields: readonly (NumericField | BooleanField)[]
): NullableNumber {
  for (const field of fields) {
    const value = feature.properties?.[field]
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (field === 'surcharged' && typeof value === 'boolean') return value ? 1 : 0
  }
  return null
}
