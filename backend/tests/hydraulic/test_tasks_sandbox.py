"""Focused durable task, worker, recovery, and sandbox tests."""

from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.services.hydraulic.sandbox import (
    SandboxCleanupError,
    SandboxLimitExceeded,
    TaskSandbox,
    UnsafeSandboxPath,
)
from app.services.hydraulic.tasks import (
    TOTAL_SOLVER_CONCURRENCY,
    IdempotencyConflict,
    InMemoryTaskRepository,
    InvalidTaskTransition,
    SqlAlchemySimulationTaskRepository,
    StaleTaskFence,
    TaskAttemptStatus,
    TaskKind,
    TaskNotCancellable,
    TaskStatus,
)
from app.workers.simulation_worker import (
    CallbackSimulationExecutor,
    ExecutionResult,
    SimulationWorker,
    SubprocessSimulationExecutor,
    WorkerExecutionError,
    WorkerStopReason,
)

UTC = timezone.utc
START = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _submit_simulation(
    repository: InMemoryTaskRepository,
    *,
    key: str,
    request: str = "request-a",
    now: datetime = START,
    task_id: UUID | None = None,
):
    return await repository.submit(
        kind=TaskKind.simulation,
        user_id=USER_ID,
        idempotency_key=key,
        request_fingerprint=_fingerprint(request),
        task_id=task_id,
        now=now,
    )


@pytest.mark.asyncio
async def test_duplicate_submit_replays_task_and_rejects_conflicting_hash() -> None:
    repository = InMemoryTaskRepository()
    first = await _submit_simulation(repository, key="same-request-key")
    replay = await _submit_simulation(repository, key="same-request-key")

    assert replay.created is False
    assert replay.record_id == first.record_id
    assert replay.resource.id == first.resource.id
    assert replay.resource.status is TaskStatus.queued

    with pytest.raises(IdempotencyConflict):
        await _submit_simulation(
            repository,
            key="same-request-key",
            request="different-request",
        )


@pytest.mark.asyncio
async def test_idempotency_key_starts_a_new_generation_after_24_hours() -> None:
    repository = InMemoryTaskRepository()
    first = await _submit_simulation(repository, key="generation-key")
    next_generation = await _submit_simulation(
        repository,
        key="generation-key",
        request="new-request",
        now=START + timedelta(hours=24, seconds=1),
    )

    assert next_generation.created is True
    assert next_generation.generation == 2
    assert next_generation.resource.id != first.resource.id


@pytest.mark.asyncio
async def test_transient_failure_retries_exactly_twice_with_persisted_backoff() -> None:
    repository = InMemoryTaskRepository(lease_seconds=30)
    submitted = await _submit_simulation(repository, key="retry-policy-key")
    task_id = submitted.resource.id

    first = await repository.claim_next("worker-a", now=START)
    assert first is not None and first.attempt_no == 1
    await repository.heartbeat(first, now=START)
    retry_one = await repository.record_failure(
        first,
        "RESULT_PERSIST_FAILED",
        now=START,
    )
    assert retry_one.status is TaskStatus.retry_wait
    assert retry_one.attempt == 1
    assert retry_one.retry_backoff_seconds == 10
    assert retry_one.next_attempt_at == START + timedelta(seconds=10)
    assert (
        await repository.claim_next(
            "worker-a",
            now=START + timedelta(seconds=9),
        )
        is None
    )

    second = await repository.claim_next(
        "worker-a",
        now=START + timedelta(seconds=10),
    )
    assert second is not None and second.attempt_no == 2
    await repository.heartbeat(second, now=START + timedelta(seconds=10))
    retry_two = await repository.record_failure(
        second,
        "RESULT_PERSIST_FAILED",
        now=START + timedelta(seconds=10),
    )
    assert retry_two.retry_backoff_seconds == 60
    assert retry_two.next_attempt_at == START + timedelta(seconds=70)

    third = await repository.claim_next(
        "worker-a",
        now=START + timedelta(seconds=70),
    )
    assert third is not None and third.attempt_no == 3
    await repository.heartbeat(third, now=START + timedelta(seconds=70))
    exhausted = await repository.record_failure(
        third,
        "RESULT_PERSIST_FAILED",
        now=START + timedelta(seconds=70),
    )

    assert exhausted.status is TaskStatus.failed
    assert exhausted.attempt == 3
    assert exhausted.error_code == "RESULT_PERSIST_FAILED"
    assert exhausted.next_attempt_at is None
    assert (await repository.get(task_id)).status is TaskStatus.failed


