"""Simulation worker orchestration for leased, cancellable solver processes.

The module is both the lease/sandbox orchestration library and the process
entry point used by ``python -m app.workers.simulation_worker`` (the
``simulation-worker`` Compose service).  The entry point owns, and only owns,
worker process concerns:

* fail-closed startup checks (config, database, schema head/profile, writable
  sandbox and result roots, resolvable solver command);
* the durable claim/heartbeat loop against the real session-backed task
  repository;
* ``/healthz`` (liveness) and ``/readyz`` (readiness) on a dependency-free
  HTTP responder;
* SIGTERM/SIGINT graceful shutdown that stops claiming, terminates the solver
  process tree, and runs sandbox cleanup inside the Compose grace period;
* distinct process exit codes and a redacted structured log.

Solver numerical behavior lives in ``app.services.hydraulic.solvers`` and is
deliberately untouched here; the worker only supplies the process boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.core.config import settings
from app.services.hydraulic.sandbox import (
    DEFAULT_INPUT_MAX_BYTES,
    DEFAULT_OUTPUT_MAX_BYTES,
    DEFAULT_WORKSPACE_MAX_BYTES,
    ProcessTreeJob,
    SandboxCleanupError,
    SandboxError,
    SandboxLimitExceeded,
    TaskSandbox,
    UnsafeSandboxPath,
)
from app.services.hydraulic.tasks import (
    InvalidTaskTransition,
    SessionSimulationTaskRepository,
    StaleTaskFence,
    TaskClaim,
    TaskKind,
    TaskRepository,
    TaskStateError,
    TaskStatus,
)

logger = logging.getLogger(__name__)

UTC = timezone.utc


def _free_disk_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


class WorkerExecutionError(RuntimeError):
    """Structured execution failure mapped to the durable retry policy."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    return_code: int
    result_committed: bool = False


class ExecutionHandle(Protocol):
    async def wait(self) -> ExecutionResult: ...

    async def kill_process_tree(self) -> None: ...


class SimulationExecutor(Protocol):
    def start(
        self,
        claim: TaskClaim,
        workspace: TaskSandbox,
    ) -> ExecutionHandle: ...


@dataclass(frozen=True, slots=True)
class WorkerRunResult:
    task_id: UUID
    attempt_no: int
    status: TaskStatus | None
    reason: str
    cleanup_pending: bool = False


class WorkerStopReason(str, Enum):
    no_work = "no_work"
    completed = "completed"
    cancelled = "cancelled"
    timed_out = "timed_out"
    retry_wait = "retry_wait"
    failed = "failed"
    stale_lease = "stale_lease"


