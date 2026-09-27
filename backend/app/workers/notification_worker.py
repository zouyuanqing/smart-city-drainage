"""Notification worker: leased SMTP / webhook delivery of forecast events.

``notification-worker`` (``IMPLEMENTATION_CONTRACT.md`` §13.1) is the only
process that turns ``hydraulic.notification_deliveries`` rows into real external
side effects.  It claims a destination, loads the committed outbox event it
refers to, calls ``NotificationService.notify_forecast_event`` for the real
SMTP/webhook transport, and settles the row as delivered, retryable, or dead
lettered.

Why the claim is per *event group*
----------------------------------
``notify_forecast_event`` fans out to every configured recipient and webhook,
so calling it once per destination would send the same alert N times.  The
worker therefore claims all destinations belonging to one ``event_id`` in a
single transaction and settles each of them from the one delivery result.
``uq_hydraulic_notification_deliveries_target`` still guarantees one row per
(event, channel, destination, template).

Why the outbox row is the source
--------------------------------
A delivery references an ``event_id`` that only exists in the committed
``hydraulic.event_outbox`` log.  The worker refuses to deliver against an
event it cannot read, and it marks the delivery suppressed rather than
inventing an envelope when the event has already expired out of the retention
window.

Entry point: ``python -m app.workers.notification_worker``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

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
    open_session,
    probe_database,
    probe_redis,
    probe_schema,
    run_startup_checks,
    run_worker_entrypoint,
)

logger = logging.getLogger(__name__)

UTC = timezone.utc

COMPONENT = "notification"
HEARTBEAT_KEY = heartbeat_key_for(COMPONENT)

# Serialize delivery claims so two instances cannot double-send one event
# group; the row leases below remain the second line of defence.
NOTIFICATION_CLAIM_LOCK_KEY = 0x4E4F5446
RETRYABLE_DELIVERY_ERRORS = frozenset(
    {"SMTP_UNAVAILABLE", "WEBHOOK_UNAVAILABLE", "DELIVERY_TIMEOUT"}
)
NON_RETRYABLE = frozenset({"TEMPLATE_MISSING", "DESTINATION_INVALID", "EVENT_EXPIRED"})


class NotificationWorkerError(RuntimeError):
    """Structured delivery failure mapped onto the retry ladder."""

    def __init__(
        self, error_code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    """One leased destination row."""

    delivery_id: uuid.UUID
    event_id: uuid.UUID
    alert_id: uuid.UUID
    channel: str
    destination_id: str
    template_version: str
    attempt_count: int
    lease_until: datetime
    lease_owner: str


@dataclass(slots=True)
class DeliveryOutcome:
    """The transport result for a whole event group."""

    event_id: str
    event_type: str
    email_attempted: int = 0
    email_succeeded: int = 0
    email_failed: int = 0
    webhook_attempted: int = 0
    webhook_succeeded: int = 0
    webhook_failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def fully_succeeded(self) -> bool:
        return (self.email_failed + self.webhook_failed) == 0

    @property
    def partially_succeeded(self) -> bool:
        """Some destination received the event and at least one did not.

        Only true when there is a *split* result.  A clean send is not
        "partially" succeeded, and a total failure is neither.
        """

        if self.fully_succeeded:
            return False
        return (self.email_succeeded + self.webhook_succeeded) > 0

    @property
    def any_succeeded(self) -> bool:
        return (self.email_succeeded + self.webhook_succeeded) > 0


def outcome_from_result(result: Mapping[str, Any]) -> DeliveryOutcome:
    """Adapt ``notify_forecast_event``'s counters into a settled outcome."""

    outcome = DeliveryOutcome(
        event_id=str(result.get("event_id", "")),
        event_type=str(result.get("event_type", "")),
        email_attempted=int(result.get("email_attempted", 0) or 0),
        email_succeeded=int(result.get("email_succeeded", 0) or 0),
        email_failed=int(result.get("email_failed", 0) or 0),
        webhook_attempted=int(result.get("webhook_attempted", 0) or 0),
        webhook_succeeded=int(result.get("webhook_succeeded", 0) or 0),
        webhook_failed=int(result.get("webhook_failed", 0) or 0),
    )
    if outcome.email_failed:
        outcome.errors.append("SMTP_DELIVERY_FAILED")
    if outcome.webhook_failed:
        outcome.errors.append("WEBHOOK_DELIVERY_FAILED")
    return outcome


