/**
 * 告警中心全屏页 — 告警管理与统计分析
 */

import { useEffect, useState, useCallback } from 'react'
import { Select, Button, message, Badge, Descriptions, Tag } from 'antd'
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
  DownloadOutlined,
} from '@ant-design/icons'
import { AlertPanel } from '@components/alerts/AlertPanel'
import { Loading, EmptyState } from '@components/common'
import { useAppStore } from '@/store/useAppStore'
import { useSSE } from '@/hooks/useSSE'
import { alertAPI, exportAPI } from '@/services/api'
import type { Alert, AlertLevel } from '@/types'

export function AlertCenter() {
  const { alerts, addAlert, acknowledgeAlert, resolveAlert, selectedAlertId, setSelectedAlert } =
    useAppStore()
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<AlertLevel | 'all'>('all')
  const [fetchedAlerts, setFetchedAlerts] = useState<Alert[]>([])

  const handleExportCSV = useCallback(async () => {
    try {
      const params: { level?: string } = {}
      if (filter !== 'all') params.level = filter
      const res = await exportAPI.alertData(params)
      const blob = new Blob([res.data], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `alerts_${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch {
      message.error('导出失败')
    }
  }, [filter])

  useSSE({
    onAlert: (alert: Alert) => addAlert(alert),
  })

  const fetchAlerts = useCallback(async () => {
    setLoading(true)
    try {
      const res = await alertAPI.getList(200, filter === 'all' ? undefined : filter)
      setFetchedAlerts(res.data.alerts || [])
    } catch {
      message.error('获取告警列表失败')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    fetchAlerts()
  }, [fetchAlerts])

  // 合并 SSE 实时告警和 API 历史告警，去重
  const seenIds = new Set<string>()
  const mergedAlerts = [...alerts, ...fetchedAlerts].filter((a) => {
    if (seenIds.has(a.id)) return false
    seenIds.add(a.id)
    return true
  })
  const filteredAlerts = mergedAlerts.filter((a) => filter === 'all' || a.level === filter)

  const stats = {
    total: mergedAlerts.length,
    unacknowledged: mergedAlerts.filter((a) => !a.is_acknowledged).length,
    critical: mergedAlerts.filter((a) => a.level === 'critical' && !a.is_resolved).length,
    resolved: mergedAlerts.filter((a) => a.is_resolved).length,
  }

  const selectedAlert = mergedAlerts.find((a) => a.id === selectedAlertId)

  return (
    <div className="h-full flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="panel-title">🚨 告警中心</div>
        <div className="flex gap-2 items-center">
          <Select
            value={filter}
            onChange={setFilter}
            size="small"
            className="w-24 font-mono"
            options={[
              { value: 'all', label: '全部' },
              { value: 'critical', label: '紧急' },
              { value: 'warning', label: '重要' },
              { value: 'info', label: '一般' },
            ]}
          />
          <Button size="small" icon={<ReloadOutlined />} onClick={fetchAlerts} />
          <Button size="small" icon={<DownloadOutlined />} onClick={handleExportCSV}>
            导出 CSV
          </Button>
        </div>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label: '总告警', value: stats.total, color: '#8899AA' },
          { label: '未确认', value: stats.unacknowledged, color: '#FF8C00' },
          { label: '紧急', value: stats.critical, color: '#FF3366' },
          { label: '已解决', value: stats.resolved, color: '#00FF88' },
        ].map((s) => (
          <div
            key={s.label}
            className="bg-cyber-dark border border-cyber-border rounded p-3 text-center"
          >
            <div className="data-value text-lg" style={{ color: s.color }}>
              {s.value}
            </div>
            <div className="text-text-muted text-[10px] font-mono mt-1">{s.label}</div>
          </div>
        ))}
      </div>

      {/* 双栏: 列表 + 详情 */}
      <div className="flex-1 grid grid-cols-3 gap-3 min-h-0">
        {/* 告警列表 */}
        <div className="col-span-1 overflow-hidden">
          {loading ? (
            <Loading tip="加载告警..." />
          ) : filteredAlerts.length === 0 ? (
            <EmptyState title="暂无告警" description="系统运行正常" />
          ) : (
            <div className="h-full overflow-y-auto pr-1">
              <AlertPanel
                alerts={filteredAlerts}
                onAcknowledge={(id) => {
                  acknowledgeAlert(id)
                  alertAPI.acknowledge(id, 'acknowledge').catch(() => {})
                }}
                onAlertClick={(alert) => setSelectedAlert(alert.id)}
              />
            </div>
          )}
        </div>

        {/* 告警详情 */}
        <div className="col-span-2 bg-cyber-dark border border-cyber-border rounded-lg p-4 overflow-y-auto">
          {selectedAlert ? (
            <div>
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <Tag
                    color={
                      selectedAlert.level === 'critical'
                        ? 'red'
                        : selectedAlert.level === 'warning'
                          ? 'orange'
                          : 'blue'
                    }
                    className="font-mono text-xs"
                  >
                    {selectedAlert.level.toUpperCase()}
                  </Tag>
                  <span className="text-text-primary font-medium">{selectedAlert.title}</span>
                </div>
                <div className="flex gap-2">
                  {!selectedAlert.is_acknowledged && (
                    <Button
                      size="small"
                      type="primary"
                      icon={<CheckCircleOutlined />}
                      onClick={() => {
                        acknowledgeAlert(selectedAlert.id)
                        alertAPI.acknowledge(selectedAlert.id, 'acknowledge').catch(() => {})
                      }}
                    >
                      确认
                    </Button>
                  )}
                  {!selectedAlert.is_resolved && (
                    <Button
                      size="small"
                      icon={<CloseCircleOutlined />}
                      onClick={() => {
                        resolveAlert(selectedAlert.id)
                        alertAPI.acknowledge(selectedAlert.id, 'resolve').catch(() => {})
                      }}
                    >
                      解决
                    </Button>
                  )}
                </div>
              </div>

              <Descriptions
                column={2}
                size="small"
                bordered
                styles={{
                  label: { color: '#8899AA', fontSize: 11 },
                  content: { color: '#E8EDF2', fontSize: 11 },
                }}
              >
                <Descriptions.Item label="告警类型">{selectedAlert.alert_type}</Descriptions.Item>
                <Descriptions.Item label="级别">
                  <span
                    style={{ color: selectedAlert.level === 'critical' ? '#FF3366' : '#FF8C00' }}
                  >
                    {selectedAlert.level}
                  </span>
                </Descriptions.Item>
                <Descriptions.Item label="设备">
                  {selectedAlert.device_name || '--'}
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Badge
                    status={
                      selectedAlert.is_resolved
                        ? 'success'
                        : selectedAlert.is_acknowledged
                          ? 'processing'
                          : 'error'
                    }
                    text={
                      selectedAlert.is_resolved
                        ? '已解决'
                        : selectedAlert.is_acknowledged
                          ? '已确认'
                          : '未处理'
                    }
                  />
                </Descriptions.Item>
                <Descriptions.Item label="置信度">
                  {selectedAlert.detection_confidence
                    ? `${(selectedAlert.detection_confidence * 100).toFixed(1)}%`
                    : '--'}
                </Descriptions.Item>
                <Descriptions.Item label="时间">
                  {new Date(selectedAlert.created_at).toLocaleString('zh-CN')}
                </Descriptions.Item>
                <Descriptions.Item label="描述" span={2}>
                  {selectedAlert.description || '--'}
                </Descriptions.Item>
              </Descriptions>
            </div>
          ) : (
            <div className="flex items-center justify-center h-full text-text-muted text-sm font-mono">
              点击左侧告警查看详情
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
