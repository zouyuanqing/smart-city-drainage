import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  buildObservedPredictedOption,
  buildSimulationComparisonOption,
} from '../charts/chartOptions'
import { ComparisonMetricsTable } from '../charts/ComparisonMetricsTable'
import { DifferenceLegend } from '../charts/DifferenceLegend'
import { ObservedPredictedChart } from '../charts/ObservedPredictedChart'
import {
  alignSimulationSeries,
  formatRelativeDelta,
  maxAbsoluteDelta,
  differenceColorFor,
} from '../comparison'

const echartsMock = vi.hoisted(() => {
  const chart = {
    setOption: vi.fn(),
    resize: vi.fn(),
    dispose: vi.fn(),
  }
  return {
    chart,
    init: vi.fn(() => chart),
    use: vi.fn(),
  }
})

vi.mock('echarts/core', () => ({
  init: echartsMock.init,
  use: echartsMock.use,
}))

vi.mock('echarts/charts', () => ({
  BarChart: {},
  LineChart: {},
}))

vi.mock('echarts/components', () => ({
  AriaComponent: {},
  GridComponent: {},
  LegendComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
}))

vi.mock('echarts/renderers', () => ({
  CanvasRenderer: {},
}))

const comparisonLabels = {
  baseline: 'Baseline',
  candidate: 'Candidate',
  difference: 'Difference',
}

const observedPredictedLabels = {
  observed: 'Observed',
  predicted: 'Predicted',
  uncertainty: 'Uncertainty',
  issuedAt: 'Issued at',
}

const aligned = [
  {
    timestamp: '2026-09-24T00:00:00.000Z',
    timestamp_ms: Date.parse('2026-09-24T00:00:00.000Z'),
    baseline: 1,
    candidate: 2,
    delta: 1,
  },
  {
    timestamp: '2026-09-24T00:05:00.000Z',
    timestamp_ms: Date.parse('2026-09-24T00:05:00.000Z'),
    baseline: null,
    candidate: 3,
    delta: null,
  },
  {
    timestamp: '2026-09-24T00:10:00.000Z',
    timestamp_ms: Date.parse('2026-09-24T00:10:00.000Z'),
    baseline: 4,
    candidate: null,
    delta: null,
  },
]

describe('simulation comparison pure data helpers', () => {
  it('aligns two simulations by absolute epoch time without filling missing samples', () => {
    const result = alignSimulationSeries(
      [
        { timestamp: '2026-09-24T00:00:00Z', value: 10 },
        { timestamp: '2026-09-24T00:05:00Z', value: null },
        { timestamp: '2026-09-24T00:10:00Z', value: 14 },
      ],
      [
        { timestamp: '2026-09-24T00:00:00Z', value: 12 },
        { timestamp: '2026-09-24T00:05:00Z', value: 13 },
      ]
    )

    expect(result).toEqual([
      {
        timestamp: '2026-09-24T00:00:00.000Z',
        timestamp_ms: Date.parse('2026-09-24T00:00:00Z'),
        baseline: 10,
        candidate: 12,
        delta: 2,
      },
      {
        timestamp: '2026-09-24T00:05:00.000Z',
        timestamp_ms: Date.parse('2026-09-24T00:05:00Z'),
        baseline: null,
        candidate: 13,
        delta: null,
      },
      {
        timestamp: '2026-09-24T00:10:00.000Z',
        timestamp_ms: Date.parse('2026-09-24T00:10:00Z'),
        baseline: 14,
        candidate: null,
        delta: null,
      },
    ])
    expect(() => alignSimulationSeries([], [{ timestamp: 'not-a-time', value: 1 }])).toThrow(
      /UTC RFC3339/
    )
  })

  it('uses fixed difference endpoints, neutral zero, and null for invalid deltas', () => {
    expect(maxAbsoluteDelta(aligned)).toBe(1)
    expect(differenceColorFor(-1, 1)).toBe('#1D4ED8')
    expect(differenceColorFor(0, 1)).toBe('#E2E8F0')
    expect(differenceColorFor(1, 1)).toBe('#C2410C')
    expect(differenceColorFor(Number.NaN, 1)).toBe('#E2E8F0')
  })

  it('formats backend relative deltas without recalculating them', () => {
    expect(formatRelativeDelta(0.125, 'en-US')).toBe('12.5%')
    expect(formatRelativeDelta(null, 'en-US')).toBe('—')
  })
})

