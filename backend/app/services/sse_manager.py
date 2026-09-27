"""
SSE (Server-Sent Events) 管理器
--------------------------------
管理 legacy SSE 连接，并提供独立的 forecast 事件流能力。

Forecast 事件必须先由持久化 outbox/Redis relay 传入；本模块只负责把已提交的
``sequence`` 暴露为 SSE ID，并按稳定 ``event_id``/``sequence`` 去重。生产路径不得
同时从 producer 直接调用 forecast broadcast，否则会与 Redis 回声形成双投递。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Mapping, Optional, Protocol
from uuid import uuid4

from app.core.config import settings

logger = logging.getLogger(__name__)

FORECAST_EVENT_TYPES = frozenset(
    {
        "alert.created",
        "alert.updated",
        "alert.escalated",
        "alert.observed",
        "alert.resolved",
        "alert.expired",
        "forecast.resync_required",
    }
)
_FORECAST_REQUIRED_FIELDS = (
    "event_id",
    "sequence",
    "event_type",
    "occurred_at",
    "network_id",
    "nowcast_run_id",
    "simulation_id",
    "alert",
)


class ForecastReplayStore(Protocol):
    """Durable replay port; implementations query the event outbox."""

    async def replay(
        self,
        *,
        network_id: str | None,
        after_sequence: int,
        retention_hours: int,
    ) -> tuple[dict[str, str], ...]: ...

    async def bounds(self, *, network_id: str | None) -> tuple[int, int]: ...


def _parse_occurred_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "forecast occurred_at must be an RFC3339 string",
        )

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "forecast occurred_at must be an RFC3339 string",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("forecast occurred_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_forecast_envelope(envelope: Mapping[str, Any]) -> None:
    if envelope.get("schema_version") != 1:
        raise ValueError("unsupported forecast schema_version")
    required: tuple[str, ...] = _FORECAST_REQUIRED_FIELDS
    if envelope.get("event_type") == "forecast.resync_required":
        required = (
            "event_id",
            "sequence",
            "event_type",
            "network_id",
            "from_sequence",
            "to_sequence",
        )
    missing = [field for field in required if field not in envelope]
    if missing:
        missing_fields = ",".join(missing)
        prefix = "forecast envelope is missing required fields:"
        message = f"{prefix} {missing_fields}"
        raise ValueError(message)
    if (
        not isinstance(envelope.get("event_id"), str)
        or not envelope["event_id"].strip()
    ):
        raise ValueError("forecast event_id must be a non-empty string")
    if (
        isinstance(envelope.get("sequence"), bool)
        or not isinstance(envelope.get("sequence"), int)
        or envelope["sequence"] <= 0
    ):
        raise ValueError("forecast sequence must be a positive integer")
    if envelope.get("event_type") not in FORECAST_EVENT_TYPES:
        raise ValueError("unsupported forecast event_type")
    for identity_field in ("network_id", "nowcast_run_id", "simulation_id"):
        if identity_field not in required:
            continue
        if (
            not isinstance(envelope.get(identity_field), str)
            or not envelope[identity_field].strip()
        ):
            raise ValueError(
                f"forecast {identity_field} must be a non-empty string",
            )
    if envelope.get("event_type") != "forecast.resync_required":
        _parse_occurred_at(envelope.get("occurred_at"))
    alert = envelope.get("alert")
    if envelope["event_type"] != "forecast.resync_required" and not isinstance(
        alert, Mapping
    ):
        raise ValueError("forecast alert must be an object")
    if isinstance(alert, Mapping):
        required_alert = (
            "id",
            "type",
            "status",
            "phase",
            "level",
            "target",
            "expected_at",
            "confidence",
            "risk_score",
            "version",
        )
        if any(alert_field not in alert for alert_field in required_alert):
            raise ValueError("forecast alert is missing required fields")
        if alert.get("level") not in {"info", "warning", "critical"}:
            raise ValueError("forecast alert level is invalid")
        if (
            isinstance(alert.get("risk_score"), bool)
            or not isinstance(alert.get("risk_score"), (int, float))
            or not math.isfinite(float(alert["risk_score"]))
        ):
            raise ValueError(
                "forecast alert risk_score must be finite numeric",
            )
        if (
            isinstance(alert.get("confidence"), bool)
            or not isinstance(alert.get("confidence"), (int, float))
            or not math.isfinite(float(alert["confidence"]))
        ):
            raise ValueError(
                "forecast alert confidence must be finite numeric",
            )


class SSECursorExpiredError(ValueError):
    """Last-Event-ID 无效、早于保留边界或超前于当前持久序列。"""

    code = "SSE_CURSOR_EXPIRED"

    def __init__(
        self,
        last_event_id: str | int | None,
        *,
        earliest_sequence: int,
        latest_sequence: int,
    ) -> None:
        self.last_event_id = last_event_id
        self.earliest_sequence = earliest_sequence
        self.latest_sequence = latest_sequence
        super().__init__(
            "Last-Event-ID is invalid or outside the retained "
            "forecast sequence range"
        )


@dataclass
class SSEClient:
    """单个 SSE 客户端连接。"""

    client_id: str
    queue: asyncio.Queue[dict[str, str]]
    connected_at: float = field(default_factory=time.time)
    last_event_at: float = field(default_factory=time.time)
    event_count: int = 0
    network_id: str | None = None
    last_event_id: int | None = None
    last_enqueued_sequence: int | None = None
    last_delivered_sequence: int | None = None
    replay_events: tuple[dict[str, str], ...] = ()


class SSEManager:
    _instance: Optional["SSEManager"] = None
    _MAX_HISTORY = 100_000

    def __init__(
        self,
        *,
        forecast_history_limit: int | None = None,
        forecast_queue_size: int | None = None,
        replay_store: ForecastReplayStore | None = None,
        forecast_replay_retention_hours: int | None = None,
    ) -> None:
        if not hasattr(self, "_clients"):
            self._clients: dict[str, SSEClient] = {}
            self._lock = asyncio.Lock()
            self._total_events_sent: int = 0
            self._event_counter: int = 0
            self._event_history: list[tuple[int, str, str]] = []

            # Forecast 客户端与 legacy 客户端物理隔离，防止 legacy stream 收到
            # forecast 业务事件。sequence 来自持久化 outbox，不由本进程生成。
            self._forecast_clients: set[str] = set()
            self._forecast_history_limit = (
                forecast_history_limit
                if forecast_history_limit is not None
                else self._MAX_HISTORY
            )
            self._forecast_queue_size = (
                forecast_queue_size
                if forecast_queue_size is not None
                else settings.FORECAST_SSE_QUEUE_SIZE
            )
            if self._forecast_history_limit <= 0:
                raise ValueError("forecast_history_limit must be positive")
            if self._forecast_queue_size <= 0:
                raise ValueError("forecast_queue_size must be positive")
            self._forecast_history: list[tuple[int, str, str, str]] = []
            self._forecast_history_times: dict[int, datetime] = {}
            self._forecast_event_ids: set[str] = set()
            self._forecast_latest_sequence = 0
            self._replay_store = replay_store
            self._forecast_replay_retention_hours = (
                forecast_replay_retention_hours
                if forecast_replay_retention_hours is not None
                else settings.FORECAST_REPLAY_RETENTION_HOURS
            )
            if self._forecast_replay_retention_hours <= 0:
                raise ValueError(
                    "forecast_replay_retention_hours must be positive",
                )

    @classmethod
    def get_instance(cls) -> "SSEManager":
        """获取单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def set_forecast_replay_store(
        self, replay_store: ForecastReplayStore | None
    ) -> None:
        """Inject the durable forecast replay port during application startup.

        The API process must never silently fall back to its local history once
        the database-backed store is available.  Tests may still pass ``None``
        explicitly to exercise the isolated in-memory adapter.
        """

        self._replay_store = replay_store

    @property
    def forecast_replay_store(self) -> ForecastReplayStore | None:
        return self._replay_store

    async def connect(
        self, last_event_id: str | None = None
    ) -> tuple[str, asyncio.Queue]:
        """
        注册新的 legacy SSE 客户端。

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

    async def connect_forecast(
        self,
        last_event_id: str | int | None = None,
        *,
        network_id: str | None = None,
    ) -> tuple[str, asyncio.Queue]:
        """
        注册 forecast SSE 客户端并快照重放事件。

        调用方应在 HTTP 层把 :class:`SSECursorExpiredError` 映射为
        ``409 SSE_CURSOR_EXPIRED``。重放快照与 live 队列在同一把锁内建立，避免
        快照之后、订阅之前出现事件空窗。
        """
        client_id = str(uuid4())[:8]
        queue: asyncio.Queue[dict[str, str]] = asyncio.Queue(
            maxsize=self._forecast_queue_size
        )
        cursor = self._parse_cursor(last_event_id)

        replay: tuple[dict[str, str], ...]
        if self._replay_store is not None:
            replay = await self._durable_replay(cursor, network_id=network_id)
        else:
            async with self._lock:
                replay = self._forecast_replay(cursor, network_id=network_id)
        async with self._lock:
            self._clients[client_id] = SSEClient(
                client_id=client_id,
                queue=queue,
                network_id=network_id,
                last_event_id=cursor,
                replay_events=replay,
            )
            self._forecast_clients.add(client_id)

        logger.info(
            "🔗 Forecast SSE 客户端连接: %s network=%s replay=%d",
            client_id,
            network_id or "*",
            len(replay),
        )
        return client_id, queue

    async def disconnect(self, client_id: str) -> None:
        """注销 SSE 客户端。"""
        async with self._lock:
            client = self._clients.pop(client_id, None)
            self._forecast_clients.discard(client_id)
        if client:
            logger.info(
                "🔌 SSE 客户端断开: %s | 共接收 %d 条事件 | 存活 %.1f 秒",
                client_id,
                client.event_count,
                time.time() - client.connected_at,
            )

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """广播 legacy SSE 事件（保持原 API/行为）。"""
        event_data = json.dumps(data, ensure_ascii=False, default=str)

        async with self._lock:
            self._event_counter += 1
            event_id = self._event_counter
            self._event_history.append((event_id, event_type, event_data))
            if len(self._event_history) > self._MAX_HISTORY:
                history_limit = self._MAX_HISTORY
                self._event_history = self._event_history[-history_limit:]
            clients = [
                (client_id, client)
                for client_id, client in self._clients.items()
                if client_id not in self._forecast_clients
            ]

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
                await self.disconnect(client_id)

    async def broadcast_forecast_event(
        self,
        event: Mapping[str, Any] | str,
        data: Mapping[str, Any] | None = None,
        *,
        sequence: int | str | None = None,
        event_id: str | None = None,
        event_type: str | None = None,
    ) -> bool:
        """
        接收一条已持久化的 forecast outbox/Redis 事件并 fan-out。

        可以传入完整 envelope，或使用 ``event_type, data, sequence, event_id``
        组装。返回 ``True`` 表示首次接收，``False`` 表示重复/非递增事件。

        该方法不分配业务 sequence，也不调用 Redis；它只消费 relay 消息，从而
        保持 ``outbox -> Redis -> API fan-out`` 的单一路径。
        """
        if isinstance(event, str):
            if event_type is not None and event_type != event:
                raise ValueError("conflicting forecast event_type values")
            event_type = event
            if data is None:
                raise ValueError("forecast event data is required")
            envelope: dict[str, Any] = dict(data)
        elif isinstance(event, Mapping):
            envelope = dict(event)
        else:
            raise TypeError(
                "forecast event must be a mapping or event type string",
            )

        sequence_value = sequence
        if sequence_value is None:
            sequence_value = envelope.get("sequence")
        normalized_sequence = self._normalize_event_sequence(sequence_value)
        normalized_event_id = event_id or envelope.get("event_id")
        event_id_value = normalized_event_id
        if not isinstance(event_id_value, str):
            raise ValueError("forecast event_id must be a non-empty string")
        if not event_id_value.strip():
            raise ValueError("forecast event_id must be a non-empty string")
        normalized_event_id = event_id_value.strip()

        normalized_event_type = event_type or envelope.get("event_type")
        if (
            not isinstance(normalized_event_type, str)
            or normalized_event_type not in FORECAST_EVENT_TYPES
        ):
            raise ValueError("unsupported forecast event_type")

        envelope.update(
            {
                "schema_version": envelope.get("schema_version", 1),
                "event_id": normalized_event_id,
                "sequence": normalized_sequence,
                "event_type": normalized_event_type,
            }
        )
        _validate_forecast_envelope(envelope)
        event_data = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        queue_event = {
            "event": "forecast",
            "data": event_data,
            "id": str(normalized_sequence),
        }

        async with self._lock:
            if (
                normalized_event_id in self._forecast_event_ids
                or normalized_sequence <= self._forecast_latest_sequence
            ):
                logger.info(
                    "Forecast SSE duplicate skipped: event_id=%s sequence=%s",
                    normalized_event_id,
                    normalized_sequence,
                )
                return False

            self._forecast_latest_sequence = normalized_sequence
            self._forecast_event_ids.add(normalized_event_id)
            self._forecast_history.append(
                (
                    normalized_sequence,
                    normalized_event_id,
                    normalized_event_type,
                    event_data,
                )
            )
            occurred_at = envelope.get("occurred_at")
            parsed_occurred_at = _parse_occurred_at(occurred_at)
            history_times = self._forecast_history_times
            history_times[normalized_sequence] = parsed_occurred_at
            now = datetime.now(timezone.utc)
            retention_cutoff = now - timedelta(
                hours=self._forecast_replay_retention_hours
            )
            expired = []
            for item in self._forecast_history:
                item_time = self._forecast_history_times.get(item[0], now)
                if item_time < retention_cutoff:
                    expired.append(item[0])
            if expired:
                expired_set = set(expired)
                self._forecast_history = [
                    item
                    for item in self._forecast_history
                    if item[0] not in expired_set
                ]
                for sequence in expired:
                    self._forecast_history_times.pop(sequence, None)
            if len(self._forecast_history) > self._forecast_history_limit:
                history_limit = self._forecast_history_limit
                removed = self._forecast_history[:-history_limit]
                retained_history = self._forecast_history[-history_limit:]
                self._forecast_history = retained_history
                removed_event_ids = (item[1] for item in removed)
                self._forecast_event_ids.difference_update(removed_event_ids)
                for item in removed:
                    self._forecast_history_times.pop(item[0], None)

            clients = [
                client
                for client_id, client in self._clients.items()
                if client_id in self._forecast_clients
                and (
                    client.network_id is None
                    or client.network_id == envelope.get("network_id")
                )
            ]

        for client in clients:
            try:
                client.queue.put_nowait(queue_event)
                client.last_event_at = time.time()
                client.last_enqueued_sequence = normalized_sequence
                client.event_count += 1
                self._total_events_sent += 1
            except asyncio.QueueFull:
                self._mark_forecast_resync_required(client)

        return True

    # 兼容 Redis handler 的直观别名。
    broadcast_forecast = broadcast_forecast_event
    handle_redis_forecast_event = broadcast_forecast_event

    async def broadcast_sensor(self, readings: list[dict[str, Any]]) -> None:
        """广播传感器数据。"""
        await self.broadcast(
            "sensors",
            {
                "readings": readings,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def broadcast_alert(self, alert_data: dict[str, Any]) -> None:
        """广播 legacy 告警。"""
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
        queue: asyncio.Queue[dict[str, str]],
        last_event_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async def generate() -> AsyncGenerator[dict[str, Any], None]:
            try:
                yield {"retry": 3000}

                connected_data = json.dumps(
                    {"client_id": client_id, "status": "connected"}
                )
                yield {
                    "event": "connected",
                    "data": connected_data,
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
                        event = await asyncio.wait_for(queue.get(), timeout=15)
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

    def forecast_event_generator(
        self,
        client_id: str,
        queue: asyncio.Queue[dict[str, str]],
        last_event_id: str | int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        async def generate() -> AsyncGenerator[dict[str, Any], None]:
            last_sent_sequence: int | None = None
            try:
                yield {"retry": 3000}

                client = self._clients.get(client_id)
                if client is None:
                    return
                if last_event_id is not None:
                    last_sent_sequence = self._parse_cursor(last_event_id)
                elif client.last_event_id is not None:
                    last_sent_sequence = client.last_event_id

                replay = client.replay_events
                for event in replay:
                    if "id" in event:
                        last_sent_sequence = int(event["id"])
                        client.last_delivered_sequence = last_sent_sequence
                        client.last_event_id = last_sent_sequence
                    yield event

                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        if "id" in event:
                            last_sent_sequence = int(event["id"])
                            client.last_delivered_sequence = last_sent_sequence
                            client.last_event_id = last_sent_sequence
                        yield event
                        is_resync = self._is_resync_event(event)
                        if event.get("event") == "forecast" and is_resync:
                            return
                    except asyncio.TimeoutError:
                        yield {"comment": f"heartbeat {int(time.time())}"}

            except asyncio.CancelledError:
                logger.debug("Forecast SSE 客户端 %s 被取消", client_id)
            finally:
                if last_sent_sequence is not None:
                    logger.info(
                        "Forecast SSE 客户端 %s 最后处理 sequence=%s",
                        client_id,
                        last_sent_sequence,
                    )
                await self.disconnect(client_id)

        return generate()

    event_generator_forecast = forecast_event_generator

    async def _durable_replay(
        self, cursor: int | None, *, network_id: str | None
    ) -> tuple[dict[str, str], ...]:
        if self._replay_store is None:
            return ()
        try:
            bounds = await self._replay_store.bounds(network_id=network_id)
            earliest, latest = int(bounds[0]), int(bounds[1])
            if cursor is not None and cursor > latest:
                raise SSECursorExpiredError(
                    cursor, earliest_sequence=earliest, latest_sequence=latest
                )
            if cursor is not None and earliest and cursor < earliest - 1:
                raise SSECursorExpiredError(
                    cursor, earliest_sequence=earliest, latest_sequence=latest
                )
            after = cursor or 0
            events = await self._replay_store.replay(
                network_id=network_id,
                after_sequence=after,
                retention_hours=self._forecast_replay_retention_hours,
            )
            normalized: list[dict[str, str]] = []
            for item in events:
                try:
                    payload = (
                        json.loads(item.get("data", ""))
                        if isinstance(item, Mapping)
                        else None
                    )
                except (TypeError, json.JSONDecodeError) as exc:
                    raise SSECursorExpiredError(
                        cursor if cursor is not None else 0,
                        earliest_sequence=earliest,
                        latest_sequence=latest,
                    ) from exc
                if not isinstance(payload, Mapping):
                    raise SSECursorExpiredError(
                        cursor if cursor is not None else 0,
                        earliest_sequence=earliest,
                        latest_sequence=latest,
                    )
                _validate_forecast_envelope(payload)
                payload_network_id = payload.get("network_id")
                if network_id is not None and payload_network_id != network_id:
                    continue
                sequence = int(payload["sequence"])
                if sequence <= after:
                    continue
                normalized.append(
                    {
                        "event": "forecast",
                        "id": str(sequence),
                        "data": json.dumps(
                            payload,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    }
                )
            return tuple(normalized)
        except SSECursorExpiredError:
            raise
        except Exception as exc:
            raise SSECursorExpiredError(
                cursor if cursor is not None else 0,
                earliest_sequence=1,
                latest_sequence=0,
            ) from exc

    def _prune_forecast_history(self) -> None:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self._forecast_replay_retention_hours)
        expired = {
            sequence
            for sequence, occurred_at in self._forecast_history_times.items()
            if occurred_at < cutoff
        }
        if not expired:
            return
        history = self._forecast_history
        removed_ids = {
            event_id
            for sequence, event_id, _event_type, _data in history
            if sequence in expired
        }
        self._forecast_event_ids.difference_update(removed_ids)
        self._forecast_history = [
            item for item in self._forecast_history if item[0] not in expired
        ]
        for sequence in expired:
            self._forecast_history_times.pop(sequence, None)

    def _forecast_replay(
        self,
        cursor: int | None,
        *,
        network_id: str | None,
    ) -> tuple[dict[str, str], ...]:
        self._prune_forecast_history()
        if cursor is None:
            return ()

        if not self._forecast_history:
            if cursor > 0:
                raise SSECursorExpiredError(
                    cursor,
                    earliest_sequence=1,
                    latest_sequence=self._forecast_latest_sequence,
                )
            return ()

        earliest_sequence = self._forecast_history[0][0]
        if cursor > self._forecast_latest_sequence:
            raise SSECursorExpiredError(
                cursor,
                earliest_sequence=earliest_sequence,
                latest_sequence=self._forecast_latest_sequence,
            )
        if cursor < earliest_sequence - 1:
            raise SSECursorExpiredError(
                cursor,
                earliest_sequence=earliest_sequence,
                latest_sequence=self._forecast_latest_sequence,
            )

        replay: list[dict[str, str]] = []
        history = self._forecast_history
        for sequence, _event_id, _event_type, event_data in history:
            if sequence <= cursor:
                continue
            try:
                envelope = json.loads(event_data)
            except json.JSONDecodeError:
                logger.error("Forecast replay history contains invalid JSON")
                continue
            if network_id is not None:
                if envelope.get("network_id") != network_id:
                    continue
            replay.append(
                {
                    "event": "forecast",
                    "data": event_data,
                    "id": str(sequence),
                }
            )
        return tuple(replay)

    def _mark_forecast_resync_required(self, client: SSEClient) -> None:
        """用 resync sentinel 替换一个未消费事件并关闭慢客户端。"""
        try:
            client.queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

        from_sequence = (
            client.last_delivered_sequence or client.last_event_id or 0
        ) + 1
        to_sequence = max(
            client.last_enqueued_sequence or 0,
            self._forecast_latest_sequence,
        )
        last_event_id = client.last_delivered_sequence or client.last_event_id
        resync_payload = {
            "schema_version": 1,
            "event_id": f"resync-{client.client_id}",
            "event_type": "forecast.resync_required",
            "network_id": client.network_id,
            "last_event_id": last_event_id,
            "from_sequence": from_sequence,
            "to_sequence": to_sequence,
            "reason": "forecast_client_queue_full",
        }
        try:
            client.queue.put_nowait(
                {
                    "event": "forecast",
                    "data": json.dumps(
                        resync_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
        except asyncio.QueueFull:
            logger.error(
                "Forecast SSE resync queue %s is full",
                client.client_id,
            )

    @staticmethod
    def _is_resync_event(event: Mapping[str, str]) -> bool:
        try:
            payload = json.loads(event.get("data", "{}"))
        except json.JSONDecodeError:
            return False
        return bool(payload.get("event_type") == "forecast.resync_required")

    @staticmethod
    def _normalize_event_sequence(value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("forecast sequence must be a positive integer")
        if isinstance(value, int):
            sequence = value
        elif isinstance(value, str) and value.isdigit():
            sequence = int(value)
        else:
            raise ValueError("forecast sequence must be a positive integer")
        if sequence <= 0:
            raise ValueError("forecast sequence must be a positive integer")
        return sequence

    @staticmethod
    def _parse_cursor(value: str | int | None) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise SSECursorExpiredError(
                value,
                earliest_sequence=1,
                latest_sequence=0,
            )
        if isinstance(value, int):
            cursor = value
        elif isinstance(value, str) and value.isdigit():
            cursor = int(value)
        else:
            raise SSECursorExpiredError(
                value,
                earliest_sequence=1,
                latest_sequence=0,
            )
        if cursor < 0:
            raise SSECursorExpiredError(
                cursor,
                earliest_sequence=1,
                latest_sequence=0,
            )
        return cursor

    def reset_forecast(self) -> None:
        """Clear only the process-local forecast cache for isolated tests."""

        self._forecast_history.clear()
        self._forecast_history_times.clear()
        self._forecast_event_ids.clear()
        self._forecast_latest_sequence = 0
        for client_id in list(self._forecast_clients):
            self._clients.pop(client_id, None)
        self._forecast_clients.clear()

    @property
    def client_count(self) -> int:
        """当前 legacy 连接数量（读操作无锁，值可能短暂滞后）。"""
        return len(self._clients) - len(self._forecast_clients)

    @property
    def forecast_client_count(self) -> int:
        return len(self._forecast_clients)

    @property
    def total_events_sent(self) -> int:
        """累计发送事件数（读操作无锁，值可能短暂滞后）。"""
        return self._total_events_sent


sse_manager = SSEManager.get_instance()
