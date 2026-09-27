import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RainfallScenarioEditor } from '../rainfall/RainfallScenarioEditor'
import { buildRainfallChartOption } from '../rainfall/chartOption'
import { createDefaultRainfallScenario } from '../rainfall/model'

vi.mock('../rainfall/RainfallChart', () => ({
  RainfallChart: ({
    samples,
    selectedIndex,
    onSelect,
  }: {
    samples: Array<{ rainfall_mm_per_hour: number }>
    selectedIndex?: number | null
    onSelect?: (index: number) => void
  }) => (
    <div
      data-testid="rainfall-chart"
      data-point-count={samples.length}
      data-selected-index={selectedIndex ?? ''}
      data-last-value={samples[0]?.rainfall_mm_per_hour ?? ''}
    >
      <button type="button" onClick={() => onSelect?.(1)}>
        Select second bar
      </button>
    </div>
  ),
}))

afterEach(() => {
  vi.restoreAllMocks()
})

describe('RainfallScenarioEditor', () => {
  it('selects a quick return-period scenario and updates the generated table', () => {
    const onChange = vi.fn()
    render(
      <RainfallScenarioEditor
        initialScenario={createDefaultRainfallScenario({
          duration_seconds: 3_600,
          step_seconds: 900,
        })}
        onChange={onChange}
      />
    )

    fireEvent.click(screen.getByTestId('rainfall-preset-50'))

    expect(screen.getByTestId('rainfall-preset-50')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('rainfall-sample-count')).toHaveTextContent('5 / 5 samples')
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ return_period_years: 50 }))
  })

  it('keeps a 72 hour custom scenario valid and blocks durations above the limit', () => {
    const onSubmit = vi.fn()
    render(
      <RainfallScenarioEditor
        initialScenario={createDefaultRainfallScenario({
          name: '72 hour custom',
          return_period_years: 'custom',
          duration_seconds: 72 * 3_600,
          step_seconds: 900,
        })}
        onSubmit={onSubmit}
      />
    )

    expect(screen.getByTestId('rainfall-validation')).toHaveAttribute('data-valid', 'true')
    expect(screen.getByTestId('rainfall-submit')).not.toBeDisabled()

    fireEvent.change(screen.getByTestId('rainfall-duration-hours'), {
      target: { value: '72.1' },
    })

    expect(screen.getByTestId('rainfall-validation')).toHaveAttribute('data-valid', 'false')
    expect(screen.getByTestId('rainfall-submit')).toBeDisabled()
    expect(screen.getByTestId('rainfall-validation')).toHaveTextContent('72 hours')
  })

  it('blocks negative table values and synchronizes chart selection and edits', () => {
    render(<RainfallScenarioEditor />)

    const chart = screen.getByTestId('rainfall-chart')
    fireEvent.click(screen.getByRole('button', { name: 'Select second bar' }))
    expect(screen.getByTestId('rainfall-row-1')).toHaveAttribute('data-selected', 'true')
    expect(chart).toHaveAttribute('data-selected-index', '1')

    fireEvent.click(screen.getByTestId('rainfall-row-0'))
    expect(chart).toHaveAttribute('data-selected-index', '0')

    fireEvent.change(screen.getByTestId('rainfall-intensity-0'), {
      target: { value: '12.5' },
    })
    expect(chart).toHaveAttribute('data-last-value', '12.5')

    fireEvent.change(screen.getByTestId('rainfall-intensity-0'), {
      target: { value: '-1' },
    })
    expect(screen.getByTestId('rainfall-validation')).toHaveAttribute('data-valid', 'false')
    expect(screen.getByTestId('rainfall-submit')).toBeDisabled()
  })

  it('submits the validated payload', async () => {
    const onSubmit = vi.fn()
    render(<RainfallScenarioEditor onSubmit={onSubmit} />)

    fireEvent.click(screen.getByTestId('rainfall-submit'))

    await waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1))
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        return_period_years: 10,
        duration_seconds: 86_400,
        step_seconds: 300,
      })
    )
    expect(screen.getByTestId('rainfall-validation')).toHaveTextContent('Scenario submitted.')
  })

  it('passes the real network and idempotency context through the typed create boundary', async () => {
    const onCreateScenario = vi.fn(async () => undefined)
    render(
      <RainfallScenarioEditor
        initialScenario={createDefaultRainfallScenario({
          duration_seconds: 900,
          step_seconds: 900,
        })}
        networkId="11111111-1111-4111-8111-111111111111"
        idempotencyKey="rainfall-create-key"
        onCreateScenario={onCreateScenario}
      />
    )

    fireEvent.click(screen.getByTestId('rainfall-submit'))
    await waitFor(() => expect(onCreateScenario).toHaveBeenCalledTimes(1))
    expect(onCreateScenario).toHaveBeenCalledWith(
      expect.objectContaining({
        idempotencyKey: 'rainfall-create-key',
        request: expect.objectContaining({
          network_id: '11111111-1111-4111-8111-111111111111',
        }),
      })
    )
  })

  it('builds chart series from the same sample array used by the table', () => {
    const scenario = createDefaultRainfallScenario({ duration_seconds: 900, step_seconds: 900 })
    const option = buildRainfallChartOption(scenario.samples, 1)
    const series = option.series as Array<{ data: Array<{ value: number }> }>

    expect(series[0].data.map((point) => point.value)).toEqual(
      scenario.samples.map((sample) => sample.rainfall_mm_per_hour)
    )
  })
})
