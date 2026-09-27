"""Shared process runtime for the durable hydraulic Compose workers.

The target service topology (``IMPLEMENTATION_CONTRACT.md`` §13.1) splits the
API process into four more long-running services beside the existing
``simulation-worker``::

    network-job-worker  (DB claim; import/validate/seed; lease/fencing)
    nowcast-worker      (DB claim; reserved slot + scheduler singleton)
    outbox-relay        (single logical publisher)
    notification-worker (SMTP/webhook delivery)

All four are ordinary OS processes, and all four need the same process
boundary: fail-closed preflight, a real claim/heartbeat loop, ``/healthz`` and
``/readyz`` on a dependency-free HTTP responder, and SIGTERM/SIGINT shutdown
inside the Compose grace period.  That boundary lives here so the four worker
modules stay business-only.

Two properties this module deliberately does *not* relax:

* **Readiness is evidence, not intent.**  ``/readyz`` is 200 only after a
  successful database round-trip, a successful claim/relay round-trip, and a
  heartbeat published to Redis.  Nothing here ever returns a canned 200.
* **The heartbeat key belongs to one process.**  ``/readyz`` on the API reads
  ``hydraulic:worker:heartbeat:<component>`` and requires the value to be a
  recent ``time.time()`` (``app.main._check_worker_heartbeats``).  A worker
  publishes its own key and deletes it on shutdown; it never writes another
  component's key, and it never writes a key it has not proven it can claim
  from.  Because these workers *are* the producers of their keys, publication
  failure is a readiness failure, not a warning.

Process-level knobs are read straight from the environment rather than from
``app.core.config.Settings``: the frozen dependency-handoff contract
(``scripts/check_dependency_handoff.py``) pins the Settings registry to
exactly 105 named fields matching ``.env.example``, so adding fields there is
a contract-owner re-approval, not a worker change.  The Compose service passes
these values as environment variables, which is where they are read.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

UTC = timezone.utc

# The Redis key namespace the API /readyz reads
# (backend/app/main.py ``_WORKER_HEARTBEAT_KEYS``).
HEARTBEAT_KEY_PREFIX = "hydraulic:worker:heartbeat:"
# API freshness window is ``0 < now - observed <= 75.0``; keep every published
# TTL strictly inside it so a paused worker cannot leave a permanently fresh
# key behind and keep the API ready after the worker is gone.
HEARTBEAT_TTL_SECONDS = 30

EXIT_OK = 0
EXIT_CONFIG_INVALID = 2
EXIT_STARTUP_UNAVAILABLE = 3
EXIT_RUNTIME_FAILED = 4
EXIT_INTERRUPTED = 130

#: Environment-variable prefix for the per-worker process knobs.  A module
#: constant rather than a dataclass field, because a field default is read back
#: off the class as a slot descriptor, not as the value.
WORKER_ENV_PREFIX = "SCN_WORKER"


def heartbeat_key_for(component: str) -> str:
    """The Redis key the API ``/readyz`` reads for ``component``."""

    name = str(component).strip()
    if not name or any(character.isspace() for character in name):
        raise ValueError("worker component must be a non-blank token")
    return f"{HEARTBEAT_KEY_PREFIX}{name}"


class StartupFailure(str, Enum):
    """Distinct fail-closed reasons surfaced to logs and to ``/readyz``."""

    database_unavailable = "database_unavailable"
    schema_head_or_profile_mismatch = "schema_head_or_profile_mismatch"
    redis_unavailable = "redis_unavailable"
    staging_root_not_writable = "staging_root_not_writable"
    policy_config_invalid = "policy_config_invalid"
    claim_probe_failed = "claim_probe_failed"
    relay_probe_failed = "relay_probe_failed"
    worker_heartbeat_stale = "worker_heartbeat_stale"
    worker_stopping = "worker_stopping"
    ready = "ready"


class StopSource(str, Enum):
    """Who asked the worker to stop; all of them exit through the same path."""

    signal_term = "signal_term"
    signal_int = "signal_int"
    lifecycle = "lifecycle"


@dataclass(frozen=True, slots=True)
class StartupReport:
    """Outcome of the fail-closed preflight, safe to log verbatim."""

    ready: bool
    reason: StartupFailure
    detail: str = ""
    checks: Mapping[str, bool] = field(default_factory=dict)


def env_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = str(environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def env_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = str(environ.get(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def env_flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(environ.get(name, "") or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True, slots=True)
class WorkerRuntimeConfig:
    """Per-process knobs read from the worker's own environment."""

    component: str
    health_host: str = "0.0.0.0"
    health_port: int = 8020
    shutdown_grace_seconds: float = 25.0
    idle_seconds: float = 1.0
    probe_seconds: float = 5.0
    redis_url_configured: bool = True

    def __post_init__(self) -> None:
        heartbeat_key_for(self.component)

    @classmethod
    def from_environ(
        cls,
        component: str,
        environ: Mapping[str, str] | None = None,
    ) -> "WorkerRuntimeConfig":
        env = os.environ if environ is None else environ
        base = f"{WORKER_ENV_PREFIX}_{component.upper().replace('-', '_')}"
        return cls(
            component=component,
            health_host=env.get(f"{base}_HEALTH_HOST", "").strip() or "0.0.0.0",
            health_port=env_int(
                env,
                f"{base}_HEALTH_PORT",
                8020,
                minimum=1,
                maximum=65535,
            ),
            # Must stay below the Compose stop_grace_period (30s) so the
            # container is not SIGKILLed mid-drain.
            shutdown_grace_seconds=env_float(
                env,
                f"{base}_SHUTDOWN_GRACE_SECONDS",
                25.0,
                minimum=0.1,
                maximum=600.0,
            ),
            idle_seconds=env_float(
                env,
                f"{base}_IDLE_SECONDS",
                1.0,
                minimum=0.001,
                maximum=60.0,
            ),
            probe_seconds=env_float(
                env,
                f"{base}_PROBE_SECONDS",
                5.0,
                minimum=0.001,
                maximum=60.0,
            ),
        )

    @property
    def heartbeat_key(self) -> str:
        return heartbeat_key_for(self.component)

    @property
    def heartbeat_max_age(self) -> float:
        """A heartbeat older than this makes ``/readyz`` fail closed."""

        return max(2 * self.probe_seconds, 1.0)


