import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
} from 'react'
import { useTranslation } from 'react-i18next'
import './rainfall.css'
import {
  MAX_CUSTOM_DURATION_HOURS,
  MAX_CUSTOM_DURATION_SECONDS,
  RAINFALL_RETURN_PERIODS,
  RAINFALL_STEP_MINUTES,
  cloneRainfallSamples,
  createDefaultRainfallScenario,
  generateRainfallSamples,
  getExpectedSampleCount,
  isValidRainfallSeed,
  stepSecondsFromMinutes,
  type RainfallReturnPeriod,
  type RainfallSample,
  type RainfallScenario,
  type RainfallStepMinutes,
} from './model'
import {
  buildRainfallScenarioCreateCommand,
  type RainfallScenarioCreateCommand,
} from './apiAdapter'
import { RainfallChart } from './RainfallChart'
import {
  toRainfallScenarioPayload,
  validateRainfallScenario,
  type RainfallValidationResult,
} from './validation'

const TABLE_PAGE_SIZE = 50

export type RainfallScenarioEditorScenario = Partial<Record<keyof RainfallScenario, unknown>> & {
  samples?: RainfallSample[]
}

export interface RainfallScenarioEditorProps {
  initialScenario?: RainfallScenarioEditorScenario
  /** Alias useful when the parent already calls the value a scenario. */
  scenario?: RainfallScenarioEditorScenario
  onSubmit?: (scenario: RainfallScenario) => void | Promise<void>
  /** Backwards-compatible descriptive alias for `onSubmit`. */
  onScenarioSubmit?: (scenario: RainfallScenario) => void | Promise<void>
  /** Typed frozen create-command boundary; when present it is used instead of the local callback. */
  onCreateScenario?: (command: RainfallScenarioCreateCommand) => void | Promise<void>
  networkId?: string
  idempotencyKey?: string
  onChange?: (scenario: RainfallScenario) => void
  onValidationChange?: (result: RainfallValidationResult) => void
  disabled?: boolean
  submitLabel?: ReactNode
  className?: string
}

function normalizeInitialScenario(input?: RainfallScenarioEditorScenario): RainfallScenario {
  const value = input ?? {}
  const hasSuppliedSamples = Array.isArray(value.samples)
  const suppliedSamples = hasSuppliedSamples ? cloneRainfallSamples(value.samples ?? []) : []
  const base = createDefaultRainfallScenario(value as Partial<RainfallScenario>)
  if (hasSuppliedSamples) return { ...base, samples: suppliedSamples }
  return base
}

function nextSampleTime(last: RainfallSample | undefined, stepSeconds: number): string {
  const fallback = Date.parse('2026-01-01T00:00:00.000Z')
  const previous = last?.time ? Date.parse(last.time) : fallback
  const timestamp = (Number.isFinite(previous) ? previous : fallback) + stepSeconds * 1_000
  return new Date(timestamp).toISOString()
}

