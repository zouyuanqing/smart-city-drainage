import {
  MAX_CUSTOM_DURATION_SECONDS,
  MAX_RAINFALL_ALGORITHM_LENGTH,
  MAX_RAINFALL_NAME_LENGTH,
  RAINFALL_RETURN_PERIODS,
  isRainfallReturnPeriod,
  isRainfallStepSeconds,
  isValidRainfallSeed,
  isValidRainfallTimestamp,
  getExpectedSampleCount,
  type RainfallSample,
  type RainfallScenario,
} from './model'

export type RainfallValidationCode =
  | 'NAME_REQUIRED'
  | 'NAME_TOO_LONG'
  | 'ALGORITHM_INVALID'
  | 'RETURN_PERIOD_INVALID'
  | 'STEP_INVALID'
  | 'DURATION_INVALID'
  | 'DURATION_TOO_LONG'
  | 'SOURCE_TYPE_INVALID'
  | 'SEED_INVALID'
  | 'SAMPLES_REQUIRED'
  | 'SAMPLE_COUNT_MISMATCH'
  | 'SAMPLE_TIME_INVALID'
  | 'SAMPLE_TIME_NOT_INCREASING'
  | 'SAMPLE_INTERVAL_MISMATCH'
  | 'SAMPLE_INTENSITY_INVALID'

export interface RainfallValidationIssue {
  code: RainfallValidationCode
  field: string
  message: string
  severity: 'error' | 'warning'
  index?: number
}

export interface RainfallValidationResult {
  valid: boolean
  /** Alias kept for callers that use the common `isValid` convention. */
  isValid: boolean
  issues: RainfallValidationIssue[]
  errors: RainfallValidationIssue[]
  warnings: RainfallValidationIssue[]
  expectedSampleCount: number
  actualSampleCount: number
}

export interface RainfallSamplesValidationInput {
  samples: RainfallSample[]
  durationSeconds: number
  stepSeconds: number
  /** Keep the grid check enabled by default for submitted scenarios. */
  enforceGrid?: boolean
}

function issue(
  code: RainfallValidationCode,
  field: string,
  message: string,
  index?: number
): RainfallValidationIssue {
  return {
    code,
    field,
    message,
    severity: 'error',
    ...(index === undefined ? {} : { index }),
  }
}

function makeResult(
  issues: RainfallValidationIssue[],
  expected: number,
  actual: number
): RainfallValidationResult {
  const errors = issues.filter((item) => item.severity === 'error')
  const warnings = issues.filter((item) => item.severity === 'warning')
  return {
    valid: errors.length === 0,
    isValid: errors.length === 0,
    issues,
    errors,
    warnings,
    expectedSampleCount: expected,
    actualSampleCount: actual,
  }
}

function validDuration(durationSeconds: unknown): durationSeconds is number {
  return (
    typeof durationSeconds === 'number' &&
    Number.isFinite(durationSeconds) &&
    Number.isInteger(durationSeconds) &&
    durationSeconds > 0
  )
}

function validSeed(seed: unknown): seed is number {
  return isValidRainfallSeed(seed)
}

/**
 * Validate the time grid and values of a rainfall table independently from
 * the metadata around it.  Keeping this function pure makes it useful for a
 * row-level editor and for tests without mounting React.
 */
