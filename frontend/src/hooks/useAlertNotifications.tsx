/**
 * 告警通知 Hook
 * 监听新告警并使用 antd notification 提示
 */

import { useEffect, useRef } from 'react';
import { notification } from 'antd';
import type { Alert } from '@/types';
import { useAppStore } from '@/store/useAppStore';

const levelLabel: Record<string, string> = {
  critical: '紧急',
  warning: '重要',
  info: '一般',
};

export function useAlertNotifications() {
  const alerts = useAppStore((s) => s.alerts);
  const setSelectedAlert = useAppStore((s) => s.setSelectedAlert);
  const notifiedIds = useRef<Set<string>>(new Set());

  useEffect(() => {
    for (const alert of alerts) {
      if (notifiedIds.current.has(alert.id)) continue;
      if (alert.is_acknowledged || alert.is_resolved) continue;

      notifiedIds.current.add(alert.id);

      const message = `[${levelLabel[alert.level] || '一般'}] ${alert.title}`;
      const description = alert.description || '';

      const onClick = () => setSelectedAlert(alert.id);
      const btn =
        <a onClick={onClick} className="text-neon-blue text-xs font-mono" key="view">
          查看详情
        </a>;

      if (alert.level === 'critical') {
        notification.error({ message, description, placement: 'topRight', onClick, btn, duration: 0, key: alert.id });
      } else if (alert.level === 'warning') {
        notification.warning({ message, description, placement: 'topRight', onClick, btn, duration: 8, key: alert.id });
      } else {
        notification.info({ message, description, placement: 'topRight', onClick, btn, duration: 5, key: alert.id });
      }
    }

    if (notifiedIds.current.size > 500) {
      notifiedIds.current = new Set([...notifiedIds.current].slice(-200));
    }
  }, [alerts]);
}
