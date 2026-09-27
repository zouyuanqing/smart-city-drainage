"""Production wiring regressions for the integrated FastAPI application."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, generate_latest
from starlette.requests import Request

import app.main as main_module

_REQUIRED_HYDRAULIC_SAMPLES = (
    "simulation_queue_depth",
    "simulation_duration_seconds_count",
    "simulation_failures_total",
    "simulation_retries_total",
    "simulation_timeout_total",
    "simulation_cancelled_total",
    "simulation_active_slots",
)


def test_metrics_singleton_import_precedes_instrumentator_initialization() -> None:
    tree = ast.parse(inspect.getsource(main_module))
    metrics_import_lines = [
        node.lineno
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.services.hydraulic.metrics"
        and any(alias.name == "hydraulic_metrics" for alias in node.names)
    ]
    instrumentator_lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Instrumentator"
    ]

    assert len(metrics_import_lines) == 1
    assert instrumentator_lines
    assert metrics_import_lines[0] < min(instrumentator_lines)


def test_application_import_registers_hydraulic_metrics_singleton() -> None:
    """The real app module owns the singleton before Instrumentator registration."""

    assert hasattr(main_module, "hydraulic_metrics")
    assert main_module.hydraulic_metrics.registry is REGISTRY

    exposition = generate_latest(REGISTRY).decode("utf-8")
    for sample_name in _REQUIRED_HYDRAULIC_SAMPLES:
        assert sample_name in exposition


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_hydraulic_collectors() -> None:
    """The mounted production endpoint serves the same default registry."""

    transport = httpx.ASGITransport(app=cast(Any, main_module.app))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for sample_name in _REQUIRED_HYDRAULIC_SAMPLES:
        assert sample_name in response.text


def _write_policy(
    path: Path, payload: dict[str, Any], *, raw: str | None = None
) -> None:
    path.write_text(
        raw if raw is not None else json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    Path(f"{path}.sha256").write_text(
        hashlib.sha256(canonical).hexdigest(), encoding="utf-8"
    )


def test_forecast_policy_gate_requires_closed_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arbitrary = tmp_path / "arbitrary.json"
    _write_policy(arbitrary, {"unexpected": "not-a-valid-policy"})
    monkeypatch.setattr(
        main_module.settings, "FORECAST_POLICY_CONFIG_PATH", str(arbitrary)
    )
    assert main_module._forecast_component_ready() is False

    valid = tmp_path / "policy.json"
    payload = {
        "schema_version": 1,
        "networks": {
            str(uuid.uuid4()): {
                "probability_calibration_ids": {
                    "flood_forecast": str(uuid.uuid4()),
                    "pipe_surcharge": str(uuid.uuid4()),
                },
                "forecast_rule_ids": {
                    "flood_forecast": str(uuid.uuid4()),
                    "pipe_surcharge": str(uuid.uuid4()),
                },
            }
        },
    }
    _write_policy(valid, payload)
    monkeypatch.setattr(main_module.settings, "FORECAST_POLICY_CONFIG_PATH", str(valid))
    assert main_module._forecast_component_ready() is True

    duplicate = tmp_path / "duplicate.json"
    _write_policy(duplicate, payload, raw='{"schema_version":1,"schema_version":1}')
    monkeypatch.setattr(
        main_module.settings, "FORECAST_POLICY_CONFIG_PATH", str(duplicate)
    )
    assert main_module._forecast_component_ready() is False


@pytest.mark.asyncio
async def test_outbox_relay_acknowledges_success_without_subscribers() -> None:
    from app.core.redis_client import RedisClient

    class SubscriberlessRedis:
        async def publish(self, channel: str, payload: str) -> int:
            return 0

    client = RedisClient()
    client._redis = cast(Any, SubscriberlessRedis())
    marked: list[dict[str, Any]] = []
    event = {
        "schema_version": 1,
        "sequence": 1,
        "event_id": "event-1",
        "event_type": "alert.created",
        "network_id": "network-1",
        "nowcast_run_id": "run-1",
        "simulation_id": "simulation-1",
        "occurred_at": "2026-09-25T00:00:00+00:00",
        "alert": {
            "id": "alert-1",
            "type": "flood_forecast",
            "status": "open",
            "phase": "predicted",
            "level": "warning",
            "target": {"type": "node", "id": "node-1"},
            "expected_at": "2026-09-25T00:05:00+00:00",
            "confidence": 0.9,
            "risk_score": 70.0,
            "version": 1,
        },
    }

    count = await client.relay_forecast_outbox(
        [event], mark_published=cast(Any, marked.append)
    )

    assert count == 1
    assert marked == [event]


@pytest.mark.asyncio
async def test_random_authorization_headers_do_not_create_rate_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module.redis_client, "_redis", None)
    main_module._rate_buckets.clear()

    async def call_next(_request: Request) -> Response:
        return Response(status_code=200)

    for index in range(1_000):
        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/api/forecast/latest",
                "raw_path": b"/api/forecast/latest",
                "query_string": b"",
                "root_path": "",
                "headers": [
                    (b"authorization", f"Bearer random-{index}".encode("ascii"))
                ],
                "client": ("127.0.0.1", index + 1),
                "server": ("test", 80),
                "state": {},
            }
        )
        await main_module.hydraulic_rate_limit(request, call_next)

    assert len(main_module._rate_buckets) == 1
    main_module._rate_buckets.clear()


def test_durable_metrics_snapshot_publishes_nonzero_gauges() -> None:
    previous = {
        "queue": REGISTRY.get_sample_value("simulation_queue_depth") or 0.0,
        "oldest": REGISTRY.get_sample_value("simulation_oldest_queued_seconds") or 0.0,
        "general": REGISTRY.get_sample_value(
            "simulation_active_slots", {"lane": "general"}
        )
        or 0.0,
        "nowcast": REGISTRY.get_sample_value(
            "simulation_active_slots", {"lane": "nowcast"}
        )
        or 0.0,
        "outbox": REGISTRY.get_sample_value("hydraulic_event_outbox_backlog") or 0.0,
        "alerts": REGISTRY.get_sample_value(
            "forecast_active_alerts",
            {"type": "flood_forecast", "level": "critical"},
        )
        or 0.0,
    }
    try:
        main_module._apply_hydraulic_metrics_snapshot(
            {
                "queue": {"depth": 2, "oldest_queued_seconds": 12.5},
                "slots": {"general": 1, "nowcast": 0},
                "outbox": {"depth": 3, "oldest_seconds": 4.5},
                "active_alerts": [
                    {"alert_type": "flood_forecast", "level": "critical", "count": 2}
                ],
            }
        )

        exposition = generate_latest(REGISTRY).decode("utf-8")
        assert "simulation_queue_depth 2.0" in exposition
        assert 'simulation_active_slots{lane="general"} 1.0' in exposition
        assert "hydraulic_event_outbox_backlog 3.0" in exposition
        assert (
            'forecast_active_alerts{level="critical",type="flood_forecast"} 2.0'
            in exposition
        )
    finally:
        main_module.hydraulic_metrics.set_queue_state(
            depth=int(previous["queue"]),
            oldest_queued_seconds=float(previous["oldest"]),
        )
        main_module.hydraulic_metrics.set_active_slots(
            general=int(previous["general"]), nowcast=int(previous["nowcast"])
        )
        main_module.hydraulic_metrics.set_event_outbox_backlog(
            depth=int(previous["outbox"])
        )
        main_module.hydraulic_metrics.set_active_alerts(
            alert_type="flood_forecast",
            level="critical",
            count=int(previous["alerts"]),
        )


@pytest.mark.asyncio
async def test_forecast_redis_handler_has_no_external_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import notification_service, sse_manager

    broadcast: list[dict[str, Any]] = []

    async def fake_broadcast(message: dict[str, Any]) -> None:
        broadcast.append(message)

    async def forbidden_notify(_message: dict[str, Any]) -> None:
        raise AssertionError("API Redis listeners must not deliver notifications")

    monkeypatch.setattr(
        sse_manager.sse_manager, "broadcast_forecast_event", fake_broadcast
    )
    monkeypatch.setattr(
        notification_service.notification_service,
        "notify_forecast_event",
        forbidden_notify,
    )
    event = {"event_id": "event-1"}

    await main_module._handle_redis_forecast(cast(Any, event))

    assert broadcast == [event]


@pytest.mark.asyncio
async def test_production_hydraulic_api_fails_before_route_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module.settings, "DEPLOYMENT_ENVIRONMENT", "production")
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/networks/import",
            "raw_path": b"/api/networks/import",
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "state": {},
        }
    )

    async def forbidden_route(_request: Request) -> Response:
        raise AssertionError(
            "production route must not execute without a durable store"
        )

    response = await main_module.hydraulic_rate_limit(request, forbidden_route)

    assert response.status_code == 503
    assert json.loads(response.body)["code"] == "DURABLE_STORE_UNAVAILABLE"


def test_compose_backend_is_production_and_secret_free() -> None:
    compose_path = Path(__file__).resolve().parents[3] / "docker-compose.yml"
    compose = compose_path.read_text(encoding="utf-8")

    assert "DEPLOYMENT_ENVIRONMENT: production" in compose
    assert "scn-influx-token-2024-secure" not in compose
    assert "Dr@inage_S3cur3_2024!" not in compose
    assert "R3d1s_S3cur3_2024!" not in compose
    assert "${JWT_PRIVATE_KEY:?set JWT_PRIVATE_KEY}" in compose
    assert "${REDIS_URL:?set REDIS_URL with the Redis password}" in compose


def test_real_lifespan_bootstraps_durable_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core import database as database_module
    from app.services import influxdb_service, stream_service
    from app.services.mock_data_generator import MockDataGenerator
    from app.services.sse_manager import sse_manager

    async def database_unavailable() -> bool:
        return False

    async def redis_noop() -> None:
        return None

    async def stream_start() -> None:
        return None

    async def mock_start() -> None:
        return None

    async def dispose() -> None:
        return None

    monkeypatch.setattr(main_module, "_LOG_DIRECTORY", tmp_path / "logs")
    monkeypatch.setattr(main_module, "check_database_connection", database_unavailable)
    monkeypatch.setattr(
        main_module, "get_model_manager", lambda: SimpleNamespace(active_version="v1")
    )
    monkeypatch.setattr(main_module.redis_client, "_redis", None)
    monkeypatch.setattr(main_module.redis_client, "_handlers", {})
    monkeypatch.setattr(main_module.redis_client, "connect", redis_noop)
    monkeypatch.setattr(main_module.redis_client, "start_listener", redis_noop)
    monkeypatch.setattr(main_module.redis_client, "disconnect", redis_noop)
    monkeypatch.setattr(influxdb_service.influxdb_service, "connect", redis_noop)
    monkeypatch.setattr(influxdb_service.influxdb_service, "disconnect", redis_noop)
    monkeypatch.setattr(
        stream_service.stream_service, "start_health_monitor", stream_start
    )
    monkeypatch.setattr(stream_service.stream_service, "stop_all", stream_start)
    monkeypatch.setattr(MockDataGenerator, "start", mock_start)
    monkeypatch.setattr(MockDataGenerator, "stop", mock_start)
    monkeypatch.setattr(database_module, "dispose_engine", dispose)

    with TestClient(main_module.app) as client:
        assert client.get("/health").status_code == 200
        assert isinstance(
            main_module.app.state.forecast_outbox_source,
            main_module.DatabaseForecastOutboxRelay,
        )

    assert (tmp_path / "logs" / "app.log").is_file()
    assert isinstance(
        sse_manager.forecast_replay_store, main_module.DatabaseForecastReplayStore
    )
