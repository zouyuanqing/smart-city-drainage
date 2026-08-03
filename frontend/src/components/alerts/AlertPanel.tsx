/**
 * 告警面板 — 滚动告警列表 + 确认操作
 * 支持分级显示和动画效果。
 */

import { motion, AnimatePresence } from 'framer-motion'
import { Button, Tag, Empty } from 'antd'
import { CheckCircleOutlined } from '@ant-design/icons'
import type { Alert } from '@/types'

interface AlertPanelProps {
  alerts: Alert[]
  onAcknowledge: (id: string) => void
  onAlertClick: (alert: Alert) => void
}

const levelConfig = {
  critical: { color: '#FF3366', bg: 'rgba(255,51,102,0.1)', icon: '🔴', label: '紧急' },
  warning: { color: '#FF8C00', bg: 'rgba(255,140,0,0.1)', icon: '🟠', label: '重要' },
  info: { color: '#00D4FF', bg: 'rgba(0,212,255,0.1)', icon: '🔵', label: '一般' },
}

const typeLabels: Record<string, string> = {
  water_accumulation: '积水',
  manhole_anomaly: '井盖异常',
  intrusion: '非法闯入',
  illegal_parking: '违停',
  water_level_high: '高液位',
  flow_anomaly: '流量异常',
  device_offline: '设备离线',
  system_error: '系统错误',
}

function AlertItem({
  alert,
  onAcknowledge,
  onClick,
}: {
  alert: Alert
  onAcknowledge: (id: string) => void
  onClick: (alert: Alert) => void
}) {
  const config = levelConfig[alert.level] || levelConfig.info
  const isActive = !alert.is_acknowledged && !alert.is_resolved

  return (
    <motion.div
      initial={{ opacity: 0, y: -10, height: 0 }}
      animate={{ opacity: 1, y: 0, height: 'auto' }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.3 }}
      className={`
        relative p-3 border rounded cursor-pointer transition-all mb-2
        hover:border-opacity-60
        ${
          alert.level === 'critical'
            ? 'alert-critical'
            : alert.level === 'warning'
              ? 'alert-warning'
              : 'alert-info'
        }
        ${!isActive ? 'opacity-50' : ''}
      `}
      style={{ background: config.bg, borderColor: config.color + '30' }}
      onClick={() => onClick(alert)}
    >
      <div className="flex items-start gap-2">
        <span className="text-sm mt-0.5">{config.icon}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-white text-xs font-medium truncate">{alert.title}</span>
            <Tag
              color={
                alert.level === 'critical' ? 'red' : alert.level === 'warning' ? 'orange' : 'blue'
              }
              className="font-mono text-[10px] leading-none"
            >
              {config.label}
            </Tag>
          </div>
          {alert.device_name && (
            <div className="text-text-muted text-[10px] font-mono truncate">
              {alert.device_name}
            </div>
          )}
          <div className="text-text-muted text-[10px] font-mono mt-1">
            {new Date(alert.created_at).toLocaleTimeString('zh-CN')}
            <span className="mx-1">·</span>
            {typeLabels[alert.alert_type] || alert.alert_type}
          </div>
        </div>

        {isActive && (
          <div className="flex flex-col gap-1">
            <Button
              type="text"
              size="small"
              icon={<CheckCircleOutlined />}
              onClick={(e) => {
                e.stopPropagation()
                onAcknowledge(alert.id)
              }}
              className="text-neon-green hover:text-neon-green"
              title="确认"
            />
          </div>
        )}
      </div>
    </motion.div>
  )
}

export function AlertPanel({ alerts, onAcknowledge, onAlertClick }: AlertPanelProps) {
  const sortedAlerts = [...alerts].sort((a, b) => {
    // 未确认的排在前面
    if (a.is_acknowledged !== b.is_acknowledged) {
      return a.is_acknowledged ? 1 : -1
    }
    // 级别排序
    const levelOrder = { critical: 0, warning: 1, info: 2 }
    return (levelOrder[a.level] ?? 2) - (levelOrder[b.level] ?? 2)
  })

  if (sortedAlerts.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <Empty
          description={<span className="text-text-muted">暂无告警</span>}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto pr-1 custom-scrollbar">
      <AnimatePresence initial={false}>
        {sortedAlerts.slice(0, 50).map((alert) => (
          <AlertItem
            key={alert.id}
            alert={alert}
            onAcknowledge={onAcknowledge}
            onClick={onAlertClick}
          />
        ))}
      </AnimatePresence>
      {sortedAlerts.length > 50 && (
        <div className="text-center text-text-muted text-xs font-mono py-2">
          ... 还有 {sortedAlerts.length - 50} 条告警
        </div>
      )}
    </div>
  )
}