export function RainfallScenarioEditor({
  initialScenario,
  scenario,
  onSubmit,
  onScenarioSubmit,
  onCreateScenario,
  networkId,
  idempotencyKey,
  onChange,
  onValidationChange,
  disabled = false,
  submitLabel,
  className,
}: RainfallScenarioEditorProps) {
  const [draft, setDraft] = useState<RainfallScenario>(() =>
    normalizeInitialScenario(scenario ?? initialScenario)
  )
  const [selectedIndex, setSelectedIndex] = useState<number | null>(0)
  const [page, setPage] = useState(0)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const { t } = useTranslation()
  const label = (key: string, defaultValue: string, options?: Record<string, unknown>) => {
    try {
      const translated = t(key, { ...options, defaultValue })
      return translated === key ? defaultValue : translated
    } catch {
      return defaultValue
    }
  }
  const formatReturnPeriod = (period: RainfallReturnPeriod) =>
    period === 'custom'
      ? label('rainfall.custom', 'Custom')
      : label('rainfall.returnPeriod', '{{years}}-year', { years: period })

  const validation = useMemo(() => validateRainfallScenario(draft), [draft])
  const expectedCount = getExpectedSampleCount(draft.duration_seconds, draft.step_seconds)
  const totalPages = Math.max(1, Math.ceil(draft.samples.length / TABLE_PAGE_SIZE))
  const activePage = Math.min(page, totalPages - 1)
  const pageStart = activePage * TABLE_PAGE_SIZE
  const visibleSamples = draft.samples.slice(pageStart, pageStart + TABLE_PAGE_SIZE)

  useEffect(() => {
    onValidationChange?.(validation)
  }, [onValidationChange, validation])

  useEffect(() => {
    onChange?.(toRainfallScenarioPayload(draft))
  }, [draft, onChange])

  const updateDraft = (updater: (current: RainfallScenario) => RainfallScenario) => {
    setSubmitted(false)
    setSubmitError(null)
    setDraft((current) => updater(current))
  }

  const regenerate = (
    current: RainfallScenario,
    changes: Partial<RainfallScenario> = {}
  ): RainfallScenario => {
    const next = { ...current, ...changes }
    const startAt = current.samples[0]?.time
    if (
      Number.isSafeInteger(next.duration_seconds) &&
      next.duration_seconds > 0 &&
      next.duration_seconds <= MAX_CUSTOM_DURATION_SECONDS &&
      (next.step_seconds === 60 || next.step_seconds === 300 || next.step_seconds === 900) &&
      RAINFALL_RETURN_PERIODS.includes(next.return_period_years) &&
      isValidRainfallSeed(next.random_seed)
    ) {
      try {
        next.samples = generateRainfallSamples({
          returnPeriodYears: next.return_period_years,
          durationSeconds: next.duration_seconds,
          stepSeconds: next.step_seconds,
          randomSeed: next.random_seed,
          startAt,
        })
      } catch {
        // Keep the current samples; validation exposes the invalid metadata.
      }
    }
    return next
  }

  const handleReturnPeriod = (period: RainfallReturnPeriod) => {
    updateDraft((current) => regenerate(current, { return_period_years: period }))
    setSelectedIndex(0)
    setPage(0)
  }

  const handleNameChange = (event: ChangeEvent<HTMLInputElement>) => {
    const name = event.target.value
    updateDraft((current) => ({ ...current, name }))
  }

  const handleDurationHoursChange = (event: ChangeEvent<HTMLInputElement>) => {
    const hours = event.target.value === '' ? Number.NaN : Number(event.target.value)
    const durationSeconds = Number.isFinite(hours) ? Math.round(hours * 3_600) : Number.NaN
    updateDraft((current) => regenerate(current, { duration_seconds: durationSeconds }))
    setPage(0)
  }

  const handleStepChange = (event: ChangeEvent<HTMLSelectElement>) => {
    const minutes = Number(event.target.value) as RainfallStepMinutes
    const stepSeconds = stepSecondsFromMinutes(minutes)
    updateDraft((current) => regenerate(current, { step_seconds: stepSeconds }))
    setPage(0)
  }

  const handleSeedChange = (event: ChangeEvent<HTMLInputElement>) => {
    const seed = event.target.value === '' ? Number.NaN : Number(event.target.value)
    updateDraft((current) => regenerate(current, { random_seed: seed }))
  }

  const handleSampleChange = (
    index: number,
    field: keyof Pick<RainfallSample, 'time' | 'rainfall_mm_per_hour'>,
    value: string
  ) => {
    updateDraft((current) => {
      const samples = current.samples.map((sample, sampleIndex) => {
        if (sampleIndex !== index) return sample
        if (field === 'time') return { ...sample, time: value }
        const parsed = value === '' ? Number.NaN : Number(value)
        return { ...sample, rainfall_mm_per_hour: parsed }
      })
      return { ...current, samples }
    })
    setSelectedIndex(index)
    setSubmitted(false)
  }

  const handleAddSample = () => {
    updateDraft((current) => {
      const last = current.samples[current.samples.length - 1]
      if (!last) return current
      const nextDuration = current.duration_seconds + current.step_seconds
      if (nextDuration > MAX_CUSTOM_DURATION_SECONDS) return current
      return {
        ...current,
        duration_seconds: nextDuration,
        samples: [
          ...current.samples,
          {
            time: nextSampleTime(last, current.step_seconds),
            rainfall_mm_per_hour: 0,
            point_index: current.samples.length,
          },
        ],
      }
    })
  }

  const handleRemoveSample = (index: number) => {
    updateDraft((current) => {
      if (index !== current.samples.length - 1 || current.samples.length <= 1) return current
      const samples = current.samples.slice(0, -1).map((sample, sampleIndex) => ({
        ...sample,
        point_index: sampleIndex,
      }))
      return {
        ...current,
        duration_seconds: Math.max(
          current.step_seconds,
          (samples.length - 1) * current.step_seconds
        ),
        samples,
      }
    })
    setSelectedIndex(null)
  }

  const handleChartSelect = (index: number) => {
    setSelectedIndex(index)
    setPage(Math.floor(index / TABLE_PAGE_SIZE))
  }

  const submitHandler = onSubmit ?? onScenarioSubmit
  const hasSubmitHandler = Boolean(onCreateScenario || submitHandler)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (disabled || isSubmitting || !validation.valid || !hasSubmitHandler) return
    setIsSubmitting(true)
    setSubmitted(false)
    setSubmitError(null)
    try {
      if (onCreateScenario) {
        if (!networkId || !idempotencyKey) {
          throw new Error(
            label(
              'rainfall.createMissingContext',
              'networkId and idempotencyKey are required for scenario creation.'
            )
          )
        }
        const command = await buildRainfallScenarioCreateCommand(draft, networkId, idempotencyKey)
        await onCreateScenario(command)
      } else if (submitHandler) {
        await submitHandler(toRainfallScenarioPayload(draft))
      }
      setSubmitted(true)
    } catch (error) {
      setSubmitError(
        error instanceof Error
          ? error.message
          : label('rainfall.submitError', 'Unable to submit the scenario.')
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  const durationHoursValue = Number.isFinite(draft.duration_seconds)
    ? String(draft.duration_seconds / 3_600)
    : ''
  const seedValue = Number.isFinite(draft.random_seed) ? String(draft.random_seed) : ''

  return (
    <form
      className={className ? `rainfall-editor ${className}` : 'rainfall-editor'}
      data-testid="rainfall-editor"
      aria-label={label('rainfall.editorAria', 'Rainfall scenario editor')}
      onSubmit={handleSubmit}
    >
      <div className="rainfall-editor__header">
        <div>
          <h2>{label('rainfall.editorTitle', 'Rainfall scenario')}</h2>
          <p>
            {label(
              'rainfall.editorSubtitle',
              'Choose a design storm or edit the synthetic time series.'
            )}
          </p>
        </div>
        <output data-testid="rainfall-sample-count">
          {draft.samples.length} / {expectedCount} samples
        </output>
      </div>

      <fieldset
        className="rainfall-editor__presets"
        data-testid="rainfall-presets"
        disabled={disabled}
      >
        <legend>{label('rainfall.quickScenarios', 'Quick scenarios')}</legend>
        {RAINFALL_RETURN_PERIODS.map((period) => (
          <button
            key={String(period)}
            type="button"
            data-testid={`rainfall-preset-${period}`}
            data-return-period={String(period)}
            aria-pressed={draft.return_period_years === period}
            onClick={() => handleReturnPeriod(period)}
          >
            {formatReturnPeriod(period)}
          </button>
        ))}
      </fieldset>

      <div className="rainfall-editor__controls">
        <label>
          {label('rainfall.scenarioName', 'Scenario name')}
          <input
            data-testid="rainfall-name"
            aria-label={label('rainfall.scenarioName', 'Scenario name')}
            value={draft.name}
            onChange={handleNameChange}
            disabled={disabled}
          />
        </label>
        <label>
          {label('rainfall.durationHours', 'Duration (hours)')}
          <input
            data-testid="rainfall-duration-hours"
            aria-label={label('rainfall.durationHoursAria', 'Duration in hours')}
            type="number"
            min="0"
            max={MAX_CUSTOM_DURATION_HOURS}
            step="0.25"
            value={durationHoursValue}
            onChange={handleDurationHoursChange}
            disabled={disabled}
          />
        </label>
        <label>
          {label('rainfall.step', 'Step')}
          <select
            data-testid="rainfall-step"
            aria-label={label('rainfall.stepAria', 'Rainfall step')}
            value={String(draft.step_seconds / 60)}
            onChange={handleStepChange}
            disabled={disabled || !Number.isFinite(draft.step_seconds)}
          >
            {RAINFALL_STEP_MINUTES.map((minutes) => (
              <option key={minutes} value={minutes}>
                {label(
                  'rainfall.minutesOption',
                  minutes === 1 ? '{{minutes}} minute' : '{{minutes}} minutes',
                  { minutes }
                )}
              </option>
            ))}
          </select>
        </label>
        <label>
          {label('rainfall.randomSeed', 'Random seed')}
          <input
            data-testid="rainfall-seed"
            aria-label={label('rainfall.randomSeedAria', 'Random seed')}
            type="number"
            step="1"
            value={seedValue}
            onChange={handleSeedChange}
            disabled={disabled}
          />
        </label>
      </div>

      <div className="rainfall-editor__chart-panel">
        <RainfallChart
          samples={draft.samples}
          selectedIndex={selectedIndex}
          onSelect={handleChartSelect}
        />
        <p className="rainfall-editor__chart-help">
          {label('rainfall.chartHelp', 'Click a bar to select the matching table row.')}
        </p>
      </div>

      <div className="rainfall-editor__table-panel">
        <div className="rainfall-editor__table-header">
          <h3>{label('rainfall.samples', 'Rainfall samples')}</h3>
          <button
            type="button"
            data-testid="rainfall-add-sample"
            onClick={handleAddSample}
            disabled={disabled}
          >
            {label('rainfall.addSample', 'Add sample')}
          </button>
        </div>
        <div className="rainfall-editor__table-scroll">
          <table
            data-testid="rainfall-table"
            aria-label={label('rainfall.samples', 'Rainfall samples')}
          >
            <thead>
              <tr>
                <th scope="col">{label('rainfall.index', '#')}</th>
                <th scope="col">{label('rainfall.timeUtc', 'Time (UTC)')}</th>
                <th scope="col">{label('rainfall.intensity', 'Intensity (mm/h)')}</th>
                <th scope="col">{label('rainfall.action', 'Action')}</th>
              </tr>
            </thead>
            <tbody>
              {visibleSamples.map((sample, visibleIndex) => {
                const index = pageStart + visibleIndex
                return (
                  <tr
                    key={index}
                    data-testid={`rainfall-row-${index}`}
                    data-selected={selectedIndex === index}
                    onClick={() => setSelectedIndex(index)}
                  >
                    <td>{index + 1}</td>
                    <td>
                      <input
                        data-testid={`rainfall-time-${index}`}
                        aria-label={label('rainfall.timeRow', 'Rainfall time at row {{index}}', {
                          index: index + 1,
                        })}
                        value={sample.time}
                        onChange={(event) => handleSampleChange(index, 'time', event.target.value)}
                        disabled={disabled}
                      />
                    </td>
                    <td>
                      <input
                        data-testid={`rainfall-intensity-${index}`}
                        aria-label={label(
                          'rainfall.intensityRow',
                          'Rainfall intensity at row {{index}}',
                          { index: index + 1 }
                        )}
                        type="number"
                        min="0"
                        step="0.0001"
                        value={
                          Number.isFinite(sample.rainfall_mm_per_hour)
                            ? sample.rainfall_mm_per_hour
                            : ''
                        }
                        onChange={(event) =>
                          handleSampleChange(index, 'rainfall_mm_per_hour', event.target.value)
                        }
                        disabled={disabled}
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        aria-label={label('rainfall.removeRow', 'Remove rainfall row {{index}}', {
                          index: index + 1,
                        })}
                        onClick={(event) => {
                          event.stopPropagation()
                          handleRemoveSample(index)
                        }}
                        disabled={
                          disabled ||
                          index !== draft.samples.length - 1 ||
                          draft.samples.length <= 1
                        }
                      >
                        {label('rainfall.remove', 'Remove')}
                      </button>
                    </td>
                  </tr>
                )
              })}
              {visibleSamples.length === 0 && (
                <tr>
                  <td colSpan={4}>{label('rainfall.noSamples', 'No samples')}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="rainfall-editor__pagination">
          <button
            type="button"
            aria-label={label('rainfall.previousPage', 'Previous rainfall samples page')}
            onClick={() => setPage((current) => Math.max(0, current - 1))}
            disabled={disabled || activePage === 0}
          >
            {label('rainfall.previous', 'Previous')}
          </button>
          <span>
            {label('rainfall.page', 'Page {{page}} / {{count}}', {
              page: activePage + 1,
              count: totalPages,
            })}
          </span>
          <button
            type="button"
            aria-label={label('rainfall.nextPage', 'Next rainfall samples page')}
            onClick={() => setPage((current) => Math.min(totalPages - 1, current + 1))}
            disabled={disabled || activePage >= totalPages - 1}
          >
            {label('rainfall.next', 'Next')}
          </button>
        </div>
      </div>

      <div
        className="rainfall-editor__validation"
        data-testid="rainfall-validation"
        role={validation.valid ? 'status' : 'alert'}
        data-valid={validation.valid}
      >
        <strong>
          {validation.valid
            ? label('rainfall.validationReady', 'Scenario is ready to submit.')
            : validation.errors.length === 1
              ? label(
                  `rainfall.validation.${validation.errors[0].code}`,
                  validation.errors[0].message
                )
              : label(
                  'rainfall.validation.fix',
                  'Fix {{count}} validation errors before submitting.',
                  {
                    count: validation.errors.length,
                  }
                )}
        </strong>
        {!validation.valid && (
          <ul>
            {validation.errors.map((item) => (
              <li key={`${item.code}-${item.field}-${item.index ?? ''}`}>
                {label(`rainfall.validation.${item.code}`, item.message)}
              </li>
            ))}
          </ul>
        )}
        {!hasSubmitHandler && (
          <span role="status">
            {label(
              'rainfall.draftOnly',
              'Draft only. Configure a typed submit handler to persist this scenario.'
            )}
          </span>
        )}
        {submitted && (
          <span role="status">{label('rainfall.submitted', 'Scenario submitted.')}</span>
        )}
        {submitError && <span role="alert">{submitError}</span>}
      </div>

      <button
        type="submit"
        data-testid="rainfall-submit"
        disabled={disabled || isSubmitting || !validation.valid || !hasSubmitHandler}
      >
        {isSubmitting
          ? label('rainfall.submitting', 'Submitting…')
          : submitLabel ?? label('rainfall.submit', 'Submit scenario')}
      </button>
    </form>
  )
}

export default RainfallScenarioEditor