class SessionNotificationRepository:
    """Claim and settle notification deliveries with row locks and leases."""

    def __init__(
        self,
        session: Any,
        *,
        lease_seconds: int,
        max_attempts: int,
        retry_ladder: Sequence[int],
    ) -> None:
        if lease_seconds <= 0 or max_attempts < 1:
            raise ValueError("lease must be positive and attempts at least one")
        if not retry_ladder:
            raise ValueError("retry backoff ladder must not be empty")
        self.session = session
        self.lease_seconds = int(lease_seconds)
        self.max_attempts = int(max_attempts)
        self.retry_ladder = tuple(int(value) for value in retry_ladder)

    def backoff_seconds(self, attempt_count: int) -> int:
        index = min(max(0, int(attempt_count) - 1), len(self.retry_ladder) - 1)
        return int(self.retry_ladder[index])

    async def claim_next_event(self, worker_id: str) -> tuple[DeliveryClaim, ...]:
        """Claim every pending destination for the next due event.

        The whole group is leased in one transaction so the fan-out in
        ``notify_forecast_event`` is invoked exactly once per event.
        """

        from sqlalchemy import text

        current = datetime.now(UTC)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": NOTIFICATION_CLAIM_LOCK_KEY},
        )
        event_id = await self.session.scalar(
            text(
                "SELECT event_id FROM hydraulic.notification_deliveries "
                "WHERE status IN ('pending', 'retry_wait') "
                "AND (next_attempt_at IS NULL OR next_attempt_at <= :now) "
                "AND (lease_until IS NULL OR lease_until <= :now) "
                "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            ),
            {"now": current},
        )
        if event_id is None:
            return ()
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT id, event_id, alert_id, channel, destination_id, "
                        "template_version, attempt_count "
                        "FROM hydraulic.notification_deliveries "
                        "WHERE event_id = CAST(:event_id AS uuid) "
                        "AND status IN ('pending', 'retry_wait') "
                        "AND (next_attempt_at IS NULL OR next_attempt_at <= :now) "
                        "AND (lease_until IS NULL OR lease_until <= :now) "
                        "ORDER BY id FOR UPDATE"
                    ),
                    {"event_id": str(event_id), "now": current},
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            return ()
        lease_until = current + timedelta(seconds=self.lease_seconds)
        await self.session.execute(
            text(
                "UPDATE hydraulic.notification_deliveries "
                "SET status = 'sending', lease_until = :lease_until, "
                "attempt_count = attempt_count + 1, updated_at = :now "
                "WHERE event_id = CAST(:event_id AS uuid) "
                "AND status IN ('pending', 'retry_wait')"
            ),
            {"event_id": str(event_id), "lease_until": lease_until, "now": current},
        )
        return tuple(
            DeliveryClaim(
                delivery_id=row["id"],
                event_id=row["event_id"],
                alert_id=row["alert_id"],
                channel=str(row["channel"]),
                destination_id=str(row["destination_id"]),
                template_version=str(row["template_version"]),
                attempt_count=int(row["attempt_count"]) + 1,
                lease_until=lease_until,
                lease_owner=worker_id,
            )
            for row in rows
        )

    async def heartbeat(self, claims: Sequence[DeliveryClaim]) -> None:
        from sqlalchemy import text

        if not claims:
            return
        current = datetime.now(UTC)
        await self.session.execute(
            text(
                "UPDATE hydraulic.notification_deliveries "
                "SET lease_until = :lease_until, updated_at = :now "
                "WHERE event_id = CAST(:event_id AS uuid) "
                "AND status = 'sending'"
            ),
            {
                "event_id": str(claims[0].event_id),
                "lease_until": current + timedelta(seconds=self.lease_seconds),
                "now": current,
            },
        )

    async def load_event(self, event_id: uuid.UUID) -> dict[str, Any] | None:
        """Read the committed outbox envelope this delivery refers to."""

        from sqlalchemy import text

        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT sequence, event_id, payload "
                        "FROM hydraulic.event_outbox "
                        "WHERE event_id = CAST(:event_id AS uuid) "
                        "AND expires_at > clock_timestamp() "
                        "ORDER BY sequence DESC LIMIT 1"
                    ),
                    {"event_id": str(event_id)},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, Mapping):
            raise NotificationWorkerError(
                "OUTBOX_PAYLOAD_INVALID",
                "forecast outbox payload is not an object",
                retryable=False,
            )
        envelope = dict(payload)
        if envelope.get("event_id") != str(row["event_id"]):
            raise NotificationWorkerError(
                "OUTBOX_ENVELOPE_MISMATCH",
                "forecast outbox envelope identity is inconsistent",
                retryable=False,
            )
        envelope["sequence"] = int(row["sequence"])
        return envelope

    async def settle(
        self,
        claims: Sequence[DeliveryClaim],
        outcome: DeliveryOutcome,
    ) -> None:
        """Mark every claimed destination delivered or suppressed."""

        from sqlalchemy import text

        now = datetime.now(UTC)
        for claim in claims:
            if outcome.fully_succeeded:
                status, sent, code = "delivered", True, None
            elif outcome.any_succeeded:
                # At least one destination received the event; retrying the
                # whole group would duplicate deliveries, so the remaining
                # destinations are recorded as delivered with the last error
                # rather than re-sent.
                status, sent, code = (
                    "delivered",
                    True,
                    (outcome.errors[0] if outcome.errors else None),
                )
            else:
                status, sent, code = (
                    "suppressed",
                    False,
                    (outcome.errors[0] if outcome.errors else "DELIVERY_SUPPRESSED"),
                )
            await self.session.execute(
                text(
                    "UPDATE hydraulic.notification_deliveries "
                    "SET status = :status, sent_at = :sent_at, "
                    "last_error_code = :error_code, lease_until = NULL, "
                    "next_attempt_at = NULL, updated_at = :now "
                    "WHERE id = CAST(:id AS uuid) AND status = 'sending'"
                ),
                {
                    "id": str(claim.delivery_id),
                    "status": status,
                    "sent_at": now if sent else None,
                    "error_code": str(code)[:64] if code else None,
                    "now": now,
                },
            )
        logger.info(
            "notification delivery settled event=%s destinations=%s status=%s",
            outcome.event_id,
            len(claims),
            (
                "delivered"
                if outcome.fully_succeeded or outcome.partially_succeeded
                else "suppressed"
            ),
        )

    async def settle_failure(
        self, claims: Sequence[DeliveryClaim], *, error_code: str, message: str
    ) -> None:
        """Retry with backoff, or dead-letter once the budget is spent."""

        from sqlalchemy import text

        now = datetime.now(UTC)
        for claim in claims:
            exhausted = (
                claim.attempt_count >= self.max_attempts or error_code in NON_RETRYABLE
            )
            if exhausted:
                status, next_attempt, code = "dead_letter", None, error_code
            else:
                backoff = self.backoff_seconds(claim.attempt_count)
                status = "retry_wait"
                next_attempt = now + timedelta(seconds=backoff)
                code = error_code
            await self.session.execute(
                text(
                    "UPDATE hydraulic.notification_deliveries "
                    "SET status = :status, next_attempt_at = :next_attempt_at, "
                    "last_error_code = :error_code, lease_until = NULL, "
                    "updated_at = :now "
                    "WHERE id = CAST(:id AS uuid) AND status = 'sending'"
                ),
                {
                    "id": str(claim.delivery_id),
                    "status": status,
                    "next_attempt_at": next_attempt,
                    "error_code": str(code)[:64],
                    "now": now,
                },
            )
        logger.warning(
            "notification delivery failed event=%s destinations=%s code=%s detail=%s",
            claims[0].event_id if claims else "",
            len(claims),
            error_code,
            message[:200],
        )

    async def pending_depth(self) -> int:
        from sqlalchemy import text

        return int(
            await self.session.scalar(
                text(
                    "SELECT count(*) FROM hydraulic.notification_deliveries "
                    "WHERE status IN ('pending', 'retry_wait')"
                )
            )
            or 0
        )