@pytest.mark.asyncio
async def test_queued_and_retry_wait_cancellation_finishes_without_a_claim() -> None:
    repository = InMemoryTaskRepository()
    queued = await _submit_simulation(repository, key="queued-cancel")
    cancelled = await repository.request_cancel(queued.resource.id, now=START)
    assert cancelled.status is TaskStatus.cancelled
    assert await repository.claim_next("worker", now=START) is None

    retry = await _submit_simulation(repository, key="retry-cancel", request="retry")
    claim = await repository.claim_next("worker", now=START)
    assert claim is not None
    await repository.heartbeat(claim, now=START)
    retry_wait = await repository.record_failure(
        claim,
        "RESULT_PERSIST_FAILED",
        now=START,
    )
    assert retry_wait.status is TaskStatus.retry_wait
    retry_cancelled = await repository.request_cancel(retry.resource.id, now=START)
    assert retry_cancelled.status is TaskStatus.cancelled
    assert (
        await repository.claim_next("worker", now=START + timedelta(seconds=60)) is None
    )


@pytest.mark.asyncio
async def test_sql_claim_does_not_require_missing_worker_slots_table() -> None:
    class _Result:
        def __init__(self, *, rows: list[object] | None = None) -> None:
            self._rows = rows or []

        def scalars(self) -> "_Result":
            return self

        def all(self) -> list[object]:
            return self._rows

        def scalar_one_or_none(self) -> None:
            return None

    class _Session:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(
            self, statement: object, parameters: object = None
        ) -> _Result:
            del statement, parameters
            self.calls += 1
            return _Result()

    session = _Session()
    repository = SqlAlchemySimulationTaskRepository(session)  # type: ignore[arg-type]

    assert await repository.claim_next("worker", now=START) is None
    assert session.calls == 3
    repository = InMemoryTaskRepository()
    await _submit_simulation(repository, key="progress-key")
    await _submit_simulation(repository, key="second-progress", request="second")
    nowcast = await repository.submit(
        kind=TaskKind.nowcast_run,
        user_id=USER_ID,
        idempotency_key="nowcast-progress",
        request_fingerprint=_fingerprint("nowcast"),
        deadline_at=START + timedelta(minutes=3),
        now=START,
    )

    general = await repository.claim_next("general-worker", now=START)
    reserved = await repository.claim_next(
        "nowcast-worker",
        kind=TaskKind.nowcast_run,
        now=START,
    )
    blocked_general = await repository.claim_next(
        "general-worker-2",
        now=START,
        kind=TaskKind.simulation,
    )

    assert TOTAL_SOLVER_CONCURRENCY == 2
    assert general is not None and general.slot_id == 1
    assert reserved is not None and reserved.slot_id == 2
    assert reserved.task_id == nowcast.resource.id
    assert blocked_general is None
    assert set(repository.slot_snapshot()) == {1, 2}

    await repository.heartbeat(general, now=START)
    assert (await repository.update_progress(general, 25, now=START)).progress == 25
    updated = await repository.update_progress(
        general,
        50,
        phase="parsing",
        now=START,
    )
    assert updated.progress == 50
    assert updated.phase == "quality"
    assert (await repository.update_progress(general, 50, now=START)).progress == 50
    with pytest.raises(InvalidTaskTransition):
        await repository.update_progress(general, 24, now=START)