export function validateRainfallSamples(
  input: RainfallSamplesValidationInput
): RainfallValidationResult
export function validateRainfallSamples(
  samples: RainfallSample[],
  durationSeconds: number,
  stepSeconds: number,
  enforceGrid?: boolean
): RainfallValidationResult
export function validateRainfallSamples(
  inputOrSamples: RainfallSamplesValidationInput | RainfallSample[],
  durationSecondsArg?: number,
  stepSecondsArg?: number,
  enforceGridArg = true
): RainfallValidationResult {
  const input: RainfallSamplesValidationInput = Array.isArray(inputOrSamples)
    ? {
        samples: inputOrSamples,
        durationSeconds: durationSecondsArg ?? Number.NaN,
        stepSeconds: stepSecondsArg ?? Number.NaN,
        enforceGrid: enforceGridArg,
      }
    : inputOrSamples
  const { samples, durationSeconds, stepSeconds, enforceGrid = true } = input
  const actual = Array.isArray(samples) ? samples.length : 0
  const expected = getExpectedSampleCount(durationSeconds, stepSeconds)
  const issues: RainfallValidationIssue[] = []

  if (!isRainfallStepSeconds(stepSeconds)) {
    issues.push(issue('STEP_INVALID', 'step_seconds', 'Rainfall step must be 1, 5, or 15 minutes.'))
  }
  if (!validDuration(durationSeconds)) {
    issues.push(
      issue(
        'DURATION_INVALID',
        'duration_seconds',
        'Rainfall duration must be a positive whole number of seconds.'
      )
    )
  } else if (durationSeconds > MAX_CUSTOM_DURATION_SECONDS) {
    issues.push(
      issue('DURATION_TOO_LONG', 'duration_seconds', 'Rainfall duration cannot exceed 72 hours.')
    )
  }

  if (!Array.isArray(samples) || actual === 0) {
    issues.push(issue('SAMPLES_REQUIRED', 'samples', 'Add at least one rainfall sample.'))
    return makeResult(issues, expected, actual)
  }

  if (isRainfallStepSeconds(stepSeconds) && validDuration(durationSeconds) && actual !== expected) {
    issues.push(
      issue(
        'SAMPLE_COUNT_MISMATCH',
        'samples',
        `Expected ${expected} samples for this duration and step, but found ${actual}.`
      )
    )
  }

  const parsedTimes: number[] = []
  samples.forEach((sample, index) => {
    const intensity = sample?.rainfall_mm_per_hour
    const parsedTime = isValidRainfallTimestamp(sample?.time) ? Date.parse(sample.time) : Number.NaN

    if (!Number.isFinite(parsedTime)) {
      issues.push(
        issue(
          'SAMPLE_TIME_INVALID',
          `samples[${index}].time`,
          'Sample time must be a valid timestamp.',
          index
        )
      )
    } else {
      parsedTimes[index] = parsedTime
      if (
        index > 0 &&
        Number.isFinite(parsedTimes[index - 1]) &&
        parsedTime <= parsedTimes[index - 1]
      ) {
        issues.push(
          issue(
            'SAMPLE_TIME_NOT_INCREASING',
            `samples[${index}].time`,
            'Sample times must be strictly increasing.',
            index
          )
        )
      }
    }

    if (typeof intensity !== 'number' || !Number.isFinite(intensity) || intensity < 0) {
      issues.push(
        issue(
          'SAMPLE_INTENSITY_INVALID',
          `samples[${index}].rainfall_mm_per_hour`,
          'Rainfall intensity must be a finite non-negative number.',
          index
        )
      )
    }
  })

  if (
    enforceGrid &&
    isRainfallStepSeconds(stepSeconds) &&
    validDuration(durationSeconds) &&
    parsedTimes.length === actual &&
    parsedTimes.every((value) => Number.isFinite(value))
  ) {
    const first = parsedTimes[0]
    const expectedStepMs = stepSeconds * 1_000
    parsedTimes.forEach((time, index) => {
      const expectedTime = first + index * expectedStepMs
      if (time !== expectedTime) {
        issues.push(
          issue(
            'SAMPLE_INTERVAL_MISMATCH',
            `samples[${index}].time`,
            `Sample time must follow the selected ${stepSeconds / 60}-minute grid.`,
            index
          )
        )
      }
    })
    const last = parsedTimes[parsedTimes.length - 1]
    const expectedSpan = Math.max(0, expected - 1) * stepSeconds * 1_000
    if (last - first !== expectedSpan) {
      issues.push(
        issue(
          'DURATION_INVALID',
          'samples',
          'The sample table must span every complete interval in the selected duration.'
        )
      )
    }
  }

  return makeResult(issues, expected, actual)
}

export interface ValidateRainfallScenarioOptions {
  /** Validate only the editable fields and skip metadata supplied by a backend. */
  partial?: boolean
}

