/**
 * Rainfall scenario editor domain model.
 *
 * The editor deliberately keeps the same snake_case field names as the
 * hydraulic REST contract.  This makes a validated draft safe to hand to a
 * transport layer without a lossy conversion step.
 */

export const RAINFALL_RETURN_PERIODS = [1, 2, 3, 5, 10, 20, 50, 100, 'custom'] as const
export type RainfallReturnPeriod = (typeof RAINFALL_RETURN_PERIODS)[number]
export type DesignReturnPeriodYears = Exclude<RainfallReturnPeriod, 'custom'>

export const RAINFALL_STEP_MINUTES = [1, 5, 15] as const
export type RainfallStepMinutes = (typeof RAINFALL_STEP_MINUTES)[number]
export type RainfallStepSeconds = 60 | 300 | 900

export const MAX_CUSTOM_DURATION_HOURS = 72
export const MAX_CUSTOM_DURATION_SECONDS = MAX_CUSTOM_DURATION_HOURS * 60 * 60
export const MAX_RAINFALL_SAMPLES = 4_321
export const MAX_RAINFALL_NAME_LENGTH = 255
export const MAX_RAINFALL_ALGORITHM_LENGTH = 128
export const MIN_SIGNED_64 = -(2 ** 63)
export const MAX_SIGNED_64 = 2 ** 63 - 1
export const DEFAULT_RAINFALL_START = '2026-01-01T00:00:00.000Z'
export const DEFAULT_RAINFALL_ALGORITHM = 'synthetic-design-chicago-v1'
export const DEFAULT_RAINFALL_SEED = 20260924
const UTC_RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/

export interface RainfallSample {
  /** UTC RFC 3339 timestamp. */
  time: string
  /** Rainfall intensity in mm/h. */
  rainfall_mm_per_hour: number
  /** Optional stable row index supplied by a backend response. */
  point_index?: number
}

export interface RainfallScenario {
  id?: string
  name: string
  return_period_years: RainfallReturnPeriod
  duration_seconds: number
  step_seconds: RainfallStepSeconds
  source_type: 'synthetic'
  algorithm: string
  random_seed: number
  samples: RainfallSample[]
  version?: number
  created_at?: string
  updated_at?: string
}

export type RainfallScenarioDraft = Omit<RainfallScenario, 'samples'> & {
  samples: RainfallSample[]
}

export interface GenerateRainfallSamplesOptions {
  returnPeriodYears: RainfallReturnPeriod
  durationSeconds: number
  stepSeconds: RainfallStepSeconds
  randomSeed: number
  startAt?: string
  algorithm?: string
}

export function isDesignReturnPeriod(value: unknown): value is DesignReturnPeriodYears {
  return (
    typeof value === 'number' && RAINFALL_RETURN_PERIODS.includes(value as RainfallReturnPeriod)
  )
}

export function isRainfallReturnPeriod(value: unknown): value is RainfallReturnPeriod {
  return RAINFALL_RETURN_PERIODS.includes(value as RainfallReturnPeriod)
}

export function isRainfallStepSeconds(value: unknown): value is RainfallStepSeconds {
  return value === 60 || value === 300 || value === 900
}

export function stepSecondsFromMinutes(minutes: number): RainfallStepSeconds {
  const seconds = minutes * 60
  if (!isRainfallStepSeconds(seconds)) {
    throw new Error(`Unsupported rainfall step: ${minutes} minutes`)
  }
  return seconds
}

export function stepMinutesFromSeconds(seconds: number): RainfallStepMinutes {
  const minutes = seconds / 60
  if (!RAINFALL_STEP_MINUTES.includes(minutes as RainfallStepMinutes)) {
    throw new Error(`Unsupported rainfall step: ${seconds} seconds`)
  }
  return minutes as RainfallStepMinutes
}

/**
 * The number of samples includes both the start and end of the event.
 * This is intentionally based on floor, as required by the frozen contract.
 */
export function getSampleCount(durationSeconds: number, stepSeconds: number): number {
  if (
    !Number.isSafeInteger(durationSeconds) ||
    !Number.isSafeInteger(stepSeconds) ||
    durationSeconds < 0 ||
    stepSeconds <= 0 ||
    durationSeconds > MAX_CUSTOM_DURATION_SECONDS
  ) {
    return 0
  }
  const count = Math.floor(durationSeconds / stepSeconds) + 1
  return Number.isSafeInteger(count) && count <= MAX_RAINFALL_SAMPLES ? count : 0
}

