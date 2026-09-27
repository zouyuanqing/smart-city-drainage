"""
智慧城市神经末梢 — FastAPI 应用入口
====================================
市政排水智能监测与AI安防系统后端服务。

Copyright 2024 Smart City Neural Endpoints
Licensed under the Apache License, Version 2.0

启动命令:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

# 强制 stdout 使用 UTF-8 编码
import asyncio
import hashlib
import json
import logging
import math
import sys
import time
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.message import Message
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, AsyncGenerator, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from pythonjsonlogger import jsonlogger
from sqlalchemy import text

from app.api.routes import router as api_router
from app.core.config import settings
from app.core.database import (
    check_database_connection,
    check_hydraulic_schema_readiness,
    get_db_session,
    get_session_factory,
)
from app.core.model_manager import get_model_manager
from app.core.redis_client import redis_client
from app.services.hydraulic.metrics import hydraulic_metrics
from app.services.mock_data_generator import mock_generator
from app.services.stream_service import stream_service

# ============================================================
# 日志配置
# ============================================================

_LOG_DIRECTORY = Path(__file__).resolve().parents[1] / "logs"
_REQUIRED_HYDRAULIC_TABLES = frozenset(
    {
        "ab_comparisons",
        "event_outbox",
        "forecast_alert_windows",
        "forecast_alert_transitions",
        "forecast_alerts",
        "network_imports",
        "network_jobs",
        "network_revisions",
        "network_validation_issues",
        "networks",
        "nowcast_runs",
        "notification_deliveries",
        "observation_ingest_events",
        "observation_samples",
        "observation_trust_policies",
        "simulations",
        "validation_runs",
        "worker_slots",
    }
)
_RATE_BUCKETS_MAX = 10_000
_WORKER_HEARTBEAT_KEYS = {
    "network-job": "hydraulic:worker:heartbeat:network-job",
    "simulation": "hydraulic:worker:heartbeat:simulation",
    "nowcast": "hydraulic:worker:heartbeat:nowcast",
    "outbox-relay": "hydraulic:worker:heartbeat:outbox-relay",
    "notification": "hydraulic:worker:heartbeat:notification",
}


class DatabaseForecastReplayStore:
    """PostgreSQL replay adapter for the frozen forecast event outbox."""

    async def replay(
        self, *, network_id: str | None, after_sequence: int, retention_hours: int
    ) -> tuple[dict[str, str], ...]:
        conditions = [
            "sequence > :after_sequence",
            "expires_at > clock_timestamp()",
            "occurred_at >= clock_timestamp() "
            "- make_interval(hours => :retention_hours)",
        ]
        params: dict[str, Any] = {
            "after_sequence": int(after_sequence),
            "retention_hours": int(retention_hours),
        }
        if network_id is not None:
            conditions.append("network_id = :network_id")
            params["network_id"] = str(network_id)
        statement = text(
            "SELECT sequence, event_id, payload FROM hydraulic.event_outbox WHERE "
            + " AND ".join(conditions)
            + " ORDER BY sequence"
        )
        events: list[dict[str, str]] = []
        async with get_session_factory()() as session:
            rows = (await session.execute(statement, params)).mappings().all()
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, Mapping):
                raise ValueError("forecast outbox payload must be an object")
            envelope = dict(payload)
            sequence = int(row["sequence"])
            event_id = str(row["event_id"])
            if (
                envelope.get("sequence") != sequence
                or envelope.get("event_id") != event_id
            ):
                raise ValueError("forecast outbox envelope identity is inconsistent")
            events.append(
                {
                    "event": "forecast",
                    "id": str(sequence),
                    "data": json.dumps(
                        envelope,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                }
            )
        return tuple(events)

    async def bounds(self, *, network_id: str | None) -> tuple[int, int]:
        params: dict[str, Any] = {}
        network_clause = ""
        if network_id is not None:
            network_clause = " AND network_id = :network_id"
            params["network_id"] = str(network_id)
        statement = text(
            "SELECT COALESCE(MIN(sequence), 0) AS earliest, "
            "COALESCE(MAX(sequence), 0) AS latest "
            "FROM hydraulic.event_outbox "
            "WHERE expires_at > clock_timestamp()" + network_clause
        )
        async with get_session_factory()() as session:
            row = (await session.execute(statement, params)).mappings().one()
        return int(row["earliest"]), int(row["latest"])


class DatabaseForecastOutboxRelay:
    """Single-logical-relay publisher backed by a PostgreSQL transaction."""

    async def relay_once(self, publish: Any) -> int:
        if not redis_client.is_connected:
            raise RuntimeError("Redis is disconnected; forecast outbox remains pending")
        async with get_session_factory()() as session:
            async with session.begin():
                acquired = await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                    {"lock_key": 0x4859444F},
                )
                if not acquired:
                    return 0
                rows = (
                    (
                        await session.execute(
                            text(
                                "SELECT sequence, event_id, payload "
                                "FROM hydraulic.event_outbox "
                                "WHERE published_at IS NULL "
                                "AND expires_at > clock_timestamp() "
                                "ORDER BY sequence LIMIT 100 "
                                "FOR UPDATE SKIP LOCKED"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                for row in rows:
                    payload = row["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if not isinstance(payload, Mapping):
                        raise ValueError("forecast outbox payload must be an object")
                    envelope = dict(payload)
                    sequence = int(row["sequence"])
                    event_id = str(row["event_id"])
                    if (
                        envelope.get("sequence") != sequence
                        or envelope.get("event_id") != event_id
                    ):
                        raise ValueError(
                            "forecast outbox envelope identity is inconsistent"
                        )
                    await publish(envelope)
                    result = await session.execute(
                        text(
                            "UPDATE hydraulic.event_outbox "
                            "SET published_at = clock_timestamp() "
                            "WHERE sequence = :sequence AND event_id = :event_id "
                            "AND published_at IS NULL"
                        ),
                        {"sequence": sequence, "event_id": event_id},
                    )
                    if getattr(result, "rowcount", None) != 1:
                        raise RuntimeError(
                            "forecast outbox acknowledgement lost its fence"
                        )
                return len(rows)

    async def pending_count(self) -> int:
        async with get_session_factory()() as session:
            return int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM hydraulic.event_outbox "
                        "WHERE published_at IS NULL AND expires_at > clock_timestamp()"
                    )
                )
                or 0
            )


def setup_logging():
    log_level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
    if not isinstance(log_level, int):
        log_level = logging.INFO

    json_formatter = jsonlogger.JsonFormatter(
        "%(timestamp)s %(level)s %(name)s %(message)s",
        rename_fields={
            "timestamp": "timestamp",
            "level": "level",
            "name": "logger",
        },
    )

    root_logger = logging.getLogger()
    for existing in list(root_logger.handlers):
        if getattr(existing, "_scn_managed", False):
            root_logger.removeHandler(existing)
            existing.close()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(json_formatter)
    console_handler._scn_managed = True  # type: ignore[attr-defined]
    root_logger.addHandler(console_handler)

    try:
        _LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            _LOG_DIRECTORY / "app.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        file_handler.setFormatter(json_formatter)
        file_handler._scn_managed = True  # type: ignore[attr-defined]
        root_logger.addHandler(file_handler)
    except OSError as exc:
        # Read-only containers and restricted local checkouts still get a
        # complete stdout-only application instead of failing during lifespan.
        print(
            f"file logging disabled; cannot use {_LOG_DIRECTORY}: {exc}",
            file=sys.stderr,
        )

    root_logger.setLevel(log_level)
    for lib in ["httpx", "httpcore", "urllib3", "ultralytics", "uvicorn.access"]:
        logging.getLogger(lib).setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


async def _handle_redis_alerts(message: dict):
    from app.services.sse_manager import sse_manager

    await sse_manager.broadcast("alerts", message)


async def _handle_redis_sensor(message: dict):
    from app.services.sse_manager import sse_manager

    await sse_manager.broadcast("sensors", message)


async def _handle_redis_model(message: dict):
    from app.services.sse_manager import sse_manager

    await sse_manager.broadcast("system", message)


async def _handle_redis_forecast(message: dict):
    """Fan out one committed forecast event without external side effects."""

    from app.services.sse_manager import sse_manager

    # API replicas are fan-out consumers only.  SMTP/webhook delivery belongs
    # to the durable notification worker; doing it here would multiply sends
    # across replicas and let a slow provider block the Redis listener.
    await sse_manager.broadcast_forecast_event(message)


async def _relay_forecast_outbox_once() -> int:
    """Relay committed rows and require a durable acknowledgement callback."""

    source = getattr(app.state, "forecast_outbox_source", None)
    if source is None:
        source = DatabaseForecastOutboxRelay()
    relay_once = getattr(source, "relay_once", None)
    if callable(relay_once):
        return int(await relay_once(redis_client.publish_forecast_event))
    rows = cast(Any, source)()
    if hasattr(rows, "__await__"):
        rows = await rows
    mark_published = getattr(source, "mark_published", None)
    if not callable(mark_published):
        mark_published = getattr(app.state, "forecast_outbox_ack", None)
    if not callable(mark_published):
        raise RuntimeError("forecast outbox source has no durable acknowledgement")
    return await redis_client.relay_forecast_outbox(
        rows or (), mark_published=mark_published
    )


async def _forecast_relay_loop() -> None:
    while True:
        try:
            await _relay_forecast_outbox_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Forecast outbox relay iteration failed")
        await asyncio.sleep(1.0)


class DatabaseHydraulicMetricsSource:
    """Read durable control-plane snapshots for Prometheus gauges/counters."""

    async def snapshot(self) -> dict[str, Any]:
        async with get_session_factory()() as session:
            queue = (
                (
                    await session.execute(
                        text(
                            "SELECT count(*) AS depth, "
                            "COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() - "
                            "MIN(COALESCE(next_attempt_at, created_at)))), 0) "
                            "AS oldest_seconds FROM hydraulic.simulations "
                            "WHERE status IN ('queued','retry_wait')"
                        )
                    )
                )
                .mappings()
                .one()
            )
            slot_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT lane, count(*) FILTER (WHERE busy) AS busy "
                            "FROM hydraulic.worker_slots GROUP BY lane"
                        )
                    )
                )
                .mappings()
                .all()
            )
            outbox = (
                (
                    await session.execute(
                        text(
                            "SELECT count(*) AS depth, "
                            "COALESCE(EXTRACT(EPOCH FROM (clock_timestamp() - "
                            "MIN(created_at))), 0) AS oldest_seconds "
                            "FROM hydraulic.event_outbox "
                            "WHERE published_at IS NULL "
                            "AND expires_at > clock_timestamp()"
                        )
                    )
                )
                .mappings()
                .one()
            )
            alert_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT alert_type, level, count(*) AS count "
                            "FROM hydraulic.forecast_alerts "
                            "WHERE status IN ('open','acknowledged') "
                            "GROUP BY alert_type, level"
                        )
                    )
                )
                .mappings()
                .all()
            )
            simulation_terminals = (
                (
                    await session.execute(
                        text(
                            "SELECT id, solver_id, status, "
                            "GREATEST(EXTRACT(EPOCH FROM "
                            "(COALESCE(finished_at, updated_at) "
                            "- COALESCE(started_at, created_at))), 0) "
                            "AS duration_seconds, "
                            "error_code FROM hydraulic.simulations "
                            "WHERE status IN ('completed','failed',"
                            "'cancelled','timed_out') "
                            "ORDER BY COALESCE(finished_at, updated_at) DESC LIMIT 1000"
                        )
                    )
                )
                .mappings()
                .all()
            )
            nowcast_terminals = (
                (
                    await session.execute(
                        text(
                            "SELECT id, solver_id, status, "
                            "GREATEST(EXTRACT(EPOCH FROM "
                            "(COALESCE(completed_at, updated_at) "
                            "- COALESCE(started_at, created_at))), 0) "
                            "AS duration_seconds "
                            "FROM hydraulic.nowcast_runs "
                            "WHERE status IN ('completed','failed',"
                            "'cancelled','timed_out') "
                            "ORDER BY COALESCE(completed_at, updated_at) "
                            "DESC LIMIT 1000"
                        )
                    )
                )
                .mappings()
                .all()
            )
            notification_terminals = (
                (
                    await session.execute(
                        text(
                            "SELECT id, channel, status "
                            "FROM hydraulic.notification_deliveries "
                            "WHERE status IN ('delivered','dead_letter','suppressed') "
                            "ORDER BY updated_at DESC LIMIT 1000"
                        )
                    )
                )
                .mappings()
                .all()
            )
        slots = {str(row["lane"]): int(row["busy"]) for row in slot_rows}
        return {
            "queue": {
                "depth": int(queue["depth"]),
                "oldest_queued_seconds": max(0.0, float(queue["oldest_seconds"])),
            },
            "slots": {
                "general": slots.get("general", 0),
                "nowcast": slots.get("nowcast", 0),
            },
            "outbox": {
                "depth": int(outbox["depth"]),
                "oldest_seconds": max(0.0, float(outbox["oldest_seconds"])),
            },
            "active_alerts": [dict(row) for row in alert_rows],
            "simulation_terminals": [dict(row) for row in simulation_terminals],
            "nowcast_terminals": [dict(row) for row in nowcast_terminals],
            "notification_terminals": [dict(row) for row in notification_terminals],
        }


_recorded_nowcast_terminals: set[str] = set()
_recorded_notification_terminals: set[str] = set()


def _apply_hydraulic_metrics_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Publish one durable snapshot without adding high-cardinality labels."""

    queue = dict(snapshot.get("queue") or {})
    slots = dict(snapshot.get("slots") or {})
    hydraulic_metrics.set_queue_state(
        depth=int(queue.get("depth", 0)),
        oldest_queued_seconds=float(queue.get("oldest_queued_seconds", 0.0)),
    )
    hydraulic_metrics.set_active_slots(
        general=int(slots.get("general", 0)),
        nowcast=int(slots.get("nowcast", 0)),
    )
    outbox = dict(snapshot.get("outbox") or {})
    hydraulic_metrics.set_event_outbox_backlog(depth=int(outbox.get("depth", 0)))
    if float(outbox.get("oldest_seconds", 0.0)) > 0:
        hydraulic_metrics.observe_event_publish_lag(
            lag_seconds=float(outbox["oldest_seconds"])
        )
    for row in snapshot.get("active_alerts") or ():
        hydraulic_metrics.set_active_alerts(
            alert_type=str(row["alert_type"]),
            level=str(row["level"]),
            count=int(row["count"]),
        )
    for row in snapshot.get("simulation_terminals") or ():
        hydraulic_metrics.record_simulation_terminal(
            simulation_id=str(row["id"]),
            solver_id=str(row["solver_id"]),
            status=str(row["status"]),
            duration_seconds=float(row.get("duration_seconds", 0.0)),
            error_code=(str(row["error_code"]) if row.get("error_code") else None),
        )
    for row in snapshot.get("nowcast_terminals") or ():
        identity = str(row["id"])
        if identity in _recorded_nowcast_terminals:
            continue
        _recorded_nowcast_terminals.add(identity)
        hydraulic_metrics.record_nowcast_terminal(
            status=str(row["status"]),
            solver_id=str(row["solver_id"]),
            duration_seconds=float(row.get("duration_seconds", 0.0)),
        )
    for row in snapshot.get("notification_terminals") or ():
        identity = str(row["id"])
        if identity in _recorded_notification_terminals:
            continue
        _recorded_notification_terminals.add(identity)
        hydraulic_metrics.record_notification_delivery(
            channel=str(row["channel"]),
            status=str(row["status"]),
        )
    if len(_recorded_nowcast_terminals) > 100_000:
        _recorded_nowcast_terminals.clear()
    if len(_recorded_notification_terminals) > 100_000:
        _recorded_notification_terminals.clear()


