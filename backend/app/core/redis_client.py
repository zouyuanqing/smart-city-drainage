"""
Redis 客户端 — 发布/订阅与缓存
===============================
封装 Redis 连接，提供 pub/sub 和缓存操作。
用于跨服务的实时通信（告警、传感器数据、模型状态）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

# 回调类型
MessageHandler = Callable[[dict[str, Any]], None]


class RedisClient:
    """
    Redis 异步客户端 (单例)

    频道:
      - scn:alerts — 告警事件
      - scn:sensor_data — 传感器实时数据
      - scn:model_status — 模型状态变更
    """

    _instance: Optional["RedisClient"] = None

    def __init__(self) -> None:
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._listener_task: Optional[asyncio.Task] = None

    @classmethod
    def get_instance(cls) -> "RedisClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ==================== 连接管理 ====================

    async def connect(self) -> None:
        """建立 Redis 连接"""
        if self._redis is not None:
            return

        self._redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=10,
        )
        await self._redis.ping()
        logger.info("✅ Redis 已连接: %s", settings.REDIS_URL.split("@")[-1])

    async def disconnect(self) -> None:
        """断开连接"""
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()

        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.close()

        if self._redis:
            await self._redis.close()
            self._redis = None

    @property
    def is_connected(self) -> bool:
        return self._redis is not None

    # ==================== 发布 ====================

    async def publish(self, channel: str, message: dict[str, Any]) -> int:
        """发布消息到频道"""
        if not self._redis:
            logger.warning("Redis 未连接，无法发布消息")
            return 0
        payload = json.dumps(message, ensure_ascii=False, default=str)
        return await self._redis.publish(channel, payload)

    async def publish_alert(self, alert: dict[str, Any]) -> None:
        await self.publish(settings.REDIS_CHANNEL_ALERTS, alert)

    async def publish_sensor_data(self, readings: list[dict[str, Any]]) -> None:
        await self.publish(settings.REDIS_CHANNEL_SENSOR, {"readings": readings})

    async def publish_model_status(self, status: dict[str, Any]) -> None:
        await self.publish(settings.REDIS_CHANNEL_MODEL, status)

    # ==================== 订阅 ====================

    def on(self, channel: str, handler: MessageHandler) -> None:
        """注册消息处理器"""
        self._handlers.setdefault(channel, []).append(handler)

    async def start_listener(self) -> None:
        """启动消息监听协程"""
        if self._listener_task and not self._listener_task.done():
            return

        self._pubsub = self._redis.pubsub() if self._redis else None
        if not self._pubsub:
            return

        channels = list(self._handlers.keys())
        if channels:
            await self._pubsub.subscribe(*channels)
            logger.info("📡 Redis 订阅频道: %s", channels)

        self._listener_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        """消息监听主循环"""
        if not self._pubsub:
            return

        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue

                channel = message["channel"]
                try:
                    data = json.loads(message["data"])
                except json.JSONDecodeError:
                    continue

                for handler in self._handlers.get(channel, []):
                    try:
                        result = handler(data)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        logger.error("Redis 消息处理异常 [%s]: %s", channel, exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Redis 监听异常: %s", exc)

    # ==================== 缓存操作 ====================

    async def get(self, key: str) -> Optional[str]:
        if not self._redis:
            return None
        return await self._redis.get(key)

    async def set(self, key: str, value: str, ttl: int = 300) -> None:
        if self._redis:
            await self._redis.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        if self._redis:
            await self._redis.delete(key)


# 全局单例
redis_client = RedisClient.get_instance()