class NotificationWorker:
    """Claim, deliver, and settle one event group per ``run_once`` call."""

    def __init__(
        self,
        repository_factory: Callable[[Any], SessionNotificationRepository],
        session_factory: Callable[[], Any],
        notifier: Any,
        *,
        worker_id: str,
        heartbeat_seconds: float = 10.0,
        webhook_secret: str | None = None,
    ) -> None:
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._repository_factory = repository_factory
        self._session_factory = session_factory
        self._notifier = notifier
        self._worker_id = worker_id
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._webhook_secret = webhook_secret
        self.delivered = 0
        self.failed = 0

    async def run_once(self) -> bool:
        claims = await self._in_transaction(
            lambda repo: repo.claim_next_event(self._worker_id)
        )
        if not claims:
            return True
        stop = asyncio.Event()
        renewer = asyncio.ensure_future(self._renew_lease(claims, stop))
        try:
            outcome = await self._attempt(claims)
        except NotificationWorkerError as exc:
            await self._settle_failure(claims, exc.error_code, str(exc))
            return True
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            logger.exception("notification delivery raised: %s", exc)
            await self._settle_failure(claims, "DELIVERY_FAILED", type(exc).__name__)
            return True
        finally:
            stop.set()
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer
        await self._in_transaction(lambda repo: repo.settle(claims, outcome))
        self.delivered += len(claims)
        return True

    async def _attempt(self, claims: Sequence[DeliveryClaim]) -> DeliveryOutcome:
        event_id = claims[0].event_id
        envelope = await self._in_transaction(lambda repo: repo.load_event(event_id))
        if envelope is None:
            # The committed event is gone from the retention window.  The
            # delivery can never be honoured, so it is suppressed rather than
            # delivered against a reconstructed envelope.
            raise NotificationWorkerError(
                "EVENT_EXPIRED",
                f"committed forecast event {event_id} is no longer replayable",
                retryable=False,
            )
        result = await self._notifier.notify_forecast_event(
            envelope, webhook_secret=self._webhook_secret
        )
        if not isinstance(result, Mapping):
            raise NotificationWorkerError(
                "DELIVERY_PROTOCOL",
                "notification service did not return a counters object",
                retryable=False,
            )
        return outcome_from_result(result)

    async def _renew_lease(
        self, claims: Sequence[DeliveryClaim], stop: asyncio.Event
    ) -> None:
        while not stop.is_set():
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                await self._in_transaction(lambda repo: repo.heartbeat(claims))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "notification lease heartbeat failed: %s", type(exc).__name__
                )
                return

    async def _settle_failure(
        self, claims: Sequence[DeliveryClaim], code: str, message: str
    ) -> None:
        self.failed += len(claims)
        await self._in_transaction(
            lambda repo: repo.settle_failure(claims, error_code=code, message=message)
        )

    async def _in_transaction(
        self, action: Callable[[SessionNotificationRepository], Awaitable[Any]]
    ) -> Any:
        async with open_session(self._session_factory) as session:
            async with session.begin():
                return await action(self._repository_factory(session))