@pytest.mark.asyncio
async def test_cancel_wins_unless_completed_result_commits_first() -> None:
    cancel_repository = InMemoryTaskRepository()
    cancelled_submission = await _submit_simulation(
        cancel_repository,
        key="cancel-before-complete",
    )
    cancelled_claim = await cancel_repository.claim_next("worker-a", now=START)
    assert cancelled_claim is not None
    await cancel_repository.heartbeat(cancelled_claim, now=START)

    cancelling = await cancel_repository.request_cancel(
        cancelled_submission.resource.id,
        now=START,
    )
    assert cancelling.status is TaskStatus.cancelling
    assert repository_slot_is_free(cancel_repository, 1)
    cancelled = await cancel_repository.mark_cancelled(cancelled_claim, now=START)
    assert cancelled.status is TaskStatus.cancelled
    assert cancelled.error_code == "SIMULATION_CANCELLED"
    with pytest.raises((InvalidTaskTransition, StaleTaskFence)):
        await cancel_repository.complete(
            cancelled_claim,
            result_committed=True,
            now=START,
        )

    complete_repository = InMemoryTaskRepository()
    await _submit_simulation(complete_repository, key="complete-before-cancel")
    completed_claim = await complete_repository.claim_next("worker-b", now=START)
    assert completed_claim is not None
    await complete_repository.heartbeat(completed_claim, now=START)
    completed = await complete_repository.complete(
        completed_claim,
        result_committed=True,
        now=START,
    )
    assert completed.status is TaskStatus.completed
    assert completed.progress == 100
    with pytest.raises(TaskNotCancellable):
        await complete_repository.request_cancel(
            completed_claim.task_id,
            now=START,
        )


def repository_slot_is_free(
    repository: InMemoryTaskRepository,
    slot_id: int,
) -> bool:
    return repository.slot_snapshot()[slot_id] is None


