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

    _instance: Optional["SSEManager"] = None
    _MAX_HISTORY = 1000

    def __init__(self) -> None:
        if not hasattr(self, "_clients"):
            self._clients: dict[str, SSEClient] = {}
            self._lock = asyncio.Lock()
            self._total_events_sent: int = 0
            self._event_counter: int = 0
            self._event_history: list[tuple[int, str, str]] = []

    @classmethod
    def get_instance(cls) -> "SSEManager":
        """获取单例"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def connect(
        self, last_event_id: str | None = None
    ) -> tuple[str, asyncio.Queue]:
        """
        注册新的 SSE 客户端

        Args:
            last_event_id: 客户端上次收到的事件 ID，用于断线重连时重放

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

        logger.info(
            "🔗 SSE 客户端连接: %s (当前连接数: %d)", client_id, len(self._clients)
        )
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
        event_data = json.dumps(data, ensure_ascii=False, default=str)

        async with self._lock:
            self._event_counter += 1
            event_id = self._event_counter
            self._event_history.append((event_id, event_type, event_data))
            if len(self._event_history) > self._MAX_HISTORY:
                self._event_history = self._event_history[-self._MAX_HISTORY :]
            clients = list(self._clients.items())

        dead_clients: list[str] = []

        for client_id, client in clients:
            try:
                client.queue.put_nowait(
                    {
                        "event": event_type,
                        "data": event_data,
                        "id": str(event_id),
                    }
                )
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
        await self.broadcast(
            "sensors",
            {
                "readings": readings,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def broadcast_alert(self, alert_data: dict[str, Any]) -> None:
        """广播告警"""
        await self.broadcast(
            "alerts",
            {
                "alert": alert_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    def event_generator(
        self,
        client_id: str,
        queue: asyncio.Queue,
        last_event_id: str | None = None,
    ) -> AsyncGenerator:
        async def generate():
            try:
                yield {"retry": 3000}

                yield {
                    "event": "connected",
                    "data": json.dumps({"client_id": client_id, "status": "connected"}),
                }

                if last_event_id is not None:
                    try:
                        last_id = int(last_event_id)
                    except (ValueError, TypeError):
                        last_id = 0
                    history_snapshot = list(self._event_history)
                    for eid, etype, edata in history_snapshot:
                        if eid > last_id:
                            yield {
                                "event": etype,
                                "data": edata,
                                "id": str(eid),
                            }

                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                        result: dict[str, str] = {
                            "event": event["event"],
                            "data": event["data"],
                        }
                        if "id" in event:
                            result["id"] = event["id"]
                        yield result
                    except asyncio.TimeoutError:
                        yield {"comment": f"heartbeat {int(time.time())}"}

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
