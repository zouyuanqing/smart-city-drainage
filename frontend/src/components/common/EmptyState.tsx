import { Empty } from 'antd'
import type { ReactNode } from 'react'

interface EmptyStateProps {
  icon?: ReactNode
  title?: string
  description?: string
  action?: ReactNode
}

export function EmptyState({ icon, title = '暂无数据', description, action }: EmptyStateProps) {
  return (
    <div className="flex items-center justify-center h-full min-h-[200px]">
      <Empty
        image={icon || Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <div>
            <div className="text-text-secondary text-sm font-medium mb-1">{title}</div>
            {description && <div className="text-text-muted text-xs font-mono">{description}</div>}
          </div>
        }
      >
        {action}
      </Empty>
    </div>
  )
}
