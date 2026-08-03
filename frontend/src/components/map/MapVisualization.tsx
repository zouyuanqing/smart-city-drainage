/**
 * GIS 地图可视化组件
 * Leaflet + 深色瓦片 + 脉冲标记 + 设备交互
 */

import { useEffect, useRef, useMemo, useCallback, memo } from 'react'
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { motion, AnimatePresence } from 'framer-motion'
import type { Device, SensorReading, Alert } from '@/types'

// ---- 深色地图瓦片 ----
const DARK_TILES = {
  url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
}

// ---- 颜色常量 ----
const COLORS = {
  online: '#00FF88',
  offline: '#667788',
  fault: '#FF8C00',
  alert: '#FF3366',
} as const

function createIcon(color: string): L.DivIcon {
  const html = `
    <div class="pulse-marker-wrapper" style="position:relative;width:28px;height:28px;">
      <div class="pulse-marker-ring" style="color:${color}"></div>
      <div class="pulse-marker-dot" style="background:${color};box-shadow:0 0 10px ${color},0 0 20px ${color}66"></div>
    </div>`

  return L.divIcon({
    className: '',
    html,
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -16],
  })
}

// ---- 单个设备标记 (memoized) ----
interface DeviceMarkerProps {
  device: Device
  reading?: SensorReading
  isSelected: boolean
  hasAlert: boolean
  onClick: () => void
}

const DeviceMarker = memo(
  function DeviceMarker({ device, reading, isSelected, hasAlert, onClick }: DeviceMarkerProps) {
    const color = hasAlert
      ? COLORS.alert
      : device.status === 'online'
        ? COLORS.online
        : device.status === 'fault'
          ? COLORS.fault
          : COLORS.offline

    const icon = createIcon(color)

    if (isSelected) {
      icon.options.iconSize = [36, 36]
      icon.options.iconAnchor = [18, 18]
    }

    return (
      <Marker position={[device.lat, device.lng]} icon={icon} eventHandlers={{ click: onClick }}>
        <Popup className="cyber-popup" maxWidth={280} minWidth={200}>
          <div className="bg-cyber-dark text-text-primary p-3 rounded-lg font-sans">
            <div className="text-sm font-bold mb-1">{device.name}</div>
            <div className="text-[10px] text-text-muted font-mono mb-2">
              {device.code} · {device.district}
            </div>

            {reading ? (
              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                <div className="text-text-muted">液位</div>
                <div className="font-mono text-neon-blue text-right">
                  {reading.water_level_mm.toFixed(0)} mm
                </div>
                <div className="text-text-muted">流量</div>
                <div className="font-mono text-neon-cyan text-right">
                  {reading.flow_rate_m3h.toFixed(1)} m³/h
                </div>
                <div className="text-text-muted">温度</div>
                <div className="font-mono text-right">{reading.temperature_c.toFixed(1)}°C</div>
                <div className="text-text-muted">电池</div>
                <div
                  className="font-mono text-right"
                  style={{
                    color: reading.battery_level > 30 ? '#00FF88' : '#FF8C00',
                  }}
                >
                  {reading.battery_level.toFixed(0)}%
                </div>
              </div>
            ) : (
              <div className="text-text-muted text-xs italic">等待数据...</div>
            )}

            {hasAlert && (
              <div className="mt-2 p-2 bg-neon-red/10 border border-neon-red/30 rounded text-xs text-neon-red font-mono">
                ⚠ 有待处理告警
              </div>
            )}

            {!hasAlert && device.status === 'online' && (
              <div className="mt-2 text-[10px] text-neon-green font-mono">● 运行正常</div>
            )}
          </div>
        </Popup>
      </Marker>
    )
  },
  (prev, next) =>
    prev.device.id === next.device.id &&
    prev.device.status === next.device.status &&
    prev.isSelected === next.isSelected &&
    prev.hasAlert === next.hasAlert
)

// ---- 告警覆盖层 ----
function AlertOverlay({
  alerts,
  onAlertClick,
}: {
  alerts: Alert[]
  onAlertClick: (alert: Alert) => void
}) {
  return (
    <div className="absolute top-4 right-4 z-[1000] space-y-2 max-w-xs pointer-events-none">
      <AnimatePresence>
        {alerts
          .filter((a) => !a.is_acknowledged)
          .slice(0, 5)
          .map((alert) => (
            <motion.div
              key={alert.id}
              initial={{ opacity: 0, x: 50, scale: 0.9 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 50 }}
              className={`p-3 rounded-lg cursor-pointer backdrop-blur-md border pointer-events-auto
              ${
                alert.level === 'critical'
                  ? 'bg-neon-red/10 border-neon-red/50'
                  : alert.level === 'warning'
                    ? 'bg-neon-orange/10 border-neon-orange/50'
                    : 'bg-neon-blue/10 border-neon-blue/50'
              }`}
              onClick={() => onAlertClick(alert)}
            >
              <div className="flex items-center gap-2">
                <span>
                  {alert.level === 'critical' ? '🔴' : alert.level === 'warning' ? '🟠' : '🔵'}
                </span>
                <span className="text-white text-xs font-medium truncate">{alert.title}</span>
              </div>
            </motion.div>
          ))}
      </AnimatePresence>
    </div>
  )
}

