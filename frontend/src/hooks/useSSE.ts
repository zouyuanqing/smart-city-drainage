/**
 * SSE (Server-Sent Events) 客户端 Hook
 * 自动连接、自动重连（指数退避）、事件分发。
 *
 * 重连策略:
 *   - 初始延迟 1s，每次翻倍，最大 30s
 *   - 连接成功后重置退避时间
 *   - 浏览器 EventSource 自动携带 Last-Event-ID 请求头，
 *     配合服务端事件重放实现断线续传
 */

import { useEffect, useRef, useCallback, useState } from 'react';
import type { SensorReading, Alert } from '@/types';

interface UseSSEOptions {
  onSensorData?: (readings: SensorReading[]) => void;
  onAlert?: (alert: Alert) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onError?: (error: Event) => void;
}

interface UseSSEReturn {
  isConnected: boolean;
  reconnect: () => void;
  disconnect: () => void;
}

const INITIAL_RETRY_DELAY = 1000;
const MAX_RETRY_DELAY = 30000;

export function useSSE(options: UseSSEOptions = {}): UseSSEReturn {
  const {
    onSensorData,
    onAlert,
    onConnected,
    onDisconnected,
    onError,
  } = options;

  const [isConnected, setIsConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef(true);
  const retryCountRef = useRef(0);

  const callbacksRef = useRef({ onSensorData, onAlert, onConnected, onDisconnected, onError });
  callbacksRef.current = { onSensorData, onAlert, onConnected, onDisconnected, onError };

  const getReconnectDelay = useCallback(() => {
    return Math.min(INITIAL_RETRY_DELAY * Math.pow(2, retryCountRef.current), MAX_RETRY_DELAY);
  }, []);

  const connect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    const es = new EventSource('/api/sse/events');
    eventSourceRef.current = es;

    es.onopen = () => {
      if (!isMountedRef.current) return;
      retryCountRef.current = 0;
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
      callbacksRef.current.onDisconnected?.();
      callbacksRef.current.onError?.(new Event('error'));
      es.close();

      if (isMountedRef.current) {
        const delay = getReconnectDelay();
        retryCountRef.current += 1;
        reconnectTimerRef.current = setTimeout(connect, delay);
      }
    };
  }, [getReconnectDelay]);

  const disconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    retryCountRef.current = 0;
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
  }, []);

  return { isConnected, reconnect: connect, disconnect };
}