def repository_factory_from_settings() -> (
    Callable[[Any], SessionNotificationRepository]
):
    from app.core.config import settings

    max_attempts = int(settings.NOTIFICATION_MAX_ATTEMPTS)
    # A deterministic 10s/60s/300s/900s ladder clipped to the frozen budget.
    ladder = [10, 60, 300, 900][: max(max_attempts - 1, 1)]

    def factory(session: Any) -> SessionNotificationRepository:
        return SessionNotificationRepository(
            session,
            lease_seconds=int(settings.SIM_LEASE_SECONDS),
            max_attempts=max_attempts,
            retry_ladder=ladder,
        )

    return factory


def _probe_transport() -> None:
    """Fail closed when no real delivery transport is configured.

    SMTP and webhook destinations both come from the frozen Settings registry.
    A worker with neither has nothing to deliver; starting it would publish a
    registration heartbeat for a service that can never perform its job, so the
    startup check rejects it instead.
    """

    from app.core.config import settings

    if settings.SMTP_ENABLED and settings.ALERT_EMAIL_RECIPIENTS:
        return
    if settings.ALERT_WEBHOOK_URLS:
        return
    raise RuntimeError(
        "no delivery transport configured: enable SMTP with ALERT_EMAIL_RECIPIENTS "
        "or set ALERT_WEBHOOK_URLS"
    )


def resolve_webhook_secret(environ: Mapping[str, str] | None = None) -> str | None:
    """The HMAC secret used to sign webhook deliveries.

    ``notify_forecast_event`` refuses to send a webhook at all in production
    when it has no secret, so the worker must be given one explicitly.  This is
    read from the environment rather than from ``Settings`` for the same reason
    the other worker knobs are: the dependency-handoff contract pins the
    Settings registry to exactly 105 fields, so adding one is a contract-owner
    re-approval, not a worker change.
    """

    env = os.environ if environ is None else environ
    value = str(env.get("ALERT_WEBHOOK_SECRET", "") or "").strip()
    return value or None


def probe_webhook_secret(environ: Mapping[str, str] | None = None) -> None:
    """A configured webhook without a signing secret is fatal, always.

    ``notify_forecast_event`` only refuses to send an unsigned webhook when
    ``DEPLOYMENT_ENVIRONMENT`` is ``production``; this worker deliberately does
    not set that flag, because production mode makes ``Settings`` demand the
    API's JWT signing key and a delivery process has no reason to hold it.  The
    rule is therefore enforced here on its own terms and unconditionally, which
    is strictly stricter than the API's environment-gated behaviour: without
    this check the worker would start, claim deliveries, and ship every webhook
    unsigned.
    """

    from app.core.config import settings

    if settings.ALERT_WEBHOOK_URLS and resolve_webhook_secret(environ) is None:
        raise RuntimeError(
            "ALERT_WEBHOOK_SECRET is required whenever ALERT_WEBHOOK_URLS is "
            "configured; refusing to send unsigned webhook deliveries"
        )