async def _hydraulic_metrics_loop(source: Any) -> None:
    while True:
        try:
            # app.state.hydraulic_metrics_source is a DatabaseHydraulicMetricsSource
            # instance, so the reader is its .snapshot() method.  Fall back to a
            # plain callable for injected test doubles.
            reader = getattr(source, "snapshot", None)
            snapshot = reader() if callable(reader) else source()
            if hasattr(snapshot, "__await__"):
                snapshot = await snapshot
            _apply_hydraulic_metrics_snapshot(dict(snapshot))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Durable hydraulic metrics snapshot failed: %s", exc)
        await asyncio.sleep(2.0)


def _json_object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_forecast_policy_config() -> dict[str, Any] | None:
    path_text = str(getattr(settings, "FORECAST_POLICY_CONFIG_PATH", "") or "")
    if not path_text:
        return None
    try:
        config_path = Path(path_text)
        sidecar_path = Path(f"{path_text}.sha256")
        if not config_path.is_file() or not sidecar_path.is_file():
            return None
        payload = json.loads(
            config_path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number {value}")
            ),
        )
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "networks",
        }:
            return None
        if payload.get("schema_version") != 1 or not isinstance(
            payload["networks"], dict
        ):
            return None
        for network_id, network in payload["networks"].items():
            uuid.UUID(str(network_id))
            if not isinstance(network, dict) or set(network) != {
                "probability_calibration_ids",
                "forecast_rule_ids",
            }:
                return None
            for field in ("probability_calibration_ids", "forecast_rule_ids"):
                selection = network[field]
                if not isinstance(selection, dict) or set(selection) != {
                    "flood_forecast",
                    "pipe_surcharge",
                }:
                    return None
                for artifact_id in selection.values():
                    parsed = uuid.UUID(str(artifact_id))
                    if parsed.int == 0:
                        return None
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        expected = hashlib.sha256(canonical).hexdigest()
        sidecar = sidecar_path.read_text(encoding="utf-8")
        if sidecar.endswith("\n"):
            sidecar = sidecar[:-1]
        if "\n" in sidecar or "\r" in sidecar or sidecar != expected:
            return None
        if len(expected) != 64 or expected.strip("0123456789abcdef"):
            return None
        return payload
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