export function getExpectedSampleCount(durationSeconds: number, stepSeconds: number): number {
  return getSampleCount(durationSeconds, stepSeconds)
}

function assertPositiveInteger(value: number, field: string): void {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`${field} must be a positive safe integer`)
  }
}

export function isValidRainfallSeed(value: unknown): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= MIN_SIGNED_64 &&
    value <= MAX_SIGNED_64
  )
}

export function isValidRainfallTimestamp(value: unknown): value is string {
  return (
    typeof value === 'string' && UTC_RFC3339_RE.test(value) && Number.isFinite(Date.parse(value))
  )
}

function seededUnitNoise(seed: number, index: number, period: number | string): number {
  // A small, deterministic integer hash avoids Math.random() in the editor.
  // The result is in [0, 1] and is stable across browsers and reloads.
  let value = (seed ^ Math.imul(index + 1, 0x9e3779b1)) >>> 0
  const periodSeed = typeof period === 'number' ? Math.trunc(period) : 0x5f3759df
  value = (value ^ Math.imul(periodSeed, 0x85ebca6b)) >>> 0
  value ^= value >>> 16
  value = Math.imul(value, 0x7feb352d) >>> 0
  value ^= value >>> 15
  value = Math.imul(value, 0x846ca68b) >>> 0
  value ^= value >>> 16
  return (value >>> 0) / 0x100000000
}

function roundIntensity(value: number): number {
  // Four decimal places keeps the payload stable without hiding meaningful
  // small rainfall values in the chart/table round trip.
  return Math.round(value * 10_000) / 10_000
}

function startTimestamp(startAt: string | undefined): number {
  if (startAt !== undefined) {
    if (!isValidRainfallTimestamp(startAt)) {
      throw new Error('startAt must be a UTC RFC3339 timestamp')
    }
    return Date.parse(startAt)
  }
  return Date.parse(DEFAULT_RAINFALL_START)
}

function returnPeriodFactor(returnPeriodYears: RainfallReturnPeriod): number {
  if (returnPeriodYears === 'custom') return 1
  // A smooth, monotonic design-storm factor.  The exact curve is a UI fixture;
  // the backend remains authoritative for the submitted design rainfall.
  return 0.55 + Math.sqrt(returnPeriodYears / 100) * 2.15
}

/**
 * Generate a deterministic synthetic rainfall series for a quick scenario.
 * The same options always produce byte-for-byte equivalent values, which is
 * required for reproducible simulation inputs.
 */
export function generateRainfallSamples({
  returnPeriodYears,
  durationSeconds,
  stepSeconds,
  randomSeed,
  startAt,
}: GenerateRainfallSamplesOptions): RainfallSample[] {
  assertPositiveInteger(durationSeconds, 'durationSeconds')
  if (durationSeconds > MAX_CUSTOM_DURATION_SECONDS) {
    throw new Error('durationSeconds cannot exceed 72 hours')
  }
  if (!isRainfallStepSeconds(stepSeconds)) {
    throw new Error('stepSeconds must be one of 60, 300, or 900')
  }
  if (!isRainfallReturnPeriod(returnPeriodYears)) {
    throw new Error(`Unsupported return period: ${String(returnPeriodYears)}`)
  }
  if (!isValidRainfallSeed(randomSeed)) {
    throw new Error('randomSeed must be a signed 64-bit safe integer')
  }

  const count = getSampleCount(durationSeconds, stepSeconds)
  if (count === 0 || count > MAX_RAINFALL_SAMPLES) {
    throw new Error('rainfall sample allocation exceeds the browser safety budget')
  }
  const firstTimestamp = startTimestamp(startAt)
  const factor = returnPeriodFactor(returnPeriodYears)

  return Array.from({ length: count }, (_, index) => {
    const progress = count <= 1 ? 0 : index / (count - 1)
    const peak = Math.exp(-Math.pow((progress - 0.42) / 0.26, 2))
    const secondary = 0.18 * Math.exp(-Math.pow((progress - 0.72) / 0.12, 2))
    const noise = 0.78 + seededUnitNoise(Math.trunc(randomSeed), index, returnPeriodYears) * 0.34
    const intensity = Math.max(0, (peak + secondary) * factor * noise)
    const time = new Date(firstTimestamp + index * stepSeconds * 1_000).toISOString()

    return {
      time,
      rainfall_mm_per_hour: roundIntensity(intensity),
      point_index: index,
    }
  })
}

