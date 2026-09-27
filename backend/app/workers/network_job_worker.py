"""Network-job worker: DB-claimed import / validate / seed execution.

``network-job-worker`` (``IMPLEMENTATION_CONTRACT.md`` §13.1) owns the
``independent`` task lane: it claims a row of ``hydraulic.network_jobs`` under a
lease and a fencing token, runs the real business for that ``job_type``, and
folds the outcome back into the durable job state.

The three job types map onto the frozen business entry points:

``import``
    ``app.services.hydraulic.importers.swmm.parse_swmm`` over the staged
    source bytes, recorded as a ``hydraulic.network_imports`` row.
``validate``
    ``app.services.hydraulic.topology.validate_topology`` over the parsed
    model, recorded as ``hydraulic.validation_runs`` plus
    ``hydraulic.network_validation_issues``.
``seed``
    ``app.services.hydraulic.seed.generate_tree_loop_seed`` over a staged seed
    manifest, recorded as an import row with the generated counts.

Staged input contract
---------------------
A job's payload is a *file*, not a database column, so the worker defines the
staging layout it consumes.  For a job ``<id>`` of type ``import``/``validate``
the staged document is::

    ${HYDRAULIC_IMPORT_ROOT}/<id>.inp

and for a ``seed`` job it is::

    ${HYDRAULIC_IMPORT_ROOT}/<id>.json   (a SeedConfig mapping)

In every case the file's SHA-256 must equal the job's ``request_fingerprint``
(64 lowercase hex).  A missing file, an oversized file, a hash mismatch, or an
unparsable manifest fails the attempt closed with a non-retryable error code —
the worker never invents an import that was never staged.

Transaction shape
-----------------
Claiming commits on its own so the lease is durable before any work starts.
The business step is pure computation (parse / validate / generate) and holds
no transaction, so a long job never pins row locks.  Persistence of the result
*and* the terminal job transition then share a single transaction: a crash
between them is impossible by construction, and a fenced-out lease rolls both
back rather than leaving an orphan import row.

Lane isolation
--------------
This worker only ever claims ``job_type IN ('import','validate','seed')`` rows.
It takes a transaction advisory lock of its own so the independent lane
serializes, and it never touches ``hydraulic.simulations`` or
``hydraulic.worker_slots``, which belong to the simulation and nowcast workers.

Entry point: ``python -m app.workers.network_job_worker``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
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

COMPONENT = "network-job"
HEARTBEAT_KEY = heartbeat_key_for(COMPONENT)

# A private advisory-lock key for the independent lane.  It is deliberately
# distinct from the simulation claim lock (0x48594452) and the outbox relay lock
# (0x4859444F) so the three serialize independently.
NETWORK_JOB_CLAIM_LOCK_KEY = 0x4E544A4F
JOB_TYPES = ("import", "validate", "seed")

# Non-retryable: the staged input is wrong, so a retry would fail identically.
NON_RETRYABLE = frozenset(
    {
        "STAGED_INPUT_MISSING",
        "STAGED_INPUT_TOO_LARGE",
        "STAGED_INPUT_HASH_MISMATCH",
        "SEED_MANIFEST_INVALID",
        "ATTEMPT_BUDGET_EXHAUSTED",
    }
)


class NetworkJobError(RuntimeError):
    """Structured failure mapped onto the durable retry policy."""

    def __init__(
        self, error_code: str, message: str, *, retryable: bool = True
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class NetworkJobClaim:
    """One leased ``hydraulic.network_jobs`` row."""

    job_id: uuid.UUID
    job_type: str
    attempt_no: int
    fencing_token: int
    lease_token: uuid.UUID
    lease_until: datetime
    request_fingerprint: str
    network_id: uuid.UUID | None
    created_by: uuid.UUID | None

    @property
    def lane(self) -> str:
        return "independent"


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """The pure result of one attempt, ready to persist in one transaction."""

    kind: str  # "import" | "validation"
    source_filename: str
    source_format: str
    source_bytes: bytes
    counts: Mapping[str, Any]
    result_sha256: str
    report: Any | None = None


# --------------------------------------------------------------------------
# Durable job control plane
# --------------------------------------------------------------------------


class SessionNetworkJobRepository:
    """Claim/complete network jobs with row locks and fencing tokens.

    The session is injected per operation so each control-plane action runs in
    exactly one transaction opened by the caller.
    """

    def __init__(
        self,
        session: Any,
        *,
        lease_seconds: int,
        attempt_timeout_seconds: int,
        max_attempts: int,
        retry_ladder: Sequence[int],
    ) -> None:
        if lease_seconds <= 0 or attempt_timeout_seconds <= 0:
            raise ValueError("lease and timeout must be positive")
        if max_attempts < 2:
            raise ValueError("network jobs need at least two attempts")
        if not retry_ladder:
            raise ValueError("retry backoff ladder must not be empty")
        self.session = session
        self.lease_seconds = int(lease_seconds)
        self.attempt_timeout_seconds = int(attempt_timeout_seconds)
        self.max_attempts = int(max_attempts)
        self.retry_ladder = tuple(int(value) for value in retry_ladder)

    def backoff_seconds(self, attempt_no: int) -> int:
        index = min(max(0, int(attempt_no) - 1), len(self.retry_ladder) - 1)
        return int(self.retry_ladder[index])

    async def claim_next(self, worker_id: str) -> NetworkJobClaim | None:
        from sqlalchemy import text

        current = datetime.now(UTC)
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": NETWORK_JOB_CLAIM_LOCK_KEY},
        )
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT id, job_type, attempt, max_attempts, "
                        "request_fingerprint, network_id, created_by, "
                        "fencing_token "
                        "FROM hydraulic.network_jobs "
                        "WHERE status IN ('queued', 'retry_wait') "
                        "AND job_type = ANY(CAST(:job_types AS text[])) "
                        "AND (next_attempt_at IS NULL "
                        "OR next_attempt_at <= :now) "
                        "ORDER BY created_at "
                        "FOR UPDATE SKIP LOCKED LIMIT 1"
                    ),
                    {"job_types": list(JOB_TYPES), "now": current},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        attempt_no = int(row["attempt"]) + 1
        if attempt_no > int(row["max_attempts"]):
            raise NetworkJobError(
                "ATTEMPT_BUDGET_EXHAUSTED",
                f"network job {row['id']} exhausted its attempt budget",
                retryable=False,
            )
        if str(row["job_type"]) not in JOB_TYPES:
            raise NetworkJobError(
                "UNKNOWN_JOB_TYPE",
                f"network job {row['id']} has unsupported job_type",
                retryable=False,
            )
        fencing_token = int(row["fencing_token"]) + 1
        lease_token = uuid.uuid4()
        await self.session.execute(
            text(
                "UPDATE hydraulic.network_jobs SET status = 'leased', "
                "phase = :phase, attempt = :attempt, fencing_token = :fencing, "
                "lease_owner = :owner, lease_token = CAST(:lease_token AS uuid), "
                "lease_until = :lease_until, deadline_at = :deadline, "
                "next_attempt_at = NULL, retry_backoff_seconds = NULL, "
                "last_retry_error_code = NULL, error_code = NULL, "
                "error_message = NULL, "
                "started_at = COALESCE(started_at, :now), updated_at = :now "
                "WHERE id = CAST(:job_id AS uuid)"
            ),
            {
                "job_id": str(row["id"]),
                "phase": (
                    "staging" if str(row["job_type"]) == "import" else "validating"
                ),
                "attempt": attempt_no,
                "fencing": fencing_token,
                "owner": worker_id,
                "lease_token": str(lease_token),
                "lease_until": current + timedelta(seconds=self.lease_seconds),
                "deadline": current + timedelta(seconds=self.attempt_timeout_seconds),
                "now": current,
            },
        )
        await self.session.execute(
            text(
                "INSERT INTO hydraulic.network_job_attempts "
                "(id, job_id, attempt_no, fencing_token, status, started_at) "
                "VALUES (CAST(:id AS uuid), CAST(:job_id AS uuid), :attempt_no, "
                ":fencing, 'running', :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "job_id": str(row["id"]),
                "attempt_no": attempt_no,
                "fencing": fencing_token,
                "now": current,
            },
        )
        return NetworkJobClaim(
            job_id=row["id"],
            job_type=str(row["job_type"]),
            attempt_no=attempt_no,
            fencing_token=fencing_token,
            lease_token=lease_token,
            lease_until=current + timedelta(seconds=self.lease_seconds),
            request_fingerprint=str(row["request_fingerprint"]),
            network_id=row["network_id"],
            created_by=row["created_by"],
        )

    async def heartbeat(self, claim: NetworkJobClaim) -> None:
        from sqlalchemy import text

        current = datetime.now(UTC)
        result = await self.session.execute(
            text(
                "UPDATE hydraulic.network_jobs "
                "SET lease_until = :lease_until, updated_at = :now "
                "WHERE id = CAST(:job_id AS uuid) "
                "AND status IN ('leased','running') "
                "AND lease_token = CAST(:lease_token AS uuid) "
                "AND fencing_token = :fencing"
            ),
            {
                "job_id": str(claim.job_id),
                "lease_until": current + timedelta(seconds=self.lease_seconds),
                "now": current,
                "lease_token": str(claim.lease_token),
                "fencing": claim.fencing_token,
            },
        )
        if getattr(result, "rowcount", None) != 1:
            raise NetworkJobError(
                "LEASE_EXPIRED",
                "network job lease was fenced out before the heartbeat",
                retryable=False,
            )

    async def set_phase(
        self, claim: NetworkJobClaim, *, phase: str, progress: int
    ) -> None:
        from sqlalchemy import text

        result = await self.session.execute(
            text(
                "UPDATE hydraulic.network_jobs "
                "SET status = 'running', phase = :phase, progress = :progress, "
                "updated_at = :now "
                "WHERE id = CAST(:job_id AS uuid) "
                "AND status IN ('leased','running') "
                "AND lease_token = CAST(:lease_token AS uuid) "
                "AND fencing_token = :fencing"
            ),
            {
                "job_id": str(claim.job_id),
                "phase": phase,
                "progress": max(0, min(100, int(progress))),
                "now": datetime.now(UTC),
                "lease_token": str(claim.lease_token),
                "fencing": claim.fencing_token,
            },
        )
        if getattr(result, "rowcount", None) != 1:
            raise NetworkJobError(
                "LEASE_EXPIRED", "network job lease was fenced out mid-attempt"
            )

    async def complete(self, claim: NetworkJobClaim, outcome: JobOutcome) -> None:
        """Persist the result and the terminal transition in one transaction."""

        from sqlalchemy import text

        artifact_id = await self._persist_outcome(claim, outcome)
        current = datetime.now(UTC)
        result = await self.session.execute(
            text(
                "UPDATE hydraulic.network_jobs "
                "SET status = 'completed', phase = 'cleanup', progress = 100, "
                "lease_owner = NULL, lease_token = NULL, lease_until = NULL, "
                "result_artifact_id = CAST(:result_artifact_id AS uuid), "
                "finished_at = :now, updated_at = :now "
                "WHERE id = CAST(:job_id AS uuid) "
                "AND status IN ('leased','running') "
                "AND lease_token = CAST(:lease_token AS uuid) "
                "AND fencing_token = :fencing"
            ),
            {
                "job_id": str(claim.job_id),
                "result_artifact_id": str(artifact_id),
                "now": current,
                "lease_token": str(claim.lease_token),
                "fencing": claim.fencing_token,
            },
        )
        if getattr(result, "rowcount", None) != 1:
            raise NetworkJobError(
                "LEASE_EXPIRED", "network job lease was fenced out before completion"
            )
        await self.session.execute(
            text(
                "UPDATE hydraulic.network_job_attempts "
                "SET status = 'published', finished_at = :now, "
                "parser_result_sha256 = :result_sha256 "
                "WHERE job_id = CAST(:job_id AS uuid) AND fencing_token = :fencing"
            ),
            {
                "job_id": str(claim.job_id),
                "fencing": claim.fencing_token,
                "now": current,
                "result_sha256": outcome.result_sha256,
            },
        )

    async def record_failure(
        self, claim: NetworkJobClaim, *, error_code: str, message: str, retryable: bool
    ) -> None:
        from sqlalchemy import text

        current = datetime.now(UTC)
        shared: dict[str, Any] = {
            "job_id": str(claim.job_id),
            "now": current,
            "lease_token": str(claim.lease_token),
            "fencing": claim.fencing_token,
            "error_code": str(error_code)[:128],
            "error_message": str(message)[:2000],
        }
        exhausted = not retryable or claim.attempt_no >= self.max_attempts
        if exhausted:
            await self.session.execute(
                text(
                    "UPDATE hydraulic.network_jobs "
                    "SET status = 'failed', phase = 'cleanup', "
                    "lease_owner = NULL, lease_token = NULL, lease_until = NULL, "
                    "error_code = :error_code, error_message = :error_message, "
                    "next_attempt_at = NULL, retry_backoff_seconds = NULL, "
                    "last_retry_error_code = NULL, finished_at = :now, "
                    "updated_at = :now "
                    "WHERE id = CAST(:job_id AS uuid) "
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
                    "UPDATE hydraulic.network_jobs "
                    "SET status = 'retry_wait', phase = 'accepted', "
                    "lease_owner = NULL, lease_token = NULL, lease_until = NULL, "
                    "error_code = :error_code, error_message = :error_message, "
                    "next_attempt_at = :next_attempt_at, "
                    "retry_backoff_seconds = :backoff, "
                    "last_retry_error_code = :error_code, updated_at = :now "
                    "WHERE id = CAST(:job_id AS uuid) "
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
        await self.session.execute(
            text(
                "UPDATE hydraulic.network_job_attempts "
                "SET status = 'discarded', finished_at = :now, "
                "error_code = :error_code, error_message = :error_message "
                "WHERE job_id = CAST(:job_id AS uuid) AND fencing_token = :fencing"
            ),
            shared,
        )

    async def _persist_outcome(
        self, claim: NetworkJobClaim, outcome: JobOutcome
    ) -> uuid.UUID:
        if outcome.kind == "import":
            return await self._persist_import(claim, outcome)
        return await self._persist_validation(claim, outcome)

    async def _persist_import(
        self, claim: NetworkJobClaim, outcome: JobOutcome
    ) -> uuid.UUID:
        from sqlalchemy import text

        row_id = uuid.uuid4()
        await self.session.execute(
            text(
                "INSERT INTO hydraulic.network_imports "
                "(id, network_id, network_job_id, source_filename, source_format, "
                "source_sha256, byte_size, parser_version, committed, counts, "
                "created_by, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:network_id AS uuid), "
                "CAST(:job_id AS uuid), :filename, :format, :sha256, :byte_size, "
                ":parser_version, true, CAST(:counts AS jsonb), "
                "CAST(:created_by AS uuid), :now)"
            ),
            {
                "id": str(row_id),
                "network_id": str(claim.network_id) if claim.network_id else None,
                "job_id": str(claim.job_id),
                "filename": outcome.source_filename,
                "format": outcome.source_format,
                "sha256": hashlib.sha256(outcome.source_bytes).hexdigest(),
                "byte_size": len(outcome.source_bytes),
                "parser_version": outcome.source_format,
                "counts": json.dumps(dict(outcome.counts), sort_keys=True, default=str),
                "created_by": str(claim.created_by) if claim.created_by else None,
                "now": datetime.now(UTC),
            },
        )
        return row_id

    async def _persist_validation(
        self, claim: NetworkJobClaim, outcome: JobOutcome
    ) -> uuid.UUID:
        from sqlalchemy import text

        report = outcome.report
        if report is None:  # pragma: no cover - defensive
            raise NetworkJobError(
                "VALIDATION_REPORT_MISSING",
                "validation outcome carried no report",
                retryable=False,
            )
        document = report.to_dict()
        run_id = uuid.uuid4()
        await self.session.execute(
            text(
                "INSERT INTO hydraulic.validation_runs "
                "(id, network_job_id, network_id, status_value, counts, "
                "simulation_ready, topology, created_by, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:job_id AS uuid), "
                "CAST(:network_id AS uuid), :status, CAST(:counts AS jsonb), "
                ":simulation_ready, CAST(:topology AS jsonb), "
                "CAST(:created_by AS uuid), :now)"
            ),
            {
                "id": str(run_id),
                "job_id": str(claim.job_id),
                "network_id": str(claim.network_id) if claim.network_id else None,
                "status": str(document["status"]),
                "counts": json.dumps(document["counts"], sort_keys=True),
                "simulation_ready": bool(document["simulation_ready"]),
                "topology": json.dumps(document, sort_keys=True, default=str),
                "created_by": str(claim.created_by) if claim.created_by else None,
                "now": datetime.now(UTC),
            },
        )
        for issue in document["issues"]:
            await self.session.execute(
                text(
                    "INSERT INTO hydraulic.network_validation_issues "
                    "(id, validation_run_id, network_job_id, severity, code, "
                    "entity_type, entity_key, message, details) "
                    "VALUES (CAST(:id AS uuid), CAST(:run_id AS uuid), "
                    "CAST(:job_id AS uuid), :severity, :code, :entity_type, "
                    ":entity_key, :message, CAST(:details AS jsonb))"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "run_id": str(run_id),
                    "job_id": str(claim.job_id),
                    "severity": str(issue["severity"]),
                    "code": str(issue["code"]),
                    "entity_type": str(issue["entity_type"]),
                    "entity_key": str(issue["entity_key"]),
                    "message": str(issue["message"]),
                    "details": json.dumps(
                        issue.get("details") or {}, sort_keys=True, default=str
                    ),
                },
            )
        return run_id


# --------------------------------------------------------------------------
# Staged input + pure business
# --------------------------------------------------------------------------


def staged_input_path(import_root: Path, job_id: uuid.UUID, job_type: str) -> Path:
    suffix = ".json" if job_type == "seed" else ".inp"
    return Path(import_root) / f"{job_id}{suffix}"


def read_staged_input(
    import_root: Path,
    claim: NetworkJobClaim,
    *,
    max_bytes: int,
) -> bytes:
    """Read the staged payload, proving identity against the job fingerprint."""

    root = Path(import_root).expanduser()
    path = staged_input_path(root, claim.job_id, claim.job_type)
    try:
        resolved = path.resolve(strict=True)
        canonical_root = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise NetworkJobError(
            "STAGED_INPUT_MISSING",
            f"staged input for job {claim.job_id} ({claim.job_type}) is not readable",
            retryable=False,
        ) from exc
    if canonical_root != resolved and canonical_root not in resolved.parents:
        raise NetworkJobError(
            "STAGED_INPUT_MISSING",
            "staged input escapes HYDRAULIC_IMPORT_ROOT",
            retryable=False,
        )
    if resolved.stat().st_size > max_bytes:
        raise NetworkJobError(
            "STAGED_INPUT_TOO_LARGE",
            f"staged input for job {claim.job_id} exceeds {max_bytes} bytes",
            retryable=False,
        )
    payload = resolved.read_bytes()
    if hashlib.sha256(payload).hexdigest() != claim.request_fingerprint:
        raise NetworkJobError(
            "STAGED_INPUT_HASH_MISMATCH",
            f"staged input for job {claim.job_id} does not match request_fingerprint",
            retryable=False,
        )
    return payload


def seed_config_from_payload(payload: bytes) -> Any:
    from app.services.hydraulic.seed import SeedConfig

    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NetworkJobError(
            "SEED_MANIFEST_INVALID",
            "staged seed manifest is not valid UTF-8 JSON",
            retryable=False,
        ) from exc
    if not isinstance(document, dict):
        raise NetworkJobError(
            "SEED_MANIFEST_INVALID",
            "staged seed manifest must be a JSON object",
            retryable=False,
        )
    try:
        return SeedConfig(
            n=int(document["n"]),
            m=int(document["m"]),
            k=int(document["k"]),
            random_seed=int(document["random_seed"]),
        )
    except KeyError as exc:
        raise NetworkJobError(
            "SEED_MANIFEST_INVALID",
            f"staged seed manifest is missing {exc.args[0]!r}",
            retryable=False,
        ) from exc
    except (TypeError, ValueError) as exc:
        raise NetworkJobError(
            "SEED_MANIFEST_INVALID", f"staged seed manifest is invalid: {exc}"
        ) from exc


def topology_projection(network: Any) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Project a parsed SWMM network into ``validate_topology``'s shape.

    ``SwmmNetwork.to_topology_nodes()`` already emits the node projection
    (coordinates live in a side table, so the parser must do the joining), but
    ``to_topology_pipes()`` emits a pipe projection without the optional
    ``upstream_invert_elevation_m`` / ``downstream_invert_elevation_m``
    attributes that ``validate_topology`` reads before falling back to the node
    inverts.  Rather than patch another owner's adapter, the worker builds the
    pipe records itself and leaves the inverts unset, which is the documented
    "fall back to node inverts" case in ``TopologyPipe``.
    """

    from app.services.hydraulic.topology import TopologyPipe

    nodes = tuple(network.to_topology_nodes())
    pipes = tuple(
        TopologyPipe(
            stable_key=conduit.name,
            upstream_node_key=conduit.from_node,
            downstream_node_key=conduit.to_node,
            length_m=float(conduit.length),
        )
        for conduit in network.conduits
    )
    return nodes, pipes


