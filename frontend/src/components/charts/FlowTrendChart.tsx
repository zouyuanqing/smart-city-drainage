/**
 * 流量趋势图 — ECharts 实现
 * 柱状图 + 流动渐变效果。
 */

import { useEffect, useRef, useState } from 'react';
import * as echarts from 'echarts';
import { sensorAPI } from '@/services/api';
import { useAppStore } from '@store/useAppStore';

interface FlowTrendChartProps {
  deviceId: string;
}

export function FlowTrendChart({ deviceId }: FlowTrendChartProps) {
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);
  const [data, setData] = useState<{ time: string; value: number }[]>([]);
  const { readingHistory, selectedDeviceId } = useAppStore();

  useEffect(() => {
    if (!deviceId) return;

    sensorAPI.getHistory(deviceId, 6, 5).then(res => {
      const points = (res.data.data || []).map(d => ({
        time: new Date(d.time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        value: d.flow_rate_m3h,
      }));
      setData(points);
    }).catch(() => {
      setData([]);
    });
  }, [deviceId]);

  useEffect(() => {
    const id = selectedDeviceId || deviceId;
    if (!id) return;
    const history = readingHistory.get(id);
    if (history && history.length > 0) {
      const recentData = history.slice(-30).map(r => ({
        value: r.flow_rate_m3h ?? 0,
        time: new Date(r.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
      }));
      setData(recentData);
    }
  }, [readingHistory, selectedDeviceId, deviceId]);

  useEffect(() => {
    if (!chartRef.current) return;

    const chart = echarts.init(chartRef.current, 'dark');
    instanceRef.current = chart;

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(chartRef.current);

    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, []);

  useEffect(() => {
    const chart = instanceRef.current;
    if (!chart || data.length === 0) return;

    const option: echarts.EChartsOption = {
      backgroundColor: 'transparent',
      grid: {
        top: 10, right: 20, bottom: 20, left: 45,
      },
      xAxis: {
        type: 'category',
        data: data.map(d => d.time),
        axisLine: { lineStyle: { color: '#2D3A4A' } },
        axisTick: { show: false },
        axisLabel: {
          color: '#556677',
          fontSize: 9,
          fontFamily: 'JetBrains Mono',
          interval: Math.max(1, Math.floor(data.length / 5)),
        },
      },
      yAxis: {
        type: 'value',
        name: 'm³/h',
        nameTextStyle: { color: '#556677', fontSize: 9 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: '#1A2332' } },
        axisLabel: { color: '#556677', fontSize: 9, fontFamily: 'JetBrains Mono' },
      },
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(17, 24, 39, 0.95)',
        borderColor: '#A855F7',
        textStyle: { color: '#E8EDF2', fontSize: 11, fontFamily: 'Inter' },
      },
      series: [
        {
          name: '流量',
          type: 'bar',
          data: data.map(d => d.value),
          barWidth: '60%',
          itemStyle: {
            borderRadius: [3, 3, 0, 0],
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: '#A855F7' },
              { offset: 0.5, color: '#7C3AED' },
              { offset: 1, color: 'rgba(168, 85, 247, 0.2)' },
            ]),
          },
          emphasis: {
            itemStyle: {
              color: '#C084FC',
              shadowBlur: 15,
              shadowColor: 'rgba(168, 85, 247, 0.6)',
            },
          },
        },
      ],
      animationDuration: 600,
      animationEasing: 'elasticOut',
    };

    chart.setOption(option, true);
    chart.resize();
  }, [data]);

  return (
    <div
      ref={chartRef}
      className="w-full h-full"
      style={{ minHeight: 150 }}
    />
  );
}
