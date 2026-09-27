import { useEffect, useRef } from 'react'
import { init, type ECharts } from 'echarts/core'
import { ensureEChartsModules } from './echartsSetup'
import type { SimulationChartOption } from './chartOptions'

const REPLACED_COMPONENTS = ['series', 'xAxis', 'yAxis', 'grid', 'legend', 'tooltip']

export function useEChart(option: SimulationChartOption) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<ECharts | null>(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    let chart: ECharts | null = null
    try {
      ensureEChartsModules()
      chart = init(container, undefined, { renderer: 'canvas' })
      chartRef.current = chart
    } catch {
      chartRef.current = null
      return undefined
    }

    const resize = () => {
      try {
        chart?.resize()
      } catch {
        // A hidden/zero-sized panel may be disposed between observer callbacks.
      }
    }
    let observer: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(resize)
      observer.observe(container)
    } else {
      window.addEventListener('resize', resize)
    }

    return () => {
      observer?.disconnect()
      if (typeof ResizeObserver === 'undefined') window.removeEventListener('resize', resize)
      try {
        chart?.dispose()
      } catch {
        // ECharts may already be disposed during route teardown.
      }
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    try {
      chart.setOption(option, { replaceMerge: REPLACED_COMPONENTS })
    } catch {
      // Keep the surrounding page usable when canvas/option creation fails.
    }
  }, [option])

  return containerRef
}