// ---- 地图控制 ----
function MapController({
  selectedDeviceId,
  devices,
}: {
  selectedDeviceId: string | null
  devices: Device[]
}) {
  const map = useMap()
  const hasFittedRef = useRef(false)

  // 容器尺寸变化时自动刷新地图
  useEffect(() => {
    const container = map.getContainer()
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(container)
    return () => observer.disconnect()
  }, [map])

  // 设备加载后自动适配范围
  useEffect(() => {
    if (devices.length === 0 || hasFittedRef.current) return
    const bounds = L.latLngBounds(devices.map((d) => [d.lat, d.lng] as [number, number]))
    if (bounds.isValid()) {
      hasFittedRef.current = true
      map.fitBounds(bounds, { padding: [60, 60], maxZoom: 15, animate: true, duration: 1.5 })
    }
  }, [devices, map])

  // 选中设备飞入
  useEffect(() => {
    if (!selectedDeviceId) return
    const device = devices.find((d) => d.id === selectedDeviceId)
    if (device) {
      map.flyTo([device.lat, device.lng], 18, { duration: 1.2 })
    }
  }, [selectedDeviceId, devices, map])

  return null
}

// ---- 图例 ----
function MapLegend() {
  const items = [
    { color: COLORS.online, label: '在线设备' },
    { color: COLORS.offline, label: '离线设备' },
    { color: COLORS.fault, label: '故障设备' },
    { color: COLORS.alert, label: '活跃告警', pulse: true },
  ]

  return (
    <div
      className="absolute bottom-3 left-3 z-[1000] bg-cyber-dark/90 backdrop-blur-sm
                    border border-cyber-border rounded-lg px-3 py-2
                    font-mono text-[10px] text-text-secondary"
    >
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-2 py-0.5">
          <span
            className={`inline-block w-2.5 h-2.5 rounded-full ${item.pulse ? 'pulse-dot' : ''}`}
            style={{ background: item.color, boxShadow: `0 0 6px ${item.color}` }}
          />
          <span>{item.label}</span>
        </div>
      ))}
    </div>
  )
}

// ---- 主组件 ----
interface MapVisualizationProps {
  devices: Device[]
  readings: Map<string, SensorReading>
  selectedDeviceId: string | null
  onDeviceClick: (id: string) => void
  alerts: Alert[]
  onAlertClick: (alert: Alert) => void
}

export function MapVisualization({
  devices,
  readings,
  selectedDeviceId,
  onDeviceClick,
  alerts,
  onAlertClick,
}: MapVisualizationProps) {
  const center: [number, number] = [31.23, 121.47]

  // 预计算有活跃告警的设备 ID 集合 (避免每个标记都遍历)
  const alertDeviceIds = useMemo(() => {
    const set = new Set<string>()
    for (const a of alerts) {
      if (!a.is_resolved && a.device_id) set.add(a.device_id)
    }
    return set
  }, [alerts])

  const handleDeviceClick = useCallback(
    (id: string) => {
      onDeviceClick(id === selectedDeviceId ? '' : id)
    },
    [onDeviceClick, selectedDeviceId]
  )

  return (
    <div className="relative w-full h-full">
      <MapContainer
        center={center}
        zoom={13}
        style={{ width: '100%', height: '100%' }}
        zoomControl={true}
        attributionControl={false}
      >
        <TileLayer url={DARK_TILES.url} attribution={DARK_TILES.attribution} />

        <MapController selectedDeviceId={selectedDeviceId} devices={devices} />

        {devices.map((device) => (
          <DeviceMarker
            key={device.id}
            device={device}
            reading={readings.get(device.id)}
            isSelected={device.id === selectedDeviceId}
            hasAlert={alertDeviceIds.has(device.id)}
            onClick={() => handleDeviceClick(device.id)}
          />
        ))}
      </MapContainer>

      {/* HUD 扫描线 */}
      <div className="absolute inset-0 pointer-events-none scan-line" />

      {/* 告警浮层 */}
      <AlertOverlay alerts={alerts} onAlertClick={onAlertClick} />

      {/* 图例 */}
      <MapLegend />

      {/* 左上角 HUD 信息 */}
      <div
        className="absolute top-3 left-3 z-[1000] bg-cyber-dark/80 backdrop-blur-sm
                      border border-cyber-border rounded px-3 py-1.5
                      font-mono text-[10px] text-neon-blue"
      >
        <div>📍 设备: {devices.length}</div>
        <div>⚠️ 告警: {alertDeviceIds.size}</div>
        <div>🟢 在线: {devices.filter((d) => d.status === 'online').length}</div>
      </div>
    </div>
  )
}
