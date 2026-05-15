/**
 * 全局键盘快捷键 Hook
 * 穿越全系统（排除 input/textarea/select）
 */

import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '@/store/useAppStore';

export function useKeyboardShortcuts() {
  const navigate = useNavigate();
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const setSelectedDevice = useAppStore((s) => s.setSelectedDevice);

  useEffect(() => {
    const INPUT_TAGS = new Set(['INPUT', 'TEXTAREA', 'SELECT']);

    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (INPUT_TAGS.has(target.tagName) || target.isContentEditable) return;

      const key = e.key;
      const ctrl = e.ctrlKey || e.metaKey;

      // Ctrl+B — 侧边栏
      if (ctrl && key === 'b') {
        e.preventDefault();
        toggleSidebar();
        return;
      }

      // Escape — 取消选中/关闭面板
      if (key === 'Escape') {
        setSelectedDevice(null);
        useAppStore.getState().setSelectedAlert(null);
        return;
      }

      // 数字键 1-5 — 快速导航
      if (!ctrl && !e.altKey && key >= '1' && key <= '5') {
        const routes = ['/dashboard', '/map', '/video', '/alerts', '/settings'];
        navigate(routes[Number(key) - 1]);
        return;
      }

      // + / - — 地图缩放
      // (这些由 Leaflet 地图内部处理，不在此拦截)
    };

    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [navigate, toggleSidebar, setSelectedDevice]);
}