async def _validate_forecast_artifacts(
    config: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    probability_ids: set[str] = set()
    rule_ids: set[str] = set()
    expected_probability_metrics: dict[str, str] = {}
    expected_rule_types: dict[str, str] = {}
    for network in config["networks"].values():
        for alert_type, artifact_id in network["probability_calibration_ids"].items():
            identity = str(uuid.UUID(str(artifact_id)))
            probability_ids.add(identity)
            expected_probability_metrics[identity] = (
                "water_depth_m" if alert_type == "flood_forecast" else "fill_ratio"
            )
        for alert_type, artifact_id in network["forecast_rule_ids"].items():
            identity = str(uuid.UUID(str(artifact_id)))
            rule_ids.add(identity)
            expected_rule_types[identity] = str(alert_type)

    artifact_root = Path(settings.HYDRAULIC_ARTIFACT_ROOT).expanduser().resolve()

    def validate_file(
        row: Any, schema_version: int, artifact_format: str | None
    ) -> None:
        """Validate one bound artifact file against its database row.

        ``artifact_format`` is declared only on
        ``hydraulic.probability_calibrations``; ``hydraulic.forecast_rules`` has
        no such column, so the caller states the value it actually holds rather
        than the validator reading a key that the rules SELECT never selects.
        """
        uri = str(row["artifact_uri"])
        candidate = Path(uri)
        if not candidate.is_absolute():
            raise ValueError("forecast artifact URI must be absolute")
        resolved = candidate.resolve()
        if resolved != artifact_root and artifact_root not in resolved.parents:
            raise ValueError("forecast artifact escapes the controlled root")
        if not resolved.is_file():
            raise ValueError("forecast artifact file is missing")
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if digest != str(row["artifact_sha256"]):
            raise ValueError("forecast artifact hash mismatch")
        if artifact_format is not None and str(artifact_format).lower() != "json":
            raise ValueError("forecast artifact format must be json")
        document = json.loads(
            resolved.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_without_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number {value}")
            ),
        )
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != schema_version
        ):
            raise ValueError("forecast artifact schema version mismatch")

    async with get_session_factory()() as session:
        probability_rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, artifact_schema_version, metric, artifact_uri, "
                        "artifact_sha256, artifact_format, lead_min_minutes, "
                        "lead_max_minutes, magnitude_min, magnitude_max, "
                        "valid_from, valid_until "
                        "FROM hydraulic.probability_calibrations"
                    )
                )
            )
            .mappings()
            .all()
        )
        rule_rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, rule_schema_version, alert_type, risk_scale_m, "
                        "risk_scale_ratio, artifact_uri, artifact_sha256, valid_from, "
                        "valid_until FROM hydraulic.forecast_rules"
                    )
                )
            )
            .mappings()
            .all()
        )
    probabilities = {str(row["id"]): row for row in probability_rows}
    rules = {str(row["id"]): row for row in rule_rows}
    if not probability_ids <= set(probabilities) or not rule_ids <= set(rules):
        raise ValueError("forecast policy references an unknown artifact ID")
    for identity in probability_ids:
        row = probabilities[identity]
        schema_version = int(row["artifact_schema_version"])
        if (
            schema_version < 1
            or str(row["metric"]) != expected_probability_metrics[identity]
        ):
            raise ValueError("forecast probability schema or metric is invalid")
        if not 0 <= int(row["lead_min_minutes"]) <= int(row["lead_max_minutes"]) < 120:
            raise ValueError("forecast probability lead domain is invalid")
        magnitude_min = float(row["magnitude_min"])
        magnitude_max = float(row["magnitude_max"])
        if (
            not math.isfinite(magnitude_min)
            or not math.isfinite(magnitude_max)
            or magnitude_min > magnitude_max
        ):
            raise ValueError("forecast probability magnitude domain is invalid")
        valid_from = row["valid_from"]
        valid_until = row["valid_until"]
        if valid_from > current or valid_until <= current:
            raise ValueError("forecast probability artifact is not currently valid")
        validate_file(row, schema_version, row["artifact_format"])
    for identity in rule_ids:
        row = rules[identity]
        schema_version = int(row["rule_schema_version"])
        if (
            schema_version < 1
            or str(row["alert_type"]) != expected_rule_types[identity]
        ):
            raise ValueError("forecast rule schema or alert type is invalid")
        risk_scale = (
            row["risk_scale_m"]
            if row["alert_type"] == "flood_forecast"
            else row["risk_scale_ratio"]
        )
        if (
            risk_scale is None
            or not math.isfinite(float(risk_scale))
            or float(risk_scale) <= 0
        ):
            raise ValueError("forecast rule risk scale is invalid")
        valid_from = row["valid_from"]
        valid_until = row["valid_until"]
        if valid_from > current or valid_until <= current:
            raise ValueError("forecast rule artifact is not currently valid")
        validate_file(row, schema_version, None)
    return {
        "network_count": len(config["networks"]),
        "probability_artifact_count": len(probability_ids),
        "rule_artifact_count": len(rule_ids),
    }


