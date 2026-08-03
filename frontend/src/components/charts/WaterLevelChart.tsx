/**
 * 动态水位图 — ECharts 实现
 * 包含流动波光、呼吸灯效果、平滑过渡动画。
 */

import { useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { sensorAPI } from '@/services/api'
import { useAppStore } from '@store/useAppStore'

interface WaterLevelChartProps {
  deviceId: string
}

export function WaterLevelChart({ deviceId }: WaterLevelChartProps) {
  const chartRef = useRef<HTMLDivElement>(null)
  const instanceRef = useRef<echarts.ECharts | null>(null)
  const [data, setData] = useState<{ time: string; value: number }[]>([])
  const { readingHistory, selectedDeviceId } = useAppStore()

  useEffect(() => {
    if (!deviceId) return

    sensorAPI
      .getHistory(deviceId, 6, 5)
      .then((res) => {
        const points = (res.data.data || []).map((d) => ({
          time: new Date(d.time).toLocaleTimeString('zh-CN', {
            hour: '2-digit',
            minute: '2-digit',
          }),
          value: d.water_level_mm,
        }))
        setData(points)
      })
      .catch(() => {
        setData([])
      })
  }, [deviceId])

  useEffect(() => {
    const id = selectedDeviceId || deviceId
    if (!id) return
    const history = readingHistory.get(id)
    if (history && history.length > 0) {
      const recentData = history.slice(-30).map((r) => ({
        value: r.water_level_mm ?? 0,
        time: new Date(r.timestamp).toLocaleTimeString('zh-CN', {
          hour: '2-digit',
          minute: '2-digit',
        }),
      }))
      setData(recentData)
    }
  }, [readingHistory, selectedDeviceId, deviceId])

  useEffect(() => {
    if (!chartRef.current) return

    const chart = echarts.init(chartRef.current, 'dark')
    instanceRef.current = chart

    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(chartRef.current)

    return () => {
      observer.disconnect()
      chart.dispose()
    }
  }, [])

  useEffect(() => {
    const chart = instanceRef.current
    if (!chart || data.length === 0) return

    const option: echarts.EChartsOption = {
      backgroundColor: 'transparent',
      grid: {
        top: 10,
        right: 20,
        bottom: 20,
        left: 45,
      },
      xAxis: {
        type: 'category',
        data: data.map((d) => d.time),
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#2D3A4A' } },
        axisTick: { show: false },
        axisLabel: {
          color: '#556677',
          fontSize: 9,
          fontFamily: 'JetBrains Mono',
          interval: Math.max(1, Math.floor(data.length / 6)),
        },
      },
      yAxis: {
        type: 'value',
        name: 'mm',
        nameTextStyle: { color: '#556677', fontSize: 9 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#1A2332' } },
        axisLabel: { color: '#556677', fontSize: 9, fontFamily: 'JetBrains Mono' },
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(17, 24, 39, 0.95)',
        borderColor: '#00D4FF',
        textStyle: { color: '#E8EDF2', fontSize: 11, fontFamily: 'Inter' },
      },
      series: [
        {
          name: '液位',
          type: 'line',
          data: data.map((d) => d.value),
          smooth: true,
          symbol: 'none',
          sampling: 'lttb',
          lineStyle: {
            color: '#00D4FF',
            width: 2,
            shadowBlur: 15,
            shadowColor: 'rgba(0, 212, 255, 0.4)',
          },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: 'rgba(0, 212, 255, 0.25)' },
              { offset: 1, color: 'rgba(0, 212, 255, 0.02)' },
            ]),
          },
          markLine: {
            silent: true,
            symbol: 'none',
            data: [{ yAxis: 180, name: '警戒线' }],
            lineStyle: { color: '#FF3366', type: 'dashed', width: 1 },
            label: {
              color: '#FF3366',
              fontSize: 9,
              fontFamily: 'JetBrains Mono',
              formatter: '⚠ {c}mm',
            },
          },
        },
      ],
      animationDuration: 800,
      animationEasing: 'cubicInOut',
    }

    chart.setOption(option, true)
    chart.resize()
  }, [data])

  return <div ref={chartRef} className="w-full h-full" style={{ minHeight: 150 }} />
}