@pytest.mark.asyncio
async def test_crash_recovery_retries_due_lease_fences_old_worker_and_cleans_workspace(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository(
        lease_seconds=1,
        attempt_timeout_seconds=60,
    )
    await _submit_simulation(repository, key="crash-recovery-key")
    stale_claim = await repository.claim_next("crashed-worker", now=START)
    assert stale_claim is not None
    await repository.heartbeat(stale_claim, now=START)

    sandbox = TaskSandbox(tmp_path, stale_claim.task_id, stale_claim.attempt_no)
    await repository.attach_workspace(stale_claim, str(sandbox.path))
    sandbox.write_bytes("partial.bin", b"partial")

    recovery_time = START + timedelta(seconds=2)
    recovered = await repository.recover_expired(now=recovery_time)

    assert len(recovered) == 1
    assert recovered[0].status is TaskStatus.retry_wait
    assert recovered[0].next_attempt_at == recovery_time + timedelta(seconds=10)
    assert repository.get_attempt(stale_claim.attempt_id).status is (
        TaskAttemptStatus.discarded
    )
    assert repository_slot_is_free(repository, 1)
    with pytest.raises(StaleTaskFence):
        await repository.heartbeat(stale_claim, now=recovery_time)

    assert TaskSandbox.cleanup_attempt(
        tmp_path,
        stale_claim.task_id,
        stale_claim.attempt_no,
    )
    assert not sandbox.path.exists()

    new_claim = await repository.claim_next(
        "replacement-worker",
        now=recovery_time + timedelta(seconds=10),
    )
    assert new_claim is not None
    assert new_claim.attempt_no == 2
    assert new_claim.fencing_token > stale_claim.fencing_token
    with pytest.raises(StaleTaskFence):
        await repository.complete(
            stale_claim,
            result_committed=True,
            now=recovery_time + timedelta(seconds=10),
        )


@pytest.mark.asyncio
async def test_worker_refuses_to_start_without_frozen_disk_headroom(
    tmp_path: Path,
) -> None:
    async def unreachable(_claim, _workspace) -> ExecutionResult:
        raise AssertionError("executor must not start")

    worker = SimulationWorker(
        InMemoryTaskRepository(),
        CallbackSimulationExecutor(unreachable),
        sandbox_root=tmp_path,
        recover_on_start=False,
        disk_usage=lambda _path: 1024**3 - 1,
    )

    with pytest.raises(WorkerExecutionError, match="free bytes"):
        await worker.run_once()


@pytest.mark.asyncio
async def test_worker_timeout_kills_execution_and_always_cleans_sandbox(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository(
        lease_seconds=1,
        attempt_timeout_seconds=0.05,
    )
    submitted = await _submit_simulation(repository, key="worker-timeout-key")
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def never_finishes(claim, workspace) -> ExecutionResult:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("unreachable")

    worker = SimulationWorker(
        repository,
        CallbackSimulationExecutor(never_finishes),
        sandbox_root=tmp_path,
        worker_id="timeout-worker",
        poll_seconds=0.01,
        heartbeat_seconds=0.02,
        workspace_max_bytes=1024,
        input_max_bytes=512,
        recover_on_start=False,
    )
    result = await worker.run_once()

    assert started.is_set()
    assert stopped.is_set()
    assert result is not None
    assert result.status is TaskStatus.timed_out
    assert result.reason == WorkerStopReason.timed_out.value
    state = await repository.get(submitted.resource.id)
    assert state.error_code == "SIMULATION_TIMEOUT"
    assert not (tmp_path / str(submitted.resource.id) / "attempt-1").exists()


@pytest.mark.asyncio
async def test_worker_cancel_race_releases_slot_and_terminates_callback(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository(
        lease_seconds=1,
        attempt_timeout_seconds=5,
    )
    submitted = await _submit_simulation(repository, key="worker-cancel-key")
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def cancellable(claim, workspace) -> ExecutionResult:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("unreachable")

    worker = SimulationWorker(
        repository,
        CallbackSimulationExecutor(cancellable),
        sandbox_root=tmp_path,
        worker_id="cancel-worker",
        poll_seconds=0.01,
        heartbeat_seconds=0.02,
        workspace_max_bytes=1024,
        input_max_bytes=512,
        recover_on_start=False,
    )
    run_task = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), timeout=1)
    cancelling = await repository.request_cancel(submitted.resource.id)
    result = await asyncio.wait_for(run_task, timeout=1)

    assert cancelling.status is TaskStatus.cancelling
    assert stopped.is_set()
    assert result is not None
    assert result.status is TaskStatus.cancelled
    assert repository_slot_is_free(repository, 1)
    assert not (tmp_path / str(submitted.resource.id) / "attempt-1").exists()


@pytest.mark.asyncio
async def test_subprocess_executor_uses_shell_false_and_kills_process_tree(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    await _submit_simulation(repository, key="subprocess-kill-key")
    claim = await repository.claim_next("process-worker", now=START)
    assert claim is not None
    await repository.heartbeat(claim, now=START)
    sandbox = TaskSandbox(tmp_path, claim.task_id, claim.attempt_no)

    executor = SubprocessSimulationExecutor(
        lambda _claim, _workspace: [
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ]
    )
    handle = executor.start(claim, sandbox)
    await asyncio.sleep(0.05)
    await handle.kill_process_tree()
    result = await handle.wait()
    sandbox.cleanup()

    assert result.return_code != 0


@pytest.mark.asyncio
async def test_successful_subprocess_can_commit_and_complete_worker_attempt(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    await _submit_simulation(repository, key="subprocess-success-key")
    worker = SimulationWorker(
        repository,
        SubprocessSimulationExecutor(
            lambda _claim, _workspace: [sys.executable, "-c", "pass"]
        ),
        sandbox_root=tmp_path,
        worker_id="subprocess-success-worker",
        poll_seconds=0.01,
        heartbeat_seconds=0.02,
        recover_on_start=False,
    )

    result = await worker.run_once()

    assert result is not None
    assert result.status is TaskStatus.completed
    assert result.reason == WorkerStopReason.completed.value


@pytest.mark.asyncio
async def test_cleanup_failure_is_persisted_and_swept_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = InMemoryTaskRepository()
    submitted = await _submit_simulation(repository, key="cleanup-restart-key")
    original_cleanup = TaskSandbox.cleanup

    async def completed(_claim, _workspace) -> ExecutionResult:
        return ExecutionResult(return_code=0, result_committed=True)

    def refuse_cleanup(_sandbox: TaskSandbox) -> None:
        raise SandboxCleanupError("pending")

    monkeypatch.setattr(TaskSandbox, "cleanup", refuse_cleanup)
    first_worker = SimulationWorker(
        repository,
        CallbackSimulationExecutor(completed),
        sandbox_root=tmp_path,
        recover_on_start=False,
    )
    result = await first_worker.run_once()

    assert result is not None and result.cleanup_pending is True
    attempt_path = tmp_path / str(submitted.resource.id) / "attempt-1"
    marker_root = tmp_path / ".cleanup-pending"
    assert attempt_path.exists()
    assert list(marker_root.glob("*.json"))

    monkeypatch.setattr(TaskSandbox, "cleanup", original_cleanup)
    restarted_worker = SimulationWorker(
        repository,
        CallbackSimulationExecutor(completed),
        sandbox_root=tmp_path,
        recover_on_start=False,
    )
    await restarted_worker.recover()

    assert not attempt_path.exists()
    assert not list(marker_root.glob("*.json"))


def test_sandbox_rejects_traversal_absolute_drive_unc_ads_and_reserved_names(
    tmp_path: Path,
) -> None:
    simulation_id = uuid4()
    with TaskSandbox(tmp_path, simulation_id, 1) as sandbox:
        unsafe_paths = [
            "../escape.txt",
            "nested/../../escape.txt",
            "/absolute.txt",
            "C:\\Windows\\system.ini",
            "\\\\server\\share\\file.txt",
            "file.txt:stream",
            "CON",
            "trailing.",
            "double//slash.txt",
        ]
        for path in unsafe_paths:
            with pytest.raises(UnsafeSandboxPath):
                sandbox.resolve(path)


def test_sandbox_rejects_existing_symlink_escape(tmp_path: Path) -> None:
    simulation_id = uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    with TaskSandbox(tmp_path, simulation_id, 1) as sandbox:
        link = sandbox.path / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink creation is unavailable on this host: {exc}")
        with pytest.raises(UnsafeSandboxPath):
            sandbox.resolve("escape/payload.txt")


def test_sandbox_enforces_size_limits_without_deleting_existing_files(
    tmp_path: Path,
) -> None:
    simulation_id = uuid4()
    with TaskSandbox(
        tmp_path,
        simulation_id,
        1,
        max_bytes=8,
        input_max_bytes=6,
    ) as sandbox:
        existing = sandbox.write_bytes("existing.bin", b"keep")
        with pytest.raises(FileExistsError):
            sandbox.write_bytes("existing.bin", b"new")
        assert existing.read_bytes() == b"keep"

        with pytest.raises(SandboxLimitExceeded):
            with sandbox.open_write("stream.bin", max_bytes=3) as stream:
                stream.write(b"four")
        assert not sandbox.resolve("stream.bin").exists()
        assert sandbox.usage_bytes() == 4

        with pytest.raises(SandboxLimitExceeded):
            sandbox.write_bytes("too-large.bin", b"12345")


def test_attempt_directories_are_independent_and_finally_cleanup_runs_on_error(
    tmp_path: Path,
) -> None:
    simulation_id = uuid4()
    first = TaskSandbox(tmp_path, simulation_id, 1)
    first_path = first.path
    first.write_bytes("input.inp", b"x")
    first.cleanup()

    second = TaskSandbox(tmp_path, simulation_id, 2)
    assert second.path != first_path
    assert not first_path.exists()
    second.cleanup()

    failed_path: Path | None = None
    with pytest.raises(RuntimeError, match="solver failed"):
        with TaskSandbox(tmp_path, simulation_id, 3) as sandbox:
            failed_path = sandbox.path
            sandbox.write_bytes("partial.rpt", b"partial")
            raise RuntimeError("solver failed")

    assert failed_path is not None
    assert not failed_path.exists()