async def _forecast_runtime_ready() -> bool:
    config = _load_forecast_policy_config()
    if config is None:
        return False
    cache = getattr(app.state, "forecast_policy_cache", None)
    now = time.monotonic()
    if isinstance(cache, dict) and now - float(cache.get("checked_at", -1)) < 5:
        return bool(cache.get("ready"))
    ready = False
    try:
        details = await _validate_forecast_artifacts(config)
        ready = True
        cache = {"checked_at": now, "ready": True, "details": details}
    except Exception as exc:
        cache = {"checked_at": now, "ready": False, "reason": type(exc).__name__}
    app.state.forecast_policy_cache = cache
    return ready


async def _check_worker_heartbeats() -> dict[str, bool]:
    probe = getattr(app.state, "worker_heartbeat_probe", None)
    if callable(probe):
        probe_result = probe()
        if hasattr(probe_result, "__await__"):
            probe_result = await probe_result
        probe_values = cast(Mapping[str, Any], probe_result)
        return {str(key): bool(value) for key, value in probe_values.items()}
    now = time.time()
    heartbeats: dict[str, bool] = {}
    for component, key in _WORKER_HEARTBEAT_KEYS.items():
        try:
            value = await redis_client.get(key)
            observed = float(value) if value is not None else 0.0
            heartbeats[component] = 0.0 < now - observed <= 75.0
        except (TypeError, ValueError):
            heartbeats[component] = False
    return heartbeats