export function createDefaultRainfallScenario(
  overrides: Partial<RainfallScenario> = {}
): RainfallScenario {
  const safeBase: RainfallScenario = {
    name: '10-year design storm',
    return_period_years: 10,
    duration_seconds: 24 * 3_600,
    step_seconds: 300,
    source_type: 'synthetic',
    algorithm: DEFAULT_RAINFALL_ALGORITHM,
    random_seed: DEFAULT_RAINFALL_SEED,
    samples: [],
  }
  const merged = { ...safeBase, ...overrides }
  if (overrides.algorithm === undefined) {
    merged.algorithm =
      merged.return_period_years === 'custom' ? 'synthetic-custom-v1' : DEFAULT_RAINFALL_ALGORITHM
  }
  if (!Array.isArray(merged.samples) || merged.samples.length === 0) {
    try {
      merged.samples = generateRainfallSamples({
        returnPeriodYears: merged.return_period_years,
        durationSeconds: merged.duration_seconds,
        stepSeconds: merged.step_seconds,
        randomSeed: merged.random_seed,
      })
    } catch {
      // Keep the invalid draft metadata intact; validation must be able to
      // report the original problem instead of throwing during initialization.
      merged.samples = generateRainfallSamples({
        returnPeriodYears: 10,
        durationSeconds: safeBase.duration_seconds,
        stepSeconds: safeBase.step_seconds,
        randomSeed: safeBase.random_seed,
      })
    }
  }
  return merged
}

export function createCustomRainfallScenario(
  overrides: Partial<RainfallScenario> = {}
): RainfallScenario {
  return createDefaultRainfallScenario({
    name: 'Custom rainfall',
    return_period_years: 'custom',
    algorithm: 'synthetic-custom-v1',
    ...overrides,
  })
}

/** Aliases make the quick-scenario vocabulary explicit for feature consumers. */
export const QUICK_RETURN_PERIODS = RAINFALL_RETURN_PERIODS
export const RETURN_PERIOD_OPTIONS = RAINFALL_RETURN_PERIODS

export function createQuickRainfallScenario(
  returnPeriodYears: Exclude<RainfallReturnPeriod, 'custom'>,
  durationSeconds = 24 * 3_600,
  stepSeconds: RainfallStepSeconds = 300,
  randomSeed = DEFAULT_RAINFALL_SEED
): RainfallScenario {
  return {
    name: `${returnPeriodYears}-year design storm`,
    return_period_years: returnPeriodYears,
    duration_seconds: durationSeconds,
    step_seconds: stepSeconds,
    source_type: 'synthetic',
    algorithm: DEFAULT_RAINFALL_ALGORITHM,
    random_seed: randomSeed,
    samples: generateRainfallSamples({
      returnPeriodYears,
      durationSeconds,
      stepSeconds,
      randomSeed,
    }),
  }
}

export function cloneScenario(scenario: RainfallScenario): RainfallScenario {
  return {
    ...scenario,
    samples: scenario.samples.map((sample) => ({ ...sample })),
  }
}

/** Convert a table row into the canonical API sample shape. */
export function normalizeRainfallSample(
  sample: Partial<RainfallSample> & { intensity_mm_per_hour?: number; intensityMmPerHour?: number }
): RainfallSample {
  const intensity =
    sample.rainfall_mm_per_hour ?? sample.intensity_mm_per_hour ?? sample.intensityMmPerHour
  return {
    time: sample.time ?? '',
    rainfall_mm_per_hour: typeof intensity === 'number' ? intensity : Number.NaN,
    ...(sample.point_index === undefined ? {} : { point_index: sample.point_index }),
  }
}

export function cloneRainfallSamples(samples: RainfallSample[]): RainfallSample[] {
  return samples.map((sample) => ({ ...sample }))
}