def compute_outcome(claim: NetworkJobClaim, payload: bytes) -> JobOutcome:
    """Run the real business for one claim.  Pure: no database, no network.

    Keeping this step transaction-free is what lets a long parse finish without
    holding a row lock, and keeps the fenced completion a single commit.
    """

    if claim.job_type == "seed":
        from app.services.hydraulic.seed import generate_tree_loop_seed

        config = seed_config_from_payload(payload)
        # Named apart from the parsed SWMM network below; they are different
        # types and the seed branch returns before that one is built.
        seeded = generate_tree_loop_seed(config)
        canonical = seeded.canonical_json().encode("utf-8")
        return JobOutcome(
            kind="import",
            source_filename=f"{claim.job_id}.json",
            source_format="seed-v1",
            source_bytes=canonical,
            counts={
                "nodes": len(seeded.nodes),
                "pipes": len(seeded.pipes),
                "candidate_bindings": len(seeded.candidate_bindings),
            },
            result_sha256=hashlib.sha256(canonical).hexdigest(),
        )

    from app.services.hydraulic.importers.swmm import parse_swmm

    # strict=False so a validation job returns every issue in one report
    # instead of raising on the first error; an import job still fails closed
    # on the first error.
    network = parse_swmm(payload, strict=claim.job_type != "validate", filename=None)
    if claim.job_type == "import":
        # One authoritative counts definition: the parser's own property, the
        # same one the API preflight reports.  Re-deriving a three-key subset
        # here made the same import report two different key sets.
        return JobOutcome(
            kind="import",
            source_filename=f"{claim.job_id}.inp",
            source_format="swmm-inp-parser-v1",
            source_bytes=payload,
            counts=network.counts,
            result_sha256=claim.request_fingerprint,
        )

    from app.services.hydraulic.topology import validate_topology

    nodes, pipes = topology_projection(network)
    report = validate_topology(nodes, pipes)
    return JobOutcome(
        kind="validation",
        source_filename=f"{claim.job_id}.inp",
        source_format="swmm-inp-parser-v1",
        source_bytes=payload,
        counts=report.to_dict()["counts"],
        result_sha256=report.content_sha256,
        report=report,
    )


