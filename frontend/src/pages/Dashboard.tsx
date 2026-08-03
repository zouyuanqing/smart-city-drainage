/**
 * Dashboard 大屏驾驶舱 - 主组件
 * 三栏栅格布局: 左侧设备看板 | 中间地图+视频 | 右侧告警+图表
 * 4K 分辨率优化，自适应布局
 */

import { useEffect, useState, useCallback } from 'react'
import { motion } from 'framer-motion'
import { Badge, Tag, Spin, Drawer, Button, Tooltip, notification } from 'antd'
import { useAlertNotifications } from '@/hooks/useAlertNotifications'
import {
  ThunderboltOutlined,
  WifiOutlined,
  CloseCircleOutlined,
  SettingOutlined,
  FullscreenOutlined,
  ReloadOutlined,
  UpOutlined,
  DownOutlined,
  DownloadOutlined,
} from '@ant-design/icons'
import { useAppStore } from '@/store/useAppStore'
import { useSSE } from '@/hooks/useSSE'
import { sensorAPI, deviceAPI, streamAPI, exportAPI } from '@/services/api'
import { MapVisualization } from '@components/map/MapVisualization'
import { VideoPlayer } from '@components/video/VideoPlayer'
import { AlertPanel } from '@components/alerts/AlertPanel'
import { DeviceDetailPanel } from '@components/device/DeviceDetailPanel'
import { DeviceSearchFilter } from '@components/device/DeviceSearchFilter'
import { WaterLevelChart } from '@components/charts/WaterLevelChart'
import { FlowTrendChart } from '@components/charts/FlowTrendChart'
import type { SensorReading, Alert, Device } from '@/types'

// ---- 设备状态卡片 ----
function DeviceCard({ device, reading }: { device: Device; reading?: SensorReading }) {
  return (
    <motion.div
      className="relative bg-cyber-dark border border-cyber-border rounded-lg p-3
                 hover:border-neon-blue/50 transition-all cursor-pointer"
      whileHover={{ scale: 1.02 }}
      layout
    >
      {/* 状态指示灯 */}
      <div className="absolute top-2 right-2">
        <span className={`status-indicator ${device.status}`} />
      </div>

      <div className="mb-2">
        <div className="text-text-primary text-sm font-medium truncate">{device.name}</div>
        <div className="text-text-muted text-[10px] font-mono">{device.code}</div>
      </div>

      {reading ? (
        <div className="space-y-1.5">
          <div className="flex justify-between text-xs">
            <span className="text-text-muted">液位</span>
            <span className="font-mono text-neon-blue">
              {reading.water_level_mm.toFixed(0)}{' '}
              <span className="text-[10px] text-text-muted">mm</span>
            </span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-text-muted">流量</span>
            <span className="font-mono text-neon-cyan">
              {reading.flow_rate_m3h.toFixed(1)}{' '}
              <span className="text-[10px] text-text-muted">m³/h</span>
            </span>
          </div>
          <div className="flex justify-between text-xs">
            <span className="text-text-muted">电池</span>
            <span
              className="font-mono"
              style={{
                color: reading.battery_level > 30 ? '#00FF88' : '#FF8C00',
              }}
            >
              {reading.battery_level.toFixed(0)}%
            </span>
          </div>
          {/* 信号强度条 */}
          <div className="flex items-center gap-1">
            <WifiOutlined className="text-[10px] text-text-muted" />
            <div className="flex-1 h-1 bg-cyber-medium rounded-full overflow-hidden">
              <div
                className="h-full bg-neon-green rounded-full transition-all"
                style={{ width: `${reading.signal_strength}%` }}
              />
            </div>
          </div>
        </div>
      ) : (
        <div className="text-text-muted text-xs italic">等待数据...</div>
      )}
    </motion.div>
  )
}

