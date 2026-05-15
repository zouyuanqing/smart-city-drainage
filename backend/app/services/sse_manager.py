"""
SSE (Server-Sent Events) 管理器
--------------------------------
管理所有 SSE 客户端连接，支持广播传感器数据和告警事件。
使用 asyncio 队列实现异步消息分发。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional
from uuid import uuid4

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SSEClient:
    """单个 SSE 客户端连接"""
    client_id: str
    queue: asyncio.Queue[dict[str, str]]
    connected_at: float = field(default_factory=time.time)
    last_event_at: float = field(default_factory=time.time)
    event_count: int = 0


class SSEManager:
    """
    SSE 连接管理器 (单例模式)

    支持多频道:
      - sensors: 传感器实时数据
      - alerts: 告警推送
      - system: 系统状态

    使用示例:
        manager = SSEManager.get_instance()
        await manager.broadcast("sensors", {"device_id": "...", "water_level_mm": 150})
    """

    _instance: Optional["SSEManager"] = None

    def __init__(self) -> None:
        if not hasattr(self, "_clients"):
            self._clients: dict[str, SSEClient] = {}
            self._lock = asyncio.Lock()
            self._total_events_sent: int = 0

    @classmethod
    def get_instance(cls) -> "SSEManager":
        """获取单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def connect(self) -> tuple[str, asyncio.Queue]:
        """
        注册新的 SSE 客户端

        Returns:
            (client_id, event_queue)
        """
        client_id = str(uuid4())[:8]
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(maxsize=256)

        async with self._lock:
            self._clients[client_id] = SSEClient(
                client_id=client_id,
                queue=queue,
            )

        logger.info("🔗 SSE 客户端连接: %s (当前连接数: %d)", client_id, len(self._clients))
        return client_id, queue

    async def disconnect(self, client_id: str) -> None:
        """注销 SSE 客户端"""
        async with self._lock:
            client = self._clients.pop(client_id, None)
        if client:
            logger.info(
                "🔌 SSE 客户端断开: %s | 共接收 %d 条事件 | 存活 %.1f 秒",
                client_id,
                client.event_count,
                time.time() - client.connected_at,
            )

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """
        向所有客户端广播事件

        Args:
            event_type: 事件类型 (sensors, alerts, system)
            data: 事件数据
        """
        event_data = json.dumps(data, ensure_ascii=False, default=str)

        async with self._lock:
            clients = list(self._clients.items())

        dead_clients: list[str] = []

        for client_id, client in clients:
            try:
                # 非阻塞放入队列
                client.queue.put_nowait({
                    "event": event_type,
                    "data": event_data,
                })
                client.event_count += 1
                self._total_events_sent += 1
            except asyncio.QueueFull:
                logger.warning("⚠️  客户端 %s 队列已满，丢弃事件", client_id)
                dead_clients.append(client_id)

        # 清理死连接
        for cid in dead_clients:
            await self.disconnect(cid)

    async def broadcast_sensor(self, readings: list[dict[str, Any]]) -> None:
        """广播传感器数据"""
        await self.broadcast("sensors", {
            "readings": readings,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def broadcast_alert(self, alert_data: dict[str, Any]) -> None:
        """广播告警"""
        await self.broadcast("alerts", {
            "alert": alert_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def event_generator(self, client_id: str, queue: asyncio.Queue) -> AsyncGenerator:
        """
        SSE 事件生成器 (用于 FastAPI StreamingResponse)

        Args:
            client_id: 客户端 ID
            queue: 事件队列

        Yields:
            SSE 格式的字符串
        """
        async def generate():
            try:
                # 发送连接成功事件
                yield f"event: connected\ndata: {json.dumps({'client_id': client_id, 'status': 'connected'})}\n\n"

                while True:
                    try:
                        # 等待事件 (带超时，用于心跳)
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield f"event: {event['event']}\ndata: {event['data']}\n\n"
                    except asyncio.TimeoutError:
                        # 发送心跳保持连接
                        yield f": heartbeat {int(time.time())}\n\n"

            except asyncio.CancelledError:
                logger.debug("SSE 客户端 %s 被取消", client_id)
            finally:
                await self.disconnect(client_id)

        return generate()

    @property
    def client_count(self) -> int:
        """当前连接的客户端数量（读操作无锁，值可能短暂滞后）"""
        return len(self._clients)

    @property
    def total_events_sent(self) -> int:
        """累计发送事件数（读操作无锁，值可能短暂滞后）"""
        return self._total_events_sent


sse_manager = SSEManager.get_instance()
