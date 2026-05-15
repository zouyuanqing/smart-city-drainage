/**
 * GIS 地图全屏页 — 独立的地图监控视图
 */

import { useEffect, useState } from 'react';
import { MapVisualization } from '@components/map/MapVisualization';
import { Loading } from '@components/common';
import { useAppStore } from '@/store/useAppStore';
import { useSSE } from '@/hooks/useSSE';
import { deviceAPI } from '@/services/api';
import type { SensorReading, Alert } from '@/types';

export function MapView() {
  const {
    sensorReadings, setSensorReadings,
    alerts, addAlert,
    devices, setDevices,
    selectedDeviceId, setSelectedDevice,
  } = useAppStore();

  const [loading, setLoading] = useState(true);

  useSSE({
    onSensorData: (readings: SensorReading[]) => setSensorReadings(readings),
    onAlert: (alert: Alert) => addAlert(alert),
  });

  useEffect(() => {
    Promise.all([deviceAPI.getList()]).then(([devRes]) => {
      setDevices(
        (devRes.data.devices || []).map((d: any) => ({
          id: d.id,
          code: d.code,
          name: d.name,
          device_type: d.device_type || 'manhole_cover',
          status: d.status || 'offline',
          lat: d.lat,
          lng: d.lng,
          district: d.district,
          battery_level: d.battery_level ?? 0,
          signal_strength: d.signal_strength ?? 0,
        }))
      );
    }).catch(console.error).finally(() => setLoading(false));
  }, []);

  if (loading) return <Loading tip="加载地图数据..." fullScreen />;

  return (
    <div className="h-full flex flex-col gap-3">
      <div className="panel-title">🌍 GIS 城市排水管网可视化</div>
      <div className="flex-1 map-container rounded-lg">
        <MapVisualization
          devices={devices}
          readings={sensorReadings}
          selectedDeviceId={selectedDeviceId}
          onDeviceClick={(id) => setSelectedDevice(id)}
          alerts={alerts.filter(a => !a.is_resolved)}
          onAlertClick={(alert) => setSelectedDevice(alert.device_id || null)}
        />
      </div>
    </div>
  );
}