async def check_runtime_readiness() -> dict[str, object]:
    """Fail closed on incomplete schema, extensions, artifacts, or workers."""

    schema = await check_hydraulic_schema_readiness()
    result: dict[str, object] = {
        "ready": False,
        "code": "RUNTIME_NOT_READY",
        "schema": schema,
        "checks": {},
    }
    if not schema.get("ready"):
        result["reason"] = "schema_head_or_profile_mismatch"
        return result
    checks: dict[str, Any] = {}
    try:
        async with get_db_session() as session:
            table_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_schema = 'hydraulic'"
                        )
                    )
                )
                .mappings()
                .all()
            )
            present = {str(row["table_name"]) for row in table_rows}
            missing_tables = sorted(_REQUIRED_HYDRAULIC_TABLES - present)
            checks["required_tables"] = not missing_tables
            checks["missing_tables"] = missing_tables

            extension_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT extname FROM pg_extension "
                            "WHERE extname IN ('timescaledb','postgis')"
                        )
                    )
                )
                .mappings()
                .all()
            )
            extensions = {str(row["extname"]) for row in extension_rows}
            required_extensions = {"timescaledb"}
            if settings.HYDRAULIC_SPATIAL_BACKEND == "postgis":
                required_extensions.add("postgis")
            checks["extensions"] = required_extensions <= extensions
            checks["missing_extensions"] = sorted(required_extensions - extensions)

            hypertable_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT hypertable_schema, hypertable_name "
                            "FROM timescaledb_information.hypertables"
                        )
                    )
                )
                .mappings()
                .all()
            )
            hypertables = {
                (str(row["hypertable_schema"]), str(row["hypertable_name"]))
                for row in hypertable_rows
            }
            checks["hypertables"] = {
                ("hydraulic", "simulation_frames"),
                ("hydraulic", "observation_samples"),
            } <= hypertables
    except Exception as exc:
        checks["database_error"] = type(exc).__name__
        result["checks"] = checks
        result["reason"] = "database_contract_check_failed"
        return result

    try:
        checks["worker_heartbeats"] = await _check_worker_heartbeats()
    except Exception as exc:
        checks["worker_heartbeats"] = False
        checks["worker_heartbeat_error"] = type(exc).__name__
    try:
        checks["forecast_policy"] = await _forecast_runtime_ready()
    except Exception as exc:
        checks["forecast_policy"] = False
        checks["forecast_policy_error"] = type(exc).__name__

    required = [
        checks.get("required_tables") is True,
        checks.get("extensions") is True,
        checks.get("hypertables") is True,
        checks.get("forecast_policy") is True,
        len(checks.get("worker_heartbeats") or {}) == len(_WORKER_HEARTBEAT_KEYS),
        all((checks.get("worker_heartbeats") or {}).values()),
    ]
    result["checks"] = checks
    result["ready"] = all(required)
    if not result["ready"]:
        result["reason"] = "runtime_contract_check_failed"
    return result