class NetworkJobWorker:
    """Claim, execute, and settle one network job per ``run_once`` call."""

    def __init__(
        self,
        repository_factory: Callable[[Any], SessionNetworkJobRepository],
        session_factory: Callable[[], Any],
        *,
        worker_id: str,
        import_root: Path,
        max_input_bytes: int,
        heartbeat_seconds: float = 10.0,
    ) -> None:
        if heartbeat_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._repository_factory = repository_factory
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._import_root = Path(import_root)
        self._max_input_bytes = int(max_input_bytes)
        self._heartbeat_seconds = float(heartbeat_seconds)
        self.completed = 0
        self.failed = 0

    async def run_once(self) -> bool:
        """Claim and settle at most one job.  True when the claim ran."""

        claim = await self._in_transaction(
            lambda repo: repo.claim_next(self._worker_id)
        )
        if claim is None:
            return True
        try:
            outcome = await self._attempt(claim)
        except NetworkJobError as exc:
            await self._settle_failure(claim, exc.error_code, str(exc), exc.retryable)
            return True
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            logger.exception("network job %s raised: %s", claim.job_id, exc)
            await self._settle_failure(
                claim, "RESULT_PERSIST_FAILED", type(exc).__name__, True
            )
            return True
        await self._in_transaction(lambda repo: repo.complete(claim, outcome))
        self.completed += 1
        logger.info(
            "network job %s (%s) completed attempt=%s fencing=%s",
            claim.job_id,
            claim.job_type,
            claim.attempt_no,
            claim.fencing_token,
        )
        return True

    async def _attempt(self, claim: NetworkJobClaim) -> JobOutcome:
        """Run the business step beside a lease-heartbeat renewer."""

        stop = asyncio.Event()
        renewer = asyncio.ensure_future(self._renew_lease(claim, stop))
        try:
            payload = read_staged_input(
                self._import_root, claim, max_bytes=self._max_input_bytes
            )
            await self._in_transaction(
                lambda repo: repo.set_phase(
                    claim,
                    phase="parsing" if claim.job_type == "import" else "validating",
                    progress=20,
                )
            )
            return await asyncio.to_thread(compute_outcome, claim, payload)
        finally:
            stop.set()
            renewer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewer

    async def _renew_lease(self, claim: NetworkJobClaim, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                await self._in_transaction(lambda repo: repo.heartbeat(claim))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "network job %s heartbeat failed: %s",
                    claim.job_id,
                    type(exc).__name__,
                )
                return

    async def _settle_failure(
        self, claim: NetworkJobClaim, code: str, message: str, retryable: bool
    ) -> None:
        self.failed += 1
        logger.warning(
            "network job %s attempt %s failed code=%s",
            claim.job_id,
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
        self, action: Callable[[SessionNetworkJobRepository], Awaitable[Any]]
    ) -> Any:
        async with open_session(self._session_factory) as session:
            async with session.begin():
                return await action(self._repository_factory(session))