class WorkerReadiness:
    """Mutable readiness snapshot shared by the loops and the health server.

    Single event loop, single writer per field: plain attribute updates are
    atomic enough here and keep the health responder allocation-free.
    """

    def __init__(self, *, worker_id: str, component: str) -> None:
        self.worker_id = worker_id
        self.component = component
        self.started_at = datetime.now(UTC)
        self._startup_ok = False
        self._reason: StartupFailure = StartupFailure.claim_probe_failed
        self._detail = "startup checks have not completed"
        self._claim_probe_ok = False
        self._heartbeat_published = False
        self._last_heartbeat_monotonic: float | None = None
        self._stopping = False
        self._units_completed = 0
        self._units_failed = 0
        self._claims_observed = 0

    # -- startup -------------------------------------------------------
    def record_startup(self, report: StartupReport) -> None:
        self._startup_ok = report.ready
        self._reason = report.reason
        self._detail = report.detail

    # -- claim/heartbeat evidence --------------------------------------
    def record_claim(self, *, claimed: bool) -> None:
        self._claim_probe_ok = True
        if claimed:
            self._claims_observed += 1

    def record_heartbeat(self, *, monotonic_now: float) -> None:
        self._last_heartbeat_monotonic = monotonic_now

    def record_heartbeat_published(self, *, published: bool) -> None:
        self._heartbeat_published = published

    def record_unit_result(self, *, failed: bool) -> None:
        if failed:
            self._units_failed += 1
        else:
            self._units_completed += 1

    def record_failure(self, reason: StartupFailure, detail: str) -> None:
        self._claim_probe_ok = False
        self._reason = reason
        self._detail = detail

    # -- lifecycle -----------------------------------------------------
    def begin_shutdown(self) -> None:
        self._stopping = True
        self._reason = StartupFailure.worker_stopping
        self._detail = "worker is draining and will exit"

    @property
    def stopping(self) -> bool:
        return self._stopping

    def evaluate(
        self,
        *,
        monotonic_now: float,
        heartbeat_max_age: float,
    ) -> tuple[bool, StartupFailure, str]:
        if self._stopping:
            return False, StartupFailure.worker_stopping, self._detail
        if not self._startup_ok:
            return False, self._reason, self._detail
        if not self._claim_probe_ok:
            return (
                False,
                StartupFailure.claim_probe_failed,
                "no successful claim round-trip has completed yet",
            )
        if not self._heartbeat_published:
            return (
                False,
                StartupFailure.redis_unavailable,
                "worker registration heartbeat has not been published",
            )
        last = self._last_heartbeat_monotonic
        if last is None or monotonic_now - last > heartbeat_max_age:
            return (
                False,
                StartupFailure.worker_heartbeat_stale,
                "worker heartbeat has not been refreshed",
            )
        return True, StartupFailure.ready, ""

    def snapshot(
        self,
        *,
        monotonic_now: float,
        heartbeat_max_age: float,
    ) -> dict[str, Any]:
        ready, reason, detail = self.evaluate(
            monotonic_now=monotonic_now,
            heartbeat_max_age=heartbeat_max_age,
        )
        return {
            "ready": ready,
            "reason": reason.value,
            "detail": detail,
            "component": self.component,
            "worker_id": self.worker_id,
            "started_at": self.started_at.isoformat(),
            "claims_observed": self._claims_observed,
            "units_completed": self._units_completed,
            "units_failed": self._units_failed,
            "heartbeat_age_seconds": (
                None
                if self._last_heartbeat_monotonic is None
                else round(monotonic_now - self._last_heartbeat_monotonic, 3)
            ),
        }