# ============================================================
# 应用生命周期
# ============================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Wire durable runtime dependencies and fail closed in production."""

    setup_logging()
    logger.info("=" * 70)
    logger.info("🏙️  %s v%s 启动中...", settings.APP_NAME, settings.APP_VERSION)
    logger.info("=" * 70)

    app.state.hydraulic_metrics = hydraulic_metrics
    app.state.forecast_policy_cache = None
    from app.services.sse_manager import sse_manager

    sse_manager.set_forecast_replay_store(DatabaseForecastReplayStore())
    if getattr(app.state, "forecast_outbox_source", None) is None:
        app.state.forecast_outbox_source = DatabaseForecastOutboxRelay()
    if getattr(app.state, "hydraulic_metrics_source", None) is None:
        app.state.hydraulic_metrics_source = DatabaseHydraulicMetricsSource()

    redis_ready = False
    try:
        try:
            manager = get_model_manager()
            logger.info(
                "✅ ModelManager 就绪 | 活跃模型: %s", manager.active_version or "无"
            )
        except Exception as exc:
            if settings.DEPLOYMENT_ENVIRONMENT == "production":
                raise RuntimeError(
                    "production ModelManager initialization failed"
                ) from exc
            logger.warning("⚠️  模型管理器初始化失败 (系统将以无模型模式运行): %s", exc)

        db_ok = await check_database_connection()
        if not db_ok and settings.DEPLOYMENT_ENVIRONMENT == "production":
            raise RuntimeError("production database connection is unavailable")
        if db_ok:
            logger.info("✅ 数据库已连接；schema 由 Alembic 独占管理")
        else:
            logger.warning("⚠️  数据库不可用；仅开发/测试模式继续启动")

        try:
            await redis_client.connect()
            redis_client.on(settings.REDIS_CHANNEL_ALERTS, _handle_redis_alerts)
            redis_client.on(settings.REDIS_CHANNEL_SENSOR, _handle_redis_sensor)
            redis_client.on(settings.REDIS_CHANNEL_MODEL, _handle_redis_model)
            redis_client.on_forecast(_handle_redis_forecast)
            await redis_client.start_listener()
            redis_ready = True
            logger.info("✅ Redis 已连接")
        except Exception as exc:
            if settings.DEPLOYMENT_ENVIRONMENT == "production":
                raise RuntimeError(
                    "production Redis connection is unavailable"
                ) from exc
            logger.warning("⚠️  Redis 连接失败 (实时消息推送不可用): %s", exc)

        try:
            from app.services.influxdb_service import influxdb_service

            await influxdb_service.connect()
        except Exception as exc:
            if settings.DEPLOYMENT_ENVIRONMENT == "production":
                raise RuntimeError(
                    "production InfluxDB connection is unavailable"
                ) from exc
            logger.warning("⚠️  InfluxDB 连接失败: %s", exc)

        await stream_service.start_health_monitor()

        if settings.DEPLOYMENT_ENVIRONMENT != "production":
            try:
                await mock_generator.start()
                logger.info("✅ 开发模式模拟数据生成器已启动")
            except Exception as exc:
                logger.warning("⚠️  模拟数据生成器启动失败: %s", exc)
        else:
            logger.info("✅ production 模式已禁用 mock 数据生成器")

        if db_ok:
            app.state.forecast_relay_task = asyncio.create_task(_forecast_relay_loop())
            app.state.hydraulic_metrics_task = asyncio.create_task(
                _hydraulic_metrics_loop(app.state.hydraulic_metrics_source)
            )

        logger.info("🚀 服务进程已启动; 数据就绪状态请检查 /readyz")
        logger.info("=" * 70)
        yield
    finally:
        logger.info("🛑 正在优雅关闭...")
        for state_name in ("forecast_relay_task", "hydraulic_metrics_task"):
            task = getattr(app.state, state_name, None)
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        try:
            await mock_generator.stop()
        except Exception:
            pass
        try:
            await stream_service.stop_all()
        except Exception:
            pass
        try:
            from app.services.influxdb_service import influxdb_service

            await influxdb_service.disconnect()
        except Exception:
            pass
        if redis_ready:
            await redis_client.disconnect()

        from app.core.database import dispose_engine

        await dispose_engine()
        logger.info("✅ 服务已安全关闭")


# ============================================================
# FastAPI 应用实例
# ============================================================

_HYDRAULIC_IMPORT_PATH = "/api/networks/import"
_RAW_UPLOAD_HEADER_LIMIT = 16 * 1024