describe('comparison chart options', () => {
  it('switches overlay, split, and difference layouts while preserving null gaps', () => {
    const overlay = buildSimulationComparisonOption(aligned, {
      labels: comparisonLabels,
      mode: 'overlay',
      reducedMotion: false,
    })
    const split = buildSimulationComparisonOption(aligned, {
      labels: comparisonLabels,
      mode: 'split',
      reducedMotion: false,
    })
    const difference = buildSimulationComparisonOption(aligned, {
      labels: comparisonLabels,
      mode: 'difference',
      reducedMotion: false,
    })

    expect(overlay.grid).toHaveLength(1)
    expect(overlay.series).toHaveLength(2)
    expect(split.grid).toHaveLength(2)
    expect(split.series).toHaveLength(2)
    expect(difference.grid).toHaveLength(1)
    expect(difference.series?.[0]).toMatchObject({ id: 'difference', type: 'bar' })
    expect(
      overlay.series.every((series) => series.type === 'line' && series.connectNulls === false)
    ).toBe(true)
    expect(
      split.series.every((series) => series.type === 'line' && series.connectNulls === false)
    ).toBe(true)
    expect((overlay.series?.[0].data as Array<[number, number | null]>)[1]).toEqual([
      aligned[1].timestamp_ms,
      null,
    ])
  })

  it('disables animation for reduced motion or more than 500 aligned points', () => {
    const regular = buildSimulationComparisonOption(aligned, {
      labels: comparisonLabels,
      mode: 'overlay',
      reducedMotion: false,
    })
    const reduced = buildSimulationComparisonOption(aligned, {
      labels: comparisonLabels,
      mode: 'overlay',
      reducedMotion: true,
    })
    const largeData = Array.from({ length: 501 }, (_, index) => ({
      timestamp: new Date(Date.parse('2026-09-24T00:00:00Z') + index * 300_000).toISOString(),
      timestamp_ms: Date.parse('2026-09-24T00:00:00Z') + index * 300_000,
      baseline: index,
      candidate: index,
      delta: 0,
    }))
    const large = buildSimulationComparisonOption(largeData, {
      labels: comparisonLabels,
      mode: 'overlay',
      reducedMotion: false,
    })

    expect(regular.animation).toBe(true)
    expect(reduced.animation).toBe(false)
    expect(large.animation).toBe(false)
  })

  it('builds observed, predicted, and available stacked uncertainty band series', () => {
    const option = buildObservedPredictedOption(
      [
        {
          timestamp: '2026-09-24T00:00:00Z',
          observed: 0.2,
          predicted: 0.25,
          lower_bound: 0.2,
          upper_bound: 0.3,
        },
        {
          timestamp: '2026-09-24T00:05:00Z',
          observed: null,
          predicted: 0.4,
          lower_bound: null,
          upper_bound: 0.5,
        },
      ],
      {
        issueTime: '2026-09-24T00:00:00Z',
        labels: observedPredictedLabels,
        reducedMotion: false,
      }
    )

    const byId = new Map(option.series?.map((series) => [series.id, series]))
    expect(byId.get('observed')).toMatchObject({ type: 'line', connectNulls: false })
    expect(byId.get('predicted')).toMatchObject({ type: 'line', connectNulls: false })
    expect(byId.get('uncertainty-lower')).toMatchObject({ stack: 'uncertainty' })
    expect(byId.get('uncertainty-band')).toMatchObject({
      stack: 'uncertainty',
      data: [
        [Date.parse('2026-09-24T00:00:00Z'), expect.closeTo(0.1, 8)],
        [Date.parse('2026-09-24T00:05:00Z'), null],
      ],
    })
    expect(byId.get('predicted')?.markLine?.data).toEqual([
      expect.objectContaining({ xAxis: Date.parse('2026-09-24T00:00:00Z') }),
    ])
  })
})

describe('chart React integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      }
    )
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  it('updates with replaceMerge and disposes the chart instance', () => {
    const view = render(
      <ObservedPredictedChart
        ariaLabel="Observed versus predicted"
        data={[
          {
            timestamp: '2026-09-24T00:00:00Z',
            observed: 0.2,
            predicted: 0.25,
            lower_bound: 0.2,
            upper_bound: 0.3,
          },
        ]}
        labels={observedPredictedLabels}
      />
    )

    expect(screen.getByRole('img', { name: 'Observed versus predicted' })).toBeInTheDocument()
    expect(echartsMock.chart.setOption).toHaveBeenCalledWith(
      expect.any(Object),
      expect.objectContaining({ replaceMerge: expect.arrayContaining(['series']) })
    )

    view.unmount()
    expect(echartsMock.chart.dispose).toHaveBeenCalledTimes(1)
  })

  it('renders an accessible fixed difference colour scale', () => {
    render(
      <DifferenceLegend
        ariaLabel="Difference colour scale"
        data={aligned}
        labels={{ negative: 'Lower', positive: 'Higher', zero: 'No change' }}
      />
    )

    const legend = screen.getByRole('img', { name: 'Difference colour scale' })
    expect(legend.querySelector('div')?.getAttribute('style')).toContain('linear-gradient')
    expect(screen.getByText('No change')).toBeInTheDocument()
  })

  it('honors the OS reduced-motion preference when no override is supplied', () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => ({
        matches: true,
        media: '(prefers-reduced-motion: reduce)',
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }))
    )

    render(
      <ObservedPredictedChart
        ariaLabel="Reduced motion chart"
        data={[
          {
            timestamp: '2026-09-24T00:00:00Z',
            observed: 0.2,
            predicted: 0.25,
          },
        ]}
        labels={observedPredictedLabels}
      />
    )

    const calls = echartsMock.chart.setOption.mock.calls
    const latestOption = calls[calls.length - 1][0] as { animation: boolean }
    expect(latestOption.animation).toBe(false)
  })

  it('renders backend-provided A/B metrics and preserves null relative deltas', () => {
    render(
      <ComparisonMetricsTable
        ariaLabel="Comparison metrics"
        labels={{
          absoluteDelta: 'Absolute delta',
          betterDirection: 'Better direction',
          betterDirections: {
            higher: 'Higher is better',
            lower: 'Lower is better',
            target: 'Closer to target',
          },
          baseline: 'Baseline',
          candidate: 'Candidate',
          metric: 'Metric',
          notAvailable: 'Not available',
          relativeDelta: 'Relative delta',
          unit: 'Unit',
        }}
        locale="en-US"
        metrics={[
          {
            key: 'rmse_m',
            label: 'RMSE',
            baseline: 0.4,
            candidate: 0.3,
            absolute_delta: -0.1,
            relative_delta: null,
            unit: 'm',
            better_direction: 'lower',
          },
        ]}
      />
    )

    expect(screen.getByRole('table', { name: 'Comparison metrics' })).toBeInTheDocument()
    expect(screen.getByText('RMSE')).toBeInTheDocument()
    expect(screen.getByText('Not available')).toBeInTheDocument()
    expect(screen.getByText('Lower is better')).toBeInTheDocument()
  })
})
