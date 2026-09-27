"""Forecast Redis/SSE replay、去重和通知隔离测试。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import pytest

from app.core.config import settings
from app.core.redis_client import FORECAST_REDIS_CHANNEL, RedisClient
from app.services.notification_service import NotificationService
from app.services.sse_manager import SSECursorExpiredError, SSEManager

NETWORK_ID = "11111111-1111-4111-8111-111111111111"


def _event(
    sequence: int,
    *,
    event_id: str | None = None,
    event_type: str = "alert.created",
    network_id: str = NETWORK_ID,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": event_id or f"event-{sequence}",
        "sequence": sequence,
        "event_type": event_type,
        "occurred_at": "2026-09-25T08:00:00Z",
        "network_id": network_id,
        "nowcast_run_id": "22222222-2222-4222-8222-222222222222",
        "simulation_id": "33333333-3333-4333-8333-333333333333",
        "alert": {
            "id": "44444444-4444-4444-8444-444444444444",
            "type": "flood_forecast",
            "status": "open",
            "phase": "predicted",
            "level": "warning",
            "target": {"type": "node", "id": "stable-node-1"},
            "expected_at": "2026-09-25T08:35:00Z",
            "lead_time_minutes": 35,
            "measurements": {"depth_m": 0.42, "fill_ratio": None},
            "confidence": 0.86,
            "confidence_method": "ensemble_exceedance_frequency",
            "risk_score": 78.4,
            "version": 1,
        },
    }


@pytest.mark.asyncio
async def test_forecast_broadcast_uses_persisted_sequence_and_deduplicates() -> None:
    manager = SSEManager(forecast_queue_size=4)
    client_id, queue = await manager.connect_forecast(network_id=NETWORK_ID)

    first = _event(41)
    assert await manager.broadcast_forecast_event(first) is True
    assert await manager.broadcast_forecast_event(first) is False
    # 同一 event_id 即使 relay 崩溃后带了不同 sequence，也不会形成第二业务投递。
    assert (
        await manager.broadcast_forecast_event(_event(42, event_id="event-41")) is False
    )

    delivered = queue.get_nowait()
    assert delivered["event"] == "forecast"
    assert delivered["id"] == "41"
    assert json.loads(delivered["data"])["event_id"] == "event-41"
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()

    await manager.disconnect(client_id)


@pytest.mark.asyncio
async def test_forecast_last_event_id_replays_only_newer_events() -> None:
    manager = SSEManager(forecast_history_limit=4, forecast_queue_size=4)
    assert await manager.broadcast_forecast_event(_event(1)) is True
    assert await manager.broadcast_forecast_event(_event(2)) is True
    assert await manager.broadcast_forecast_event(_event(3)) is True

    client_id, queue = await manager.connect_forecast("1", network_id=NETWORK_ID)
    stream = manager.forecast_event_generator(client_id, queue, "1")
    assert await anext(stream) == {"retry": 3000}
    replayed_second = await anext(stream)
    replayed_third = await anext(stream)
    assert replayed_second["id"] == "2"
    assert replayed_third["id"] == "3"

    await stream.aclose()
    assert manager.forecast_client_count == 0

    with pytest.raises(SSECursorExpiredError):
        await manager.connect_forecast("99", network_id=NETWORK_ID)


@pytest.mark.asyncio
async def test_forecast_expired_cursor_fails_instead_of_silent_gap() -> None:
    manager = SSEManager(forecast_history_limit=1, forecast_queue_size=2)
    assert await manager.broadcast_forecast_event(_event(10)) is True
    assert await manager.broadcast_forecast_event(_event(11)) is True

    with pytest.raises(SSECursorExpiredError) as exc_info:
        await manager.connect_forecast("0", network_id=NETWORK_ID)

    assert exc_info.value.code == "SSE_CURSOR_EXPIRED"
    assert exc_info.value.earliest_sequence == 11
    assert exc_info.value.latest_sequence == 11


@pytest.mark.asyncio
async def test_forecast_clients_are_isolated_from_legacy_stream() -> None:
    manager = SSEManager(forecast_queue_size=4)
    legacy_id, legacy_queue = await manager.connect()
    forecast_id, forecast_queue = await manager.connect_forecast(network_id=NETWORK_ID)

    assert await manager.broadcast_forecast_event(_event(1)) is True

    forecast_event = forecast_queue.get_nowait()
    assert forecast_event["event"] == "forecast"
    with pytest.raises(asyncio.QueueEmpty):
        legacy_queue.get_nowait()

    await manager.disconnect(legacy_id)
    await manager.disconnect(forecast_id)


@pytest.mark.asyncio
async def test_forecast_network_filter_does_not_leak_other_network_events() -> None:
    manager = SSEManager(forecast_queue_size=4)
    other_network = "99999999-9999-4999-8999-999999999999"
    client_id, queue = await manager.connect_forecast(network_id=NETWORK_ID)

    assert await manager.broadcast_forecast_event(_event(1)) is True
    assert (
        await manager.broadcast_forecast_event(
            _event(2, event_id="other-event", network_id=other_network)
        )
        is True
    )
    assert queue.get_nowait()["id"] == "1"
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()

    await manager.disconnect(client_id)


@pytest.mark.asyncio
async def test_forecast_slow_client_gets_resync_then_disconnects() -> None:
    manager = SSEManager(forecast_queue_size=1)
    client_id, queue = await manager.connect_forecast(network_id=NETWORK_ID)
    assert await manager.broadcast_forecast_event(_event(1)) is True
    assert await manager.broadcast_forecast_event(_event(2)) is True

    stream = manager.forecast_event_generator(client_id, queue, "0")
    assert await anext(stream) == {"retry": 3000}
    resync = await anext(stream)
    assert resync["event"] == "forecast"
    assert json.loads(resync["data"])["event_type"] == "forecast.resync_required"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert manager.forecast_client_count == 0


class _RecordingRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> int:
        self.calls.append((channel, payload))
        return 1


@pytest.mark.asyncio
async def test_redis_forecast_publish_uses_single_relay_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = RedisClient()
    recording_redis = _RecordingRedis()
    redis._redis = recording_redis  # type: ignore[assignment]

    async def fail_if_direct_broadcast(_event: dict[str, Any]) -> None:
        raise AssertionError("Redis publisher must not broadcast locally")

    monkeypatch.setattr(
        "app.services.sse_manager.sse_manager.broadcast_forecast_event",
        fail_if_direct_broadcast,
    )

    assert await redis.publish_forecast_event(_event(101)) == 1
    assert len(recording_redis.calls) == 1
    channel, payload = recording_redis.calls[0]
    assert channel == FORECAST_REDIS_CHANNEL
    assert json.loads(payload)["sequence"] == 101


def test_forecast_notification_template_escapes_all_dynamic_html() -> None:
    service = NotificationService()
    event = _event(201, event_id="event-template")
    event["alert"]["title"] = '<script>alert("x")</script>'
    event["alert"]["description"] = '<img src=x onerror="boom">'
    event["alert"]["target"] = {
        "type": "node",
        "id": '<svg onload="boom">',
    }

    template = service.render_forecast_notification(event)

    assert "<script>" not in template["html"]
    assert "<img" not in template["html"]
    assert "<svg" not in template["html"]
    assert "&lt;script&gt;" in template["html"]
    assert "&lt;img" in template["html"]
    assert "&lt;svg" in template["html"]
    assert template["webhook_payload"]["event_id"] == "event-template"
    assert template["webhook_payload"]["alert"]["type"] == "flood_forecast"


@pytest.mark.asyncio
async def test_forecast_notification_isolates_each_failed_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = NotificationService()
    monkeypatch.setattr(
        settings,
        "ALERT_EMAIL_RECIPIENTS",
        ["ok@example.com", "broken@example.com"],
    )
    monkeypatch.setattr(
        settings,
        "ALERT_WEBHOOK_URLS",
        [
            "https://8.8.8.8/ok",
            "https://127.0.0.1/private",
            "https://8.8.8.8/failed",
        ],
    )

    async def fake_email(recipient: str, *_args: Any) -> bool:
        if recipient == "broken@example.com":
            raise RuntimeError("smtp failure")
        return True

    webhook_calls: list[tuple[str, str | None]] = []

    async def fake_webhook(
        url: str,
        _payload: dict[str, Any],
        *,
        event_id: str,
        signature_secret: str | None,
    ) -> bool:
        webhook_calls.append((url, event_id))
        return url.endswith("/ok")

    monkeypatch.setattr(service, "send_email", fake_email)
    monkeypatch.setattr(service, "send_webhook", fake_webhook)

    result = await service.notify_forecast_event(_event(202))

    assert result["email_attempted"] == 2
    assert result["email_succeeded"] == 1
    assert result["email_failed"] == 1
    assert result["webhook_attempted"] == 3
    assert result["webhook_succeeded"] == 1
    assert result["webhook_failed"] == 2
    assert webhook_calls == [
        ("https://8.8.8.8/ok", "event-202"),
        ("https://8.8.8.8/failed", "event-202"),
    ]


@pytest.mark.asyncio
async def test_forecast_updated_event_does_not_repeat_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = NotificationService()
    monkeypatch.setattr(settings, "ALERT_EMAIL_RECIPIENTS", ["ops@example.com"])
    monkeypatch.setattr(settings, "ALERT_WEBHOOK_URLS", ["https://hooks.example.com"])

    async def unexpected_call(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("alert.updated must not create an external delivery")

    monkeypatch.setattr(service, "send_email", unexpected_call)
    monkeypatch.setattr(service, "send_webhook", unexpected_call)

    result = await service.notify_forecast_event(
        _event(203, event_type="alert.updated")
    )

    assert result["email_attempted"] == 0
    assert result["webhook_attempted"] == 0


@pytest.mark.asyncio
async def test_forecast_webhook_uses_event_id_idempotency_and_hmac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = NotificationService()
    monkeypatch.setattr(settings, "WEBHOOK_ENABLED", True)
    captured: dict[str, Any] = {}

    class _Response:
        status_code = 202

    class _Client:
        async def __aenter__(self) -> "_Client":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> _Response:
            captured.update({"url": url, **kwargs})
            return _Response()

    monkeypatch.setattr(
        "app.services.notification_service.httpx.AsyncClient",
        lambda **_kwargs: _Client(),
    )

    payload = {"event_id": "event-hmac", "value": "<unsafe>"}
    assert (
        await service.send_webhook(
            "https://8.8.8.8/forecast",
            payload,
            event_id="event-hmac",
            signature_secret="top-secret",
        )
        is True
    )

    body = captured["content"]
    expected_signature = hmac.new(b"top-secret", body, hashlib.sha256).hexdigest()
    assert captured["headers"]["X-Forecast-Event-Id"] == "event-hmac"
    assert captured["headers"]["Idempotency-Key"] == "event-hmac"
    assert captured["headers"]["X-Forecast-Signature"] == f"sha256={expected_signature}"
    assert json.loads(body) == payload
