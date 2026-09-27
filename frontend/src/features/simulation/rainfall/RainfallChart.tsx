import { useEffect, useMemo, useRef } from 'react'
import { BarChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { init, use as registerECharts, type EChartsType } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { RainfallSample } from './model'
import { buildRainfallChartOption, toRainfallChartPoints } from './chartOption'

// The pure data/option helpers live in ./chartOption so they stay importable
// without the ECharts runtime. Re-exported here to keep the public surface of
// this module unchanged.
export { buildRainfallChartOption, toRainfallChartPoints } from './chartOption'
export type { RainfallChartPoint } from './chartOption'

registerECharts([BarChart, GridComponent, TooltipComponent, CanvasRenderer])

export interface RainfallChartProps {
  samples: RainfallSample[]
  selectedIndex?: number | null
  onSelect?: (index: number) => void
  height?: number | string
  className?: string
}

function isResizeObserverAvailable(): boolean {
  return typeof ResizeObserver !== 'undefined'
}

export function RainfallChart({
  samples,
  selectedIndex = null,
  onSelect,
  height = 260,
  className,
}: RainfallChartProps) {
  const chartElementRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<EChartsType | null>(null)
  const onSelectRef = useRef(onSelect)
  const points = useMemo(() => toRainfallChartPoints(samples), [samples])

  onSelectRef.current = onSelect

  useEffect(() => {
    const element = chartElementRef.current
    if (!element) return undefined

    let chart: EChartsType | null = null
    try {
      chart = init(element, undefined, { renderer: 'canvas' })
      chartRef.current = chart
    } catch {
      // jsdom and zero-sized intermediate panels do not have a canvas yet.
      // Rendering resumes when the panel is mounted in a real browser.
      chartRef.current = null
    }
    if (chart) {
      chart.on('click', (event) => {
        const dataIndex = (event as { dataIndex?: number }).dataIndex
        if (Number.isInteger(dataIndex) && dataIndex !== undefined) onSelectRef.current?.(dataIndex)
      })
    }

    const resize = () => {
      try {
        chart?.resize()
      } catch {
        // Ignore resize errors while the host is being unmounted.
      }
    }
    let observer: ResizeObserver | undefined
    if (isResizeObserverAvailable()) {
      observer = new ResizeObserver(resize)
      observer.observe(element)
    } else {
      window.addEventListener('resize', resize)
    }

    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', resize)
      try {
        chart?.dispose()
      } catch {
        // ECharts may already be disposed by a test teardown.
      }
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    try {
      chart.setOption(buildRainfallChartOption(samples, selectedIndex), {
        replaceMerge: ['series', 'xAxis', 'yAxis', 'grid', 'tooltip'],
        lazyUpdate: true,
      })
    } catch {
      // A chart failure must not prevent table editing or validation.
    }
  }, [samples, selectedIndex])

  return (
    <div
      ref={chartElementRef}
      className={className}
      data-testid="rainfall-chart"
      data-point-count={points.length}
      data-selected-index={selectedIndex ?? ''}
      aria-label="Rainfall intensity bar chart"
      role="img"
      style={{ width: '100%', height, minHeight: 180 }}
    />
  )
}

export default RainfallChart
