import type { BarSeriesOption, LineSeriesOption } from 'echarts/charts'
import type {
  AriaComponentOption,
  GridComponentOption,
  LegendComponentOption,
  MarkLineComponentOption,
  TooltipComponentOption,
} from 'echarts/components'
import type { ComposeOption } from 'echarts/core'
import {
  differenceColorFor,
  maxAbsoluteDelta,
  type AlignedSimulationPoint,
  type ComparisonViewMode,
  type ObservedPredictedLabels,
  type ObservedPredictedPoint,
  type SimulationComparisonLabels,
} from '../comparison'

export interface SimulationComparisonOptionConfig {
  readonly labels: SimulationComparisonLabels
  readonly mode: ComparisonViewMode
  readonly reducedMotion: boolean
}

export interface ObservedPredictedOptionConfig {
  readonly labels: ObservedPredictedLabels
  readonly issueTime?: string | null
  readonly reducedMotion: boolean
}

type EChartsSimulationOption = ComposeOption<
  | LineSeriesOption
  | BarSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | LegendComponentOption
  | MarkLineComponentOption
  | AriaComponentOption
>

export type SimulationChartOption = Omit<EChartsSimulationOption, 'series'> & {
  series: Array<LineSeriesOption | BarSeriesOption>
}

type TimeValue = [number | null, number | null]

const COLORS = {
  baseline: '#2563EB',
  candidate: '#F97316',
  observed: '#0F766E',
  predicted: '#7C3AED',
  uncertainty: '#A855F7',
  text: '#475569',
  zero: '#64748B',
}

function timestampMs(timestamp: string): number | null {
  const value = Date.parse(timestamp)
  return Number.isFinite(value) ? value : null
}

function timeValue(timestamp: string, value: number | null): TimeValue {
  return [timestampMs(timestamp), Number.isFinite(value ?? Number.NaN) ? value : null]
}

function shouldAnimate(pointCount: number, reducedMotion: boolean): boolean {
  return !reducedMotion && pointCount <= 500
}

function commonOption(pointCount: number, reducedMotion: boolean) {
  return {
    animation: shouldAnimate(pointCount, reducedMotion),
    animationDuration: 250,
    backgroundColor: 'transparent',
    color: [COLORS.baseline, COLORS.candidate, COLORS.observed, COLORS.predicted],
    textStyle: { color: COLORS.text },
    tooltip: {
      trigger: 'axis' as const,
      axisPointer: { type: 'cross' as const },
      confine: true,
    },
    legend: {
      type: 'scroll' as const,
      top: 0,
      icon: 'roundRect' as const,
      itemWidth: 18,
      itemHeight: 8,
    },
  }
}

function lineSeries(
  id: string,
  name: string,
  points: readonly AlignedSimulationPoint[],
  value: (point: AlignedSimulationPoint) => number | null,
  color: string,
  xAxisIndex: number,
  yAxisIndex: number
): LineSeriesOption {
  return {
    id,
    name,
    type: 'line',
    xAxisIndex,
    yAxisIndex,
    data: points.map((point) => timeValue(point.timestamp, value(point))),
    showSymbol: false,
    symbol: 'none',
    connectNulls: false,
    smooth: false,
    lineStyle: { color, width: 2 },
    emphasis: { focus: 'series' },
    z: 3,
  }
}

export function buildSimulationComparisonOption(
  points: readonly AlignedSimulationPoint[],
  config: SimulationComparisonOptionConfig
): SimulationChartOption {
  const base = commonOption(points.length, config.reducedMotion)
  const { labels, mode } = config

  if (mode === 'difference') {
    const domain = maxAbsoluteDelta(points)
    const differenceSeries: BarSeriesOption = {
      id: 'difference',
      name: labels.difference,
      type: 'bar',
      data: points.map((point) => ({
        value: timeValue(point.timestamp, point.delta),
        itemStyle: { color: differenceColorFor(point.delta, domain) },
      })),
      barMaxWidth: 24,
      markLine: {
        silent: true,
        symbol: 'none',
        data: [{ yAxis: 0 }],
        lineStyle: { color: COLORS.zero, type: 'solid', width: 1 },
        label: { show: false },
      },
      z: 3,
    }

    return {
      ...base,
      aria: { enabled: true },
      grid: [{ left: 52, right: 20, top: 40, bottom: 42 }],
      xAxis: [{ type: 'time', gridIndex: 0 }],
      yAxis: [
        {
          type: 'value',
          gridIndex: 0,
          scale: true,
          splitLine: { lineStyle: { color: '#E2E8F0' } },
        },
      ],
      series: [differenceSeries],
    }
  }

  if (mode === 'split') {
    return {
      ...base,
      aria: { enabled: true },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 52, right: 20, top: 40, height: '42%' },
        { left: 52, right: 20, top: '58%', bottom: 42 },
      ],
      xAxis: [
        { type: 'time', gridIndex: 0, axisLabel: { show: false } },
        { type: 'time', gridIndex: 1 },
      ],
      yAxis: [
        { type: 'value', gridIndex: 0, scale: true, name: labels.baseline },
        { type: 'value', gridIndex: 1, scale: true, name: labels.candidate },
      ],
      series: [
        lineSeries(
          'baseline',
          labels.baseline,
          points,
          (point) => point.baseline,
          COLORS.baseline,
          0,
          0
        ),
        lineSeries(
          'candidate',
          labels.candidate,
          points,
          (point) => point.candidate,
          COLORS.candidate,
          1,
          1
        ),
      ],
    }
  }

  return {
    ...base,
    aria: { enabled: true },
    grid: [{ left: 52, right: 20, top: 40, bottom: 42 }],
    xAxis: [{ type: 'time' }],
    yAxis: [{ type: 'value', scale: true }],
    series: [
      lineSeries(
        'baseline',
        labels.baseline,
        points,
        (point) => point.baseline,
        COLORS.baseline,
        0,
        0
      ),
      lineSeries(
        'candidate',
        labels.candidate,
        points,
        (point) => point.candidate,
        COLORS.candidate,
        0,
        0
      ),
    ],
  }
}