class _ProcessHandle:
    def __init__(
        self,
        process: subprocess.Popen[bytes],
        *,
        task: asyncio.Task[ExecutionResult],
        job: ProcessTreeJob | None = None,
    ) -> None:
        self._process = process
        self._task = task
        self._job = job
        self._kill_lock = asyncio.Lock()
        self._killed = False

    async def wait(self) -> ExecutionResult:
        return await asyncio.shield(self._task)

    async def kill_process_tree(self) -> None:
        """Terminate the solver and every descendant, not only its leaf."""

        async with self._kill_lock:
            if self._killed:
                return
            self._killed = True
            await asyncio.to_thread(self._kill_tree_sync)
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(self._task)

    def _kill_tree_sync(self) -> None:
        job = self._job
        try:
            if job is not None:
                job.terminate()
            if os.name == "nt":
                creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                result = subprocess.run(
                    ["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=creation_flags,
                    shell=False,
                )
                if result.returncode != 0:
                    self._process.kill()
                return
            killpg = getattr(os, "killpg", None)
            sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            if killpg is not None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    killpg(self._process.pid, sigkill)
            with contextlib.suppress(ProcessLookupError):
                os.kill(self._process.pid, sigkill)
        finally:
            if job is not None:
                job.close()


class SubprocessSimulationExecutor:
    """Start a solver with ``shell=False`` in an attempt-local directory."""

    def __init__(
        self,
        command_factory: Callable[[TaskClaim, TaskSandbox], Sequence[str]],
        *,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._command_factory = command_factory
        self._environment = dict(environment or {})

    def start(
        self,
        claim: TaskClaim,
        workspace: TaskSandbox,
    ) -> ExecutionHandle:
        command = tuple(
            os.fspath(part) for part in self._command_factory(claim, workspace)
        )
        if not command or not all(command):
            raise WorkerExecutionError(
                "SOLVER_VERSION_MISMATCH",
                "solver command must contain non-empty argv entries",
                retryable=False,
            )
        env = os.environ.copy()
        env.update(self._environment)
        env.update(
            {
                "HYDRAULIC_SANDBOX_PATH": str(workspace.path),
                "SIMULATION_ID": str(claim.task_id),
                "SIMULATION_ATTEMPT": str(claim.attempt_no),
            }
        )
        process_options: dict[str, Any] = {
            "creationflags": (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            )
        }
        if os.name != "nt":
            process_options["start_session"] = True

        job: ProcessTreeJob | None = None
        try:
            job = ProcessTreeJob.create()
            stdout = (workspace.path / "solver.stdout.log").open("xb")
            stderr = (workspace.path / "solver.stderr.log").open("xb")
        except OSError as exc:
            if job is not None:
                with contextlib.suppress(Exception):
                    job.close()
            raise WorkerExecutionError(
                "RESULT_PERSIST_FAILED",
                "failed to create solver isolation or log files",
            ) from exc
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(  # type: ignore[call-overload]
                list(command),
                cwd=workspace.path,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                shell=False,
                **process_options,
            )
            if job is not None:
                job.assign(process)
        except OSError as exc:
            if process is not None:
                with contextlib.suppress(OSError):
                    process.kill()
            if job is not None:
                with contextlib.suppress(Exception):
                    job.close()
            raise WorkerExecutionError(
                "SOLVER_UNAVAILABLE",
                "solver executable or isolation boundary could not start",
                retryable=False,
            ) from exc
        finally:
            stdout.close()
            stderr.close()
        assert process is not None

        async def wait_for_process() -> ExecutionResult:
            return_code = await asyncio.to_thread(process.wait)
            return ExecutionResult(
                return_code=return_code,
                result_committed=return_code == 0,
            )

        return _ProcessHandle(
            process,
            task=asyncio.create_task(wait_for_process()),
            job=job,
        )


class CallbackSimulationExecutor:
    """Adapter for an already-isolated async solver callback.

    Production EPA/Python solver adapters may use this boundary when they own
    their subprocess.  Timeout/cancel still awaits and terminates the callback
    task, while :class:`SubprocessSimulationExecutor` provides explicit OS
    process-tree termination.
    """

    def __init__(
        self,
        callback: Callable[
            [TaskClaim, TaskSandbox],
            Awaitable[ExecutionResult],
        ],
    ) -> None:
        self._callback = callback

    def start(
        self,
        claim: TaskClaim,
        workspace: TaskSandbox,
    ) -> ExecutionHandle:
        async def invoke() -> ExecutionResult:
            return await self._callback(claim, workspace)

        return _CallbackHandle(asyncio.create_task(invoke()))


class _CallbackHandle:
    def __init__(self, task: asyncio.Task[ExecutionResult]) -> None:
        self._task = task
        self._kill_lock = asyncio.Lock()
        self._killed = False

    async def wait(self) -> ExecutionResult:
        return await asyncio.shield(self._task)

    async def kill_process_tree(self) -> None:
        async with self._kill_lock:
            if self._killed:
                return
            self._killed = True
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(self._task)


class SimulationWorker:
    """Run one durable lease with heartbeat, cancel, timeout, and cleanup."""

    def __init__(
        self,
        repository: TaskRepository,
        executor: SimulationExecutor,
        *,
        sandbox_root: str | os.PathLike[str] | None = None,
        worker_id: str | None = None,
        poll_seconds: float = 1.0,
        heartbeat_seconds: float = 10.0,
        workspace_max_bytes: int = DEFAULT_WORKSPACE_MAX_BYTES,
        input_max_bytes: int = DEFAULT_INPUT_MAX_BYTES,
        recover_on_start: bool = True,
        monotonic: Callable[[], float] | None = None,
        wall_clock: Callable[[], datetime] | None = None,
        disk_usage: Callable[[Path], int] | None = None,
    ) -> None:
        if not 0 < poll_seconds <= 1:
            raise ValueError("cancel polling must be in (0, 1]")
        if heartbeat_seconds <= poll_seconds:
            raise ValueError("heartbeat interval must exceed cancel polling")
        if workspace_max_bytes <= 0 or input_max_bytes <= 0:
            raise ValueError("sandbox limits must be positive")
        self.repository = repository
        self.executor = executor
        sandbox_path = sandbox_root or settings.HYDRAULIC_SANDBOX_ROOT
        self.sandbox_root = Path(sandbox_path)
        self.worker_id = worker_id or f"simulation-worker-{uuid4()}"
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.workspace_max_bytes = workspace_max_bytes
        self.input_max_bytes = input_max_bytes
        self.recover_on_start = recover_on_start
        self._monotonic = monotonic or __import__("time").monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._disk_usage = disk_usage or _free_disk_bytes

    def _ensure_disk_capacity(self) -> None:
        probe = self.sandbox_root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        required = max(1024**3, 2 * DEFAULT_OUTPUT_MAX_BYTES)
        try:
            free_bytes = int(self._disk_usage(probe))
        except OSError as exc:
            raise WorkerExecutionError(
                "RESULT_PERSIST_FAILED",
                "sandbox filesystem capacity could not be checked",
                retryable=True,
            ) from exc
        if free_bytes < required:
            raise WorkerExecutionError(
                "OUTPUT_LIMIT_EXCEEDED",
                f"sandbox requires at least {required} free bytes; "
                f"found {free_bytes}",
                retryable=False,
            )

    def _cleanup_marker(self, task_id: UUID, attempt_no: int) -> Path:
        root = self.sandbox_root / ".cleanup-pending"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return root / f"{task_id}-attempt-{attempt_no}.json"

    def _record_cleanup_pending(self, task_id: UUID, attempt_no: int) -> None:
        marker = self._cleanup_marker(task_id, attempt_no)
        payload = {
            "schema": "simulation-cleanup-pending-v1",
            "task_id": str(task_id),
            "attempt_no": attempt_no,
        }
        temporary = marker.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(marker)

    def _sweep_cleanup_markers(self) -> None:
        root = self.sandbox_root / ".cleanup-pending"
        if not root.is_dir():
            return
        for marker in root.glob("*.json"):
            try:
                payload = json.loads(marker.read_text(encoding="utf-8"))
                task_id = UUID(str(payload["task_id"]))
                attempt_no = int(payload["attempt_no"])
                TaskSandbox.cleanup_attempt(
                    self.sandbox_root,
                    task_id,
                    attempt_no,
                )
                marker.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError, SandboxError):
                logger.exception(
                    "sandbox cleanup marker remains pending marker=%s", marker
                )

    async def recover(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[UUID, ...]:
        """Recover expired leases and clean deterministic attempt paths."""

        self._sweep_cleanup_markers()
        recovered = await self.repository.recover_expired(now=now)
        cleaned: list[UUID] = []
        for action in recovered:
            try:
                removed = TaskSandbox.cleanup_attempt(
                    self.sandbox_root,
                    action.task_id,
                    action.attempt_no,
                )
            except (OSError, SandboxCleanupError) as exc:
                self._record_cleanup_pending(action.task_id, action.attempt_no)
                logger.error(
                    "crash-recovery sandbox cleanup pending "
                    "task_id=%s attempt=%s "
                    "error=%s",
                    action.task_id,
                    action.attempt_no,
                    type(exc).__name__,
                )
                continue
            if removed or action.workspace_ref is None:
                cleaned.append(action.task_id)
        return tuple(cleaned)

    async def _stop_handle(
        self,
        handle: ExecutionHandle | None,
        claim: TaskClaim,
    ) -> bool:
        if handle is None:
            return True
        try:
            await handle.kill_process_tree()
            return True
        except Exception:
            logger.exception(
                "solver process-tree cleanup pending task_id=%s attempt=%s",
                claim.task_id,
                claim.attempt_no,
            )
            self._record_cleanup_pending(claim.task_id, claim.attempt_no)
            return False

    async def run_once(self) -> WorkerRunResult | None:
        self._ensure_disk_capacity()
        if self.recover_on_start:
            await self.recover()
        claim = await self.repository.claim_next(
            self.worker_id,
            kind=TaskKind.simulation,
        )
        if claim is None:
            return None

        sandbox: TaskSandbox | None = None
        handle: ExecutionHandle | None = None
        status: TaskStatus | None = None
        reason = WorkerStopReason.failed.value
        cleanup_pending = False

        try:
            state = await self.repository.heartbeat(claim)
            sandbox = TaskSandbox(
                self.sandbox_root,
                claim.task_id,
                claim.attempt_no,
                max_bytes=self.workspace_max_bytes,
                input_max_bytes=self.input_max_bytes,
            )
            attach_workspace = getattr(
                self.repository,
                "attach_workspace",
                None,
            )
            if attach_workspace is not None:
                await attach_workspace(claim, str(sandbox.path))
            if state.status is TaskStatus.cancelling:
                raise _CancellationRequested
            handle = self.executor.start(claim, sandbox)
            status, reason = await self._monitor(claim, handle, sandbox)
        except _CancellationRequested:
            cleanup_pending = (
                not await self._stop_handle(handle, claim) or cleanup_pending
            )
            status = (await self.repository.mark_cancelled(claim)).status
            reason = WorkerStopReason.cancelled.value
        except StaleTaskFence:
            cleanup_pending = (
                not await self._stop_handle(handle, claim) or cleanup_pending
            )
            status = None
            reason = WorkerStopReason.stale_lease.value
        except asyncio.CancelledError:
            cleanup_pending = (
                not await self._stop_handle(handle, claim) or cleanup_pending
            )
            with contextlib.suppress(StaleTaskFence, TaskStateError):
                status = (await self.repository.mark_cancelled(claim)).status
            reason = WorkerStopReason.cancelled.value
            raise
        except WorkerExecutionError as exc:
            cleanup_pending = (
                not await self._stop_handle(handle, claim) or cleanup_pending
            )
            status, reason = await self._record_execution_failure(claim, exc)
        except InvalidTaskTransition:
            cleanup_pending = (
                not await self._stop_handle(handle, claim) or cleanup_pending
            )
            with contextlib.suppress(StaleTaskFence, TaskStateError):
                status = (await self.repository.mark_cancelled(claim)).status
                reason = WorkerStopReason.cancelled.value
        except Exception as exc:
            cleanup_pending = (
                not await self._stop_handle(handle, claim) or cleanup_pending
            )
            logger.exception(
                "simulation worker failed task_id=%s attempt=%s",
                claim.task_id,
                claim.attempt_no,
            )
            status, reason = await self._record_execution_failure(
                claim,
                WorkerExecutionError(
                    "RESULT_PERSIST_FAILED",
                    f"unhandled worker failure: {type(exc).__name__}",
                ),
            )
        finally:
            cleanup_pending = (
                not await self._stop_handle(handle, claim) or cleanup_pending
            )
            if sandbox is not None:
                try:
                    sandbox.cleanup()
                except SandboxCleanupError:
                    cleanup_pending = True
                    cleanup_message = "cleanup pending task=%s attempt=%s"
                    logger.error(
                        cleanup_message,
                        claim.task_id,
                        claim.attempt_no,
                    )
                    self._record_cleanup_pending(
                        claim.task_id,
                        claim.attempt_no,
                    )
                    marker = getattr(
                        self.repository,
                        "record_cleanup_pending",
                        None,
                    )
                    if marker is not None:
                        with contextlib.suppress(Exception):
                            await marker(claim, "sandbox cleanup is pending")

        return WorkerRunResult(
            task_id=claim.task_id,
            attempt_no=claim.attempt_no,
            status=status,
            reason=reason,
            cleanup_pending=cleanup_pending,
        )

    async def run_forever(
        self,
        *,
        stop_event: asyncio.Event | None = None,
        idle_seconds: float = 0.1,
    ) -> None:
        if idle_seconds <= 0:
            raise ValueError("idle_seconds must be positive")
        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            result = await self.run_once()
            if result is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
                except TimeoutError:
                    pass

    async def _monitor(
        self,
        claim: TaskClaim,
        handle: ExecutionHandle,
        sandbox: TaskSandbox,
    ) -> tuple[TaskStatus, str]:
        completion = asyncio.create_task(handle.wait())
        next_poll = self._monotonic() + self.poll_seconds
        next_heartbeat = self._monotonic() + self.heartbeat_seconds
        deadline = self._monotonic() + max(
            0.0,
            (claim.deadline_at - self._wall_clock()).total_seconds(),
        )

        while True:
            now_mono = self._monotonic()
            if now_mono >= deadline:
                state = await self.repository.get(claim.task_id)
                if state.status is TaskStatus.cancelling:
                    await handle.kill_process_tree()
                    with contextlib.suppress(asyncio.CancelledError):
                        await completion
                    cancelled = await self.repository.mark_cancelled(claim)
                    status = cancelled.status
                    return status, WorkerStopReason.cancelled.value
                await handle.kill_process_tree()
                status = (await self.repository.mark_timeout(claim)).status
                return status, WorkerStopReason.timed_out.value

            if now_mono >= next_poll:
                try:
                    sandbox.ensure_capacity(0)
                except SandboxLimitExceeded as exc:
                    raise WorkerExecutionError(
                        "OUTPUT_LIMIT_EXCEEDED",
                        str(exc),
                        retryable=False,
                    ) from exc
                except UnsafeSandboxPath as exc:
                    raise WorkerExecutionError(
                        "UNSAFE_PATH",
                        str(exc),
                        retryable=False,
                    ) from exc
                except SandboxError as exc:
                    raise WorkerExecutionError(
                        "RESULT_PERSIST_FAILED",
                        str(exc),
                    ) from exc
                state = await self.repository.get(claim.task_id)
                if state.status is TaskStatus.cancelling:
                    await handle.kill_process_tree()
                    with contextlib.suppress(asyncio.CancelledError):
                        await completion
                    cancelled_state = await self.repository.mark_cancelled(
                        claim,
                    )
                    status = cancelled_state.status
                    return status, WorkerStopReason.cancelled.value
                next_poll = now_mono + self.poll_seconds

            if now_mono >= next_heartbeat:
                await self.repository.heartbeat(claim)
                next_heartbeat = now_mono + self.heartbeat_seconds

            next_deadline = min(deadline, next_poll, next_heartbeat)
            wait_for = max(0.0, next_deadline - self._monotonic())
            done, _ = await asyncio.wait({completion}, timeout=wait_for)
            if not done:
                continue

            execution = completion.result()
            if execution.return_code != 0:
                raise WorkerExecutionError(
                    "RESULT_PERSIST_FAILED",
                    f"solver exited with code {execution.return_code}",
                )
            if not execution.result_committed:
                raise WorkerExecutionError(
                    "RESULT_PERSIST_FAILED",
                    "solver exited before frame, summary, and quality "
                    "finalization committed",
                )

            state = await self.repository.get(claim.task_id)
            if state.status is TaskStatus.cancelling:
                await self.repository.mark_cancelled(claim)
                return state.status, WorkerStopReason.cancelled.value
            status = (
                await self.repository.complete(
                    claim,
                    result_committed=True,
                )
            ).status
            return status, WorkerStopReason.completed.value

    async def _record_execution_failure(
        self,
        claim: TaskClaim,
        error: WorkerExecutionError,
    ) -> tuple[TaskStatus, str]:
        try:
            status = await self.repository.record_failure(
                claim,
                error.error_code,
                error_message=str(error),
                retryable=error.retryable,
            )
        except StaleTaskFence:
            return TaskStatus.retry_wait, WorkerStopReason.stale_lease.value
        reason = (
            WorkerStopReason.retry_wait.value
            if status.status is TaskStatus.retry_wait
            else WorkerStopReason.failed.value
        )
        return status.status, reason


class _CancellationRequested(RuntimeError):
    pass


# ============================================================================
# Process entry point
# ============================================================================

# Exit codes. 0 is reserved for a graceful shutdown; every abnormal path is
# non-zero so `restart: unless-stopped` and orchestrators can see the failure
# instead of a loop of successful-looking exits.
EXIT_OK = 0
EXIT_CONFIG_INVALID = 2
EXIT_STARTUP_UNAVAILABLE = 3
EXIT_RUNTIME_FAILED = 4
EXIT_INTERRUPTED = 130

# The key the API's /readyz reads (backend/app/main.py _WORKER_HEARTBEAT_KEYS).
WORKER_HEARTBEAT_KEY = "hydraulic:worker:heartbeat:simulation"
WORKER_HEARTBEAT_COMPONENT = "simulation"
# API freshness window is `0 < now - observed <= 75.0`; keep the published TTL
# strictly inside it so a paused worker cannot leave a permanently fresh key.
WORKER_HEARTBEAT_TTL_SECONDS = 30


class StartupFailure(str, Enum):
    """Distinct fail-closed reasons surfaced to logs and to ``/readyz``."""

    database_unavailable = "database_unavailable"
    schema_head_or_profile_mismatch = "schema_head_or_profile_mismatch"
    workspace_not_writable = "workspace_not_writable"
    result_root_not_writable = "result_root_not_writable"
    solver_command_unavailable = "solver_command_unavailable"
    claim_probe_failed = "claim_probe_failed"
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


class WorkerReadiness:
    """Mutable readiness snapshot shared by the loops and the health server.

    Single event loop, single writer per field: plain attribute updates are
    atomic enough here and keep the health responder allocation-free.
    """

    def __init__(self, *, worker_id: str) -> None:
        self.worker_id = worker_id
        self.started_at = datetime.now(UTC)
        self._startup_ok = False
        self._reason: StartupFailure = StartupFailure.claim_probe_failed
        self._detail = "startup checks have not completed"
        self._claim_probe_ok = False
        self._last_heartbeat_monotonic: float | None = None
        self._stopping = False
        self._tasks_completed = 0
        self._tasks_failed = 0
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

    def record_task_result(self, *, failed: bool) -> None:
        if failed:
            self._tasks_failed += 1
        else:
            self._tasks_completed += 1

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
            "worker_id": self.worker_id,
            "started_at": self.started_at.isoformat(),
            "claims_observed": self._claims_observed,
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "heartbeat_age_seconds": (
                None
                if self._last_heartbeat_monotonic is None
                else round(monotonic_now - self._last_heartbeat_monotonic, 3)
            ),
        }


class _HealthServer:
    """Dependency-free HTTP/1.1 responder for ``/healthz`` and ``/readyz``.

    ``asyncio.start_server`` keeps the worker a plain process: no ASGI app, no
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
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._readiness = readiness
        self._host = host
        self._port = port
        self._heartbeat_max_age = heartbeat_max_age
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
            "simulation worker health endpoint listening host=%s port=%s",
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
            logger.debug("simulation worker health request aborted")
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


def _ensure_writable_directory(path: Path, label: str, /) -> None:
    """Create ``path`` and prove it is writable with a real create+delete."""
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkerExecutionError(
            (
                StartupFailure.workspace_not_writable.value
                if label == "sandbox"
                else StartupFailure.result_root_not_writable.value
            ),
            f"{label} root could not be created: {type(exc).__name__}",
            retryable=False,
        ) from exc
    probe = path / f".worker-write-probe-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        with contextlib.suppress(OSError):
            probe.unlink(missing_ok=True)
        raise WorkerExecutionError(
            (
                StartupFailure.workspace_not_writable.value
                if label == "sandbox"
                else StartupFailure.result_root_not_writable.value
            ),
            f"{label} root is not writable: {type(exc).__name__}",
            retryable=False,
        ) from exc


def parse_solver_argv(value: str | Sequence[str] | None) -> tuple[str, ...]:
    """Split ``SIMULATION_SOLVER_COMMAND`` into argv entries.

    Accepts a native sequence, a JSON array, or a comma-separated string.  An
    empty result is the honest "no solver process is wired" state, never an
    implicit default command.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return ()
            if not isinstance(parsed, list):
                return ()
            return tuple(str(item) for item in parsed)
        return tuple(part.strip() for part in text.split(",") if part.strip())
    return tuple(str(item) for item in value)


def _env_float(
    environ: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise WorkerExecutionError(
            StartupFailure.solver_command_unavailable.value,
            f"{name} must be a number",
            retryable=False,
        ) from exc
    if not minimum <= value <= maximum:
        raise WorkerExecutionError(
            StartupFailure.solver_command_unavailable.value,
            f"{name} must be within [{minimum}, {maximum}]",
            retryable=False,
        )
    return value


def _env_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    return int(
        _env_float(environ, name, float(default), minimum=minimum, maximum=maximum)
    )


@dataclass(frozen=True, slots=True)
class WorkerRuntimeConfig:
    """Process-level knobs for the worker, read from its own environment.

    These deliberately do **not** live in ``app.core.config.Settings``: the
    frozen dependency-handoff contract
    (``scripts/check_dependency_handoff.py``) pins the Settings registry to
    exactly 105 named fields whose keys must match ``.env.example`` one for
    one, so adding fields there is a contract-owner re-approval, not a worker
    change.  The Compose service passes these values as environment variables
    (``docker-compose.yml``), which is where the worker reads them.
    """

    health_host: str = "0.0.0.0"
    health_port: int = 8010
    shutdown_grace_seconds: float = 25.0
    idle_seconds: float = 1.0
    probe_seconds: float = 5.0
    solver_argv: tuple[str, ...] = ()

    @classmethod
    def from_environ(
        cls, environ: Mapping[str, str] | None = None
    ) -> "WorkerRuntimeConfig":
        env = os.environ if environ is None else environ
        return cls(
            health_host=env.get("SIMULATION_WORKER_HEALTH_HOST", "").strip()
            or "0.0.0.0",
            health_port=_env_int(
                env,
                "SIMULATION_WORKER_HEALTH_PORT",
                8010,
                minimum=1,
                maximum=65535,
            ),
            # Must stay below the Compose stop_grace_period (30s) so the
            # container is not SIGKILLed mid-drain.
            shutdown_grace_seconds=_env_float(
                env,
                "SIMULATION_WORKER_SHUTDOWN_GRACE_SECONDS",
                25.0,
                minimum=0.1,
                maximum=600.0,
            ),
            idle_seconds=_env_float(
                env,
                "SIMULATION_WORKER_IDLE_SECONDS",
                1.0,
                minimum=0.001,
                maximum=60.0,
            ),
            probe_seconds=_env_float(
                env,
                "SIMULATION_WORKER_PROBE_SECONDS",
                5.0,
                minimum=0.001,
                maximum=60.0,
            ),
            solver_argv=parse_solver_argv(env.get("SIMULATION_SOLVER_COMMAND")),
        )

    @property
    def heartbeat_max_age(self) -> float:
        """A heartbeat older than this makes /readyz fail closed."""
        return max(2 * self.probe_seconds, 1.0)


def resolve_solver_command(
    command: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Validate the production solver argv, failing closed when absent.

    An empty command is the honest "no solver process is wired" state.  The
    worker then refuses to start rather than claiming tasks it cannot solve.
    """

    argv = tuple(
        part.strip()
        for part in (
            command
            if command is not None
            else WorkerRuntimeConfig.from_environ(environ).solver_argv
        )
    )
    if not argv:
        raise WorkerExecutionError(
            StartupFailure.solver_command_unavailable.value,
            "SIMULATION_SOLVER_COMMAND is empty; no solver process is wired",
            retryable=False,
        )
    if any(not part for part in argv):
        raise WorkerExecutionError(
            StartupFailure.solver_command_unavailable.value,
            "SIMULATION_SOLVER_COMMAND must not contain blank argv entries",
            retryable=False,
        )
    return argv


def _probe_executable(argv: Sequence[str]) -> None:
    """Fail closed when the configured solver executable cannot be resolved."""

    program = argv[0]
    if os.sep in program or (os.altsep and os.altsep in program):
        candidate = Path(program).expanduser()
    else:
        found = shutil.which(program)
        if found is None:
            raise WorkerExecutionError(
                StartupFailure.solver_command_unavailable.value,
                f"solver executable {program!r} is not on PATH",
                retryable=False,
            )
        return
    if not candidate.is_file():
        raise WorkerExecutionError(
            StartupFailure.solver_command_unavailable.value,
            f"solver executable path does not exist: {program!r}",
            retryable=False,
        )


async def _database_reachable() -> None:
    from sqlalchemy import text

    from app.core.database import get_engine

    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _schema_matches() -> None:
    from app.core.database import check_hydraulic_schema_readiness

    report = await check_hydraulic_schema_readiness()
    if not report.get("ready"):
        raise WorkerExecutionError(
            StartupFailure.schema_head_or_profile_mismatch.value,
            "alembic head or spatial profile does not match the frozen contract",
            retryable=False,
        )


async def run_startup_checks(
    *,
    sandbox_root: Path,
    result_root: Path,
    solver_command: Sequence[str],
    database_probe: Callable[[], Awaitable[None]] = _database_reachable,
    schema_probe: Callable[[], Awaitable[None]] = _schema_matches,
    ensure_directory: Callable[[Path, str], None] = _ensure_writable_directory,
    executable_probe: Callable[[Sequence[str]], None] = _probe_executable,
) -> StartupReport:
    """Run every fail-closed preflight and report the first blocking reason."""

    checks = {
        "database": False,
        "schema": False,
        "sandbox_writable": False,
        "result_root_writable": False,
        "solver_command": False,
    }
    steps: tuple[tuple[str, Callable[[], Any], StartupFailure], ...] = (
        ("database", database_probe, StartupFailure.database_unavailable),
        ("schema", schema_probe, StartupFailure.schema_head_or_profile_mismatch),
        (
            "sandbox_writable",
            lambda: ensure_directory(sandbox_root, "sandbox"),
            StartupFailure.workspace_not_writable,
        ),
        (
            "result_root_writable",
            lambda: ensure_directory(result_root, "results"),
            StartupFailure.result_root_not_writable,
        ),
        (
            "solver_command",
            lambda: executable_probe(solver_command),
            StartupFailure.solver_command_unavailable,
        ),
    )
    for name, step, failure in steps:
        try:
            result = step()
            if asyncio.iscoroutine(result):
                await result
        except WorkerExecutionError as exc:
            return StartupReport(False, failure, str(exc), checks)
        except Exception as exc:
            # Driver messages can embed the DSN, so only the exception type
            # reaches the report and the log; the target is emitted redacted.
            reason = type(exc).__name__
            logger.error(
                "simulation worker startup check failed check=%s reason=%s target=%s",
                name,
                reason,
                redacted_startup_target(name),
            )
            return StartupReport(False, failure, reason, checks)
        checks[name] = True
    return StartupReport(True, StartupFailure.ready, "", checks)


def redacted_startup_target(check: str) -> str:
    """A log-safe description of what a failing startup check was aimed at."""

    if check == "database":
        from app.core.database import _mask_password

        return _mask_password(settings.DATABASE_URL)
    if check == "schema":
        return f"alembic_head={settings.HYDRAULIC_SCHEMA_REVISION}"
    if check == "sandbox_writable":
        return "HYDRAULIC_SANDBOX_ROOT=<configured>"
    if check == "result_root_writable":
        return "HYDRAULIC_EXPORT_ROOT=<configured>"
    return "SIMULATION_SOLVER_COMMAND=<configured>"


class WorkerRegistrationHeartbeat:
    """Best-effort publication of this worker's liveness key.

    The API's ``/readyz`` reads ``hydraulic:worker:heartbeat:simulation``.  The
    simulation-worker Compose service has no Redis dependency, so publication is
    additive: when Redis is configured and reachable the key is refreshed, and
    otherwise the worker logs the fact and keeps serving its own ``/readyz``.
    A publication failure never changes this worker's exit code.
    """

    def __init__(
        self,
        *,
        key: str = WORKER_HEARTBEAT_KEY,
        ttl_seconds: int = WORKER_HEARTBEAT_TTL_SECONDS,
        enabled: bool = True,
    ) -> None:
        self._key = key
        self._ttl = ttl_seconds
        self._enabled = enabled
        self.published = 0
        self.disabled_reason = ""

    async def publish_once(self) -> bool:
        if not self._enabled:
            return False
        try:
            from app.core.redis_client import redis_client

            if not redis_client.is_connected:
                await redis_client.connect()
            await redis_client.set(
                self._key,
                repr(time.time()),
                ttl=self._ttl,
            )
        except Exception as exc:
            if not self.disabled_reason:
                self.disabled_reason = type(exc).__name__
                logger.warning(
                    "simulation worker registration heartbeat unavailable: %s",
                    self.disabled_reason,
                )
            return False
        self.published += 1
        return True

    async def close(self) -> None:
        try:
            from app.core.redis_client import redis_client

            await redis_client.delete(self._key)
        except Exception:  # pragma: no cover - best effort teardown
            logger.debug("simulation worker heartbeat key cleanup skipped")


class SimulationWorkerService:
    """Wire the claim loop, readiness probe, health server, and shutdown.

    Every collaborator is injectable so the lifecycle is exercised by tests
    without PostgreSQL, Redis, or a solver subprocess.
    """

    def __init__(
        self,
        worker: SimulationWorker,
        readiness: WorkerReadiness,
        *,
        health_server: _HealthServer | None = None,
        heartbeat: WorkerRegistrationHeartbeat | None = None,
        idle_seconds: float = 1.0,
        probe_seconds: float = 5.0,
        heartbeat_max_age: float = 15.0,
        shutdown_grace_seconds: float = 25.0,
        database_probe: Callable[[], Awaitable[None]] = _database_reachable,
        schema_probe: Callable[[], Awaitable[None]] = _schema_matches,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if idle_seconds <= 0 or probe_seconds <= 0:
            raise ValueError("idle and probe intervals must be positive")
        if heartbeat_max_age <= 0 or shutdown_grace_seconds <= 0:
            raise ValueError("heartbeat age and grace period must be positive")
        self.worker = worker
        self.readiness = readiness
        self._health_server = health_server
        self._heartbeat = heartbeat
        self._idle_seconds = idle_seconds
        self._probe_seconds = probe_seconds
        self._heartbeat_max_age = heartbeat_max_age
        self._shutdown_grace = shutdown_grace_seconds
        self._database_probe = database_probe
        self._schema_probe = schema_probe
        self._monotonic = monotonic
        self._stop_event: asyncio.Event | None = None
        self._stop_source: StopSource | None = None

    @property
    def heartbeat_max_age(self) -> float:
        return self._heartbeat_max_age

    @property
    def stop_source(self) -> StopSource | None:
        return self._stop_source

    def request_stop(self, source: StopSource) -> None:
        """Ask for a graceful stop; the first source wins."""

        if self._stop_source is not None:
            return
        self._stop_source = source
        self.readiness.begin_shutdown()
        logger.info("simulation worker stop requested source=%s", source.value)
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
        # The liveness heartbeat is mandatory: /readyz depends on a recent
        # database + schema round-trip.  Only the Redis publication inside it is
        # optional, so it must not gate this task.
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("simulation worker started worker_id=%s", self.readiness.worker_id)
        exit_code = EXIT_OK
        try:
            try:
                await self._run_claim_loop()
            except asyncio.CancelledError:
                self.request_stop(StopSource.lifecycle)
                raise
            except Exception as exc:
                logger.exception("simulation worker loop failed: %s", exc)
                self.readiness.record_failure(
                    StartupFailure.claim_probe_failed,
                    f"claim loop failed: {type(exc).__name__}",
                )
                exit_code = EXIT_RUNTIME_FAILED
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            if self._health_server is not None:
                await self._health_server.stop()
            if self._heartbeat is not None:
                await self._heartbeat.close()
        source = self._stop_source
        logger.info(
            "simulation worker stopped worker_id=%s source=%s exit=%s",
            self.readiness.worker_id,
            source.value if source is not None else "unsignalled",
            exit_code,
        )
        return exit_code

    async def _run_claim_loop(self) -> None:
        """Drain queued simulation work with a real claim round-trip each pass.

        A stop request must not wait out a long solver: the in-flight attempt is
        cancelled so ``run_once`` kills the solver process tree, cleans the
        sandbox, and records the attempt as cancelled.
        """

        stop = self._stop_event
        assert stop is not None
        while not stop.is_set():
            attempt = asyncio.ensure_future(self._claim_and_record())
            stopper = asyncio.ensure_future(stop.wait())
            try:
                await asyncio.wait(
                    {attempt, stopper},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                stopper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stopper
            if not attempt.done():
                attempt.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await attempt
                logger.info("simulation worker cancelled in-flight attempt on stop")
                return
            if attempt.cancelled():  # pragma: no cover - defensive
                return
            failure = attempt.exception()
            if failure is not None:
                raise failure
            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._idle_seconds)
            except TimeoutError:
                pass

    async def _claim_and_record(self) -> None:
        """Run one durable attempt and fold its outcome into readiness."""

        result = await self.worker.run_once()
        self.readiness.record_claim(claimed=result is not None)
        if result is not None:
            failed = result.reason in {
                WorkerStopReason.failed.value,
                WorkerStopReason.timed_out.value,
            }
            self.readiness.record_task_result(failed=failed)

    async def _heartbeat_loop(self) -> None:
        """Refresh worker liveness from a real database + schema round-trip."""

        while True:
            now = self._monotonic()
            try:
                await self._database_probe()
                await self._schema_probe()
            except Exception as exc:
                self.readiness.record_failure(
                    StartupFailure.claim_probe_failed,
                    f"readiness probe failed: {type(exc).__name__}",
                )
                logger.warning(
                    "simulation worker readiness probe failed: %s",
                    type(exc).__name__,
                )
            else:
                self.readiness.record_heartbeat(monotonic_now=now)
            if self._heartbeat is not None:
                await self._heartbeat.publish_once()
            await asyncio.sleep(self._probe_seconds)


def _install_signal_handlers(
    service: SimulationWorkerService,
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


def _configure_logging(level: str) -> None:
    """Log to stderr with a redacted, machine-greppable prefix."""

    resolved = getattr(logging, str(level).upper(), logging.INFO)
    root = logging.getLogger()
    if not any(getattr(h, "_scn_worker", False) for h in root.handlers):
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        handler._scn_worker = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(resolved)


def build_solver_executor(
    solver_command: Sequence[str],
) -> SubprocessSimulationExecutor:
    """Production executor: one solver process per attempt, ``shell=False``."""

    argv = tuple(solver_command)
    return SubprocessSimulationExecutor(lambda _claim, _workspace: argv)


def build_service(
    *,
    worker_id: str | None = None,
    solver_command: Sequence[str] | None = None,
    sandbox_root: Path | None = None,
    health_host: str | None = None,
    health_port: int | None = None,
    log_level: str | None = None,
    database_probe: Callable[[], Awaitable[None]] = _database_reachable,
    schema_probe: Callable[[], Awaitable[None]] = _schema_matches,
    repository: TaskRepository | None = None,
    startup_report: StartupReport | None = None,
    registration_heartbeat: WorkerRegistrationHeartbeat | None = None,
    runtime_config: WorkerRuntimeConfig | None = None,
) -> SimulationWorkerService:
    """Assemble the real worker service from config and a live database."""

    if log_level is not None:
        _configure_logging(log_level)
    config = runtime_config or WorkerRuntimeConfig.from_environ()
    argv = resolve_solver_command(
        solver_command if solver_command is not None else config.solver_argv
    )
    root = Path(sandbox_root or settings.HYDRAULIC_SANDBOX_ROOT)
    task_repository = repository or SessionSimulationTaskRepository(
        _session_factory(),
        lease_seconds=settings.SIM_LEASE_SECONDS,
        attempt_timeout_seconds=settings.SIM_ATTEMPT_TIMEOUT_SECONDS,
    )
    worker = SimulationWorker(
        task_repository,
        build_solver_executor(argv),
        sandbox_root=root,
        worker_id=worker_id,
        poll_seconds=float(settings.SIM_CANCEL_POLL_SECONDS),
        heartbeat_seconds=float(settings.SIM_HEARTBEAT_SECONDS),
        workspace_max_bytes=settings.SIM_WORKSPACE_MAX_BYTES,
        input_max_bytes=settings.SIM_INPUT_MAX_BYTES,
    )
    readiness = WorkerReadiness(worker_id=worker.worker_id)
    if startup_report is not None:
        readiness.record_startup(startup_report)
    server = _HealthServer(
        readiness,
        host=config.health_host if health_host is None else health_host,
        port=config.health_port if health_port is None else health_port,
        heartbeat_max_age=config.heartbeat_max_age,
    )
    return SimulationWorkerService(
        worker,
        readiness,
        health_server=server,
        heartbeat=registration_heartbeat,
        idle_seconds=config.idle_seconds,
        probe_seconds=config.probe_seconds,
        heartbeat_max_age=config.heartbeat_max_age,
        shutdown_grace_seconds=config.shutdown_grace_seconds,
        database_probe=database_probe,
        schema_probe=schema_probe,
    )


def _session_factory() -> Any:
    from app.core.database import get_session_factory

    return get_session_factory()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.workers.simulation_worker",
        description=(
            "Claim and run leased simulation attempts until SIGTERM. "
            "Exits non-zero on every abnormal path."
        ),
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override LOG_LEVEL for this process.",
    )
    parser.add_argument(
        "--worker-id",
        default=None,
        help="Stable worker identity written to the lease (default: random).",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help=(
            "Run the fail-closed startup checks, print the report, and exit "
            "without claiming any task."
        ),
    )
    return parser


async def _run_preflight(
    solver_command: Sequence[str] | None = None,
) -> StartupReport:
    return await run_startup_checks(
        sandbox_root=Path(settings.HYDRAULIC_SANDBOX_ROOT),
        result_root=Path(settings.HYDRAULIC_EXPORT_ROOT),
        solver_command=resolve_solver_command(solver_command),
    )


async def amain(argv: Sequence[str] | None = None) -> int:
    """Async entry point: preflight, serve, and shut down gracefully."""

    args = build_parser().parse_args(list(argv) if argv is not None else None)
    _configure_logging(args.log_level or settings.LOG_LEVEL)
    try:
        config = WorkerRuntimeConfig.from_environ()
        solver_command = resolve_solver_command(config.solver_argv)
    except WorkerExecutionError as exc:
        logger.error(
            "simulation worker startup rejected reason=%s detail=%s",
            exc.error_code,
            exc,
        )
        return EXIT_STARTUP_UNAVAILABLE
    except (ValueError, TypeError) as exc:
        logger.error("simulation worker configuration invalid: %s", exc)
        return EXIT_CONFIG_INVALID

    if args.preflight_only:
        report = await _run_preflight(solver_command)
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

    report = await _run_preflight(solver_command)
    if not report.ready:
        # Fail closed: an unusable worker must not claim and fail every task.
        logger.error(
            "simulation worker startup rejected reason=%s detail=%s",
            report.reason.value,
            report.detail,
        )
        return EXIT_STARTUP_UNAVAILABLE
    logger.info(
        "simulation worker startup checks passed checks=%s",
        json.dumps(dict(report.checks), sort_keys=True),
    )

    service = build_service(
        worker_id=args.worker_id,
        solver_command=solver_command,
        startup_report=report,
        runtime_config=config,
        # The API's /readyz reads hydraulic:worker:heartbeat:simulation and
        # fails closed when it is absent or stale, so the process that actually
        # runs simulations is the one that must publish it.  Without this the
        # key is never written and the API could never report ready for a
        # reason that has nothing to do with simulation health.
        registration_heartbeat=WorkerRegistrationHeartbeat(),
    )
    grace = config.shutdown_grace_seconds
    uninstall = _install_signal_handlers(service)
    runner = asyncio.ensure_future(service.run())
    stopper = asyncio.ensure_future(service.wait_for_stop())
    try:
        # Either the loop ends on its own or a signal asked it to drain.
        await asyncio.wait(
            {runner, stopper},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        uninstall()
        stopper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stopper
    if not runner.done():
        # The Compose grace period expired with work in flight.  Cancelling the
        # loop runs the ``run_once`` cancel path, which kills the solver process
        # tree, cleans the sandbox, and marks the attempt cancelled; a hard exit
        # here still cannot fake a completion.
        logger.warning(
            "simulation worker grace period expired; cancelling in-flight attempt"
        )
        service.request_stop(StopSource.lifecycle)
        try:
            await asyncio.wait_for(asyncio.shield(runner), timeout=grace)
        except TimeoutError:
            logger.error("simulation worker did not stop within the grace period")
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
            return EXIT_RUNTIME_FAILED
    return int(await runner)


def main(argv: Sequence[str] | None = None) -> int:
    """Process entry point for ``python -m app.workers.simulation_worker``."""

    try:
        return asyncio.run(amain(argv))
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        return EXIT_INTERRUPTED


__all__ = [
    "CallbackSimulationExecutor",
    "EXIT_CONFIG_INVALID",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_RUNTIME_FAILED",
    "EXIT_STARTUP_UNAVAILABLE",
    "ExecutionHandle",
    "ExecutionResult",
    "SimulationExecutor",
    "SimulationWorker",
    "SimulationWorkerService",
    "StartupFailure",
    "StartupReport",
    "StopSource",
    "SubprocessSimulationExecutor",
    "WorkerExecutionError",
    "WorkerReadiness",
    "WorkerRegistrationHeartbeat",
    "WorkerRunResult",
    "WorkerRuntimeConfig",
    "WorkerStopReason",
    "WORKER_HEARTBEAT_KEY",
    "amain",
    "build_parser",
    "build_service",
    "build_solver_executor",
    "main",
    "parse_solver_argv",
    "redacted_startup_target",
    "resolve_solver_command",
    "run_startup_checks",
]


if __name__ == "__main__":
    raise SystemExit(main())