// ---- 全屏数字时钟 ----
function DigitalClock() {
  const [time, setTime] = useState('')

  useEffect(() => {
    const tick = () => {
      const now = new Date()
      setTime(now.toLocaleTimeString('zh-CN', { hour12: false }))
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="text-center">
      <div className="font-display text-4xl font-bold neon-text tracking-widest">{time}</div>
      <div className="text-text-muted text-xs font-mono mt-1">系统运行中</div>
    </div>
  )
}

// ---- 实时统计条 ----
function StatsBar() {
  const { sensorReadings, alerts } = useAppStore()
  const onlineCount = Array.from(sensorReadings.values()).filter((r) => r.battery_level > 0).length
  const totalAlerts = alerts.length
  const criticalAlerts = alerts.filter((a) => a.level === 'critical' && !a.is_resolved).length

  return (
    <div className="grid grid-cols-3 lg:grid-cols-5 gap-3">
      {[
        {
          label: '在线设备',
          value: onlineCount,
          unit: '/8',
          color: '#00FF88',
          icon: <ThunderboltOutlined />,
        },
        {
          label: '总告警',
          value: totalAlerts,
          unit: '条',
          color: '#FF8C00',
          icon: <CloseCircleOutlined />,
        },
        {
          label: '紧急告警',
          value: criticalAlerts,
          unit: '条',
          color: '#FF3366',
          icon: <CloseCircleOutlined />,
        },
        {
          label: 'AI 推理',
          value: 'v1',
          unit: 'active',
          color: '#00D4FF',
          icon: <SettingOutlined />,
        },
        { label: '网络延迟', value: '<10', unit: 'ms', color: '#A855F7', icon: <WifiOutlined /> },
      ].map((s, i) => (
        <div
          key={i}
          className="bg-cyber-dark border border-cyber-border rounded-lg p-3 text-center"
        >
          <div className="text-text-muted text-[10px] font-mono uppercase tracking-wider mb-1">
            {s.label}
          </div>
          <div className="flex items-baseline justify-center gap-1">
            <span className="data-value text-xl" style={{ color: s.color }}>
              {s.value}
            </span>
            <span className="text-text-muted text-xs">{s.unit}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

// ---- 主 Dashboard ----
export function Dashboard() {
  const {
    sensorReadings,
    setSensorReadings,
    alerts,
    addAlert,
    acknowledgeAlert,
    devices,
    setDevices,
    selectedDeviceId,
    setSelectedDevice,
    setSelectedAlert,
  } = useAppStore()

  const [isLoading, setIsLoading] = useState(true)
  const [videoPanelVisible, setVideoPanelVisible] = useState(false)
  const [detailPanelVisible, setDetailPanelVisible] = useState(false)
  const [filteredDevices, setFilteredDevices] = useState<Device[]>([])
  const [alertsCollapsed, setAlertsCollapsed] = useState(false)
  const [activeStreams, setActiveStreams] = useState<any[]>([])

  const handleExportSensorData = useCallback(async () => {
    try {
      const params: { device_id?: string } = {}
      if (selectedDeviceId) params.device_id = selectedDeviceId
      const res = await exportAPI.sensorData(params)
      const blob = new Blob([res.data], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `sensor_data_${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(url)
      notification.success({ message: '导出成功', description: '传感器数据已下载' })
    } catch {
      notification.error({ message: '导出失败', description: '请稍后重试' })
    }
  }, [selectedDeviceId])

  // 告警通知弹窗
  useAlertNotifications()

  // SSE 连接 — 实时接收传感器和告警数据
  const { isConnected, reconnect } = useSSE({
    onSensorData: (readings: SensorReading[]) => {
      setSensorReadings(readings)
    },
    onAlert: (alert: Alert) => {
      addAlert(alert)
    },
    onConnected: () => {
      console.info('✅ SSE 实时数据流已连接')
    },
    onDisconnected: () => {
      console.warn('⚠️ SSE 连接断开，尝试重连...')
    },
  })

  // 初始化加载
  useEffect(() => {
    Promise.all([deviceAPI.getList(), sensorAPI.getLatest()])
      .then(([devRes, sensorRes]) => {
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
        )
        setSensorReadings(sensorRes.data.readings || [])
      })
      .catch(console.error)
      .finally(() => setIsLoading(false))
  }, [setDevices, setSensorReadings])

  useEffect(() => {
    streamAPI
      .getStatus()
      .then((res) => {
        const streams = Object.entries(res.data.streams || {})
          .filter(([_, info]: [string, any]) => info.is_active)
          .map(([id, info]: [string, any]) => ({
            id,
            name: info.name || id,
            hlsUrl: `/hls/${id}/index.m3u8`,
          }))
        setActiveStreams(streams)
      })
      .catch(() => {})
  }, [])

  // 告警点击 → 打开视频面板
  const handleAlertClick = useCallback(
    (alert: Alert) => {
      setSelectedAlert(alert.id)
      if (alert.device_id) {
        setSelectedDevice(alert.device_id)
      }
      setVideoPanelVisible(true)
    },
    [setSelectedAlert, setSelectedDevice]
  )

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Spin size="large" tip="正在加载城市神经末梢系统..." />
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col gap-3">
      {/* 顶部统计条 + 时钟 */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-3">
        <div className="lg:col-span-4">
          <StatsBar />
        </div>
        <div className="hidden lg:flex items-center justify-center bg-cyber-dark border border-cyber-border rounded-lg p-2">
          <DigitalClock />
        </div>
      </div>

      {/* 三栏主布局 */}
      <div className="flex-1 grid grid-cols-12 gap-3 min-h-0">
        {/* ============ 左侧: 设备状态看板 ============ */}
        <div className="col-span-12 lg:col-span-2 flex flex-col gap-3">
          <div className="panel-title">
            <ThunderboltOutlined /> 设备状态
          </div>
          <DeviceSearchFilter devices={devices} onFilterChange={setFilteredDevices} />
          <div className="flex-1 overflow-y-auto space-y-2 pr-1 custom-scrollbar">
            {(filteredDevices.length > 0 || devices.length > 0
              ? filteredDevices.length > 0
                ? filteredDevices
                : devices
              : devices
            ).map((device) => (
              <div
                key={device.id}
                onClick={() => {
                  const isDeselecting = device.id === selectedDeviceId
                  setSelectedDevice(isDeselecting ? null : device.id)
                  setDetailPanelVisible(!isDeselecting)
                }}
              >
                <DeviceCard device={device} reading={sensorReadings.get(device.id)} />
              </div>
            ))}
          </div>

          {/* SSE 状态指示器 */}
          <div className="bg-cyber-dark border border-cyber-border rounded-lg p-3">
            <div className="flex items-center justify-between">
              <span className="text-text-muted text-xs font-mono">实时数据流</span>
              <Badge
                status={isConnected ? 'processing' : 'error'}
                color={isConnected ? 'cyan' : 'red'}
                text={isConnected ? 'ONLINE' : 'OFFLINE'}
              />
            </div>
            {!isConnected && (
              <Button
                size="small"
                icon={<ReloadOutlined />}
                onClick={reconnect}
                className="w-full mt-2 font-mono text-[10px]"
              >
                重连
              </Button>
            )}
          </div>
        </div>

        {/* ============ 中间: GIS 地图 + 视频矩阵 ============ */}
        <div className="col-span-12 lg:col-span-7 flex flex-col gap-3">
          {/* GIS 地图 */}
          <div className="flex-1 min-h-[350px]">
            <div className="panel-title">
              <FullscreenOutlined /> 城市排水数字孪生
            </div>
            <div className="h-[calc(100%-30px)] map-container rounded-lg">
              <MapVisualization
                devices={devices}
                readings={sensorReadings}
                selectedDeviceId={selectedDeviceId}
                onDeviceClick={(id) => setSelectedDevice(id)}
                alerts={alerts.filter((a) => !a.is_resolved)}
                onAlertClick={handleAlertClick}
              />
            </div>
          </div>

          {/* 视频矩阵 (底部) */}
          <div className="h-[200px]">
            <div className="panel-title">
              📹 实时视频矩阵
              <Tooltip title="全屏视频监控">
                <Button
                  type="text"
                  size="small"
                  icon={<FullscreenOutlined />}
                  onClick={() => setVideoPanelVisible(true)}
                  className="ml-auto text-neon-blue"
                />
              </Tooltip>
            </div>
            <div className="grid grid-cols-3 gap-2 h-[calc(100%-30px)]">
              {Array.from({ length: 3 }, (_, i) => activeStreams[i] || null).map((stream, i) => (
                <div key={i} className="video-container relative">
                  <div className="absolute inset-0 flex items-center justify-center bg-cyber-black text-text-muted text-xs">
                    {stream ? (
                      <VideoPlayer streamType="hls" streamUrl={stream.hlsUrl} />
                    ) : (
                      <div className="text-center">
                        <div className="w-6 h-6 mx-auto mb-1 border border-cyber-border rounded-full flex items-center justify-center">
                          <span className="text-neon-blue text-sm">📷</span>
                        </div>
                        <span className="font-mono text-[10px]">CAM-{i + 1}</span>
                        <br />
                        <span className="text-text-muted text-[10px]">等待视频流...</span>
                      </div>
                    )}
                  </div>
                  <div className="absolute top-2 left-2 z-10">
                    <Tag color={stream ? 'green' : 'cyan'} className="font-mono text-[10px]">
                      {stream ? stream.name : 'LIVE'}
                    </Tag>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ============ 右侧: 告警 + 图表 ============ */}
        <div className="col-span-12 lg:col-span-3 flex flex-col gap-3">
          {/* 告警滚动列表 (可折叠) */}
          <div className={alertsCollapsed ? '' : 'flex-1 min-h-0'}>
            <div
              className="panel-title cursor-pointer select-none"
              onClick={() => setAlertsCollapsed(!alertsCollapsed)}
            >
              <CloseCircleOutlined /> 实时告警
              <Badge
                count={alerts.filter((a) => !a.is_acknowledged).length}
                className="ml-2"
                size="small"
              />
              <span className="ml-auto text-text-muted">
                {alertsCollapsed ? <DownOutlined /> : <UpOutlined />}
              </span>
            </div>
            {!alertsCollapsed && (
              <div className="h-[calc(100%-30px)]">
                <AlertPanel
                  alerts={alerts}
                  onAcknowledge={(id) => acknowledgeAlert(id)}
                  onAlertClick={handleAlertClick}
                />
              </div>
            )}
          </div>

          {/* 水位趋势图 */}
          <div className={alertsCollapsed ? 'flex-1 min-h-[180px]' : 'h-[180px]'}>
            <div className="panel-title flex items-center">
              <span>📊 液位实时趋势</span>
              <Button
                size="small"
                icon={<DownloadOutlined />}
                onClick={handleExportSensorData}
                className="ml-auto text-[10px]"
                type="text"
              >
                导出数据
              </Button>
            </div>
            <div className="h-[calc(100%-30px)]">
              <WaterLevelChart deviceId={selectedDeviceId || devices[0]?.id} />
            </div>
          </div>

          {/* 流量趋势图 */}
          <div className={alertsCollapsed ? 'flex-1 min-h-[180px]' : 'h-[180px]'}>
            <div className="panel-title">📈 流量实时监测</div>
            <div className="h-[calc(100%-30px)]">
              <FlowTrendChart deviceId={selectedDeviceId || devices[0]?.id} />
            </div>
          </div>

          {/* 部署信息 */}
          <div className="bg-cyber-dark border border-cyber-border rounded-lg p-3">
            <div className="text-text-muted text-[10px] font-mono uppercase tracking-wider mb-1">
              系统运行状态
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-text-muted">SSE: </span>
                <span className="text-neon-green font-mono">● 正常</span>
              </div>
              <div>
                <span className="text-text-muted">WS: </span>
                <span className="text-neon-green font-mono">● 正常</span>
              </div>
              <div>
                <span className="text-text-muted">AI: </span>
                <span className="text-neon-green font-mono">● 就绪</span>
              </div>
              <div>
                <span className="text-text-muted">流: </span>
                <span className="text-neon-yellow font-mono">● 等待</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 设备详情面板 */}
      <DeviceDetailPanel
        open={detailPanelVisible}
        onClose={() => {
          setDetailPanelVisible(false)
          setSelectedDevice(null)
        }}
      />

      {/* 视频抽屉 (弹窗) */}
      <Drawer
        title={<span className="font-display text-neon-blue tracking-wider">📹 视频监控详情</span>}
        placement="right"
        width="70%"
        open={videoPanelVisible}
        onClose={() => setVideoPanelVisible(false)}
        className="cyber-drawer"
        styles={{ body: { background: '#0A0E17', padding: 16 } }}
      >
        <div className="grid grid-cols-2 gap-4">
          <div className="video-container aspect-video">
            {activeStreams.length > 0 ? (
              <VideoPlayer streamType="hls" streamUrl={activeStreams[0].hlsUrl} />
            ) : (
              <div className="flex items-center justify-center h-full text-text-muted text-xs">
                暂无活跃视频流
              </div>
            )}
            <div className="absolute top-2 left-2 z-10">
              <Tag color="cyan" className="font-mono text-[10px]">
                RTSP → HLS
              </Tag>
            </div>
          </div>
          <div className="video-container aspect-video">
            <VideoPlayer streamType="local" />
            <div className="absolute top-2 left-2 z-10">
              <Tag color="purple" className="font-mono text-[10px]">
                本地摄像头
              </Tag>
            </div>
          </div>
        </div>
        <div className="mt-4 p-4 bg-cyber-dark border border-cyber-border rounded-lg">
          <div className="text-text-secondary text-sm">
            选择设备:
            <span className="font-mono text-neon-blue ml-2">
              {devices.find((d) => d.id === selectedDeviceId)?.name || '未选择'}
            </span>
          </div>
        </div>
      </Drawer>
    </div>
  )
}
