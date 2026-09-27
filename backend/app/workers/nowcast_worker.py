"""Nowcast worker: scheduler singleton plus the reserved nowcast lane.

``nowcast-worker`` (``IMPLEMENTATION_CONTRACT.md`` §13.1) owns two things that
no other service may duplicate:

1. **The scheduler singleton.**  A PostgreSQL advisory lock guarantees exactly
   one live scheduler, so a time slot is never created twice by racing workers.
2. **The reserved nowcast lane.**  ``hydraulic.worker_slots`` slot 2 is
   permanently reserved for nowcast runs and is never lent to the general
   simulation lane, so a long nowcast can never starve a simulation.

Policy gating
-------------
Before any run is created the worker loads the closed forecast policy config
named by ``FORECAST_POLICY_CONFIG_PATH`` and re-verifies its sidecar SHA-256
with the *same* loader the API uses.  A missing config, a missing sidecar, a
schema violation, or a hash mismatch means the worker does not schedule and
does not run: it records ``FORECAST_POLICY_UNAVAILABLE`` and stays unready
rather than producing a run with unproven policy lineage.  This mirrors the
frozen contract — "调度前 policy 工件缺失、过期、越域或 config/artifact hash 不符
时不创建 run".

The materializer
----------------
A nowcast run cannot be *computed* by this module: the forecast materialization
layer is owned elsewhere and is not part of this tree.  Rather than invent
horizon points, the worker delegates execution to a real runner process named by
``NOWCAST_RUNNER_COMMAND`` — the same fail-closed contract the simulation worker
applies to ``SIMULATION_SOLVER_COMMAND`` (``resolve_solver_command``,
``app/workers/simulation_worker.py``).  An empty or unresolvable command is the
honest "no materializer is wired" state, and the worker refuses to start
instead of claiming runs it cannot produce.  One attempt is one subprocess,
``shell=False``, and a non-zero exit fails the attempt.

Entry point: ``python -m app.workers.nowcast_worker``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
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

COMPONENT = "nowcast"
HEARTBEAT_KEY = heartbeat_key_for(COMPONENT)

# Advisory lock that makes the scheduler a singleton.  Distinct from the
# simulation claim lock (0x48594452), the outbox relay lock (0x4859444F), and
# the network-job lane lock (0x4E544A4F).
NOWCAST_SCHEDULER_LOCK_KEY = 0x4E4F5743
# The nowcast lane is worker_slots.slot_id = 2 and is never lent out.
NOWCAST_SLOT_ID = 2
NOWCAST_TERMINAL_ERROR = "FORECAST_POLICY_INVALID"
NOWCAST_MISSING_POLICY = "FORECAST_POLICY_UNAVAILABLE"
NON_RETRYABLE = frozenset({NOWCAST_MISSING_POLICY, NOWCAST_TERMINAL_ERROR})


class NowcastWorkerError(RuntimeError):
    """Structured failure mapped onto the durable retry policy."""

    def __init__(
        self, error_code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class NowcastClaim:
    """One leased ``hydraulic.nowcast_runs`` row in the reserved lane."""

    run_id: uuid.UUID
    network_id: uuid.UUID
    network_revision_id: uuid.UUID
    scheduled_for: datetime
    forecast_origin_at: datetime
    deadline_at: datetime
    attempt_no: int
    fencing_token: int
    lease_token: uuid.UUID
    slot_id: int
    policy_bindings: Mapping[str, str]


# --------------------------------------------------------------------------
# Forecast policy config — the same closed loader the API gates /readyz on
# --------------------------------------------------------------------------


def load_forecast_policy() -> dict[str, Any] | None:
    """Load and verify the closed forecast policy config, failing closed.

    Delegates to ``app.main._load_forecast_policy_config`` so the worker and the
    API share one implementation of the closed schema + sidecar hash rule; a
    ``None`` result means the config is absent or unverifiable, which this
    worker treats as "do not schedule".
    """

    from app.main import _load_forecast_policy_config

    return _load_forecast_policy_config()


def policy_bindings_for(
    config: Mapping[str, Any], network_id: uuid.UUID
) -> dict[str, str]:
    """The artifact IDs this worker must bind a run to for ``network_id``."""

    entry = config.get("networks", {}).get(str(network_id))
    if not isinstance(entry, dict):
        raise NowcastWorkerError(
            NOWCAST_MISSING_POLICY,
            f"forecast policy config has no entry for network {network_id}",
            retryable=False,
        )
    calibration = entry["probability_calibration_ids"]
    rules = entry["forecast_rule_ids"]
    return {
        "flood_forecast_probability_calibration_id": str(calibration["flood_forecast"]),
        "pipe_surcharge_probability_calibration_id": str(calibration["pipe_surcharge"]),
        "flood_forecast_rule_id": str(rules["flood_forecast"]),
        "pipe_surcharge_rule_id": str(rules["pipe_surcharge"]),
    }


# --------------------------------------------------------------------------
# Runner command — the materializer this worker will and will not fake
# --------------------------------------------------------------------------


def parse_runner_argv(raw: str | None) -> tuple[str, ...]:
    """Parse a JSON array, a comma list, or a native sequence into argv."""

    text = str(raw or "").strip()
    if not text:
        return ()
    if text.startswith("["):
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            return ()
        if not isinstance(decoded, list):
            return ()
        parts = [str(part) for part in decoded]
    else:
        parts = [part.strip() for part in text.split(",")]
    return tuple(part for part in parts if part)


def resolve_runner_command(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Validate the production runner argv, failing closed when absent.

    An empty command is the honest "no materializer is wired" state, so the
    worker refuses to start rather than claiming runs it cannot produce.
    """

    argv = parse_runner_argv(
        (os.environ if environ is None else environ).get("NOWCAST_RUNNER_COMMAND")
    )
    if not argv:
        raise NowcastWorkerError(
            "runner_command_unavailable",
            "NOWCAST_RUNNER_COMMAND is empty; no nowcast materializer is wired",
            retryable=False,
        )
    if any(not part for part in argv):
        raise NowcastWorkerError(
            "runner_command_unavailable",
            "NOWCAST_RUNNER_COMMAND must not contain blank argv entries",
            retryable=False,
        )
    return argv


