from __future__ import annotations

import asyncio
from datetime import datetime, timezone


async def _check_postgresql() -> dict:
    try:
        from app.core.database import check_database_connection

        connected = await check_database_connection()
        if connected:
            return {"status": "connected", "detail": ""}
        return {"status": "error", "detail": "connection check returned False"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


async def _check_influxdb() -> dict:
    try:
        from app.services.influxdb_service import influxdb_client

        if influxdb_client is None:
            return {"status": "not_configured", "detail": "influxdb_client is None"}
        ping = influxdb_client.ping()
        if ping:
            return {"status": "connected", "detail": ""}
        return {"status": "error", "detail": "ping returned False"}
    except ImportError:
        return {
            "status": "not_configured",
            "detail": "influxdb_service module not found",
        }
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


async def _check_redis() -> dict:
    try:
        from app.core.redis_client import redis_client

        if redis_client.is_connected:
            return {"status": "connected", "detail": ""}
        return {"status": "error", "detail": "redis client not connected"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


async def _check_model() -> dict:
    try:
        from app.core.config import settings
        from app.core.model_manager import get_model_manager

        manager = get_model_manager()
        return {
            "status": "ready" if manager.is_ready else "not_ready",
            "active_version": manager.active_version,
            "device": settings.MODEL_INFERENCE_DEVICE,
        }
    except Exception as exc:
        return {
            "status": "not_ready",
            "active_version": "",
            "device": "",
            "detail": str(exc),
        }


async def get_system_status() -> dict:
    pg, influx, redis, model = await asyncio.gather(
        _check_postgresql(),
        _check_influxdb(),
        _check_redis(),
        _check_model(),
    )
    return {
        "postgresql": pg,
        "influxdb": influx,
        "redis": redis,
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