class HealthServer:
    """Dependency-free HTTP/1.1 responder for ``/healthz`` and ``/readyz``.

    ``asyncio.start_server`` keeps a worker a plain process: no ASGI app, no
    extra wheel, and readiness stays a pure function of
    :class:`WorkerReadiness`.
    """

    def __init__(
        self,
        readiness: WorkerReadiness,
        *,
        host: str,
        port: int,
        heartbeat_max_age: float,
        component: str,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._readiness = readiness
        self._host = host
        self._port = port
        self._heartbeat_max_age = heartbeat_max_age
        self._component = component
        self._monotonic = monotonic
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        """Actual bound port (differs from the request when port 0 was used)."""

        if self._server is None or not self._server.sockets:
            return self._port
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle,
            host=self._host,
            port=self._port,
        )
        logger.info(
            "%s worker health endpoint listening host=%s port=%s",
            self._component,
            self._host,
            self.port,
        )

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        with contextlib.suppress(Exception):
            await server.wait_closed()

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not request_line:
                return
            parts = request_line.decode("latin-1").split()
            method = parts[0] if parts else ""
            target = parts[1] if len(parts) > 1 else ""
            path = target.split("?", 1)[0]
            # Drain headers so the peer sees a clean response.
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line in (b"\r\n", b"\n", b""):
                    break
            await self._respond(writer, method=method, path=path)
        except (asyncio.TimeoutError, ConnectionError, ValueError):
            logger.debug("%s worker health request aborted", self._component)
        finally:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    async def _respond(
        self,
        writer: asyncio.StreamWriter,
        *,
        method: str,
        path: str,
    ) -> None:
        if path not in ("/healthz", "/readyz"):
            await self._write(writer, 404, {"status": "not_found", "path": path})
            return
        if method not in ("GET", "HEAD"):
            await self._write(
                writer,
                405,
                {"status": "method_not_allowed", "path": path},
            )
            return
        if path == "/healthz":
            # Liveness: the event loop answered, which is the whole contract.
            # It must not require queued work.
            payload: dict[str, Any] = {
                "status": "ok",
                "component": self._readiness.component,
                "worker_id": self._readiness.worker_id,
            }
            await self._write(writer, 200, payload)
            return
        snapshot = self._readiness.snapshot(
            monotonic_now=self._monotonic(),
            heartbeat_max_age=self._heartbeat_max_age,
        )
        status = 200 if snapshot["ready"] else 503
        await self._write(
            writer,
            status,
            {"status": "ok" if status == 200 else "unready", **snapshot},
        )

    async def _write(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        payload: Mapping[str, Any],
    ) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed"}.get(
            status,
            "Service Unavailable",
        )
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n"
            "\r\n"
        )
        writer.write(head.encode("latin-1") + body.encode("utf-8"))
        with contextlib.suppress(ConnectionError):
            await writer.drain()


