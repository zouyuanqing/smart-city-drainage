"""Canonical durable task state, idempotency, queue, and recovery services.

This module deliberately owns control-plane behavior only.  Solver execution and
frame publication stay behind repository/finalizer boundaries.  The concrete SQL
repository uses PostgreSQL row locks and the two frozen worker lanes; the
in-memory repository is a deterministic executable specification used by worker
tests and local development.
"""

from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Generic, Protocol, TypeVar
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.hydraulic_models import (
    IdempotencyRecord,
    SimulationAttempt,
    SimulationFrameRegistration,
    SimulationResult,
    SimulationRun,
)
from app.services.hydraulic.solvers.base import to_persistence_phase

UTC = timezone.utc
IDEMPOTENCY_TTL = timedelta(hours=24)
TOTAL_SOLVER_CONCURRENCY = 2
NOWCAST_RESERVED_SLOTS = 1
MAX_RETRIES = 2
DEFAULT_LEASE_SECONDS = 30
DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 600
DEFAULT_GENERAL_BACKOFF_SECONDS = (10, 60)
DEFAULT_NOWCAST_BACKOFF_SECONDS = (5, 15)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

T = TypeVar("T")


class TaskStateError(RuntimeError):
    """Base durable-task control-plane error."""

    code = "TASK_STATE_ERROR"


class TaskNotFound(TaskStateError):
    code = "TASK_NOT_FOUND"


class InvalidTaskTransition(TaskStateError):
    code = "INVALID_TASK_TRANSITION"


class StaleTaskFence(TaskStateError):
    code = "LEASE_EXPIRED"


class TaskNotCancellable(TaskStateError):
    code = "SIMULATION_NOT_CANCELLABLE"


class IdempotencyConflict(TaskStateError):
    code = "IDEMPOTENCY_KEY_REUSED"


class IdempotencyIncomplete(TaskStateError):
    code = "IDEMPOTENCY_RECORD_INCOMPLETE"


class TaskKind(str, Enum):
    simulation = "simulation"
    network_job = "network_job"
    nowcast_run = "nowcast_run"
    calibration_run = "calibration_run"


class TaskLane(str, Enum):
    general = "general"
    nowcast = "nowcast"
    independent = "independent"


class TaskStatus(str, Enum):
    queued = "queued"
    leased = "leased"
    running = "running"
    retry_wait = "retry_wait"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"
    timed_out = "timed_out"


class TaskAttemptStatus(str, Enum):
    running = "running"
    discarded = "discarded"
    published = "published"
    cleanup_pending = "cleanup_pending"


class TaskEvent(str, Enum):
    claim = "claim"
    heartbeat = "heartbeat"
    request_cancel = "request_cancel"
    progress = "progress"
    retryable_failure = "retryable_failure"
    failure = "failure"
    timeout = "timeout"
    complete = "complete"
    cancel_complete = "cancel_complete"
    expire_retry = "expire_retry"


RETRYABLE_ERROR_CODES: Mapping[TaskKind, frozenset[str]] = {
    TaskKind.simulation: frozenset(
        {
            "LEASE_EXPIRED",
            "NUMERICAL_UNSTABLE",
            "NUMERICAL_STABILITY_LIMIT",
            "RESULT_PERSIST_FAILED",
        }
    ),
    TaskKind.network_job: frozenset(
        {"LEASE_EXPIRED", "TRANSIENT_IMPORT_ERROR", "RESULT_PERSIST_FAILED"}
    ),
    TaskKind.nowcast_run: frozenset({"LEASE_EXPIRED", "RESULT_PERSIST_FAILED"}),
    TaskKind.calibration_run: frozenset({"LEASE_EXPIRED", "RESULT_PERSIST_FAILED"}),
}

KIND_LANES: Mapping[TaskKind, TaskLane] = {
    TaskKind.simulation: TaskLane.general,
    TaskKind.nowcast_run: TaskLane.nowcast,
    TaskKind.network_job: TaskLane.independent,
    TaskKind.calibration_run: TaskLane.independent,
}

TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.completed,
        TaskStatus.failed,
        TaskStatus.timed_out,
        TaskStatus.cancelled,
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _enum_value(value: str | Enum) -> str:
    return str(value.value) if isinstance(value, Enum) else str(value)


def _validate_idempotency_key(value: str) -> None:
    if not isinstance(value, str) or not 8 <= len(value) <= 128:
        raise ValueError("idempotency key length must be between 8 and 128")


def _validate_fingerprint(value: str) -> None:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("request fingerprint must be 64 lowercase hexadecimal chars")


@dataclass(frozen=True, slots=True)
class TaskAttempt:
    id: UUID
    task_id: UUID
    attempt_no: int
    fencing_token: int
    status: TaskAttemptStatus
    started_at: datetime
    workspace_ref: str | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class TaskState:
    id: UUID
    kind: TaskKind
    status: TaskStatus
    progress: int
    attempt: int
    max_attempts: int
    fencing_token: int
    created_at: datetime
    updated_at: datetime
    idempotency_scope: str
    idempotency_key: str
    user_id: UUID
    idempotency_generation: int
    request_fingerprint: str
    phase: str = "accepted"
    slot_id: int | None = None
    active_attempt_id: UUID | None = None
    workspace_ref: str | None = None
    lease_owner: str | None = None
    lease_token: UUID | None = None
    lease_until: datetime | None = None
    deadline_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    next_attempt_at: datetime | None = None
    retry_backoff_seconds: int | None = None
    last_retry_error_code: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    result_committed: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def lane(self) -> TaskLane:
        return KIND_LANES[self.kind]

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass(frozen=True, slots=True)
class TaskClaim:
    task_id: UUID
    attempt_id: UUID
    attempt_no: int
    fencing_token: int
    lease_owner: str
    lease_token: UUID
    lease_until: datetime
    deadline_at: datetime
    slot_id: int | None
    kind: TaskKind


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    task_id: UUID
    attempt_id: UUID
    attempt_no: int
    workspace_ref: str | None
    next_attempt_at: datetime | None
    status: TaskStatus


@dataclass(frozen=True, slots=True)
class IdempotencyResource:
    resource_id: UUID
    response_status: int
    response_body: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class IdempotencyOutcome(Generic[T]):
    record_id: UUID
    resource: T
    created: bool
    generation: int


@dataclass(frozen=True, slots=True)
class _IdempotencyEntry:
    record_id: UUID
    scope: str
    key: str
    user_id: UUID
    generation: int
    request_fingerprint: str
    resource: Any
    response_status: int
    response_body: Mapping[str, Any]
    created_at: datetime
    expires_at: datetime
    retired_at: datetime | None = None