def repository_factory_from_settings() -> Callable[[Any], SessionNetworkJobRepository]:
    """Build repositories bound to the frozen network-job Settings values."""

    from app.core.config import settings

    ladder_raw = settings.NETWORK_JOB_RETRY_BACKOFF_SECONDS
    ladder = (
        [int(value) for value in ladder_raw]
        if isinstance(ladder_raw, (list, tuple))
        else [int(part) for part in str(ladder_raw).split(",") if part.strip()]
    )

    def factory(session: Any) -> SessionNetworkJobRepository:
        return SessionNetworkJobRepository(
            session,
            lease_seconds=settings.NETWORK_JOB_LEASE_SECONDS,
            attempt_timeout_seconds=settings.NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS,
            max_attempts=settings.NETWORK_JOB_MAX_ATTEMPTS,
            retry_ladder=ladder,
        )

    return factory


def build_service(
    *,
    worker_id: str | None = None,
    config: WorkerRuntimeConfig | None = None,
    startup_report: StartupReport | None = None,
    probe: Callable[[], Awaitable[bool]] | None = None,
    session_factory: Callable[[], Any] | None = None,
) -> DurableWorkerService:
    """Assemble the real network-job service from settings and the database."""

    from app.core.config import settings
    from app.core.database import get_session_factory

    runtime = config or WorkerRuntimeConfig.from_environ(COMPONENT)
    identity = worker_id or f"network-job-{uuid.uuid4().hex[:12]}"
    worker = NetworkJobWorker(
        repository_factory_from_settings(),
        session_factory or get_session_factory,
        worker_id=identity,
        import_root=Path(settings.HYDRAULIC_IMPORT_ROOT),
        max_input_bytes=settings.NETWORK_INPUT_MAX_BYTES,
        heartbeat_seconds=float(settings.NETWORK_JOB_HEARTBEAT_SECONDS),
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


def ensure_writable_directory(path: Path) -> None:
    """Fail closed when the staging root cannot be created or written."""

    root = Path(path).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / ".network-job-worker.probe"
    marker.write_text("ok", encoding="utf-8")
    marker.unlink()


async def run_startup_checks_for_worker(*, import_root: Path) -> StartupReport:
    """Fail closed on database, schema, Redis, and the staging directory."""

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
                "import_root_writable",
                lambda: ensure_writable_directory(import_root),
                StartupFailure.staging_root_not_writable,
            ),
        ),
        component=COMPONENT,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.network_job_worker",
        description=(
            "Claim and run leased import/validate/seed network jobs until "
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
    """Async entry point: preflight, claim, and shut down gracefully."""

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    from app.core.config import settings

    configure_logging(args.log_level or settings.LOG_LEVEL, component=COMPONENT)
    try:
        config = WorkerRuntimeConfig.from_environ(COMPONENT)
    except ValueError as exc:
        logger.error("network job worker configuration invalid: %s", exc)
        return EXIT_CONFIG_INVALID

    report = await run_startup_checks_for_worker(
        import_root=Path(settings.HYDRAULIC_IMPORT_ROOT)
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
        logger.error(
            "network job worker startup rejected reason=%s detail=%s",
            report.reason.value,
            report.detail,
        )
        return EXIT_STARTUP_UNAVAILABLE
    logger.info(
        "network job worker startup checks passed checks=%s",
        json.dumps(dict(report.checks), sort_keys=True),
    )

    service = build_service(
        worker_id=args.worker_id, config=config, startup_report=report
    )
    return await drive_until_stopped(
        service, shutdown_grace_seconds=config.shutdown_grace_seconds
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Process entry point for ``python -m app.workers.network_job_worker``."""

    return run_worker_entrypoint(amain, argv)


__all__ = [
    "COMPONENT",
    "HEARTBEAT_KEY",
    "JOB_TYPES",
    "NETWORK_JOB_CLAIM_LOCK_KEY",
    "NON_RETRYABLE",
    "JobOutcome",
    "NetworkJobClaim",
    "NetworkJobError",
    "NetworkJobWorker",
    "SessionNetworkJobRepository",
    "amain",
    "build_parser",
    "build_service",
    "compute_outcome",
    "main",
    "read_staged_input",
    "repository_factory_from_settings",
    "run_startup_checks_for_worker",
    "seed_config_from_payload",
    "staged_input_path",
]


if __name__ == "__main__":
    raise SystemExit(main())