class RedisHeartbeatPublisher:
    """Publish this worker's own ``hydraulic:worker:heartbeat:<component>`` key.

    Unlike the simulation worker's best-effort registration hook, publication is
    mandatory here: these four services exist *to* produce their key, and the
    API's ``/readyz`` treats a missing or stale key as not ready.  A publish
    failure therefore marks the worker unready rather than being swallowed, and
    the key is only ever written after a real ``SET`` on the shared
    :mod:`app.core.redis_client` connection.
    """

    def __init__(
        self,
        component: str,
        *,
        key: str | None = None,
        ttl_seconds: int = HEARTBEAT_TTL_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.component = component
        self._key = key or heartbeat_key_for(component)
        self._ttl = ttl_seconds
        self._now = now
        self.published = 0
        self.last_error = ""

    @property
    def key(self) -> str:
        return self._key

    async def publish_once(self) -> bool:
        try:
            from app.core.redis_client import redis_client

            if not redis_client.is_connected:
                await redis_client.connect()
            await redis_client.set(self._key, repr(self._now()), ttl=self._ttl)
        except Exception as exc:
            # Driver messages can embed the DSN, so only the type is retained.
            self.last_error = type(exc).__name__
            return False
        self.last_error = ""
        self.published += 1
        return True

    async def close(self) -> None:
        try:
            from app.core.redis_client import redis_client

            await redis_client.delete(self._key)
        except Exception:  # pragma: no cover - best effort teardown
            logger.debug("%s worker heartbeat key cleanup skipped", self.component)


class WorkerLoop(Protocol):
    """One unit of durable business work."""

    async def run_once(self) -> bool: ...


class DurableWorkerService:
    """Wire the claim loop, readiness probe, health server, and shutdown.

    Every collaborator is injectable so the lifecycle is exercised by tests
    without PostgreSQL, Redis, SMTP, or a webhook endpoint.
    """

    def __init__(
        self,
        worker: WorkerLoop,
        readiness: WorkerReadiness,
        *,
        component: str,
        health_server: HealthServer | None = None,
        heartbeat: RedisHeartbeatPublisher | None = None,
        config: WorkerRuntimeConfig | None = None,
        probe: Callable[[], Awaitable[bool]] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.worker = worker
        self.readiness = readiness
        self.component = component
        self._health_server = health_server
        self._heartbeat = heartbeat
        self._config = config
        self._probe = probe
        self._monotonic = monotonic
        self._stop_event: asyncio.Event | None = None
        self._stop_source: StopSource | None = None

    @property
    def stop_source(self) -> StopSource | None:
        return self._stop_source

    def request_stop(self, source: StopSource) -> None:
        """Ask for a graceful stop; the first source wins."""

        if self._stop_source is not None:
            return
        self._stop_source = source
        self.readiness.begin_shutdown()
        logger.info("%s worker stop requested source=%s", self.component, source.value)
        if self._stop_event is not None:
            self._stop_event.set()

    async def wait_for_stop(self) -> None:
        """Resolve as soon as a stop has been requested."""

        while self._stop_source is None:
            await asyncio.sleep(0.05)

    async def run(self) -> int:
        """Run until a stop is requested; returns the process exit code."""

        self._stop_event = asyncio.Event()
        if self._health_server is not None:
            await self._health_server.start()
        # Liveness and registration are mandatory, so the probe task must not
        # be gated on the business loop succeeding.
        probe_task = asyncio.create_task(self._probe_loop())
        logger.info(
            "%s worker started worker_id=%s", self.component, self.readiness.worker_id
        )
        exit_code = EXIT_OK
        try:
            try:
                await self._run_claim_loop()
            except asyncio.CancelledError:
                self.request_stop(StopSource.lifecycle)
                raise
            except Exception as exc:
                logger.exception("%s worker loop failed: %s", self.component, exc)
                self.readiness.record_failure(
                    StartupFailure.claim_probe_failed,
                    f"work loop failed: {type(exc).__name__}",
                )
                exit_code = EXIT_RUNTIME_FAILED
        finally:
            probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await probe_task
            if self._health_server is not None:
                await self._health_server.stop()
            if self._heartbeat is not None:
                await self._heartbeat.close()
        source = self._stop_source
        logger.info(
            "%s worker stopped worker_id=%s source=%s exit=%s",
            self.component,
            self.readiness.worker_id,
            source.value if source is not None else "unsignalled",
            exit_code,
        )
        return exit_code

    async def _run_claim_loop(self) -> None:
        """Drain queued work with a real claim round-trip each pass."""

        stop = self._stop_event
        assert stop is not None
        idle = self._config.idle_seconds if self._config is not None else 1.0
        while not stop.is_set():
            claimed = await self.worker.run_once()
            self.readiness.record_claim(claimed=claimed)
            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=idle)
            except TimeoutError:
                pass

    async def _probe_loop(self) -> None:
        """Refresh readiness evidence and republish the registration key."""

        probe = self._probe
        probe_seconds = self._config.probe_seconds if self._config is not None else 5.0
        while True:
            ok = False
            if probe is not None:
                try:
                    ok = bool(await probe())
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    ok = False
                    logger.warning(
                        "%s worker readiness probe failed: %s",
                        self.component,
                        type(exc).__name__,
                    )
            if ok:
                self.readiness.record_heartbeat(monotonic_now=self._monotonic())
            if self._heartbeat is not None:
                published = await self._heartbeat.publish_once()
                self.readiness.record_heartbeat_published(published=published)
            await asyncio.sleep(probe_seconds)


async def probe_database() -> bool:
    """A real ``SELECT 1`` against the configured PostgreSQL DSN."""

    from sqlalchemy import text

    from app.core.database import get_engine

    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))
    return True


