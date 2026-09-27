"""Outbox relay worker: the single logical publisher of forecast SSE events.

``outbox-relay`` (``IMPLEMENTATION_CONTRACT.md`` §13.1) owns exactly one job:
take committed rows of ``hydraulic.event_outbox`` and publish them to the
``scn:forecast`` Redis channel so the API can fan them out to SSE clients.

The relay is *not* a heartbeat generator.  It publishes the durable event log
and nothing else.  Its registration key ``hydraulic:worker:heartbeat:outbox-relay``
is written by this process only, and only after a real Redis ``SET``.

Concurrency follows the contract: the advisory lock in
``DatabaseForecastOutboxRelay.relay_once`` serializes relays, and
``FOR UPDATE SKIP LOCKED`` means a row is published exactly once even if two
relay instances are running.  A connection failure raises *before* the
acknowledgement, so the row stays pending and is retried; a normal
zero-subscriber publish is acknowledged rather than replayed forever.

Entry point: ``python -m app.workers.outbox_relay``.
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from app.workers.durable_runtime import (
    EXIT_CONFIG_INVALID,
    EXIT_OK,
    EXIT_STARTUP_UNAVAILABLE,
    DurableWorkerService,
    HealthServer,
    PreflightStep,
    RedisHeartbeatPublisher,
    StartupFailure,
    StartupReport,
    WorkerReadiness,
    WorkerRuntimeConfig,
    combine_probes,
    configure_logging,
    drive_until_stopped,
    heartbeat_key_for,
    probe_database,
    probe_redis,
    probe_schema,
    run_startup_checks,
    run_worker_entrypoint,
)

logger = logging.getLogger(__name__)

COMPONENT = "outbox-relay"
HEARTBEAT_KEY = heartbeat_key_for(COMPONENT)


class OutboxRelay(Protocol):
    """The fenced publish/acknowledge unit owned by the API module."""

    async def relay_once(self, publish: Callable[[Mapping[str, Any]], Any]) -> int: ...

    async def pending_count(self) -> int: ...


class OutboxRelayWorker:
    """Publish committed outbox rows until SIGTERM.

    ``run_once`` returns True when the relay round-trip completed — including
    the common "nothing pending" case, which still proves the advisory lock and
    the pending SELECT both executed.  That is the claim evidence readiness
    needs; a raise is the failure path and marks the worker unready.
    """

    def __init__(
        self,
        relay: OutboxRelay,
        publisher: Callable[[Mapping[str, Any]], Awaitable[int]],
        *,
        batch_log_limit: int = 1,
    ) -> None:
        if batch_log_limit < 0:
            raise ValueError("batch log limit must not be negative")
        self._relay = relay
        self._publisher = publisher
        self._batch_log_limit = batch_log_limit
        self.published = 0

    async def run_once(self) -> bool:
        try:
            count = int(await self._relay.relay_once(self._publisher))
        except Exception as exc:
            logger.warning("outbox relay round-trip failed: %s", type(exc).__name__)
            raise
        self.published += count
        if count and self._batch_log_limit:
            logger.info("outbox relay published events=%s", count)
        return True

    async def pending_depth(self) -> int:
        return int(await self._relay.pending_count())


def _database_redis_probe() -> Callable[[], Awaitable[bool]]:
    """Readiness evidence: PostgreSQL *and* Redis, both really reachable.

    The relay cannot do its job with either missing, so a failure of either one
    keeps ``/readyz`` at 503 rather than letting a publisher-less relay report
    ready.
    """

    return combine_probes(probe_database, probe_redis)


def build_service(
    *,
    worker_id: str | None = None,
    relay: OutboxRelay | None = None,
    config: WorkerRuntimeConfig | None = None,
    publisher: Callable[[Mapping[str, Any]], Awaitable[int]] | None = None,
    probe: Callable[[], Awaitable[bool]] | None = None,
    startup_report: StartupReport | None = None,
) -> DurableWorkerService:
    """Assemble the real relay service from config, Redis, and the database."""

    runtime = config or WorkerRuntimeConfig.from_environ(COMPONENT)
    relay_impl = relay if relay is not None else _default_relay()
    publish = publisher if publisher is not None else _default_publisher()
    worker = OutboxRelayWorker(relay_impl, publish)
    readiness = WorkerReadiness(
        worker_id=worker_id or f"outbox-relay-{uuid.uuid4().hex[:12]}",
        component=COMPONENT,
    )
    if startup_report is not None:
        readiness.record_startup(startup_report)
    server = HealthServer(
        readiness,
        host=runtime.health_host,
        port=runtime.health_port,
        heartbeat_max_age=runtime.heartbeat_max_age,
        component=COMPONENT,
    )
    return DurableWorkerService(
        worker,
        readiness,
        component=COMPONENT,
        health_server=server,
        heartbeat=RedisHeartbeatPublisher(COMPONENT),
        config=runtime,
        probe=probe or _database_redis_probe(),
    )


def _default_relay() -> OutboxRelay:
    # The fenced publish/acknowledge transaction lives with the API module that
    # owns the outbox contract; importing it keeps exactly one implementation of
    # the advisory-lock + SKIP-LOCKED relay rather than a second copy here.
    from app.main import DatabaseForecastOutboxRelay

    return DatabaseForecastOutboxRelay()


def _default_publisher() -> Callable[[Mapping[str, Any]], Awaitable[int]]:
    async def publish(envelope: Mapping[str, Any]) -> int:
        from app.core.redis_client import redis_client

        return await redis_client.publish_forecast_event(envelope)

    return publish


async def _run_preflight() -> StartupReport:
    return await run_startup_checks(
        (
            PreflightStep(
                "database", probe_database, StartupFailure.database_unavailable
            ),
            PreflightStep(
                "schema", probe_schema, StartupFailure.schema_head_or_profile_mismatch
            ),
            PreflightStep("redis", probe_redis, StartupFailure.redis_unavailable),
        ),
        component=COMPONENT,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.outbox_relay",
        description=(
            "Publish committed forecast outbox rows to Redis until SIGTERM. "
            "Exits non-zero on every abnormal path."
        ),
    )
    parser.add_argument("--log-level", default=None, help="Override LOG_LEVEL.")
    parser.add_argument(
        "--worker-id", default=None, help="Stable worker identity (default: random)."
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "Run the fail-closed startup checks, print the report, and exit "
            "without publishing anything."
        ),
    )
    return parser


def _dump(report: StartupReport) -> None:
    print(
        json.dumps(
            {
                "ready": report.ready,
                "reason": report.reason.value,
                "detail": report.detail,
                "checks": dict(report.checks),
            },
            sort_keys=True,
        )
    )


async def amain(argv: Sequence[str] | None = None) -> int:
    """Async entry point: preflight, relay, and shut down gracefully."""

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    from app.core.config import settings

    configure_logging(args.log_level or settings.LOG_LEVEL, component=COMPONENT)
    try:
        config = WorkerRuntimeConfig.from_environ(COMPONENT)
    except ValueError as exc:
        logger.error("outbox relay configuration invalid: %s", exc)
        return EXIT_CONFIG_INVALID

    report = await _run_preflight()
    if args.preflight_only:
        _dump(report)
        return EXIT_OK if report.ready else EXIT_STARTUP_UNAVAILABLE
    if not report.ready:
        # Fail closed: a relay that cannot reach PostgreSQL and Redis would
        # silently stop delivering every forecast event.
        logger.error(
            "outbox relay startup rejected reason=%s detail=%s",
            report.reason.value,
            report.detail,
        )
        return EXIT_STARTUP_UNAVAILABLE
    logger.info(
        "outbox relay startup checks passed checks=%s",
        json.dumps(dict(report.checks), sort_keys=True),
    )

    service = build_service(
        worker_id=args.worker_id,
        config=config,
        startup_report=report,
    )
    return await drive_until_stopped(
        service, shutdown_grace_seconds=config.shutdown_grace_seconds
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Process entry point for ``python -m app.workers.outbox_relay``."""

    return run_worker_entrypoint(amain, argv)


__all__ = [
    "COMPONENT",
    "EXIT_OK",
    "HEARTBEAT_KEY",
    "OutboxRelay",
    "OutboxRelayWorker",
    "amain",
    "build_parser",
    "build_service",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
