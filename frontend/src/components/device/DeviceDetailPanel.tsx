/**
 * 设备详情面板 — 侧滑抽屉
 * 展示设备实时指标、历史图表、告警列表
 */

import { useEffect, useState } from 'react';
import { Drawer, Tag, Descriptions, Button, Empty } from 'antd';
import { CloseOutlined, EnvironmentOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { useAppStore } from '@/store/useAppStore';
import { WaterLevelChart } from '@components/charts/WaterLevelChart';
import { FlowTrendChart } from '@components/charts/FlowTrendChart';
import type { Device, SensorReading, Alert } from '@/types';

interface DeviceDetailPanelProps {
  open: boolean;
  onClose: () => void;
}

export function DeviceDetailPanel({ open, onClose }: DeviceDetailPanelProps) {
  const devices = useAppStore((s) => s.devices);
  const sensorReadings = useAppStore((s) => s.sensorReadings);
  const readingHistory = useAppStore((s) => s.readingHistory);
  const alerts = useAppStore((s) => s.alerts);
  const selectedDeviceId = useAppStore((s) => s.selectedDeviceId);
  const acknowledgeAlert = useAppStore((s) => s.acknowledgeAlert);

  const device = devices.find(d => d.id === selectedDeviceId);
  const reading = selectedDeviceId ? sensorReadings.get(selectedDeviceId) : undefined;
  const history = selectedDeviceId ? readingHistory.get(selectedDeviceId) || [] : [];

  const deviceAlerts = alerts.filter(a => a.device_id === selectedDeviceId && !a.is_resolved);
  const statusColor = device?.status === 'online' ? 'green'
    : device?.status === 'fault' ? 'orange' : 'default';

  if (!device) {
    return (
      <Drawer
        open={open}
        onClose={onClose}
        width={500}
        className="cyber-drawer"
        styles={{ body: { background: '#0A0E17', padding: 16 } }}
      >
        <Empty description="未选择设备" />
      </Drawer>
    );
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={500}
      className="cyber-drawer"
      title={
        <div className="flex items-center gap-2">
          <EnvironmentOutlined className="text-neon-blue" />
          <span className="font-display text-neon-blue tracking-wider">{device.name}</span>
          <Tag color={statusColor} className="font-mono text-[10px] ml-2">
            {device.status === 'online' ? '在线' : device.status === 'fault' ? '故障' : '离线'}
          </Tag>
        </div>
      }
      extra={
        <Button type="text" icon={<CloseOutlined />} onClick={onClose} className="text-text-muted" />
      }
      styles={{ body: { background: '#0A0E17', padding: 16 } }}
    >
      <div className="space-y-4">
        {/* 实时指标 */}
        <div className="panel-title"><ThunderboltOutlined /> 实时指标</div>
        {reading ? (
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: '液位', value: `${reading.water_level_mm.toFixed(0)}`, unit: 'mm', color: '#00D4FF' },
              { label: '流量', value: `${reading.flow_rate_m3h.toFixed(1)}`, unit: 'm³/h', color: '#00F5FF' },
              { label: '温度', value: `${reading.temperature_c.toFixed(1)}`, unit: '°C', color: '#FF8C00' },
              { label: '湿度', value: `${(reading.humidity_pct || 0).toFixed(0)}`, unit: '%', color: '#A855F7' },
              { label: '电池', value: `${reading.battery_level.toFixed(0)}`, unit: '%', color: reading.battery_level > 30 ? '#00FF88' : '#FF3366' },
              { label: '信号', value: `${reading.signal_strength}`, unit: '%', color: reading.signal_strength > 50 ? '#00FF88' : '#FF8C00' },
            ].map((m) => (
              <div key={m.label} className="bg-cyber-dark border border-cyber-border rounded p-3 text-center">
                <div className="text-text-muted text-[10px] font-mono mb-1">{m.label}</div>
                <div className="font-mono text-lg" style={{ color: m.color }}>
                  {m.value}
                  <span className="text-text-muted text-[10px] ml-0.5">{m.unit}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-text-muted text-xs font-mono">等待传感器数据...</div>
        )}

        {/* 历史趋势 */}
        {selectedDeviceId && (
          <>
            <div className="panel-title">📊 水位趋势 (近2小时)</div>
            <div className="h-[160px]">
              <WaterLevelChart deviceId={selectedDeviceId} />
            </div>
            <div className="h-[160px]">
              <FlowTrendChart deviceId={selectedDeviceId} />
            </div>
          </>
        )}

        {/* 设备信息 */}
        <div className="panel-title">📋 设备信息</div>
        <Descriptions column={1} size="small" bordered
          styles={{
            label: { color: '#8899AA', fontSize: 11, padding: '4px 8px' },
            content: { color: '#E8EDF2', fontSize: 11, padding: '4px 8px' },
          }}>
          <Descriptions.Item label="编号">{device.code}</Descriptions.Item>
          <Descriptions.Item label="类型">{device.device_type}</Descriptions.Item>
          <Descriptions.Item label="区域">{device.district || '--'}</Descriptions.Item>
          <Descriptions.Item label="坐标">
            <span className="font-mono text-neon-blue">{device.lat.toFixed(4)}, {device.lng.toFixed(4)}</span>
          </Descriptions.Item>
        </Descriptions>

        {/* 告警历史 */}
        <div className="panel-title">🚨 相关告警</div>
        {deviceAlerts.length > 0 ? (
          <div className="space-y-2">
            {deviceAlerts.slice(0, 10).map((a) => (
              <div key={a.id} className="flex items-center justify-between p-2 bg-cyber-dark border border-cyber-border rounded">
                <div className="flex-1 min-w-0">
                  <div className="text-text-primary text-xs truncate">{a.title}</div>
                  <div className="text-text-muted text-[10px] font-mono">
                    {new Date(a.created_at).toLocaleTimeString('zh-CN')}
                    <Tag color={a.level === 'critical' ? 'red' : a.level === 'warning' ? 'orange' : 'blue'}
                         className="ml-2 font-mono text-[9px] leading-none">
                      {a.level}
                    </Tag>
                  </div>
                </div>
                {!a.is_acknowledged && (
                  <Button size="small" type="link" onClick={() => acknowledgeAlert(a.id)}
                          className="text-neon-blue text-xs shrink-0">
                    确认
                  </Button>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-text-muted text-xs font-mono">该设备暂无告警</div>
        )}
      </div>
    </Drawer>
  );
}
