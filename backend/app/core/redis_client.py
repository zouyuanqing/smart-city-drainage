"""
Redis 客户端 — 发布/订阅与缓存
===============================
封装 Redis 连接，提供 pub/sub 和缓存操作。
用于跨服务的实时通信（告警、传感器数据、模型状态）。

Forecast 使用独立的 ``scn:forecast`` channel。该客户端只负责发布/订阅，不直接
调用 :class:`SSEManager`，从而避免 producer direct broadcast 与 Redis 回声双发。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Mapping
from typing import Any, Callable, Optional, Union, cast

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

FORECAST_REDIS_CHANNEL = "scn:forecast"
FORECAST_EVENT_TYPES = frozenset(
    {
        "alert.created",
        "alert.updated",
        "alert.escalated",
        "alert.observed",
        "alert.resolved",
        "alert.expired",
    }
)

# 回调类型；保持旧同步 handler API，同时允许 async handler。
MessageHandler = Callable[[dict[str, Any]], Union[None, Awaitable[None]]]


class RedisClient:
    """
    Redis 异步客户端 (单例)

    频道:
      - scn:alerts — legacy 告警事件
      - scn:sensor_data — 传感器实时数据
      - scn:model_status — 模型状态变更
      - scn:forecast — 持久化 forecast outbox relay 事件
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
        """建立 Redis 连接。"""
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
        """断开连接。"""
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

    @property
    def forecast_channel(self) -> str:
        """Forecast relay channel；未来配置存在时优先使用配置值。"""
        return cast(
            str, getattr(settings, "REDIS_CHANNEL_FORECAST", FORECAST_REDIS_CHANNEL)
        )

    # ==================== 发布 ====================

    async def publish(self, channel: str, message: dict[str, Any]) -> int:
        """发布消息到频道。"""
        if not self._redis:
            logger.warning("Redis 未连接，无法发布消息")
            return 0
        payload = json.dumps(message, ensure_ascii=False, default=str)
        return cast(int, await self._redis.publish(channel, payload))

    async def publish_alert(self, alert: dict[str, Any]) -> None:
        await self.publish(settings.REDIS_CHANNEL_ALERTS, alert)

    async def publish_sensor_data(self, readings: list[dict[str, Any]]) -> None:
        await self.publish(settings.REDIS_CHANNEL_SENSOR, {"readings": readings})

    async def publish_model_status(self, status: dict[str, Any]) -> None:
        await self.publish(settings.REDIS_CHANNEL_MODEL, status)

    async def publish_forecast_event(self, event: Mapping[str, Any]) -> int:
        """
        发布一条已提交的 forecast outbox 事件。

        本方法故意不做本地 SSE fan-out。调用链必须保持：

        ``outbox relay -> publish_forecast_event -> Redis -> API fan-out``。
        """
        envelope = dict(event)
        event_id = envelope.get("event_id")
        event_type = envelope.get("event_type")
        sequence = envelope.get("sequence")

        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("forecast event_id must be a non-empty string")
        if envelope.get("schema_version") != 1:
            raise ValueError("unsupported forecast schema_version")
        if event_type not in FORECAST_EVENT_TYPES:
            raise ValueError("unsupported forecast event_type")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise ValueError("forecast sequence must be a positive integer")
        for field in ("network_id", "nowcast_run_id", "simulation_id"):
            if not isinstance(envelope.get(field), str) or not envelope[field].strip():
                raise ValueError(f"forecast {field} must be a non-empty string")
        if not isinstance(envelope.get("occurred_at"), str):
            raise ValueError("forecast occurred_at is required")
        if not isinstance(envelope.get("alert"), dict):
            raise ValueError("forecast alert is required")
        from app.services.sse_manager import _validate_forecast_envelope

        _validate_forecast_envelope(
            {
                **envelope,
                "event_id": event_id.strip(),
                "sequence": sequence,
                "event_type": event_type,
            }
        )

        envelope["event_id"] = event_id.strip()
        if not self._redis:
            logger.warning("Redis 未连接，forecast outbox 保持未发布状态")
            return 0
        payload = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return cast(int, await self._redis.publish(self.forecast_channel, payload))

    publish_forecast = publish_forecast_event

    async def relay_forecast_outbox(
        self,
        rows: Any,
        *,
        mark_published: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> int:
        """Publish committed outbox rows and acknowledge broker acceptance.

        Redis ``PUBLISH`` returns the number of subscribers, not whether the
        command was accepted.  Zero subscribers is a normal state and must not
        leave a durable outbox row replaying forever.  Connection failures still
        raise before acknowledgement, so they remain retryable.
        """

        if not self.is_connected:
            return 0
        published = 0
        for row in rows:
            event = dict(row)
            await self.publish_forecast_event(event)
            published += 1
            if mark_published is not None:
                result = mark_published(event)
                if hasattr(result, "__await__"):
                    await result
        return published

    # ==================== 订阅 ====================

    def on(self, channel: str, handler: MessageHandler) -> None:
        """注册消息处理器。"""
        self._handlers.setdefault(channel, []).append(handler)

    def on_forecast(self, handler: MessageHandler) -> None:
        """
        注册 forecast Redis fan-out handler。

        应在 :meth:`start_listener` 前调用，例如传入
        ``sse_manager.broadcast_forecast_event``。
        """
        self.on(self.forecast_channel, handler)

    async def start_listener(self) -> None:
        """启动消息监听协程。"""
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
        """消息监听主循环；单个 handler 失败不得中断其他订阅。"""
        if not self._pubsub:
            return

        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue

                channel = message["channel"]
                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    logger.warning("忽略 Redis 消息中的无效 JSON: %s", channel)
                    continue
                if not isinstance(data, dict):
                    logger.warning("忽略 Redis 非对象消息: %s", channel)
                    continue

                for handler in self._handlers.get(channel, []):
                    try:
                        result = handler(data)
                        if asyncio.iscoroutine(result):
                            await result
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.exception("Redis 消息处理异常 [%s]: %s", channel, exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Redis 监听异常: %s", exc)

    # ==================== 缓存操作 ====================

    async def get(self, key: str) -> Optional[str]:
        if not self._redis:
            return None
        return cast(Optional[str], await self._redis.get(key))

    async def set(self, key: str, value: str, ttl: int = 300) -> None:
        if self._redis:
            await self._redis.set(key, value, ex=ttl)

    async def delete(self, key: str) -> None:
        if self._redis:
            await self._redis.delete(key)

    async def increment_rate_limit(self, key: str, ttl_seconds: int) -> int | None:
        """Atomically increment a shared fixed-window counter.

        The Lua script assigns the TTL only to the first request in a window,
        avoiding the race where concurrent callers create an immortal key.
        ``None`` means Redis is not configured/connected; callers must choose a
        bounded development fallback or fail closed in production.
        """

        if not self._redis:
            return None
        if isinstance(ttl_seconds, bool) or not 1 <= ttl_seconds <= 3600:
            raise ValueError("rate-limit TTL must be between 1 and 3600 seconds")
        script = (
            "local count = redis.call('INCR', KEYS[1]); "
            "if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end; "
            "return count"
        )
        value = await cast(Any, self._redis).eval(script, 1, key, str(ttl_seconds))
        return int(value)


# 全局单例
redis_client = RedisClient.get_instance()