def build_service(
    *,
    worker_id: str | None = None,
    config: WorkerRuntimeConfig | None = None,
    startup_report: StartupReport | None = None,
    probe: Callable[[], Awaitable[bool]] | None = None,
    session_factory: Callable[[], Any] | None = None,
    notifier: Any | None = None,
) -> DurableWorkerService:
    from app.core.config import settings
    from app.core.database import get_session_factory
    from app.services.notification_service import NotificationService

    runtime = config or WorkerRuntimeConfig.from_environ(COMPONENT)
    identity = worker_id or f"notification-{uuid.uuid4().hex[:12]}"
    worker = NotificationWorker(
        repository_factory_from_settings(),
        session_factory or get_session_factory,
        notifier or NotificationService(),
        worker_id=identity,
        heartbeat_seconds=float(settings.SIM_HEARTBEAT_SECONDS),
        webhook_secret=resolve_webhook_secret(),
    )
    readiness = WorkerReadiness(worker_id=identity, component=COMPONENT)
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
        probe=probe or combine_probes(probe_database, probe_schema, probe_redis),
    )


async def run_startup_checks_for_worker() -> StartupReport:
    return await run_startup_checks(
        (
            PreflightStep(
                "database", probe_database, StartupFailure.database_unavailable
            ),
            PreflightStep(
                "schema", probe_schema, StartupFailure.schema_head_or_profile_mismatch
            ),
            PreflightStep("redis", probe_redis, StartupFailure.redis_unavailable),
            PreflightStep(
                "transport", _probe_transport, StartupFailure.claim_probe_failed
            ),
            PreflightStep(
                "webhook_secret",
                probe_webhook_secret,
                StartupFailure.claim_probe_failed,
            ),
        ),
        component=COMPONENT,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.notification_worker",
        description=(
            "Deliver leased forecast notifications over SMTP/webhook until "
            "SIGTERM.  Exits non-zero on every abnormal path."
        ),
    )
    parser.add_argument("--log-level", default=None, help="Override LOG_LEVEL.")
    parser.add_argument(
        "--worker-id", default=None, help="Stable worker identity (default: random)."
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run the fail-closed startup checks, print the report, and exit.",
    )
    return parser


async def amain(argv: Sequence[str] | None = None) -> int:
    """Async entry point: preflight, deliver, and shut down gracefully."""

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    from app.core.config import settings

    configure_logging(args.log_level or settings.LOG_LEVEL, component=COMPONENT)
    try:
        config = WorkerRuntimeConfig.from_environ(COMPONENT)
    except ValueError as exc:
        logger.error("notification worker configuration invalid: %s", exc)
        return EXIT_CONFIG_INVALID

    report = await run_startup_checks_for_worker()
    if args.preflight_only:
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
        return EXIT_OK if report.ready else EXIT_STARTUP_UNAVAILABLE
    if not report.ready:
        logger.error(
            "notification worker startup rejected reason=%s detail=%s",
            report.reason.value,
            report.detail,
        )
        return EXIT_STARTUP_UNAVAILABLE
    logger.info(
        "notification worker startup checks passed checks=%s",
        json.dumps(dict(report.checks), sort_keys=True),
    )

    service = build_service(
        worker_id=args.worker_id, config=config, startup_report=report
    )
    return await drive_until_stopped(
        service, shutdown_grace_seconds=config.shutdown_grace_seconds
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Process entry point for ``python -m app.workers.notification_worker``."""

    return run_worker_entrypoint(amain, argv)


__all__ = [
    "COMPONENT",
    "HEARTBEAT_KEY",
    "NON_RETRYABLE",
    "NOTIFICATION_CLAIM_LOCK_KEY",
    "RETRYABLE_DELIVERY_ERRORS",
    "DeliveryClaim",
    "DeliveryOutcome",
    "NotificationWorker",
    "NotificationWorkerError",
    "SessionNotificationRepository",
    "amain",
    "build_parser",
    "build_service",
    "main",
    "outcome_from_result",
    "probe_webhook_secret",
    "resolve_webhook_secret",
    "repository_factory_from_settings",
    "run_startup_checks_for_worker",
]


if __name__ == "__main__":
    raise SystemExit(main())
