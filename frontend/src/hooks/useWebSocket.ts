/**
 * WebSocket 控制通道 Hook
 * 用于发送控制指令和接收设备状态变更。
 */

import { useEffect, useRef, useCallback, useState } from 'react'

interface UseWebSocketOptions {
  onMessage?: (data: any) => void
  onConnected?: () => void
  onDisconnected?: () => void
  autoReconnect?: boolean
}

interface UseWebSocketReturn {
  isConnected: boolean
  send: (type: string, payload?: any) => void
  reconnect: () => void
}

export function useWebSocket(options: UseWebSocketOptions = {}): UseWebSocketReturn {
  const { onMessage, onConnected, onDisconnected, autoReconnect = true } = options
  const [isConnected, setIsConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.host}/api/ws/control`

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setIsConnected(true)
      onConnected?.()
    }

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        onMessage?.(data)
      } catch (err) {
        console.error('[WS] 消息解析失败:', err)
      }
    }

    ws.onclose = () => {
      setIsConnected(false)
      onDisconnected?.()
      if (autoReconnect) {
        reconnectTimer.current = setTimeout(connect, 5000)
      }
    }

    ws.onerror = (err) => {
      console.error('[WS] 连接错误:', err)
      ws.close()
    }
  }, [wsUrl, onMessage, onConnected, onDisconnected, autoReconnect])

  const send = useCallback((type: string, payload?: any) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type, payload }))
    } else {
      console.warn('[WS] 未连接，无法发送消息')
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { isConnected, send, reconnect: connect }
}