async def probe_schema() -> bool:
    """A real Alembic head + spatial profile check."""

    from app.core.database import check_hydraulic_schema_readiness

    report = await check_hydraulic_schema_readiness()
    if not report.get("ready"):
        raise RuntimeError("alembic head or spatial profile does not match")
    return True


async def probe_redis() -> bool:
    """A real ``PING`` against the shared Redis connection."""

    from app.core.redis_client import redis_client

    if not redis_client.is_connected:
        await redis_client.connect()
    if redis_client._redis is None:  # pragma: no cover - defensive
        raise RuntimeError("redis client has no connection")
    await redis_client._redis.ping()
    return True


def combine_probes(
    *probes: Callable[[], Awaitable[bool]]
) -> Callable[[], Awaitable[bool]]:
    """Run every probe and require all of them, surfacing the first failure."""

    async def combined() -> bool:
        for probe in probes:
            if not await probe():
                return False
        return True

    return combined


@dataclass(frozen=True, slots=True)
class PreflightStep:
    name: str
    action: Callable[[], Any]
    failure: StartupFailure


def open_session(source: Any) -> Any:
    """Return an async context manager yielding one ``AsyncSession``.

    ``app.core.database.get_session_factory()`` is a *function* that returns an
    ``async_sessionmaker``, and the maker is itself callable.  A worker can
    therefore be handed either the function or the maker, and calling the
    wrong one of them twice yields a TypeError on a long path that is easy to
    miss.  Normalising here keeps every worker's transaction helper honest:

    * ``source()`` -> ``async_sessionmaker``  -> call it for the session
    * ``source`` is an ``async_sessionmaker`` -> call it for the session
    """

    produced = source() if not isinstance(source, async_sessionmaker) else source
    if not isinstance(produced, async_sessionmaker):
        # A factory that returned something else callable still yields a session.
        return produced()
    return produced()


async def run_startup_checks(
    steps: Sequence[PreflightStep],
    *,
    component: str,
) -> StartupReport:
    """Run every fail-closed preflight and report the first blocking reason."""

    checks: dict[str, bool] = {step.name: False for step in steps}
    for step in steps:
        try:
            result = step.action()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            # Driver messages can embed the DSN, so only the exception type
            # reaches the report and the log.
            reason = type(exc).__name__
            logger.error(
                "%s worker startup check failed check=%s reason=%s",
                component,
                step.name,
                reason,
            )
            return StartupReport(False, step.failure, reason, checks)
        checks[step.name] = True
    return StartupReport(True, StartupFailure.ready, "", checks)


