/**
 * SSE (Server-Sent Events) 客户端 Hook
 * 自动连接、自动重连、事件分发。
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import type { SensorReading, Alert } from '@/types';

interface UseSSEOptions {
  onSensorData?: (readings: SensorReading[]) => void;
  onAlert?: (alert: Alert) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onError?: (error: Event) => void;
  reconnectInterval?: number;
}

interface UseSSEReturn {
  isConnected: boolean;
  reconnect: () => void;
  disconnect: () => void;
}

export function useSSE(options: UseSSEOptions = {}): UseSSEReturn {
  const {
    onSensorData,
    onAlert,
    onConnected,
    onDisconnected,
    onError,
    reconnectInterval = 5000,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef(true);

  // 使用 ref 存储回调，避免回调引用变化导致 SSE 重连
  const callbacksRef = useRef({ onSensorData, onAlert, onConnected, onDisconnected, onError });
  callbacksRef.current = { onSensorData, onAlert, onConnected, onDisconnected, onError };

  const connect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const es = new EventSource('/api/sse/events');
    eventSourceRef.current = es;

    es.onopen = () => {
      if (!isMountedRef.current) return;
      setIsConnected(true);
      callbacksRef.current.onConnected?.();
    };

    es.addEventListener('connected', (event) => {
      try {
        const data = JSON.parse(event.data);
        console.info(`[SSE] 已连接 (ID: ${data.client_id}, 在线: ${data.sse_clients})`);
      } catch {}
    });

    es.addEventListener('sensors', (event) => {
      if (!isMountedRef.current) return;
      try {
        const payload = JSON.parse(event.data);
        callbacksRef.current.onSensorData?.(payload.readings || []);
      } catch (err) {
        console.error('[SSE] 传感器数据解析失败:', err);
      }
    });

    es.addEventListener('alerts', (event) => {
      if (!isMountedRef.current) return;
      try {
        const payload = JSON.parse(event.data);
        callbacksRef.current.onAlert?.(payload.alert);
      } catch (err) {
        console.error('[SSE] 告警数据解析失败:', err);
      }
    });

    es.onerror = () => {
      if (!isMountedRef.current) return;
      setIsConnected(false);
      callbacksRef.current.onError?.(new Event('error'));
      es.close();

      // 自动重连
      if (isMountedRef.current) {
        reconnectTimerRef.current = setTimeout(connect, reconnectInterval);
      }
    };
  }, [reconnectInterval]);

  const disconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setIsConnected(false);
    callbacksRef.current.onDisconnected?.();
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    connect();

    return () => {
      isMountedRef.current = false;
      disconnect();
    };
  }, []); // 只在挂载/卸载时连接/断开

  return { isConnected, reconnect: connect, disconnect };
}
