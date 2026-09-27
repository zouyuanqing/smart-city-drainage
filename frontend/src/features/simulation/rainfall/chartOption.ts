import type { EChartsOption } from 'echarts'
import type { RainfallSample } from './model'

// Pure chart data/option helpers. This module deliberately has no runtime
// ECharts import: `EChartsOption` is a type-only import, so the option builder
// stays testable (and importable) without pulling in the render engine.

export interface RainfallChartPoint {
  index: number
  time: string
  label: string
  value: number
}

export function toRainfallChartPoints(samples: RainfallSample[]): RainfallChartPoint[] {
  return samples.map((sample, index) => ({
    index,
    time: sample.time,
    label: formatChartTime(sample.time),
    value: sample.rainfall_mm_per_hour,
  }))
}

function formatChartTime(value: string): string {
  const parsed = Date.parse(value)
  if (!Number.isFinite(parsed)) return 'Invalid time'
  return new Date(parsed).toISOString().slice(11, 16)
}

export function buildRainfallChartOption(
  samples: RainfallSample[],
  selectedIndex: number | null = null
): EChartsOption {
  const points = toRainfallChartPoints(samples)
  return {
    animation: false,
    grid: { top: 20, right: 16, bottom: 42, left: 48 },
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const first = Array.isArray(params) ? params[0] : params
        const point = points[Number(first?.dataIndex ?? -1)]
        if (!point) return ''
        return `${point.label}\n${Number.isFinite(point.value) ? point.value.toFixed(2) : 'No data'} mm/h`
      },
    },
    xAxis: {
      type: 'category',
      data: points.map((point) => point.label),
      axisLabel: {
        interval: Math.max(0, Math.floor(points.length / 6) - 1),
        color: '#94a3b8',
        fontSize: 10,
      },
      axisLine: { lineStyle: { color: '#475569' } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      name: 'mm/h',
      min: 0,
      nameTextStyle: { color: '#94a3b8', fontSize: 10 },
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitLine: { lineStyle: { color: 'rgba(148, 163, 184, 0.18)' } },
    },
    series: [
      {
        name: 'Rainfall intensity',
        type: 'bar',
        data: points.map((point, index) => ({
          value: point.value,
          itemStyle:
            index === selectedIndex
              ? { color: '#f59e0b', borderColor: '#fef3c7', borderWidth: 1 }
              : { color: '#22d3ee' },
        })),
        barMaxWidth: 18,
        emphasis: { itemStyle: { color: '#f59e0b' } },
      },
    ],
  }
}