def install_signal_handlers(
    service: DurableWorkerService,
) -> Callable[[], None]:
    """Wire SIGTERM/SIGINT to the graceful stop path, POSIX and Windows."""

    loop = asyncio.get_running_loop()
    installed: list[tuple[int, Any]] = []
    mapping = {
        getattr(signal, "SIGTERM", None): StopSource.signal_term,
        getattr(signal, "SIGINT", None): StopSource.signal_int,
    }
    for signum, source in mapping.items():
        if signum is None:  # pragma: no cover - platform dependent
            continue
        try:
            loop.add_signal_handler(signum, service.request_stop, source)
        except (NotImplementedError, RuntimeError, ValueError, OSError):
            # Windows has no add_signal_handler; fall back to the C-level
            # handler, which is delivered on the main thread while the loop
            # runs and therefore still has to hop back onto the loop.
            previous = signal.getsignal(signum)

            def _handler(received: int, frame: Any, _src: StopSource = source) -> None:
                loop.call_soon_threadsafe(service.request_stop, _src)

            try:
                signal.signal(signum, _handler)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                logger.warning("could not install handler for signal %s", signum)
                continue
            installed.append((signum, previous))
            continue
        installed.append((signum, None))

    def uninstall() -> None:
        for signum, previous in installed:
            with contextlib.suppress(NotImplementedError, RuntimeError, OSError):
                loop.remove_signal_handler(signum)
            if previous is not None:
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(signum, previous)

    return uninstall


def configure_logging(level: str, *, component: str) -> None:
    """Log to stderr with a machine-greppable component prefix."""

    resolved = getattr(logging, str(level).upper(), logging.INFO)
    root = logging.getLogger()
    if not any(getattr(h, "_scn_worker", False) for h in root.handlers):
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt=f"%(asctime)s %(levelname)s [{component}] %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        handler._scn_worker = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(resolved)


logger = logging.getLogger("app.workers.durable_runtime")


def _as_coroutine(awaitable: Awaitable[int]) -> Coroutine[Any, Any, int]:
    """Adapt an awaitable into the coroutine ``asyncio.run`` requires.

    ``run_worker_entrypoint`` accepts an ``Awaitable`` so a caller may hand it
    any awaitable, but ``asyncio.run`` rejects a bare awaitable object.
    """

    async def _await() -> int:
        return await awaitable

    return _await()


def run_worker_entrypoint(
    amain: Callable[[Sequence[str] | None], Awaitable[int]],
    argv: Sequence[str] | None = None,
) -> int:
    """Process entry point wrapper shared by every durable worker module."""

    try:
        return asyncio.run(_as_coroutine(amain(argv)))
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        return EXIT_INTERRUPTED


async def drive_until_stopped(
    service: DurableWorkerService,
    *,
    shutdown_grace_seconds: float,
) -> int:
    """Run ``service`` until a signal or the loop itself ends the process."""

    uninstall = install_signal_handlers(service)
    runner = asyncio.ensure_future(service.run())
    stopper = asyncio.ensure_future(service.wait_for_stop())
    try:
        await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        uninstall()
        stopper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stopper
    if not runner.done():
        logger.warning(
            "%s worker grace period expired; stopping the work loop",
            service.component,
        )
        service.request_stop(StopSource.lifecycle)
        try:
            await asyncio.wait_for(
                asyncio.shield(runner), timeout=shutdown_grace_seconds
            )
        except TimeoutError:
            logger.error(
                "%s worker did not stop within the grace period", service.component
            )
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
            return EXIT_RUNTIME_FAILED
    return int(await runner)


__all__ = [
    "EXIT_CONFIG_INVALID",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_RUNTIME_FAILED",
    "EXIT_STARTUP_UNAVAILABLE",
    "HEARTBEAT_KEY_PREFIX",
    "HEARTBEAT_TTL_SECONDS",
    "DurableWorkerService",
    "HealthServer",
    "PreflightStep",
    "RedisHeartbeatPublisher",
    "StartupFailure",
    "StartupReport",
    "StopSource",
    "WorkerLoop",
    "WorkerReadiness",
    "WorkerRuntimeConfig",
    "combine_probes",
    "configure_logging",
    "drive_until_stopped",
    "env_flag",
    "env_float",
    "env_int",
    "heartbeat_key_for",
    "install_signal_handlers",
    "probe_database",
    "probe_redis",
    "probe_schema",
    "run_startup_checks",
    "run_worker_entrypoint",
]