export function buildObservedPredictedOption(
  points: readonly ObservedPredictedPoint[],
  config: ObservedPredictedOptionConfig
): SimulationChartOption {
  const base = commonOption(points.length, config.reducedMotion)
  const issueTimeMs = config.issueTime ? timestampMs(config.issueTime) : null
  const lowerValues: TimeValue[] = []
  const bandValues: TimeValue[] = []

  for (const point of points) {
    const lower = point.lower_bound ?? null
    const upper = point.upper_bound ?? null
    const hasBand =
      typeof lower === 'number' &&
      Number.isFinite(lower) &&
      typeof upper === 'number' &&
      Number.isFinite(upper) &&
      upper >= lower

    lowerValues.push(timeValue(point.timestamp, hasBand ? lower : null))
    bandValues.push(timeValue(point.timestamp, hasBand ? upper - (lower as number) : null))
  }

  const lowerSeries: LineSeriesOption = {
    id: 'uncertainty-lower',
    name: config.labels.uncertainty,
    type: 'line',
    data: lowerValues,
    stack: 'uncertainty',
    stackStrategy: 'all',
    symbol: 'none',
    showSymbol: false,
    connectNulls: false,
    lineStyle: { opacity: 0 },
    areaStyle: { opacity: 0 },
    tooltip: { show: false },
    silent: true,
    z: 0,
  }
  const bandSeries: LineSeriesOption = {
    id: 'uncertainty-band',
    name: config.labels.uncertainty,
    type: 'line',
    data: bandValues,
    stack: 'uncertainty',
    stackStrategy: 'all',
    symbol: 'none',
    showSymbol: false,
    connectNulls: false,
    lineStyle: { opacity: 0 },
    areaStyle: { color: COLORS.uncertainty, opacity: 0.2 },
    tooltip: { show: false },
    silent: true,
    z: 1,
  }
  const observedSeries: LineSeriesOption = {
    id: 'observed',
    name: config.labels.observed,
    type: 'line',
    data: points.map((point) => timeValue(point.timestamp, point.observed)),
    showSymbol: false,
    symbol: 'none',
    connectNulls: false,
    smooth: false,
    lineStyle: { color: COLORS.observed, width: 2 },
    emphasis: { focus: 'series' },
    z: 3,
  }
  const predictedSeries: LineSeriesOption = {
    id: 'predicted',
    name: config.labels.predicted,
    type: 'line',
    data: points.map((point) => timeValue(point.timestamp, point.predicted)),
    showSymbol: false,
    symbol: 'none',
    connectNulls: false,
    smooth: false,
    lineStyle: { color: COLORS.predicted, width: 2, type: 'dashed' },
    emphasis: { focus: 'series' },
    markLine: {
      silent: true,
      symbol: 'none',
      data: issueTimeMs === null ? [] : [{ xAxis: issueTimeMs, name: config.labels.issuedAt }],
      lineStyle: { color: COLORS.zero, type: 'dotted', width: 1 },
      label: { formatter: config.labels.issuedAt },
    },
    z: 2,
  }

  return {
    ...base,
    aria: { enabled: true },
    legend: {
      ...base.legend,
      data: [config.labels.observed, config.labels.predicted, config.labels.uncertainty],
    },
    grid: [{ left: 52, right: 20, top: 40, bottom: 42 }],
    xAxis: [{ type: 'time' }],
    yAxis: [{ type: 'value', scale: true }],
    series: [observedSeries, predictedSeries, lowerSeries, bandSeries],
  }
}