class DurableJobStateMachine:
    """The single transition policy shared by all hydraulic durable jobs."""

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        general_backoff_seconds: tuple[int, int] = DEFAULT_GENERAL_BACKOFF_SECONDS,
        nowcast_backoff_seconds: tuple[int, int] = DEFAULT_NOWCAST_BACKOFF_SECONDS,
    ) -> None:
        if max_attempts < 2 or max_attempts > 3:
            raise ValueError(
                "max_attempts must allow exactly two retries and cap at three"
            )
        for label, values in {
            "general": general_backoff_seconds,
            "nowcast": nowcast_backoff_seconds,
        }.items():
            if len(values) != max_attempts - 1 or any(value <= 0 for value in values):
                raise ValueError(
                    f"{label} backoff must contain one positive value per retry"
                )
            if any(
                current >= following for current, following in zip(values, values[1:])
            ):
                raise ValueError(f"{label} backoff must be strictly increasing")
        self.max_attempts = max_attempts
        self._backoff = {
            TaskKind.nowcast_run: nowcast_backoff_seconds,
            TaskKind.simulation: general_backoff_seconds,
            TaskKind.network_job: general_backoff_seconds,
            TaskKind.calibration_run: general_backoff_seconds,
        }

    def initial(
        self,
        *,
        task_id: UUID,
        kind: TaskKind,
        user_id: UUID,
        idempotency_scope: str,
        idempotency_key: str,
        idempotency_generation: int,
        request_fingerprint: str,
        now: datetime | None = None,
        deadline_at: datetime | None = None,
    ) -> TaskState:
        _validate_idempotency_key(idempotency_key)
        _validate_fingerprint(request_fingerprint)
        current = _as_utc(now or _utc_now())
        if kind is TaskKind.nowcast_run and deadline_at is None:
            raise ValueError("nowcast tasks require a fixed deadline")
        if deadline_at is not None and _as_utc(deadline_at) <= current:
            raise ValueError("task deadline must be in the future")
        return TaskState(
            id=task_id,
            kind=kind,
            status=TaskStatus.queued,
            progress=0,
            attempt=0,
            max_attempts=self.max_attempts,
            fencing_token=0,
            created_at=current,
            updated_at=current,
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
            user_id=user_id,
            idempotency_generation=idempotency_generation,
            request_fingerprint=request_fingerprint,
            deadline_at=_as_utc(deadline_at) if deadline_at is not None else None,
        )

    def transition(
        self,
        state: TaskState,
        event: TaskEvent,
        *,
        now: datetime | None = None,
        worker_id: str | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        attempt_timeout_seconds: int = DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
        attempt_id: UUID | None = None,
        slot_id: int | None = None,
        progress: int | None = None,
        phase: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        result_committed: bool = False,
        fencing_token: int | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        if event is TaskEvent.claim:
            return self._claim(
                state,
                current,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                attempt_timeout_seconds=attempt_timeout_seconds,
                attempt_id=attempt_id,
                slot_id=slot_id,
            )
        if event is TaskEvent.heartbeat:
            return self._heartbeat(
                state,
                current,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
                fencing_token=fencing_token,
            )
        if event is TaskEvent.request_cancel:
            return self._request_cancel(state, current)
        if event is TaskEvent.progress:
            return self._progress(
                state,
                current,
                progress=progress,
                phase=phase,
            )
        if event in {TaskEvent.retryable_failure, TaskEvent.failure}:
            return self._failure(
                state,
                current,
                error_code=error_code,
                error_message=error_message,
                force_terminal=event is TaskEvent.failure,
            )
        if event is TaskEvent.timeout:
            return self._timeout(state, current)
        if event is TaskEvent.complete:
            return self._complete(
                state,
                current,
                result_committed=result_committed,
                fencing_token=fencing_token,
            )
        if event is TaskEvent.cancel_complete:
            return self._cancel_complete(state, current)
        if event is TaskEvent.expire_retry:
            return self._expire_retry(state, current)
        raise InvalidTaskTransition(f"unsupported task event: {event.value}")

    def _claim(
        self,
        state: TaskState,
        now: datetime,
        *,
        worker_id: str | None,
        lease_seconds: int,
        attempt_timeout_seconds: int,
        attempt_id: UUID | None,
        slot_id: int | None,
    ) -> TaskState:
        if not worker_id or not worker_id.strip():
            raise ValueError("worker_id is required for claim")
        if lease_seconds <= 0 or attempt_timeout_seconds <= 0:
            raise ValueError("lease and attempt timeout must be positive")
        due = state.status is TaskStatus.queued or (
            state.status is TaskStatus.retry_wait
            and state.next_attempt_at is not None
            and state.next_attempt_at <= now
        )
        if not due:
            raise InvalidTaskTransition(f"{state.status.value} task is not due")
        if state.attempt >= state.max_attempts:
            raise InvalidTaskTransition("attempt budget is exhausted")
        if state.kind is TaskKind.nowcast_run:
            if state.deadline_at is None or now >= state.deadline_at:
                raise InvalidTaskTransition("nowcast deadline has already expired")
        attempt_no = state.attempt + 1
        lease_token = uuid.uuid4()
        deadline = state.deadline_at
        if state.kind is not TaskKind.nowcast_run:
            deadline = now + timedelta(seconds=attempt_timeout_seconds)
        return replace(
            state,
            status=TaskStatus.leased,
            phase="solving" if state.kind is TaskKind.simulation else "accepted",
            attempt=attempt_no,
            fencing_token=state.fencing_token + 1,
            active_attempt_id=attempt_id or uuid.uuid4(),
            lease_owner=worker_id,
            lease_token=lease_token,
            lease_until=now + timedelta(seconds=lease_seconds),
            deadline_at=deadline,
            slot_id=slot_id,
            next_attempt_at=None,
            retry_backoff_seconds=None,
            last_retry_error_code=None,
            error_code=None,
            error_message=None,
            started_at=state.started_at or now,
            updated_at=now,
        )

    def _heartbeat(
        self,
        state: TaskState,
        now: datetime,
        *,
        worker_id: str | None,
        lease_seconds: int,
        fencing_token: int | None,
    ) -> TaskState:
        if state.status not in {TaskStatus.leased, TaskStatus.running}:
            raise InvalidTaskTransition("only an active lease can heartbeat")
        self._assert_fence(state, fencing_token)
        if worker_id is not None and state.lease_owner != worker_id:
            raise StaleTaskFence("heartbeat owner does not match the active lease")
        if state.lease_until is None or state.lease_until <= now:
            raise StaleTaskFence("lease already expired")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        return replace(
            state,
            status=TaskStatus.running,
            lease_until=now + timedelta(seconds=lease_seconds),
            updated_at=now,
        )

    def _request_cancel(self, state: TaskState, now: datetime) -> TaskState:
        if state.status is TaskStatus.cancelling:
            return state
        if state.status in TERMINAL_STATUSES:
            raise TaskNotCancellable(f"{state.status.value} tasks cannot be cancelled")
        if state.status not in {
            TaskStatus.queued,
            TaskStatus.leased,
            TaskStatus.running,
            TaskStatus.retry_wait,
        }:
            raise InvalidTaskTransition("current state cannot request cancellation")
        return replace(
            state,
            status=TaskStatus.cancelling,
            cancel_requested_at=state.cancel_requested_at or now,
            next_attempt_at=None,
            retry_backoff_seconds=None,
            last_retry_error_code=None,
            lease_owner=None,
            lease_token=None,
            lease_until=None,
            slot_id=None,
            updated_at=now,
        )

    def _progress(
        self,
        state: TaskState,
        now: datetime,
        *,
        progress: int | None,
        phase: str | None = None,
    ) -> TaskState:
        if state.status is not TaskStatus.running:
            raise InvalidTaskTransition("progress can only advance while running")
        if progress is None or not 0 <= progress <= 99:
            raise ValueError("running progress must be between 0 and 99")
        if progress < state.progress:
            raise InvalidTaskTransition("progress must be monotonic")
        return replace(
            state,
            progress=progress,
            phase=state.phase if phase is None else to_persistence_phase(phase),
            updated_at=now,
        )

    def _failure(
        self,
        state: TaskState,
        now: datetime,
        *,
        error_code: str | None,
        error_message: str | None,
        force_terminal: bool,
    ) -> TaskState:
        if state.status not in {
            TaskStatus.leased,
            TaskStatus.running,
            TaskStatus.retry_wait,
        }:
            raise InvalidTaskTransition("only active/retry-wait tasks can fail")
        if not error_code or not error_code.strip():
            raise ValueError("failure error_code is required")
        retryable = error_code in RETRYABLE_ERROR_CODES[state.kind]
        exhausted = state.attempt >= state.max_attempts
        if not force_terminal and retryable and not exhausted:
            delay = self._backoff[state.kind][state.attempt - 1]
            next_attempt = now + timedelta(seconds=delay)
            if (
                state.kind is TaskKind.nowcast_run
                and state.deadline_at is not None
                and (now >= state.deadline_at or next_attempt >= state.deadline_at)
            ):
                return self._timed_out_state(state, now, next_attempt)
            return replace(
                state,
                status=TaskStatus.retry_wait,
                next_attempt_at=next_attempt,
                retry_backoff_seconds=delay,
                last_retry_error_code=error_code,
                error_code=None,
                error_message=None,
                lease_owner=None,
                lease_token=None,
                lease_until=None,
                slot_id=None,
                active_attempt_id=None,
                workspace_ref=None,
                deadline_at=(
                    None
                    if state.kind is not TaskKind.nowcast_run
                    else state.deadline_at
                ),
                updated_at=now,
            )
        if error_code == "SIMULATION_TIMEOUT":
            raise InvalidTaskTransition("timeout must use the timed_out transition")
        return replace(
            state,
            status=TaskStatus.failed,
            error_code=error_code,
            error_message=error_message,
            next_attempt_at=None,
            retry_backoff_seconds=None,
            last_retry_error_code=None,
            lease_owner=None,
            lease_token=None,
            lease_until=None,
            slot_id=None,
            active_attempt_id=None,
            workspace_ref=None,
            finished_at=now,
            updated_at=now,
        )

    def _timeout(self, state: TaskState, now: datetime) -> TaskState:
        if state.status is not TaskStatus.running:
            raise InvalidTaskTransition("only a running attempt can time out")
        return self._timed_out_state(state, now, None)

    def _timed_out_state(
        self, state: TaskState, now: datetime, next_attempt: datetime | None
    ) -> TaskState:
        return replace(
            state,
            status=TaskStatus.timed_out,
            error_code="SIMULATION_TIMEOUT",
            error_message=(
                "retry would cross the fixed nowcast deadline"
                if next_attempt is not None
                else "attempt deadline exceeded"
            ),
            next_attempt_at=None,
            retry_backoff_seconds=None,
            last_retry_error_code=None,
            lease_owner=None,
            lease_token=None,
            lease_until=None,
            slot_id=None,
            active_attempt_id=None,
            workspace_ref=None,
            finished_at=now,
            updated_at=now,
        )

    def _complete(
        self,
        state: TaskState,
        now: datetime,
        *,
        result_committed: bool,
        fencing_token: int | None,
    ) -> TaskState:
        if state.status is not TaskStatus.running:
            raise InvalidTaskTransition(
                "cancellation wins unless the completed result commits first"
            )
        self._assert_fence(state, fencing_token)
        if not result_committed:
            raise InvalidTaskTransition("completion requires committed result evidence")
        return replace(
            state,
            status=TaskStatus.completed,
            phase="finalizing",
            progress=100,
            result_committed=True,
            error_code=None,
            error_message=None,
            lease_owner=None,
            lease_token=None,
            lease_until=None,
            slot_id=None,
            finished_at=now,
            updated_at=now,
        )

    def _cancel_complete(self, state: TaskState, now: datetime) -> TaskState:
        if state.status is not TaskStatus.cancelling:
            raise InvalidTaskTransition("only cancelling tasks can become cancelled")
        return replace(
            state,
            status=TaskStatus.cancelled,
            error_code="SIMULATION_CANCELLED",
            error_message="cancellation requested by user",
            next_attempt_at=None,
            retry_backoff_seconds=None,
            last_retry_error_code=None,
            lease_owner=None,
            lease_token=None,
            lease_until=None,
            slot_id=None,
            active_attempt_id=None,
            workspace_ref=None,
            finished_at=now,
            updated_at=now,
        )

    def _expire_retry(self, state: TaskState, now: datetime) -> TaskState:
        if state.kind is not TaskKind.nowcast_run:
            raise InvalidTaskTransition(
                "only nowcast retry_wait can expire before claim"
            )
        if (
            state.status is not TaskStatus.retry_wait
            or state.deadline_at is None
            or now < state.deadline_at
            or (
                state.next_attempt_at is not None
                and state.next_attempt_at >= state.deadline_at
            )
        ):
            raise InvalidTaskTransition("nowcast retry is not due for timeout")
        return self._timed_out_state(state, now, state.next_attempt_at)

    @staticmethod
    def _assert_fence(state: TaskState, fencing_token: int | None) -> None:
        if fencing_token is None or fencing_token != state.fencing_token:
            raise StaleTaskFence("fencing token is stale")


class InMemoryIdempotencyRegistry:
    """24-hour idempotency registry with generation rollover and fingerprint checks."""

    def __init__(self, *, ttl: timedelta = IDEMPOTENCY_TTL) -> None:
        if ttl <= timedelta(0):
            raise ValueError("idempotency TTL must be positive")
        self._ttl = ttl
        self._entries: dict[tuple[str, str, UUID], _IdempotencyEntry] = {}
        self._lock = threading.RLock()

    async def execute(
        self,
        *,
        scope: str,
        key: str,
        user_id: UUID,
        request_fingerprint: str,
        factory: Callable[[UUID, datetime, datetime], T],
        now: datetime | None = None,
    ) -> IdempotencyOutcome[T]:
        _validate_idempotency_key(key)
        _validate_fingerprint(request_fingerprint)
        current = _as_utc(now or _utc_now())
        identity = (scope, key, user_id)
        with self._lock:
            entry = self._entries.get(identity)
            if entry is not None and entry.expires_at <= current:
                entry = replace(entry, retired_at=entry.expires_at)
                self._entries[identity] = entry
                entry = None
            if entry is not None:
                if entry.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict(
                        "idempotency key was already used with a different request"
                    )
                return IdempotencyOutcome(
                    record_id=entry.record_id,
                    resource=entry.resource,
                    created=False,
                    generation=entry.generation,
                )

            record_id = uuid.uuid4()
            expires_at = current + self._ttl
            resource = factory(record_id, current, expires_at)
            generation = 1
            if identity in self._entries:
                generation = self._entries[identity].generation + 1
            self._entries[identity] = _IdempotencyEntry(
                record_id=record_id,
                scope=scope,
                key=key,
                user_id=user_id,
                generation=generation,
                request_fingerprint=request_fingerprint,
                resource=resource,
                response_status=202,
                response_body={},
                created_at=current,
                expires_at=expires_at,
            )
            return IdempotencyOutcome(
                record_id=record_id,
                resource=resource,
                created=True,
                generation=generation,
            )


class TaskRepository(Protocol):
    """Control-plane contract consumed by :mod:`simulation_worker`."""

    async def claim_next(
        self,
        worker_id: str,
        *,
        kind: TaskKind,
        now: datetime | None = None,
    ) -> TaskClaim | None: ...

    async def heartbeat(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState: ...

    async def update_progress(
        self,
        claim: TaskClaim,
        progress: int,
        *,
        phase: str | None = None,
        now: datetime | None = None,
    ) -> TaskState: ...

    async def request_cancel(
        self,
        task_id: UUID,
        *,
        now: datetime | None = None,
    ) -> TaskState: ...

    async def mark_cancelled(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState: ...

    async def mark_timeout(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState: ...

    async def record_failure(
        self,
        claim: TaskClaim,
        error_code: str,
        *,
        error_message: str | None = None,
        retryable: bool | None = None,
        now: datetime | None = None,
    ) -> TaskState: ...

    async def complete(
        self,
        claim: TaskClaim,
        *,
        result_committed: bool,
        now: datetime | None = None,
    ) -> TaskState: ...

    async def recover_expired(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[RecoveryAction, ...]: ...

    async def get(self, task_id: UUID) -> TaskState: ...


class InMemoryTaskRepository(TaskRepository):
    """Executable task store with durable-state semantics inside one process."""

    def __init__(
        self,
        *,
        state_machine: DurableJobStateMachine | None = None,
        idempotency: InMemoryIdempotencyRegistry | None = None,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        attempt_timeout_seconds: int = DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
    ) -> None:
        self.state_machine = state_machine or DurableJobStateMachine()
        self.idempotency = idempotency or InMemoryIdempotencyRegistry()
        self.lease_seconds = lease_seconds
        self.attempt_timeout_seconds = attempt_timeout_seconds
        self._tasks: dict[UUID, TaskState] = {}
        self._attempts: dict[UUID, TaskAttempt] = {}
        self._slot_owners: dict[int, tuple[str, TaskLane, UUID]] = {
            1: ("general", TaskLane.general, UUID(int=0)),
            2: ("nowcast", TaskLane.nowcast, UUID(int=0)),
        }
        self._active_slots: dict[int, TaskClaim] = {}
        self._lock = threading.RLock()

    async def submit(
        self,
        *,
        kind: TaskKind,
        user_id: UUID,
        idempotency_key: str,
        request_fingerprint: str,
        scope: str | None = None,
        task_id: UUID | None = None,
        deadline_at: datetime | None = None,
        now: datetime | None = None,
    ) -> IdempotencyOutcome[TaskState]:
        current = _as_utc(now or _utc_now())
        effective_scope = scope or f"{kind.value}.submit"

        def create(
            record_id: UUID,
            created_at: datetime,
            expires_at: datetime,
        ) -> TaskState:
            del record_id, expires_at
            state = self.state_machine.initial(
                task_id=task_id or uuid.uuid4(),
                kind=kind,
                user_id=user_id,
                idempotency_scope=effective_scope,
                idempotency_key=idempotency_key,
                idempotency_generation=1,
                request_fingerprint=request_fingerprint,
                now=created_at,
                deadline_at=deadline_at,
            )
            with self._lock:
                if state.id in self._tasks:
                    raise IdempotencyConflict("task ID already exists")
                self._tasks[state.id] = state
            return state

        outcome = await self.idempotency.execute(
            scope=effective_scope,
            key=idempotency_key,
            user_id=user_id,
            request_fingerprint=request_fingerprint,
            factory=create,
            now=current,
        )
        state = outcome.resource
        if outcome.created or state.idempotency_generation != outcome.generation:
            state = replace(
                state,
                idempotency_generation=outcome.generation,
            )
            with self._lock:
                self._tasks[state.id] = state
        return IdempotencyOutcome(
            record_id=outcome.record_id,
            resource=state,
            created=outcome.created,
            generation=outcome.generation,
        )

    async def attach_workspace(
        self,
        claim: TaskClaim,
        workspace_ref: str,
    ) -> TaskState:
        with self._lock:
            state = self._require_claim(claim)
            attempt = self._attempts[claim.attempt_id]
            self._attempts[claim.attempt_id] = replace(
                attempt, workspace_ref=workspace_ref
            )
            updated = replace(
                state,
                workspace_ref=workspace_ref,
                updated_at=_utc_now(),
            )
            self._tasks[state.id] = updated
            return updated

    async def claim_next(
        self,
        worker_id: str,
        *,
        now: datetime | None = None,
        kind: TaskKind = TaskKind.simulation,
    ) -> TaskClaim | None:
        current = _as_utc(now or _utc_now())
        with self._lock:
            candidates = sorted(
                (
                    state
                    for state in self._tasks.values()
                    if (kind is None or state.kind is kind)
                    and (
                        state.status is TaskStatus.queued
                        or (
                            state.status is TaskStatus.retry_wait
                            and state.next_attempt_at is not None
                            and state.next_attempt_at <= current
                        )
                    )
                ),
                key=lambda item: (item.created_at, str(item.id)),
            )
            for state in candidates:
                slot_id = self._slot_for(state.kind)
                if slot_id is not None and slot_id in self._active_slots:
                    continue
                if state.kind is TaskKind.nowcast_run:
                    if state.deadline_at is None or current >= state.deadline_at:
                        if state.status is TaskStatus.retry_wait:
                            self._tasks[state.id] = self.state_machine.transition(
                                state,
                                TaskEvent.expire_retry,
                                now=current,
                            )
                        continue
                attempt_id = uuid.uuid4()
                updated = self.state_machine.transition(
                    state,
                    TaskEvent.claim,
                    now=current,
                    worker_id=worker_id,
                    lease_seconds=self.lease_seconds,
                    attempt_timeout_seconds=self.attempt_timeout_seconds,
                    attempt_id=attempt_id,
                    slot_id=slot_id,
                )
                self._tasks[state.id] = updated
                self._attempts[attempt_id] = TaskAttempt(
                    id=attempt_id,
                    task_id=state.id,
                    attempt_no=updated.attempt,
                    fencing_token=updated.fencing_token,
                    status=TaskAttemptStatus.running,
                    started_at=current,
                )
                assert updated.lease_token is not None
                assert updated.lease_until is not None
                assert updated.deadline_at is not None
                claim = TaskClaim(
                    task_id=state.id,
                    attempt_id=attempt_id,
                    attempt_no=updated.attempt,
                    fencing_token=updated.fencing_token,
                    lease_owner=worker_id,
                    lease_token=updated.lease_token,
                    lease_until=updated.lease_until,
                    deadline_at=updated.deadline_at,
                    slot_id=slot_id,
                    kind=state.kind,
                )
                if slot_id is not None:
                    self._active_slots[slot_id] = claim
                return claim
            return None

    async def heartbeat(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        with self._lock:
            state = self._require_claim(claim)
            updated = self.state_machine.transition(
                state,
                TaskEvent.heartbeat,
                now=current,
                worker_id=claim.lease_owner,
                lease_seconds=self.lease_seconds,
                fencing_token=claim.fencing_token,
            )
            self._tasks[state.id] = updated
            assert updated.lease_until is not None
            refreshed = replace(claim, lease_until=updated.lease_until)
            if claim.slot_id is not None:
                self._active_slots[claim.slot_id] = refreshed
            return updated

    async def update_progress(
        self,
        claim: TaskClaim,
        progress: int,
        *,
        phase: str | None = None,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        with self._lock:
            state = self._require_claim(claim)
            updated = self.state_machine.transition(
                state,
                TaskEvent.progress,
                now=current,
                progress=progress,
                phase=phase,
            )
            self._tasks[state.id] = updated
            return updated

    async def request_cancel(
        self,
        task_id: UUID,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        with self._lock:
            state = self._require_task(task_id)
            updated = self.state_machine.transition(
                state, TaskEvent.request_cancel, now=current
            )
            if state.status in {TaskStatus.queued, TaskStatus.retry_wait}:
                updated = self.state_machine.transition(
                    updated,
                    TaskEvent.cancel_complete,
                    now=current,
                )
            self._tasks[task_id] = updated
            if state.slot_id is not None:
                self._active_slots.pop(state.slot_id, None)
            return updated

    async def mark_cancelled(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        with self._lock:
            state = self._require_claim(claim)
            updated = self.state_machine.transition(
                state, TaskEvent.cancel_complete, now=current
            )
            self._tasks[state.id] = updated
            self._discard_attempt(claim.attempt_id, current)
            self._release_slot(claim.slot_id)
            return updated

    async def mark_timeout(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        with self._lock:
            state = self._require_claim(claim)
            updated = self.state_machine.transition(
                state, TaskEvent.timeout, now=current
            )
            self._tasks[state.id] = updated
            self._discard_attempt(claim.attempt_id, current)
            self._release_slot(claim.slot_id)
            return updated

    async def record_failure(
        self,
        claim: TaskClaim,
        error_code: str,
        *,
        error_message: str | None = None,
        retryable: bool | None = None,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        with self._lock:
            state = self._require_claim(claim)
            event = TaskEvent.failure
            if (
                retryable is not False
                and error_code in RETRYABLE_ERROR_CODES[state.kind]
            ):
                event = TaskEvent.retryable_failure
            updated = self.state_machine.transition(
                state,
                event,
                now=current,
                error_code=error_code,
                error_message=error_message,
            )
            self._tasks[state.id] = updated
            self._discard_attempt(claim.attempt_id, current, error_code, error_message)
            self._release_slot(claim.slot_id)
            return updated

    async def complete(
        self,
        claim: TaskClaim,
        *,
        result_committed: bool,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        with self._lock:
            state = self._require_claim(claim)
            updated = self.state_machine.transition(
                state,
                TaskEvent.complete,
                now=current,
                result_committed=result_committed,
                fencing_token=claim.fencing_token,
            )
            self._tasks[state.id] = updated
            self._attempts[claim.attempt_id] = replace(
                self._attempts[claim.attempt_id],
                status=TaskAttemptStatus.published,
                finished_at=current,
            )
            self._release_slot(claim.slot_id)
            return updated

    async def recover_expired(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[RecoveryAction, ...]:
        current = _as_utc(now or _utc_now())
        recovered: list[RecoveryAction] = []
        with self._lock:
            expired = [
                state
                for state in self._tasks.values()
                if state.lease_until is not None
                and state.lease_until <= current
                and state.status in {TaskStatus.leased, TaskStatus.running}
            ]
            for state in sorted(expired, key=lambda item: str(item.id)):
                if (
                    state.lease_token is None
                    or state.active_attempt_id is None
                    or state.lease_until is None
                ):
                    continue
                claim = TaskClaim(
                    task_id=state.id,
                    attempt_id=state.active_attempt_id,
                    attempt_no=state.attempt,
                    fencing_token=state.fencing_token,
                    lease_owner=state.lease_owner or "expired-worker",
                    lease_token=state.lease_token,
                    lease_until=state.lease_until,
                    deadline_at=state.deadline_at or current,
                    slot_id=state.slot_id,
                    kind=state.kind,
                )
                attempt = self._attempts[state.active_attempt_id]
                updated = self.state_machine.transition(
                    state,
                    TaskEvent.retryable_failure,
                    now=current,
                    error_code="LEASE_EXPIRED",
                    error_message="worker lease expired before completion",
                )
                self._tasks[state.id] = updated
                self._discard_attempt(
                    claim.attempt_id,
                    current,
                    "LEASE_EXPIRED",
                    "worker lease expired before completion",
                )
                self._release_slot(claim.slot_id)
                recovered.append(
                    RecoveryAction(
                        task_id=state.id,
                        attempt_id=claim.attempt_id,
                        attempt_no=claim.attempt_no,
                        workspace_ref=attempt.workspace_ref,
                        next_attempt_at=updated.next_attempt_at,
                        status=updated.status,
                    )
                )
            return tuple(recovered)

    async def get(self, task_id: UUID) -> TaskState:
        with self._lock:
            return self._require_task(task_id)

    def get_attempt(self, attempt_id: UUID) -> TaskAttempt:
        with self._lock:
            try:
                return self._attempts[attempt_id]
            except KeyError as exc:
                raise TaskNotFound(f"attempt {attempt_id} does not exist") from exc

    def slot_snapshot(self) -> Mapping[int, TaskClaim | None]:
        with self._lock:
            return {
                slot_id: self._active_slots.get(slot_id)
                for slot_id in self._slot_owners
            }

    def _slot_for(self, kind: TaskKind) -> int | None:
        lane = KIND_LANES[kind]
        if lane is TaskLane.general:
            return 1
        if lane is TaskLane.nowcast:
            return 2
        return None

    def _require_task(self, task_id: UUID) -> TaskState:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise TaskNotFound(f"task {task_id} does not exist") from exc

    def _require_claim(self, claim: TaskClaim) -> TaskState:
        state = self._require_task(claim.task_id)
        if state.fencing_token != claim.fencing_token:
            raise StaleTaskFence("claim no longer owns the task lease")
        if (
            state.status is not TaskStatus.cancelling
            and state.lease_token != claim.lease_token
        ):
            raise StaleTaskFence("claim no longer owns the task lease")
        return state

    def _discard_attempt(
        self,
        attempt_id: UUID,
        now: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        attempt = self._attempts[attempt_id]
        self._attempts[attempt_id] = replace(
            attempt,
            status=TaskAttemptStatus.discarded,
            finished_at=now,
            error_code=error_code,
            error_message=error_message,
        )

    def _release_slot(self, slot_id: int | None) -> None:
        if slot_id is not None:
            self._active_slots.pop(slot_id, None)


class SqlAlchemyIdempotencyService:
    """PostgreSQL idempotency coordinator; the caller owns the transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        ttl: timedelta = IDEMPOTENCY_TTL,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("idempotency TTL must be positive")
        self.session = session
        self.ttl = ttl

    async def execute(
        self,
        *,
        scope: str,
        key: str,
        user_id: UUID,
        request_fingerprint: str,
        factory: Callable[[UUID, datetime, datetime], Awaitable[IdempotencyResource]],
        now: datetime | None = None,
    ) -> IdempotencyOutcome[IdempotencyResource]:
        _validate_idempotency_key(key)
        _validate_fingerprint(request_fingerprint)
        current = _as_utc(now or _utc_now())
        identity = json.dumps(
            [scope, key, str(user_id)],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        bind = self.session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                {"identity": identity},
            )

        statement = (
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.idempotency_key == key,
                IdempotencyRecord.user_id == user_id,
                IdempotencyRecord.retired_at.is_(None),
            )
            .with_for_update()
        )
        record = (await self.session.execute(statement)).scalar_one_or_none()
        if record is not None and _as_utc(record.expires_at) > current:
            if record.request_fingerprint != request_fingerprint:
                raise IdempotencyConflict(
                    "idempotency key was already used with a different request"
                )
            if record.resource_id is None or record.response_status is None:
                raise IdempotencyIncomplete(
                    "idempotency record has no committed resource response"
                )
            return IdempotencyOutcome(
                record_id=record.id,
                resource=IdempotencyResource(
                    resource_id=record.resource_id,
                    response_status=record.response_status,
                    response_body=record.response_body or {},
                ),
                created=False,
                generation=record.generation,
            )

        generation = 1
        if record is not None:
            record.retired_at = record.expires_at
            generation = record.generation + 1
            await self.session.flush()

        record_id = uuid.uuid4()
        expires_at = current + self.ttl
        new_record = IdempotencyRecord(
            id=record_id,
            scope=scope,
            idempotency_key=key,
            user_id=user_id,
            generation=generation,
            request_fingerprint=request_fingerprint,
            created_at=current,
            expires_at=expires_at,
        )
        self.session.add(new_record)
        await self.session.flush()
        resource = await factory(record_id, current, expires_at)
        new_record.resource_id = resource.resource_id
        new_record.response_status = resource.response_status
        new_record.response_body = dict(resource.response_body)
        await self.session.flush()
        return IdempotencyOutcome(
            record_id=record_id,
            resource=resource,
            created=True,
            generation=generation,
        )


class SqlAlchemySimulationTaskRepository(TaskRepository):
    """PostgreSQL simulation control plane using row locks and fencing tokens.

    General-slot exclusivity is serialized with a PostgreSQL transaction
    advisory lock and the durable simulation lease rows.  The reserved nowcast
    lane is owned by its separate repository.  Methods intentionally do not
    commit; the worker opens one transaction per control-plane operation.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        attempt_timeout_seconds: int = DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
    ) -> None:
        if lease_seconds <= 0 or attempt_timeout_seconds <= 0:
            raise ValueError("lease and timeout must be positive")
        self.session = session
        self.lease_seconds = lease_seconds
        self.attempt_timeout_seconds = attempt_timeout_seconds
        self.state_machine = DurableJobStateMachine()

    async def claim_next(
        self,
        worker_id: str,
        *,
        kind: TaskKind = TaskKind.simulation,
        now: datetime | None = None,
    ) -> TaskClaim | None:
        if kind is not TaskKind.simulation:
            raise ValueError("simulation repository can only claim simulation tasks")
        current = _as_utc(now or _utc_now())
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(:claim_lock_key)"),
            {"claim_lock_key": 0x48594452},
        )
        active_result = await self.session.execute(
            select(SimulationRun.id)
            .where(
                SimulationRun.status.in_(
                    [TaskStatus.leased.value, TaskStatus.running.value]
                ),
                SimulationRun.lease_until > current,
            )
            .with_for_update()
        )
        if len(active_result.scalars().all()) >= 1:
            return None
        slot_id = 1

        statement = (
            select(SimulationRun)
            .where(
                or_(
                    SimulationRun.status == TaskStatus.queued.value,
                    and_(
                        SimulationRun.status == TaskStatus.retry_wait.value,
                        SimulationRun.next_attempt_at <= current,
                    ),
                )
            )
            .order_by(SimulationRun.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = (await self.session.execute(statement)).scalar_one_or_none()
        if job is None:
            return None
        if job.attempt >= job.max_attempts:
            raise InvalidTaskTransition("simulation attempt budget is exhausted")

        lease_token = uuid.uuid4()
        lease_until = current + timedelta(seconds=self.lease_seconds)
        attempt_no = job.attempt + 1
        fencing_token = job.fencing_token + 1
        attempt_id = uuid.uuid4()
        job.status = TaskStatus.leased.value
        job.phase = "solving"
        job.attempt = attempt_no
        job.fencing_token = fencing_token
        job.lease_owner = worker_id
        job.lease_token = lease_token
        job.lease_until = lease_until
        job.deadline_at = current + timedelta(seconds=self.attempt_timeout_seconds)
        job.next_attempt_at = None
        job.retry_backoff_seconds = None
        job.last_retry_error_code = None
        job.error_code = None
        job.error_message = None
        if job.started_at is None:
            job.started_at = current
        job.updated_at = current

        attempt = SimulationAttempt(
            id=attempt_id,
            simulation_id=job.id,
            network_revision_id=job.network_revision_id,
            attempt_no=attempt_no,
            fencing_token=fencing_token,
            status=TaskAttemptStatus.running.value,
            target_manifest_id=job.target_manifest_id,
            target_manifest_sha256=job.target_manifest_sha256,
            started_at=current,
        )
        self.session.add(attempt)
        await self.session.flush()
        return TaskClaim(
            task_id=job.id,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            fencing_token=fencing_token,
            lease_owner=worker_id,
            lease_token=lease_token,
            lease_until=lease_until,
            deadline_at=job.deadline_at,
            slot_id=int(slot_id),
            kind=TaskKind.simulation,
        )

    async def heartbeat(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        lease_until = current + timedelta(seconds=self.lease_seconds)
        result = await self.session.execute(
            update(SimulationRun)
            .where(
                SimulationRun.id == claim.task_id,
                SimulationRun.fencing_token == claim.fencing_token,
                SimulationRun.lease_token == claim.lease_token,
                SimulationRun.status.in_(
                    [TaskStatus.leased.value, TaskStatus.running.value]
                ),
                SimulationRun.lease_until > current,
            )
            .values(
                status=TaskStatus.running.value,
                lease_until=lease_until,
                updated_at=current,
            )
        )
        if result.rowcount != 1:
            raise StaleTaskFence("simulation heartbeat lost its fencing lease")
        await self._heartbeat_slot(claim, current, lease_until)
        state = await self._load_state(claim.task_id)
        return state

    async def update_progress(
        self,
        claim: TaskClaim,
        progress: int,
        *,
        phase: str | None = None,
        now: datetime | None = None,
    ) -> TaskState:
        if not 0 <= progress <= 99:
            raise ValueError("running progress must be between 0 and 99")
        current = _as_utc(now or _utc_now())
        values: dict[str, Any] = {"progress": progress, "updated_at": current}
        if phase is not None:
            values["phase"] = to_persistence_phase(phase)
        result = await self.session.execute(
            update(SimulationRun)
            .where(
                SimulationRun.id == claim.task_id,
                SimulationRun.fencing_token == claim.fencing_token,
                SimulationRun.lease_token == claim.lease_token,
                SimulationRun.status == TaskStatus.running.value,
                SimulationRun.progress <= progress,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise InvalidTaskTransition(
                "progress update lost lease or was not monotonic"
            )
        return await self._load_state(claim.task_id)

    async def request_cancel(
        self,
        task_id: UUID,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        job = await self._locked_job(task_id)
        state = self._state_from_model(job)
        updated = self.state_machine.transition(
            state, TaskEvent.request_cancel, now=current
        )
        if state.status in {TaskStatus.queued, TaskStatus.retry_wait}:
            updated = self.state_machine.transition(
                updated,
                TaskEvent.cancel_complete,
                now=current,
            )
        self._apply_state(job, updated)
        await self._release_slot_for_job(task_id)
        await self.session.flush()
        return updated

    async def mark_cancelled(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        job = await self._locked_job(claim.task_id)
        state = self._state_from_model(job)
        self._assert_claim(state, claim)
        updated = self.state_machine.transition(
            state, TaskEvent.cancel_complete, now=current
        )
        self._apply_state(job, updated)
        await self._discard_attempt(claim.attempt_id, current)
        await self._release_slot(claim.slot_id)
        await self.session.flush()
        return updated

    async def mark_timeout(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        job = await self._locked_job(claim.task_id)
        state = self._state_from_model(job)
        self._assert_claim(state, claim)
        updated = self.state_machine.transition(state, TaskEvent.timeout, now=current)
        self._apply_state(job, updated)
        await self._discard_attempt(claim.attempt_id, current)
        await self._release_slot(claim.slot_id)
        await self.session.flush()
        return updated

    async def record_failure(
        self,
        claim: TaskClaim,
        error_code: str,
        *,
        error_message: str | None = None,
        retryable: bool | None = None,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        job = await self._locked_job(claim.task_id)
        state = self._state_from_model(job)
        self._assert_claim(state, claim)
        event = TaskEvent.failure
        if (
            retryable is not False
            and error_code in RETRYABLE_ERROR_CODES[TaskKind.simulation]
        ):
            event = TaskEvent.retryable_failure
        updated = self.state_machine.transition(
            state,
            event,
            now=current,
            error_code=error_code,
            error_message=error_message,
        )
        self._apply_state(job, updated)
        await self._discard_attempt(
            claim.attempt_id, current, error_code, error_message
        )
        await self._release_slot(claim.slot_id)
        await self.session.flush()
        return updated

    async def complete(
        self,
        claim: TaskClaim,
        *,
        result_committed: bool,
        now: datetime | None = None,
    ) -> TaskState:
        current = _as_utc(now or _utc_now())
        if not result_committed:
            raise InvalidTaskTransition("result evidence must be committed first")
        job = await self._locked_job(claim.task_id)
        state = self._state_from_model(job)
        self._assert_claim(state, claim)
        state = replace(state, result_committed=True)
        updated = self.state_machine.transition(
            state,
            TaskEvent.complete,
            now=current,
            result_committed=True,
            fencing_token=claim.fencing_token,
        )
        await self._validate_completion(job, claim)
        result_row = (
            await self.session.execute(
                select(SimulationResult).where(
                    SimulationResult.attempt_id == claim.attempt_id,
                    SimulationResult.simulation_id == job.id,
                    SimulationResult.fencing_token == claim.fencing_token,
                )
            )
        ).scalar_one()
        self._apply_state(job, updated)
        job.result_summary = result_row.summary
        job.published_attempt_id = claim.attempt_id
        job.published_attempt_no = claim.attempt_no
        attempt = await self._locked_attempt(claim.attempt_id)
        attempt.status = TaskAttemptStatus.published.value
        attempt.summary_id = result_row.id
        attempt.quality_report_sha256 = result_row.quality_report_sha256
        attempt.finished_at = current
        attempt.published_at = current
        await self._release_slot(claim.slot_id)
        await self.session.flush()
        return updated

    async def recover_expired(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[RecoveryAction, ...]:
        current = _as_utc(now or _utc_now())
        rows = (
            (
                await self.session.execute(
                    select(SimulationRun)
                    .where(
                        SimulationRun.status.in_(
                            [TaskStatus.leased.value, TaskStatus.running.value]
                        ),
                        SimulationRun.lease_until <= current,
                    )
                    .order_by(SimulationRun.id)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        recovered: list[RecoveryAction] = []
        for job in rows:
            task_id = job.id
            state = self._state_from_model(job)
            if state.status not in {TaskStatus.leased, TaskStatus.running}:
                continue
            attempt = (
                await self.session.execute(
                    select(SimulationAttempt)
                    .where(
                        SimulationAttempt.simulation_id == job.id,
                        SimulationAttempt.attempt_no == job.attempt,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            attempt_id = attempt.id
            workspace_ref = None
            updated = self.state_machine.transition(
                state,
                TaskEvent.retryable_failure,
                now=current,
                error_code="LEASE_EXPIRED",
                error_message="worker lease expired before completion",
            )
            self._apply_state(job, updated)
            await self._discard_attempt(
                attempt_id,
                current,
                "LEASE_EXPIRED",
                "worker lease expired before completion",
            )
            await self._release_slot(1)
            recovered.append(
                RecoveryAction(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    attempt_no=attempt.attempt_no,
                    workspace_ref=workspace_ref,
                    next_attempt_at=updated.next_attempt_at,
                    status=updated.status,
                )
            )
        await self.session.flush()
        return tuple(recovered)

    async def get(self, task_id: UUID) -> TaskState:
        return await self._load_state(task_id)

    async def _load_state(self, task_id: UUID) -> TaskState:
        job = (
            await self.session.execute(
                select(SimulationRun).where(SimulationRun.id == task_id)
            )
        ).scalar_one_or_none()
        if job is None:
            raise TaskNotFound(f"simulation {task_id} does not exist")
        return self._state_from_model(job)

    async def _locked_job(self, task_id: UUID) -> SimulationRun:
        job = (
            await self.session.execute(
                select(SimulationRun)
                .where(SimulationRun.id == task_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            raise TaskNotFound(f"simulation {task_id} does not exist")
        return job

    async def _locked_attempt(self, attempt_id: UUID) -> SimulationAttempt:
        attempt = (
            await self.session.execute(
                select(SimulationAttempt)
                .where(SimulationAttempt.id == attempt_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise TaskNotFound(f"simulation attempt {attempt_id} does not exist")
        return attempt

    def _state_from_model(self, job: SimulationRun) -> TaskState:
        status = TaskStatus(_enum_value(job.status))
        slot_id = self._slot_id_for_job(job.id, job.lease_owner)
        return TaskState(
            id=job.id,
            kind=TaskKind.simulation,
            status=status,
            progress=int(job.progress),
            attempt=int(job.attempt),
            max_attempts=int(job.max_attempts),
            fencing_token=int(job.fencing_token),
            created_at=_as_utc(job.created_at),
            updated_at=_as_utc(job.updated_at),
            idempotency_scope="simulation.submit",
            idempotency_key="persisted-resource",
            user_id=job.requested_by,
            idempotency_generation=1,
            request_fingerprint=job.request_fingerprint,
            phase=job.phase,
            slot_id=slot_id,
            active_attempt_id=None,
            lease_owner=job.lease_owner,
            lease_token=job.lease_token,
            lease_until=(
                _as_utc(job.lease_until) if job.lease_until is not None else None
            ),
            deadline_at=(
                _as_utc(job.deadline_at) if job.deadline_at is not None else None
            ),
            cancel_requested_at=(
                _as_utc(job.cancel_requested_at)
                if job.cancel_requested_at is not None
                else None
            ),
            next_attempt_at=(
                _as_utc(job.next_attempt_at)
                if job.next_attempt_at is not None
                else None
            ),
            retry_backoff_seconds=job.retry_backoff_seconds,
            last_retry_error_code=job.last_retry_error_code,
            error_code=job.error_code,
            error_message=job.error_message,
            result_committed=job.published_attempt_id is not None,
            started_at=(
                _as_utc(job.started_at) if job.started_at is not None else None
            ),
            finished_at=(
                _as_utc(job.finished_at) if job.finished_at is not None else None
            ),
        )

    @staticmethod
    def _assert_claim(state: TaskState, claim: TaskClaim) -> None:
        if state.fencing_token != claim.fencing_token:
            raise StaleTaskFence("claim no longer owns the simulation lease")
        if (
            state.status is not TaskStatus.cancelling
            and state.lease_token != claim.lease_token
        ):
            raise StaleTaskFence("claim no longer owns the simulation lease")

    def _apply_state(self, job: SimulationRun, state: TaskState) -> None:
        job.status = state.status.value
        job.progress = state.progress
        job.phase = state.phase
        job.next_attempt_at = state.next_attempt_at
        job.retry_backoff_seconds = state.retry_backoff_seconds
        job.last_retry_error_code = state.last_retry_error_code
        job.lease_owner = state.lease_owner
        job.lease_token = state.lease_token
        job.lease_until = state.lease_until
        job.deadline_at = state.deadline_at
        job.cancel_requested_at = state.cancel_requested_at
        job.error_code = state.error_code
        job.error_message = state.error_message
        job.finished_at = state.finished_at
        job.updated_at = state.updated_at

    async def _heartbeat_slot(
        self,
        claim: TaskClaim,
        heartbeat_at: datetime,
        lease_until: datetime,
    ) -> None:
        # The simulation row itself is the durable general-slot lease.  The
        # frozen reserved nowcast lane is not represented by this repository.
        del claim, heartbeat_at, lease_until

    async def _release_slot(self, slot_id: int | None) -> None:
        del slot_id

    async def _release_slot_for_job(self, task_id: UUID) -> None:
        del task_id

    def _slot_id_for_job(self, task_id: UUID, worker_id: str | None) -> int | None:
        del task_id
        return 1 if worker_id is not None else None

    async def _discard_attempt(
        self,
        attempt_id: UUID,
        now: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        # ``simulation_attempts`` has no error_code/error_message columns, and
        # neither does migration 0002.  Assigning them here only set throwaway
        # instance attributes that never reached the row, so this adapter and
        # the in-memory one disagreed about what a discard records.  Every
        # caller already persists the same reason on the owning
        # ``SimulationRun`` through _apply_state, which is where the columns
        # actually live.
        del error_code, error_message
        attempt = await self._locked_attempt(attempt_id)
        attempt.status = TaskAttemptStatus.discarded.value
        attempt.finished_at = now

    async def _validate_completion(
        self,
        job: SimulationRun,
        claim: TaskClaim,
    ) -> None:
        attempt = await self._locked_attempt(claim.attempt_id)
        expected_count = job.duration_seconds // job.frame_interval_seconds + 1
        if attempt.frame_count != expected_count:
            raise InvalidTaskTransition(
                "frame count does not match the simulation grid"
            )
        if attempt.first_frame_time != job.start_at:
            raise InvalidTaskTransition(
                "first frame time does not match simulation start"
            )
        expected_last = job.start_at + timedelta(seconds=job.duration_seconds)
        if attempt.last_frame_time != expected_last:
            raise InvalidTaskTransition("last frame time does not match simulation end")
        registration_count = (
            await self.session.execute(
                select(func.count())
                .select_from(SimulationFrameRegistration)
                .where(SimulationFrameRegistration.attempt_id == claim.attempt_id)
            )
        ).scalar_one()
        if registration_count != expected_count:
            raise InvalidTaskTransition("frame registrations are incomplete")
        if not attempt.frame_manifest_sha256 or not attempt.quality_report_sha256:
            raise InvalidTaskTransition(
                "frame manifest or quality report hash is missing"
            )


class SessionSimulationTaskRepository(TaskRepository):
    """Commit one durable control-plane operation per worker repository call."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        attempt_timeout_seconds: int = DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
    ) -> None:
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds
        self.attempt_timeout_seconds = attempt_timeout_seconds

    def _repository(self, session: AsyncSession) -> SqlAlchemySimulationTaskRepository:
        return SqlAlchemySimulationTaskRepository(
            session,
            lease_seconds=self.lease_seconds,
            attempt_timeout_seconds=self.attempt_timeout_seconds,
        )

    async def claim_next(
        self,
        worker_id: str,
        *,
        kind: TaskKind = TaskKind.simulation,
        now: datetime | None = None,
    ) -> TaskClaim | None:
        async with self.session_factory() as session:
            result = await self._repository(session).claim_next(
                worker_id,
                kind=kind,
                now=now,
            )
            await session.commit()
            return result

    async def heartbeat(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        async with self.session_factory() as session:
            result = await self._repository(session).heartbeat(claim, now=now)
            await session.commit()
            return result

    async def update_progress(
        self,
        claim: TaskClaim,
        progress: int,
        *,
        phase: str | None = None,
        now: datetime | None = None,
    ) -> TaskState:
        async with self.session_factory() as session:
            result = await self._repository(session).update_progress(
                claim,
                progress,
                phase=phase,
                now=now,
            )
            await session.commit()
            return result

    async def request_cancel(
        self,
        task_id: UUID,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        async with self.session_factory() as session:
            result = await self._repository(session).request_cancel(task_id, now=now)
            await session.commit()
            return result

    async def mark_cancelled(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        async with self.session_factory() as session:
            result = await self._repository(session).mark_cancelled(claim, now=now)
            await session.commit()
            return result

    async def mark_timeout(
        self,
        claim: TaskClaim,
        *,
        now: datetime | None = None,
    ) -> TaskState:
        async with self.session_factory() as session:
            result = await self._repository(session).mark_timeout(claim, now=now)
            await session.commit()
            return result

    async def record_failure(
        self,
        claim: TaskClaim,
        error_code: str,
        *,
        error_message: str | None = None,
        retryable: bool | None = None,
        now: datetime | None = None,
    ) -> TaskState:
        async with self.session_factory() as session:
            result = await self._repository(session).record_failure(
                claim,
                error_code,
                error_message=error_message,
                retryable=retryable,
                now=now,
            )
            await session.commit()
            return result

    async def complete(
        self,
        claim: TaskClaim,
        *,
        result_committed: bool,
        now: datetime | None = None,
    ) -> TaskState:
        async with self.session_factory() as session:
            result = await self._repository(session).complete(
                claim,
                result_committed=result_committed,
                now=now,
            )
            await session.commit()
            return result

    async def recover_expired(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[RecoveryAction, ...]:
        async with self.session_factory() as session:
            result = await self._repository(session).recover_expired(now=now)
            await session.commit()
            return result

    async def get(self, task_id: UUID) -> TaskState:
        async with self.session_factory() as session:
            return await self._repository(session).get(task_id)


__all__ = [
    "DEFAULT_ATTEMPT_TIMEOUT_SECONDS",
    "DEFAULT_GENERAL_BACKOFF_SECONDS",
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_NOWCAST_BACKOFF_SECONDS",
    "DurableJobStateMachine",
    "IDEMPOTENCY_TTL",
    "IdempotencyConflict",
    "IdempotencyIncomplete",
    "IdempotencyOutcome",
    "IdempotencyResource",
    "InMemoryIdempotencyRegistry",
    "InMemoryTaskRepository",
    "InvalidTaskTransition",
    "MAX_RETRIES",
    "NOWCAST_RESERVED_SLOTS",
    "RETRYABLE_ERROR_CODES",
    "RecoveryAction",
    "SessionSimulationTaskRepository",
    "SqlAlchemyIdempotencyService",
    "SqlAlchemySimulationTaskRepository",
    "StaleTaskFence",
    "TOTAL_SOLVER_CONCURRENCY",
    "TaskAttempt",
    "TaskAttemptStatus",
    "TaskClaim",
    "TaskEvent",
    "TaskKind",
    "TaskLane",
    "TaskNotCancellable",
    "TaskNotFound",
    "TaskRepository",
    "TaskState",
    "TaskStateError",
    "TaskStatus",
]