export function validateRainfallScenario(
  scenario: Partial<RainfallScenario> | null | undefined,
  options: ValidateRainfallScenarioOptions = {}
): RainfallValidationResult {
  const value = scenario ?? {}
  const issues: RainfallValidationIssue[] = []
  const durationSeconds = value.duration_seconds as number
  const stepSeconds = value.step_seconds as number
  const samples = value.samples ?? []

  if (!options.partial) {
    if (typeof value.name !== 'string' || value.name.trim() === '') {
      issues.push(issue('NAME_REQUIRED', 'name', 'Scenario name is required.'))
    } else if (value.name.length > MAX_RAINFALL_NAME_LENGTH) {
      issues.push(issue('NAME_TOO_LONG', 'name', 'Scenario name must be at most 255 characters.'))
    }
    if (!isRainfallReturnPeriod(value.return_period_years)) {
      issues.push(
        issue(
          'RETURN_PERIOD_INVALID',
          'return_period_years',
          `Return period must be one of ${RAINFALL_RETURN_PERIODS.join(', ')}.`
        )
      )
    }
    if (value.source_type !== 'synthetic') {
      issues.push(
        issue(
          'SOURCE_TYPE_INVALID',
          'source_type',
          'Only the synthetic rainfall source is available in v1.'
        )
      )
    }
    if (
      typeof value.algorithm !== 'string' ||
      value.algorithm.trim().length === 0 ||
      value.algorithm.length > MAX_RAINFALL_ALGORITHM_LENGTH
    ) {
      issues.push(
        issue('ALGORITHM_INVALID', 'algorithm', 'A bounded rainfall algorithm is required.')
      )
    }
    if (
      typeof value.algorithm === 'string' &&
      ((value.return_period_years === 'custom' && value.algorithm !== 'synthetic-custom-v1') ||
        (value.return_period_years !== 'custom' &&
          value.algorithm !== 'synthetic-design-chicago-v1'))
    ) {
      issues.push(
        issue('ALGORITHM_INVALID', 'algorithm', 'Algorithm does not match the return-period kind.')
      )
    }
    if (!validSeed(value.random_seed)) {
      issues.push(
        issue('SEED_INVALID', 'random_seed', 'Rainfall seed must be a signed 64-bit safe integer.')
      )
    }
  }

  const sampleResult = validateRainfallSamples({ samples, durationSeconds, stepSeconds })
  issues.push(...sampleResult.issues)
  return makeResult(issues, sampleResult.expectedSampleCount, sampleResult.actualSampleCount)
}

/** Convenience wrapper accepting either a full scenario or a table + timing. */
export function validateRainfall(
  input: Partial<RainfallScenario> | RainfallSample[] | null | undefined,
  durationSeconds?: number,
  stepSeconds?: number
): RainfallValidationResult {
  if (Array.isArray(input)) {
    return validateRainfallSamples(input, durationSeconds ?? Number.NaN, stepSeconds ?? Number.NaN)
  }
  return validateRainfallScenario(input)
}

export const validateCustomRainfallScenario = validateRainfallScenario

export function isRainfallScenarioValid(
  scenario: Partial<RainfallScenario> | null | undefined
): boolean {
  return validateRainfallScenario(scenario).valid
}

export function isValidRainfall(
  input: Partial<RainfallScenario> | RainfallSample[] | null | undefined,
  durationSeconds?: number,
  stepSeconds?: number
): boolean {
  return validateRainfall(input, durationSeconds, stepSeconds).valid
}

export const isValidRainfallScenario = isRainfallScenarioValid

export function durationHours(durationSeconds: number): number {
  if (!Number.isFinite(durationSeconds)) return Number.NaN
  return durationSeconds / 3_600
}

export function formatDuration(durationSeconds: number): string {
  if (!Number.isFinite(durationSeconds) || durationSeconds < 0) return '—'
  const hours = Math.floor(durationSeconds / 3_600)
  const minutes = Math.floor((durationSeconds % 3_600) / 60)
  const seconds = Math.floor(durationSeconds % 60)
  return (
    [hours ? `${hours}h` : '', minutes ? `${minutes}m` : '', seconds ? `${seconds}s` : '']
      .filter(Boolean)
      .join(' ') || '0s'
  )
}

/** Return a stable, serializable payload for a submit action. */
export function toRainfallScenarioPayload(scenario: RainfallScenario): RainfallScenario {
  return {
    ...scenario,
    samples: scenario.samples.map((sample, index) => ({
      time: sample.time,
      rainfall_mm_per_hour: sample.rainfall_mm_per_hour,
      point_index: sample.point_index ?? index,
    })),
  }
}
