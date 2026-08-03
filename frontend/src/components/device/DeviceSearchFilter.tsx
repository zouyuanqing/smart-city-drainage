/**
 * 设备搜索/筛选组件
 * 支持的过滤: 文字搜索, 状态筛选, 类型筛选
 */

import { useState, useEffect } from 'react'
import { Input, Select } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import type { Device } from '@/types'

interface DeviceSearchFilterProps {
  devices: Device[]
  onFilterChange: (filtered: Device[]) => void
  deviceType?: string
  onDeviceTypeChange?: (value: string) => void
}

export function DeviceSearchFilter({
  devices,
  onFilterChange,
  deviceType,
  onDeviceTypeChange,
}: DeviceSearchFilterProps) {
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')

  useEffect(() => {
    let filtered = devices

    if (query.trim()) {
      const q = query.toLowerCase().trim()
      filtered = filtered.filter(
        (d) =>
          d.name.toLowerCase().includes(q) ||
          d.code.toLowerCase().includes(q) ||
          (d.district || '').toLowerCase().includes(q)
      )
    }

    if (statusFilter !== 'all') {
      filtered = filtered.filter((d) => d.status === statusFilter)
    }

    if (deviceType) {
      filtered = filtered.filter((d) => d.device_type === deviceType)
    }

    onFilterChange(filtered)
  }, [devices, query, statusFilter, deviceType, onFilterChange])

  return (
    <div className="flex items-center gap-2 p-2">
      <Input
        prefix={<SearchOutlined className="text-text-muted" />}
        placeholder="搜索设备..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        size="small"
        allowClear
        className="bg-cyber-black border-cyber-border text-text-primary font-mono text-xs flex-1"
      />
      <Select
        value={statusFilter}
        onChange={setStatusFilter}
        size="small"
        className="w-24 font-mono text-xs"
        popupClassName="cyber-dropdown"
        options={[
          { value: 'all', label: '全部' },
          { value: 'online', label: '在线' },
          { value: 'offline', label: '离线' },
          { value: 'fault', label: '故障' },
        ]}
      />
      <Select
        value={deviceType || 'all'}
        onChange={(val) => onDeviceTypeChange?.(val === 'all' ? '' : val)}
        size="small"
        style={{ width: 140 }}
        popupClassName="cyber-dropdown"
        options={[
          { value: 'all', label: '全部类型' },
          { value: 'manhole_cover', label: '井盖监测' },
          { value: 'camera', label: '摄像头' },
          { value: 'water_level', label: '液位计' },
          { value: 'flow_meter', label: '流量计' },
        ]}
      />
    </div>
  )
}