def _raw_multipart_filename(header_block: bytes) -> str:
    """Extract the original file-part filename before Starlette normalises it."""

    message = Message()
    for line in header_block.splitlines():
        try:
            name, value = line.decode("latin-1").split(":", 1)
        except (UnicodeDecodeError, ValueError):
            continue
        if name.strip().lower() != "content-disposition":
            continue
        message["content-disposition"] = value.strip()
        parameters = (
            message.get_params(header="content-disposition", unquote=True) or []
        )
        if not any(key == "name" and item == "file" for key, item in parameters):
            continue
        filenames = [item for key, item in parameters if key == "filename"]
        if len(filenames) != 1 or not isinstance(filenames[0], str):
            return ""
        return filenames[0]
    return ""


class PreserveRawMultipartFilename:
    """Capture the original upload filename without consuming the ASGI body."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        headers = {
            key.lower(): value.lower()
            for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != _HYDRAULIC_IMPORT_PATH
            or b"multipart/form-data" not in headers.get(b"content-type", b"")
        ):
            await self.app(scope, receive, send)
            return

        captured = False
        prefix = bytearray()

        async def receive_with_capture() -> dict[str, Any]:
            nonlocal captured
            message = cast(dict[str, Any], await receive())
            if captured or message.get("type") != "http.request":
                return message
            body = message.get("body", b"")
            if body:
                prefix.extend(body)
                header_end = prefix.find(b"\r\n\r\n")
                if header_end >= 0:
                    scope["hydraulic_raw_upload_filename"] = _raw_multipart_filename(
                        bytes(prefix[:header_end])
                    )
                    scope["hydraulic_raw_upload_filename_captured"] = True
                    captured = True
                    prefix.clear()
                elif len(prefix) > _RAW_UPLOAD_HEADER_LIMIT:
                    scope["hydraulic_raw_upload_filename"] = ""
                    scope["hydraulic_raw_upload_filename_captured"] = True
                    captured = True
                    prefix.clear()
            return message

        await self.app(scope, receive_with_capture, send)


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="市政排水智能监测与AI安防系统 — 数字孪生城市管理平台",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


# ============================================================
# CORS 中间件
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Process-Time"],
)
app.add_middleware(PreserveRawMultipartFilename)


_rate_buckets: dict[tuple[str, str, int], int] = {}
_rate_buckets_lock = asyncio.Lock()


def _rate_limit_client(request: Request) -> str:
    """Use a bounded pre-auth principal, never an attacker-controlled token."""

    client = request.client
    host = client.host if client is not None else ""
    return host.strip()[:128] if host and host.strip() else "unknown"


async def _consume_rate_limit(
    request: Request, *, bucket: str, limit: int
) -> int | None:
    minute = int(time.time() // 60)
    client = _rate_limit_client(request)
    redis_key = (
        "hydraulic:rate-limit:"
        + hashlib.sha256(
            f"{bucket}\x00{client}\x00{minute}".encode("utf-8")
        ).hexdigest()
    )
    try:
        shared_count = await redis_client.increment_rate_limit(redis_key, 60)
    except Exception:
        if settings.DEPLOYMENT_ENVIRONMENT == "production":
            raise
        shared_count = None
    if shared_count is not None:
        return int(shared_count)

    async with _rate_buckets_lock:
        if len(_rate_buckets) >= _RATE_BUCKETS_MAX:
            stale = [key for key in _rate_buckets if key[2] < minute]
            for key in stale:
                _rate_buckets.pop(key, None)
            if len(_rate_buckets) >= _RATE_BUCKETS_MAX:
                oldest = min(_rate_buckets, key=lambda key: key[2])
                _rate_buckets.pop(oldest, None)
        key = (bucket, client, minute)
        count = _rate_buckets.get(key, 0) + 1
        _rate_buckets[key] = count
        return count


def _forecast_component_ready() -> bool:
    """Validate the closed policy object and exact canonical sidecar."""

    return _load_forecast_policy_config() is not None


# ============================================================
# 请求计时中间件
# ============================================================


@app.middleware("http")
async def add_process_time_header(request, call_next):
    """为每个请求添加处理时间头和版权标记头"""
    import time

    start_time = time.perf_counter()
    response = await call_next(request)
    process_time = time.perf_counter() - start_time
    response.headers["X-Process-Time"] = f"{process_time:.3f}s"
    response.headers["X-Powered-By"] = "Smart City Neural Endpoints"
    return response


@app.middleware("http")
async def request_id_middleware(request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def hydraulic_rate_limit(request, call_next):
    """Apply independent, fail-closed hydraulic request buckets."""

    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)
    if settings.DEPLOYMENT_ENVIRONMENT == "production" and path.startswith(
        (
            "/api/networks",
            "/api/network-jobs",
            "/api/network-imports",
            "/api/validation-runs",
            "/api/rainfall-scenarios",
            "/api/simulations",
            "/api/calibrations",
            "/api/forecast",
        )
    ):
        from app.api.hydraulic_routes import hydraulic_store

        if not getattr(hydraulic_store, "durable_backend", False):
            request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "5", "X-Request-ID": request_id},
                content={
                    "detail": "hydraulic durable repository is not configured",
                    "code": "DURABLE_STORE_UNAVAILABLE",
                    "request_id": request_id,
                    "errors": [],
                },
            )
    if settings.DEPLOYMENT_ENVIRONMENT == "production" and path.startswith(
        "/api/forecast"
    ):
        if not await _forecast_runtime_ready():
            request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "30", "X-Request-ID": request_id},
                content={
                    "detail": "forecast policy component is not ready",
                    "code": "FORECAST_POLICY_UNAVAILABLE",
                    "request_id": request_id,
                    "errors": [],
                },
            )
    if path.startswith("/api/forecast/events"):
        bucket = "sse_connect"
        limit = settings.RATE_LIMIT_SSE_CONNECT_PER_MINUTE
    elif path.startswith("/api/forecast/alerts") and request.method == "POST":
        bucket = "forecast_transition"
        limit = settings.RATE_LIMIT_FORECAST_TRANSITION_PER_MINUTE
    elif path.startswith("/api/forecast"):
        bucket = "forecast_read"
        limit = settings.RATE_LIMIT_FORECAST_READ_PER_MINUTE
    elif path.startswith("/api/simulations") and request.method == "POST":
        bucket = "simulation_submit"
        limit = settings.RATE_LIMIT_SIMULATION_SUBMIT_PER_MINUTE
    elif (
        path.startswith("/api/networks/import")
        or path.endswith("/validate")
        or path.endswith("/seed")
    ):
        bucket = "network_job_submit"
        limit = settings.RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE
    else:
        return await call_next(request)
    try:
        count = await _consume_rate_limit(request, bucket=bucket, limit=limit)
    except Exception as exc:
        logger.error("Shared hydraulic rate limiter unavailable: %s", exc)
        request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "5", "X-Request-ID": request_id},
            content={
                "detail": "shared rate limiter is unavailable",
                "code": "RATE_LIMITER_UNAVAILABLE",
                "request_id": request_id,
                "errors": [],
            },
        )
    if count is not None and count > limit:
        request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "60", "X-Request-ID": request_id},
            content={
                "detail": "rate limit exceeded",
                "code": "RATE_LIMITED",
                "request_id": request_id,
                "errors": [],
            },
        )
    return await call_next(request)


# ============================================================
# 注册路由
# ============================================================

app.include_router(api_router)


# ============================================================
# 静态文件服务 (HLS 视频片)
# ============================================================

# 挂载 HLS 媒体目录
try:
    app.mount(
        "/media/hls",
        StaticFiles(directory=str(settings.hls_output_dir)),
        name="hls_media",
    )
except Exception:
    logger.warning("HLS 媒体目录不存在: %s", settings.hls_output_dir)


# ============================================================
# 根路径
# ============================================================


@app.get("/")
async def root():
    """根路径 - 系统信息"""
    manager = get_model_manager()
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "running",
        "docs": "/docs",
        "model_ready": manager.is_ready,
        "active_model": manager.active_version,
    }


@app.get("/readyz")
async def readyz():
    """Readiness covers schema, extensions, artifacts, and worker heartbeats."""
    result = await check_runtime_readiness()
    if not result["ready"]:
        raise HTTPException(status_code=503, detail=result)
    return result


@app.get("/health")
async def health():
    """Docker 健康检查端点"""
    return {"status": "healthy"}


# ============================================================
# 全局异常处理
# ============================================================


def _request_id(request: Request) -> str:
    return (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or str(uuid.uuid4())
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """Return the frozen validation envelope with field-level diagnostics."""
    request_id = _request_id(request)
    errors = []
    for item in exc.errors():
        location = [
            str(part)
            for part in item.get("loc", ())
            if part not in {"body", "query", "path"}
        ]
        errors.append(
            {
                "field": ".".join(location) or "$",
                "message": str(item.get("msg", "request validation failed")),
                "retryable": False,
                "details": {"type": item.get("type", "validation_error")},
            }
        )
    return JSONResponse(
        status_code=422,
        headers={"X-Request-ID": request_id},
        content=jsonable_encoder(
            {
                "detail": "请求参数验证失败",
                "code": "VALIDATION_ERROR",
                "request_id": request_id,
                "errors": errors,
            }
        ),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Preserve legacy detail strings while exposing code/request_id."""
    request_id = _request_id(request)
    detail = exc.detail
    if isinstance(detail, dict) and detail.get("code"):
        content = {
            "detail": detail.get("detail", "request failed"),
            "code": detail.get("code", "HTTP_ERROR"),
            "request_id": request_id,
            "errors": detail.get("errors", []),
        }
    else:
        code_by_status = {
            400: "MALFORMED_REQUEST",
            401: "AUTH_REQUIRED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            413: "INPUT_TOO_LARGE",
            415: "UNSUPPORTED_IMPORT_FORMAT",
            422: "VALIDATION_ERROR",
            429: "RATE_LIMITED",
            503: "SERVICE_UNAVAILABLE",
        }
        content = {
            "detail": str(detail),
            "code": code_by_status.get(exc.status_code, "HTTP_ERROR"),
            "request_id": request_id,
            "errors": [],
        }
    return JSONResponse(
        status_code=exc.status_code,
        headers={**(exc.headers or {}), "X-Request-ID": request_id},
        content=jsonable_encoder(content),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常处理"""
    logger.error("未处理的异常: %s", exc, exc_info=True)
    request_id = _request_id(request)
    return JSONResponse(
        status_code=500,
        headers={"X-Request-ID": request_id},
        content={
            "detail": "服务器内部错误",
            "code": "SERVER_ERROR",
            "request_id": request_id,
            "errors": [],
        },
    )


# ============================================================
# 开发服务器入口
# ============================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