def probe_runner_executable(argv: Sequence[str]) -> None:
    """Fail closed when the configured runner cannot be resolved or imported."""

    program = argv[0]
    if os.sep in program or (os.altsep and os.altsep in program):
        if not Path(program).expanduser().is_file():
            raise NowcastWorkerError(
                "runner_command_unavailable",
                f"nowcast runner path does not exist: {program!r}",
                retryable=False,
            )
        return
    if shutil.which(program) is None:
        raise NowcastWorkerError(
            "runner_command_unavailable",
            f"nowcast runner executable {program!r} is not on PATH",
            retryable=False,
        )
    # ``python -m app.services.forecast.materialize`` is the expected shape, so
    # prove the module really imports before claiming readiness.
    if len(argv) >= 3 and argv[1] == "-m":
        module = argv[2]
        import importlib.util

        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, AttributeError, ValueError):
            # A missing parent package raises rather than returning None.
            spec = None
        if spec is None:
            raise NowcastWorkerError(
                "runner_command_unavailable",
                f"nowcast runner module {module!r} does not exist",
                retryable=False,
            )


# --------------------------------------------------------------------------
# Durable reserved-lane control plane
# --------------------------------------------------------------------------


class SessionNowcastRepository:
    """Claim and settle nowcast runs inside the reserved lane.

    The reserved slot is taken in the *same* transaction that leases the run, so
    a nowcast run can never be executed outside ``worker_slots`` slot 2.
    """

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

    def backoff_seconds(self, attempt_no: int) -> int:
        index = min(max(0, int(attempt_no) - 1), len(self.retry_ladder) - 1)
        return int(self.retry_ladder[index])

    async def claim_next(self, worker_id: str) -> NowcastClaim | None:
        from sqlalchemy import text

        current = datetime.now(UTC)
        # Serialize the scheduler/claimer on the singleton lock.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": NOWCAST_SCHEDULER_LOCK_KEY},
        )
        slot = (
            (
                await self.session.execute(
                    text(
                        "SELECT slot_id, busy, lease_until "
                        "FROM hydraulic.worker_slots "
                        "WHERE lane = 'nowcast' FOR UPDATE"
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if slot is None:
            raise NowcastWorkerError(
                "RESERVED_SLOT_MISSING",
                "worker_slots has no nowcast lane row",
                retryable=False,
            )
        if int(slot["slot_id"]) != NOWCAST_SLOT_ID:
            raise NowcastWorkerError(
                "RESERVED_SLOT_MISMATCH",
                f"nowcast lane resolved to slot {slot['slot_id']}, "
                f"expected {NOWCAST_SLOT_ID}",
                retryable=False,
            )
        if bool(slot["busy"]) and slot["lease_until"] is not None:
            until = slot["lease_until"]
            if until.tzinfo is None:
                until = until.replace(tzinfo=UTC)
            if until > current:
                return None

        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT id, network_id, network_revision_id, scheduled_for, "
                        "forecast_origin_at, deadline_at, attempt, max_attempts, "
                        "fencing_token, "
                        "flood_forecast_probability_calibration_id, "
                        "pipe_surcharge_probability_calibration_id, "
                        "flood_forecast_rule_id, pipe_surcharge_rule_id "
                        "FROM hydraulic.nowcast_runs "
                        "WHERE status IN ('queued', 'retry_wait') "
                        "AND (next_attempt_at IS NULL OR next_attempt_at <= :now) "
                        "ORDER BY scheduled_for "
                        "FOR UPDATE SKIP LOCKED LIMIT 1"
                    ),
                    {"now": current},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        attempt_no = int(row["attempt"]) + 1
        if attempt_no > int(row["max_attempts"]):
            raise NowcastWorkerError(
                "ATTEMPT_BUDGET_EXHAUSTED",
                f"nowcast run {row['id']} exhausted its attempt budget",
                retryable=False,
            )
        fencing_token = int(row["fencing_token"]) + 1
        lease_token = uuid.uuid4()
        lease_until = current + timedelta(seconds=self.lease_seconds)
        await self.session.execute(
            text(
                "UPDATE hydraulic.nowcast_runs SET status = 'leased', "
                "attempt = :attempt, fencing_token = :fencing, "
                "lease_owner = :owner, lease_token = CAST(:lease_token AS uuid), "
                "lease_until = :lease_until, next_attempt_at = NULL, "
                "retry_backoff_seconds = NULL, last_retry_error_code = NULL, "
                "error_code = NULL, error_message = NULL, updated_at = :now "
                "WHERE id = CAST(:run_id AS uuid)"
            ),
            {
                "run_id": str(row["id"]),
                "attempt": attempt_no,
                "fencing": fencing_token,
                "owner": worker_id,
                "lease_token": str(lease_token),
                "lease_until": lease_until,
                "now": current,
            },
        )
        await self.session.execute(
            text(
                "UPDATE hydraulic.worker_slots SET busy = true, "
                "worker_id = :owner, nowcast_run_id = CAST(:run_id AS uuid), "
                "simulation_job_id = NULL, lease_until = :lease_until, "
                "heartbeat_at = :now, updated_at = :now "
                "WHERE slot_id = :slot_id"
            ),
            {
                "owner": worker_id,
                "run_id": str(row["id"]),
                "lease_until": lease_until,
                "now": current,
                "slot_id": NOWCAST_SLOT_ID,
            },
        )
        return NowcastClaim(
            run_id=row["id"],
            network_id=row["network_id"],
            network_revision_id=row["network_revision_id"],
            scheduled_for=row["scheduled_for"],
            forecast_origin_at=row["forecast_origin_at"],
            deadline_at=row["deadline_at"],
            attempt_no=attempt_no,
            fencing_token=fencing_token,
            lease_token=lease_token,
            slot_id=NOWCAST_SLOT_ID,
            policy_bindings={
                "flood_forecast_probability_calibration_id": str(
                    row["flood_forecast_probability_calibration_id"]
                ),
                "pipe_surcharge_probability_calibration_id": str(
                    row["pipe_surcharge_probability_calibration_id"]
                ),
                "flood_forecast_rule_id": str(row["flood_forecast_rule_id"]),
                "pipe_surcharge_rule_id": str(row["pipe_surcharge_rule_id"]),
            },
        )

    async def heartbeat(self, claim: NowcastClaim) -> None:
        from sqlalchemy import text

        current = datetime.now(UTC)
        lease_until = current + timedelta(seconds=self.lease_seconds)
        result = await self.session.execute(
            text(
                "UPDATE hydraulic.nowcast_runs "
                "SET lease_until = :lease_until, updated_at = :now "
                "WHERE id = CAST(:run_id AS uuid) "
                "AND status IN ('leased','running') "
                "AND lease_token = CAST(:lease_token AS uuid) "
                "AND fencing_token = :fencing"
            ),
            {
                "run_id": str(claim.run_id),
                "lease_until": lease_until,
                "now": current,
                "lease_token": str(claim.lease_token),
                "fencing": claim.fencing_token,
            },
        )
        if getattr(result, "rowcount", None) != 1:
            raise NowcastWorkerError(
                "LEASE_EXPIRED",
                "nowcast run lease was fenced out before the heartbeat",
                retryable=False,
            )
        await self.session.execute(
            text(
                "UPDATE hydraulic.worker_slots "
                "SET lease_until = :lease_until, heartbeat_at = :now, "
                "updated_at = :now WHERE slot_id = :slot_id"
            ),
            {"lease_until": lease_until, "now": current, "slot_id": NOWCAST_SLOT_ID},
        )

    async def complete(self, claim: NowcastClaim, *, result_sha256: str) -> None:
        from sqlalchemy import text

        current = datetime.now(UTC)
        result = await self.session.execute(
            text(
                "UPDATE hydraulic.nowcast_runs "
                "SET status = 'completed', issued_at = COALESCE(issued_at, :now), "
                "published_at = :now, lease_owner = NULL, lease_token = NULL, "
                "lease_until = NULL, updated_at = :now "
                "WHERE id = CAST(:run_id AS uuid) "
                "AND status IN ('leased','running') "
                "AND lease_token = CAST(:lease_token AS uuid) "
                "AND fencing_token = :fencing"
            ),
            {
                "run_id": str(claim.run_id),
                "now": current,
                "lease_token": str(claim.lease_token),
                "fencing": claim.fencing_token,
            },
        )
        if getattr(result, "rowcount", None) != 1:
            raise NowcastWorkerError(
                "LEASE_EXPIRED", "nowcast run lease was fenced out before completion"
            )
        logger.info("nowcast run %s published result=%s", claim.run_id, result_sha256)
        await self._release_slot(claim)

    async def record_failure(
        self, claim: NowcastClaim, *, error_code: str, message: str, retryable: bool
    ) -> None:
        from sqlalchemy import text

        current = datetime.now(UTC)
        shared: dict[str, Any] = {
            "run_id": str(claim.run_id),
            "now": current,
            "lease_token": str(claim.lease_token),
            "fencing": claim.fencing_token,
            "error_code": str(error_code)[:128],
            "error_message": str(message)[:2000],
        }
        if not retryable or claim.attempt_no >= self.max_attempts:
            await self.session.execute(
                text(
                    "UPDATE hydraulic.nowcast_runs "
                    "SET status = 'failed', lease_owner = NULL, lease_token = NULL, "
                    "lease_until = NULL, error_code = :error_code, "
                    "error_message = :error_message, next_attempt_at = NULL, "
                    "retry_backoff_seconds = NULL, last_retry_error_code = NULL, "
                    "updated_at = :now "
                    "WHERE id = CAST(:run_id AS uuid) "
                    "AND status IN ('leased','running') "
                    "AND lease_token = CAST(:lease_token AS uuid) "
                    "AND fencing_token = :fencing"
                ),
                shared,
            )
        else:
            backoff = self.backoff_seconds(claim.attempt_no)
            await self.session.execute(
                text(
                    "UPDATE hydraulic.nowcast_runs "
                    "SET status = 'retry_wait', lease_owner = NULL, "
                    "lease_token = NULL, lease_until = NULL, "
                    "error_code = :error_code, error_message = :error_message, "
                    "next_attempt_at = :next_attempt_at, "
                    "retry_backoff_seconds = :backoff, "
                    "last_retry_error_code = :error_code, updated_at = :now "
                    "WHERE id = CAST(:run_id AS uuid) "
                    "AND status IN ('leased','running') "
                    "AND lease_token = CAST(:lease_token AS uuid) "
                    "AND fencing_token = :fencing"
                ),
                {
                    **shared,
                    "next_attempt_at": current + timedelta(seconds=backoff),
                    "backoff": backoff,
                },
            )
        await self._release_slot(claim)

    async def _release_slot(self, claim: NowcastClaim) -> None:
        from sqlalchemy import text

        await self.session.execute(
            text(
                "UPDATE hydraulic.worker_slots SET busy = false, worker_id = NULL, "
                "nowcast_run_id = NULL, simulation_job_id = NULL, "
                "lease_until = NULL, heartbeat_at = NULL, updated_at = :now "
                "WHERE slot_id = :slot_id AND nowcast_run_id = CAST(:run_id AS uuid)"
            ),
            {
                "now": datetime.now(UTC),
                "slot_id": NOWCAST_SLOT_ID,
                "run_id": str(claim.run_id),
            },
        )

    async def schedulable_networks(self) -> tuple[dict[str, Any], ...]:
        """Networks with a published target manifest, i.e. that may be scheduled."""

        from sqlalchemy import text

        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT DISTINCT revision.network_id AS network_id, "
                        "revision.id AS network_revision_id, "
                        "revision.target_manifest_id AS target_manifest_id, "
                        "revision.target_manifest_sha256 AS target_manifest_sha256 "
                        "FROM hydraulic.network_revisions AS revision "
                        "WHERE revision.status = 'published' "
                        "AND revision.target_manifest_id IS NOT NULL "
                        "ORDER BY revision.network_id"
                    )
                )
            )
            .mappings()
            .all()
        )
        return tuple(dict(row) for row in rows)

    async def resolve_lineage(
        self, network: Mapping[str, Any], *, horizon_minutes: int
    ) -> dict[str, Any] | None:
        """Read the real simulation/rainfall/calibration lineage for a run.

        ``nowcast_runs`` carries non-nullable ``rainfall_scenario_id``,
        ``model_version``, ``solver_id`` and ``solver_version`` columns plus a
        composite FK into ``simulations``.  Those values can only come from rows
        that already exist, so this resolver looks them up and returns ``None``
        when any of them is missing — the caller then creates no run rather
        than writing placeholder identities.
        """

        from sqlalchemy import text

        network_id = str(network["network_id"])
        revision_id = str(network["network_revision_id"])
        simulation = (
            (
                await self.session.execute(
                    text(
                        "SELECT id, model_version, solver_id, solver_version "
                        "FROM hydraulic.simulations "
                        "WHERE network_revision_id = CAST(:revision_id AS uuid) "
                        "AND status = 'completed' "
                        "ORDER BY finished_at DESC NULLS LAST, created_at DESC "
                        "LIMIT 1"
                    ),
                    {"revision_id": revision_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if simulation is None:
            return None
        scenario = (
            (
                await self.session.execute(
                    text(
                        "SELECT id FROM hydraulic.rainfall_scenarios "
                        "WHERE network_id = CAST(:network_id AS uuid) "
                        "AND duration_seconds >= :horizon_seconds "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {
                        "network_id": network_id,
                        "horizon_seconds": int(horizon_minutes) * 60,
                    },
                )
            )
            .mappings()
            .one_or_none()
        )
        if scenario is None:
            return None
        calibration = (
            (
                await self.session.execute(
                    text(
                        "SELECT id FROM hydraulic.calibration_runs "
                        "WHERE network_revision_id = CAST(:revision_id AS uuid) "
                        "AND status = 'completed' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"revision_id": revision_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return {
            "simulation_id": str(simulation["id"]),
            "model_version": str(simulation["model_version"]),
            "solver_id": str(simulation["solver_id"]),
            "solver_version": str(simulation["solver_version"]),
            "rainfall_scenario_id": str(scenario["id"]),
            "model_calibration_id": str(calibration["id"]) if calibration else None,
        }


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunResult:
    status: int
    result_sha256: str
    stdout: str


class NowcastRunner:
    """One materializer subprocess per attempt, ``shell=False``.

    The child receives the run identity and its bound policy artifact IDs on
    stdin as canonical JSON and answers with a JSON object on stdout.  A
    non-zero exit fails the attempt closed; the worker never interprets a
    partial answer as a published forecast.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float = 180.0,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("runner timeout must be positive")
        self._argv = tuple(argv)
        self._timeout = float(timeout_seconds)
        self._env = dict(env) if env is not None else None

    async def run(self, claim: NowcastClaim) -> RunResult:
        request = json.dumps(
            {
                "run_id": str(claim.run_id),
                "network_id": str(claim.network_id),
                "network_revision_id": str(claim.network_revision_id),
                "scheduled_for": claim.scheduled_for.isoformat(),
                "forecast_origin_at": claim.forecast_origin_at.isoformat(),
                "deadline_at": claim.deadline_at.isoformat(),
                "attempt_no": claim.attempt_no,
                "fencing_token": claim.fencing_token,
                "slot_id": claim.slot_id,
                "policy_bindings": dict(claim.policy_bindings),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(request.encode("utf-8")), timeout=self._timeout
            )
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
            raise NowcastWorkerError(
                "NOWCAST_RUNNER_TIMEOUT",
                f"nowcast runner exceeded {self._timeout}s for run {claim.run_id}",
            ) from None
        status = int(process.returncode or 0)
        if status != 0:
            detail = (stderr or b"").decode("utf-8", "replace").strip().splitlines()
            raise NowcastWorkerError(
                "NOWCAST_RUNNER_FAILED",
                f"nowcast runner exited {status}"
                + (f": {detail[-1][:200]}" if detail else ""),
            )
        text_out = stdout.decode("utf-8", "replace").strip()
        try:
            document = json.loads(text_out)
        except json.JSONDecodeError as exc:
            raise NowcastWorkerError(
                "NOWCAST_RUNNER_PROTOCOL",
                "nowcast runner did not answer with a JSON object",
            ) from exc
        if not isinstance(document, dict) or document.get("status") != "ok":
            raise NowcastWorkerError(
                "NOWCAST_RUNNER_PROTOCOL",
                "nowcast runner did not report status=ok",
            )
        result_sha256 = str(document.get("result_sha256", "")).strip()
        if len(result_sha256) != 64 or result_sha256.strip("0123456789abcdef"):
            raise NowcastWorkerError(
                "NOWCAST_RUNNER_PROTOCOL",
                "nowcast runner did not return a 64-character lowercase hex digest",
            )
        return RunResult(status=status, result_sha256=result_sha256, stdout=text_out)


# --------------------------------------------------------------------------
# Worker loop
# --------------------------------------------------------------------------


class NowcastWorker:
    """Claim, run, and settle one nowcast run per ``run_once`` call."""

    def __init__(
        self,
        repository_factory: Callable[[Any], SessionNowcastRepository],
        session_factory: Callable[[], Any],
        runner: NowcastRunner,
        *,
        worker_id: str,
        heartbeat_seconds: float = 10.0,
    ) -> None:
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._repository_factory = repository_factory
        self._session_factory = session_factory
        self._runner = runner
        self._worker_id = worker_id
        self._heartbeat_seconds = float(heartbeat_seconds)
        self.completed = 0
        self.failed = 0

    async def run_once(self) -> bool:
        claim = await self._in_transaction(
            lambda repo: repo.claim_next(self._worker_id)
        )
        if claim is None:
            return True
        try:
            result = await self._attempt(claim)
        except NowcastWorkerError as exc:
            await self._settle_failure(claim, exc.error_code, str(exc), exc.retryable)
            return True
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            logger.exception("nowcast run %s raised: %s", claim.run_id, exc)
            await self._settle_failure(
                claim, "RESULT_PERSIST_FAILED", type(exc).__name__, True
            )
            return True
        await self._in_transaction(
            lambda repo: repo.complete(claim, result_sha256=result.result_sha256)
        )
        self.completed += 1
        return True

    async def _attempt(self, claim: NowcastClaim) -> RunResult:
        stop = asyncio.Event()
        renewer = asyncio.ensure_future(self._renew_lease(claim, stop))
        try:
            # Re-verify the bound policy artifacts before running, so a run can
            # never be executed against artifacts that changed after creation.
            config = await asyncio.to_thread(load_forecast_policy)
            if config is None:
                raise NowcastWorkerError(
                    NOWCAST_MISSING_POLICY,
                    "forecast policy config is missing or its sidecar hash "
                    "does not match",
                    retryable=False,
                )
            for field, expected in claim.policy_bindings.items():
                if str(expected) not in _all_artifact_ids(config):
                    raise NowcastWorkerError(
                        NOWCAST_TERMINAL_ERROR,
                        f"nowcast run {claim.run_id} binds {field} to an artifact "
                        "that is not in the verified policy config",
                        retryable=False,
                    )
            return await self._runner.run(claim)
        finally:
            stop.set()
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer

    async def _renew_lease(self, claim: NowcastClaim, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                await self._in_transaction(lambda repo: repo.heartbeat(claim))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "nowcast run %s heartbeat failed: %s",
                    claim.run_id,
                    type(exc).__name__,
                )
                return

    async def _settle_failure(
        self, claim: NowcastClaim, code: str, message: str, retryable: bool
    ) -> None:
        self.failed += 1
        logger.warning(
            "nowcast run %s attempt %s failed code=%s",
            claim.run_id,
            claim.attempt_no,
            code,
        )
        await self._in_transaction(
            lambda repo: repo.record_failure(
                claim,
                error_code=code,
                message=message,
                retryable=retryable and code not in NON_RETRYABLE,
            )
        )

    async def _in_transaction(
        self, action: Callable[[SessionNowcastRepository], Awaitable[Any]]
    ) -> Any:
        async with open_session(self._session_factory) as session:
            async with session.begin():
                return await action(self._repository_factory(session))


def _all_artifact_ids(config: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for entry in config.get("networks", {}).values():
        if not isinstance(entry, dict):
            continue
        for group in ("probability_calibration_ids", "forecast_rule_ids"):
            selection = entry.get(group)
            if isinstance(selection, Mapping):
                ids.update(str(value) for value in selection.values())
    return ids


class SchedulerSingleton:
    """Create due nowcast runs under a PostgreSQL advisory lock.

    The lock is taken per tick inside one transaction, so exactly one worker
    instance can create runs for a slot; the ``uq_hydraulic_nowcast_runs_slot``
    unique constraint is the second line of defence.
    """

    def __init__(
        self,
        repository_factory: Callable[[Any], SessionNowcastRepository],
        session_factory: Callable[[], Any],
        *,
        interval_minutes: int,
        horizon_minutes: int,
        step_minutes: int,
        deadline_seconds: int,
        slot_timeout_seconds: float,
    ) -> None:
        if interval_minutes <= 0 or step_minutes <= 0:
            raise ValueError("interval and step must be positive")
        if horizon_minutes % step_minutes != 0:
            raise ValueError("horizon must be divisible by step")
        self._repository_factory = repository_factory
        self._session_factory = session_factory
        self._interval_minutes = int(interval_minutes)
        self._horizon_minutes = int(horizon_minutes)
        self._step_minutes = int(step_minutes)
        self._deadline_seconds = int(deadline_seconds)
        self._slot_timeout = float(slot_timeout_seconds)
        self.scheduled = 0
        self.policy_unavailable = 0
        self.lineage_unavailable = 0

    def align_slot(self, now: datetime) -> datetime:
        from app.services.hydraulic.nowcast import align_to_interval

        return align_to_interval(now, self._interval_minutes)

    async def tick(self, now: datetime | None = None) -> int:
        """Create at most one run per schedulable network for the due slot."""

        config = await asyncio.to_thread(load_forecast_policy)
        if config is None:
            self.policy_unavailable += 1
            logger.warning(
                "%s: forecast policy config unavailable; no run created",
                NOWCAST_MISSING_POLICY,
            )
            return 0
        current = now or datetime.now(UTC)
        slot = self.align_slot(current)
        scheduled = await self._in_transaction(
            lambda repo: self._schedule(repo, config, current, slot)
        )
        return int(scheduled)

    async def _schedule(
        self,
        repo: SessionNowcastRepository,
        config: Mapping[str, Any],
        current: datetime,
        slot: datetime,
    ) -> int:
        from sqlalchemy import text

        acquired = await repo.session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": NOWCAST_SCHEDULER_LOCK_KEY},
        )
        if not acquired:
            return 0
        networks = await repo.schedulable_networks()
        created = 0
        for network in networks:
            network_id = network["network_id"]
            try:
                bindings = policy_bindings_for(config, network_id)
            except NowcastWorkerError as exc:
                logger.warning("%s: %s", NOWCAST_MISSING_POLICY, exc)
                self.policy_unavailable += 1
                continue
            lineage = await repo.resolve_lineage(
                network, horizon_minutes=self._horizon_minutes
            )
            if lineage is None:
                # No completed simulation / long-enough rainfall scenario for
                # this revision: there is no honest lineage to write, so no run
                # is created for this network.
                self.lineage_unavailable += 1
                logger.warning(
                    "%s: no simulation/rainfall lineage for network %s; "
                    "no run created",
                    NOWCAST_MISSING_POLICY,
                    network_id,
                )
                continue
            result = await repo.session.execute(
                text(
                    "INSERT INTO hydraulic.nowcast_runs "
                    "(id, network_id, network_revision_id, target_manifest_id, "
                    "target_manifest_sha256, scheduled_for, forecast_origin_at, "
                    "deadline_at, status, attempt, max_attempts, "
                    "fencing_token, simulation_id, rainfall_scenario_id, "
                    "model_version, solver_id, solver_version, "
                    "model_calibration_id, "
                    "flood_forecast_probability_calibration_id, "
                    "pipe_surcharge_probability_calibration_id, "
                    "flood_forecast_rule_id, pipe_surcharge_rule_id, "
                    "config_hash, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), CAST(:network_id AS uuid), "
                    "CAST(:revision_id AS uuid), "
                    "CAST(:manifest_id AS uuid), :manifest_sha256, :scheduled, "
                    ":origin, :deadline, 'queued', 0, :max_attempts, 0, "
                    "CAST(:simulation_id AS uuid), "
                    "CAST(:rainfall_scenario_id AS uuid), :model_version, "
                    ":solver_id, :solver_version, "
                    "CAST(:model_calibration_id AS uuid), "
                    "CAST(:flood_calibration AS uuid), "
                    "CAST(:surcharge_calibration AS uuid), "
                    "CAST(:flood_rule AS uuid), CAST(:surcharge_rule AS uuid), "
                    ":config_hash, :now, :now) "
                    "ON CONFLICT (network_id, scheduled_for) DO NOTHING"
                ),
                {
                    "network_id": str(network_id),
                    "revision_id": str(network["network_revision_id"]),
                    "manifest_id": str(network["target_manifest_id"]),
                    "manifest_sha256": str(network["target_manifest_sha256"]),
                    "scheduled": slot,
                    "origin": current,
                    "deadline": current + timedelta(seconds=self._deadline_seconds),
                    "max_attempts": repo.max_attempts,
                    "simulation_id": lineage["simulation_id"],
                    "rainfall_scenario_id": lineage["rainfall_scenario_id"],
                    "model_version": lineage["model_version"],
                    "solver_id": lineage["solver_id"],
                    "solver_version": lineage["solver_version"],
                    "model_calibration_id": lineage["model_calibration_id"],
                    "flood_calibration": bindings[
                        "flood_forecast_probability_calibration_id"
                    ],
                    "surcharge_calibration": bindings[
                        "pipe_surcharge_probability_calibration_id"
                    ],
                    "flood_rule": bindings["flood_forecast_rule_id"],
                    "surcharge_rule": bindings["pipe_surcharge_rule_id"],
                    "config_hash": _policy_config_hash(config),
                    "now": current,
                },
            )
            if getattr(result, "rowcount", None) == 1:
                created += 1
        self.scheduled += created
        return created

    async def _in_transaction(
        self, action: Callable[[SessionNowcastRepository], Awaitable[Any]]
    ) -> Any:
        async with open_session(self._session_factory) as session:
            async with session.begin():
                return await action(self._repository_factory(session))


def _policy_config_hash(config: Mapping[str, Any]) -> str:
    """``nowcast_runs.config_hash`` over the closed policy object only."""

    canonical = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def repository_factory_from_settings() -> Callable[[Any], SessionNowcastRepository]:
    from app.core.config import settings

    ladder_raw = settings.NOWCAST_RETRY_BACKOFF_SECONDS
    ladder = (
        [int(value) for value in ladder_raw]
        if isinstance(ladder_raw, (list, tuple))
        else [int(part) for part in str(ladder_raw).split(",") if part.strip()]
    )

    def factory(session: Any) -> SessionNowcastRepository:
        return SessionNowcastRepository(
            session,
            lease_seconds=settings.SIM_LEASE_SECONDS,
            max_attempts=settings.NOWCAST_MAX_RETRIES + 1,
            retry_ladder=ladder,
        )

    return factory


# --------------------------------------------------------------------------
# Service assembly
# --------------------------------------------------------------------------


class NowcastWorkerService(DurableWorkerService):
    """A durable worker whose loop also ticks the scheduler singleton."""

    def __init__(
        self, *args: Any, scheduler: SchedulerSingleton | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.scheduler = scheduler

    async def _run_claim_loop(self) -> None:
        if self.scheduler is not None:
            try:
                await self.scheduler.tick()
            except Exception as exc:  # noqa: BLE001 - a tick failure is not fatal
                logger.warning("nowcast scheduler tick failed: %s", type(exc).__name__)
        await super()._run_claim_loop()


def build_service(
    *,
    worker_id: str | None = None,
    config: WorkerRuntimeConfig | None = None,
    startup_report: StartupReport | None = None,
    probe: Callable[[], Awaitable[bool]] | None = None,
    session_factory: Callable[[], Any] | None = None,
    runner: NowcastRunner | None = None,
    runner_argv: Sequence[str] | None = None,
) -> NowcastWorkerService:
    from app.core.config import settings
    from app.core.database import get_session_factory

    runtime = config or WorkerRuntimeConfig.from_environ(COMPONENT)
    identity = worker_id or f"nowcast-{uuid.uuid4().hex[:12]}"
    sessions = session_factory or get_session_factory
    argv = tuple(runner_argv) if runner_argv is not None else resolve_runner_command()
    worker = NowcastWorker(
        repository_factory_from_settings(),
        sessions,
        runner
        or NowcastRunner(
            argv, timeout_seconds=float(settings.NOWCAST_DEADLINE_SECONDS)
        ),
        worker_id=identity,
        heartbeat_seconds=float(settings.SIM_HEARTBEAT_SECONDS),
    )
    scheduler = SchedulerSingleton(
        repository_factory_from_settings(),
        sessions,
        interval_minutes=int(settings.NOWCAST_INTERVAL_MINUTES),
        horizon_minutes=int(settings.NOWCAST_HORIZON_MINUTES),
        step_minutes=int(settings.NOWCAST_STEP_MINUTES),
        deadline_seconds=int(settings.NOWCAST_DEADLINE_SECONDS),
        slot_timeout_seconds=float(settings.NOWCAST_DEADLINE_SECONDS),
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
    return NowcastWorkerService(
        worker,
        readiness,
        component=COMPONENT,
        health_server=server,
        heartbeat=RedisHeartbeatPublisher(COMPONENT),
        config=runtime,
        probe=probe or combine_probes(probe_database, probe_schema, probe_redis),
        scheduler=scheduler,
    )


def _probe_policy_config() -> None:
    """Fail closed when the closed policy config is absent or unverifiable."""

    if load_forecast_policy() is None:
        raise NowcastWorkerError(
            NOWCAST_MISSING_POLICY,
            "FORECAST_POLICY_CONFIG_PATH is unset, unreadable, or its sidecar "
            "hash does not match the canonical bytes",
            retryable=False,
        )


def ensure_writable_directory(path: Path) -> None:
    root = Path(path).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".nowcast-worker.probe"
    marker.write_text("ok", encoding="utf-8")
    marker.unlink()


async def run_startup_checks_for_worker(
    *, sandbox_root: Path, runner_argv: Sequence[str]
) -> StartupReport:
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
                "policy_config",
                _probe_policy_config,
                StartupFailure.policy_config_invalid,
            ),
            PreflightStep(
                "sandbox_writable",
                lambda: ensure_writable_directory(sandbox_root),
                StartupFailure.staging_root_not_writable,
            ),
            PreflightStep(
                "runner_command",
                lambda: probe_runner_executable(runner_argv),
                StartupFailure.claim_probe_failed,
            ),
        ),
        component=COMPONENT,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.nowcast_worker",
        description=(
            "Run the nowcast scheduler singleton and the reserved nowcast lane "
            "until SIGTERM.  Exits non-zero on every abnormal path."
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
    """Async entry point: preflight, schedule, claim, and shut down gracefully."""

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    from app.core.config import settings

    configure_logging(args.log_level or settings.LOG_LEVEL, component=COMPONENT)
    try:
        config = WorkerRuntimeConfig.from_environ(COMPONENT)
        runner_argv = resolve_runner_command()
    except NowcastWorkerError as exc:
        logger.error(
            "nowcast worker startup rejected reason=%s detail=%s", exc.error_code, exc
        )
        return EXIT_STARTUP_UNAVAILABLE
    except ValueError as exc:
        logger.error("nowcast worker configuration invalid: %s", exc)
        return EXIT_CONFIG_INVALID

    report = await run_startup_checks_for_worker(
        sandbox_root=Path(settings.HYDRAULIC_SANDBOX_ROOT), runner_argv=runner_argv
    )
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
        # Fail closed: a nowcast worker that cannot prove its policy lineage or
        # cannot run a materializer must not create or claim any run.
        logger.error(
            "nowcast worker startup rejected reason=%s detail=%s",
            report.reason.value,
            report.detail,
        )
        return EXIT_STARTUP_UNAVAILABLE
    logger.info(
        "nowcast worker startup checks passed checks=%s",
        json.dumps(dict(report.checks), sort_keys=True),
    )

    service = build_service(
        worker_id=args.worker_id,
        config=config,
        startup_report=report,
        runner_argv=runner_argv,
    )
    return await drive_until_stopped(
        service, shutdown_grace_seconds=config.shutdown_grace_seconds
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Process entry point for ``python -m app.workers.nowcast_worker``."""

    return run_worker_entrypoint(amain, argv)


__all__ = [
    "COMPONENT",
    "HEARTBEAT_KEY",
    "NOWCAST_MISSING_POLICY",
    "NOWCAST_SCHEDULER_LOCK_KEY",
    "NOWCAST_SLOT_ID",
    "NOWCAST_TERMINAL_ERROR",
    "NON_RETRYABLE",
    "NowcastClaim",
    "NowcastRunner",
    "NowcastWorker",
    "NowcastWorkerError",
    "NowcastWorkerService",
    "RunResult",
    "SchedulerSingleton",
    "SessionNowcastRepository",
    "amain",
    "build_parser",
    "build_service",
    "load_forecast_policy",
    "main",
    "parse_runner_argv",
    "policy_bindings_for",
    "repository_factory_from_settings",
    "resolve_runner_command",
    "run_startup_checks_for_worker",
]


if __name__ == "__main__":
    raise SystemExit(main())
