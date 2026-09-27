import { describe, expect, it } from 'vitest'
import {
  buildRainfallScenarioCreateCommand,
  createDefaultRainfallScenario,
  generateRainfallSamples,
  getSampleCount,
  MAX_CUSTOM_DURATION_SECONDS,
  validateRainfallSamples,
  validateRainfallScenario,
} from '../rainfall'

describe('rainfall validation', () => {
  it('accepts the 72 hour boundary and rejects a duration above it', () => {
    const base = createDefaultRainfallScenario({
      duration_seconds: MAX_CUSTOM_DURATION_SECONDS,
      step_seconds: 900,
    })
    const oneMinute = createDefaultRainfallScenario({
      duration_seconds: MAX_CUSTOM_DURATION_SECONDS,
      step_seconds: 60,
    })
    const remainder = createDefaultRainfallScenario({ duration_seconds: 3_601, step_seconds: 900 })

    expect(validateRainfallScenario(base).valid).toBe(true)
    expect(validateRainfallScenario(remainder).valid).toBe(true)
    expect(
      validateRainfallSamples(base.samples, base.duration_seconds, base.step_seconds).valid
    ).toBe(true)
    expect(oneMinute.samples).toHaveLength(4_321)
    expect(validateRainfallScenario(oneMinute).valid).toBe(true)
    expect(
      validateRainfallScenario({
        ...base,
        duration_seconds: MAX_CUSTOM_DURATION_SECONDS + 1,
      }).valid
    ).toBe(false)
  })

  it('rejects negative and non-finite rainfall intensities', () => {
    const base = createDefaultRainfallScenario({ duration_seconds: 3_600, step_seconds: 900 })

    expect(
      validateRainfallSamples({
        samples: [{ ...base.samples[0], rainfall_mm_per_hour: -0.1 }, ...base.samples.slice(1)],
        durationSeconds: base.duration_seconds,
        stepSeconds: base.step_seconds,
      }).valid
    ).toBe(false)
    expect(
      validateRainfallSamples({
        samples: [
          { ...base.samples[0], rainfall_mm_per_hour: Number.NaN },
          ...base.samples.slice(1),
        ],
        durationSeconds: base.duration_seconds,
        stepSeconds: base.step_seconds,
      }).valid
    ).toBe(false)
  })

  it('requires the contract sample count and strict time grid', () => {
    const base = createDefaultRainfallScenario({ duration_seconds: 3_600, step_seconds: 900 })
    expect(getSampleCount(3_600, 900)).toBe(5)

    const missing = validateRainfallScenario({ ...base, samples: base.samples.slice(0, 4) })
    expect(missing.valid).toBe(false)
    expect(missing.actualSampleCount).toBe(4)
    expect(missing.expectedSampleCount).toBe(5)

    const nonIncreasing = validateRainfallScenario({
      ...base,
      samples: base.samples.map((sample, index) =>
        index === 1 ? { ...sample, time: base.samples[0].time } : sample
      ),
    })
    expect(nonIncreasing.valid).toBe(false)
    expect(nonIncreasing.issues.some((issue) => issue.code === 'SAMPLE_TIME_NOT_INCREASING')).toBe(
      true
    )
  })
})

describe('quick rainfall scenarios', () => {
  it('rejects unsafe duration allocation and builds the frozen typed create command', async () => {
    expect(() =>
      generateRainfallSamples({
        returnPeriodYears: 10,
        durationSeconds: 100_000_000_000,
        stepSeconds: 60,
        randomSeed: 1,
      })
    ).toThrow(/72 hours|safety budget/)
    const scenario = createDefaultRainfallScenario({
      duration_seconds: 3_600,
      step_seconds: 900,
      return_period_years: 10,
    })
    const command = await buildRainfallScenarioCreateCommand(
      scenario,
      '11111111-1111-4111-8111-111111111111',
      'rainfall-key-1'
    )
    expect(command.request).toMatchObject({
      network_id: '11111111-1111-4111-8111-111111111111',
      kind: 'design',
      step_seconds: 900,
    })
    expect(command.request.points[0]).toMatchObject({ point_index: 0 })
    expect(command.request.content_sha256).toMatch(/^[0-9a-f]{64}$/)
  })

  it('generates deterministic, non-negative samples for the selected return period', () => {
    const options = {
      returnPeriodYears: 50 as const,
      durationSeconds: 24 * 3_600,
      stepSeconds: 900 as const,
      randomSeed: 42,
    }
    const first = generateRainfallSamples(options)
    const second = generateRainfallSamples(options)

    expect(first).toEqual(second)
    expect(first).toHaveLength(97)
    expect(first.every((sample) => sample.rainfall_mm_per_hour >= 0)).toBe(true)
    expect(first[0].time).toBe('2026-01-01T00:00:00.000Z')
    expect(first.at(-1)?.time).toBe('2026-01-02T00:00:00.000Z')
  })
})
