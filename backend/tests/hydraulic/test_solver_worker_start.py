"""Worker-start tests: the claim loop must run the real solver process.

``backend/app/workers/simulation_worker.py`` resolves
``SIMULATION_SOLVER_COMMAND`` and hands the argv to
``SubprocessSimulationExecutor``, which starts it with ``shell=False``, the
attempt workspace as the working directory, and ``HYDRAULIC_SANDBOX_PATH`` /
``SIMULATION_ID`` / ``SIMULATION_ATTEMPT`` in the environment.  These tests
drive that whole path against the in-memory task repository, so a wired-but-broken
solver command cannot be mistaken for a working one.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from solver_fixtures import read_json, solver_input_document, write_input

from app.services.hydraulic.solvers.cli import (
    EXIT_OK,
    FRAMES_DIRNAME,
    SOLVER_RESULT_FILENAME,
)
from app.services.hydraulic.solvers.cli import main as cli_main
from app.services.hydraulic.tasks import InMemoryTaskRepository, TaskKind, TaskStatus
from app.workers.simulation_worker import (
    SimulationWorker,
    SubprocessSimulationExecutor,
    WorkerRunResult,
    WorkerStopReason,
    parse_solver_argv,
    resolve_solver_command,
)

UTC = timezone.utc
START = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-0000-0000-000000000001")
BACKEND_DIR = Path(__file__).resolve().parents[2]

#: Exactly the argv ``docker-compose.yml`` wires into the worker.
COMPOSE_COMMAND = "python,-m,app.services.hydraulic.solvers.cli"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _solver_argv() -> tuple[str, ...]:
    """The configured argv, rooted at the interpreter running the tests.

    ``resolve_solver_command`` only requires that argv[0] resolves; the process
    it starts must be the same interpreter so ``app`` is importable from the
    repository checkout.
    """

    return (sys.executable, *parse_solver_argv(COMPOSE_COMMAND)[1:])


async def _submit(repository: InMemoryTaskRepository, key: str) -> UUID:
    submitted = await repository.submit(
        kind=TaskKind.simulation,
        user_id=USER_ID,
        idempotency_key=key,
        request_fingerprint=_fingerprint(key),
        now=START,
    )
    return submitted.resource.id


def _worker(
    repository: InMemoryTaskRepository,
    sandbox_root: Path,
    argv: tuple[str, ...] | None = None,
    *,
    worker_id: str = "solver-worker",
) -> SimulationWorker:
    command = argv if argv is not None else _solver_argv()
    return SimulationWorker(
        repository,
        SubprocessSimulationExecutor(lambda _claim, _workspace: list(command)),
        sandbox_root=sandbox_root,
        worker_id=worker_id,
    )


@pytest.fixture(autouse=True)
def _pythonpath() -> Any:
    previous = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(BACKEND_DIR)
    yield
    if previous is None:
        os.environ.pop("PYTHONPATH", None)
    else:
        os.environ["PYTHONPATH"] = previous


# ---------------------------------------------------------------------------
# the configured command
# ---------------------------------------------------------------------------


def test_the_configured_command_resolves_to_a_real_module() -> None:
    argv = resolve_solver_command(parse_solver_argv(COMPOSE_COMMAND))
    assert argv == ("python", "-m", "app.services.hydraulic.solvers.cli")

    completed = subprocess.run(
        [sys.executable, "-m", "app.services.hydraulic.solvers.cli", "--help"],
        cwd=str(BACKEND_DIR),
        env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--workspace" in completed.stdout
    assert "solver_input.json" in completed.stdout


def test_the_executor_never_uses_a_shell() -> None:
    text = inspect.getsource(SubprocessSimulationExecutor.start)
    assert "shell=False" in text
    assert "shell=True" not in text


def test_exit_code_zero_is_the_only_committed_result() -> None:
    text = inspect.getsource(SubprocessSimulationExecutor.start)
    assert "result_committed=return_code == 0" in text


# ---------------------------------------------------------------------------
# the claim loop drives the real solver
# ---------------------------------------------------------------------------


async def test_the_worker_runs_the_real_solver_and_completes_the_attempt(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    task_id = await _submit(repository, key="solver-run")
    original_attach = repository.attach_workspace

    async def attach_and_materialise(claim: Any, workspace_ref: str) -> Any:
        state = await original_attach(claim, workspace_ref)
        document = solver_input_document()
        document["run"] = {
            **document["run"],
            "simulation_id": str(claim.task_id),
            "attempt_id": str(claim.attempt_id),
            "attempt_no": claim.attempt_no,
            "fencing_token": claim.fencing_token,
        }
        write_input(Path(workspace_ref), document)
        return state

    repository.attach_workspace = attach_and_materialise  # type: ignore[method-assign]
    worker = _worker(repository, tmp_path / "sandbox")

    result: WorkerRunResult | None = await worker.run_once()

    assert result is not None
    assert result.status is TaskStatus.completed
    assert result.reason == WorkerStopReason.completed.value
    state = await repository.get(task_id)
    assert state.status is TaskStatus.completed
    assert state.progress == 100


async def test_the_solver_sees_the_frozen_workspace_and_attempt_identity(
    tmp_path: Path,
) -> None:
    """The subprocess must observe the exact environment the CLI relies on."""

    probe_script = (
        "import json, os, pathlib, sys;"
        "pathlib.Path(os.environ['PROBE_OUT']).write_text("
        "json.dumps({'cwd': os.getcwd(),"
        "'sandbox': os.environ.get('HYDRAULIC_SANDBOX_PATH', ''),"
        "'simulation_id': os.environ.get('SIMULATION_ID', ''),"
        "'attempt': os.environ.get('SIMULATION_ATTEMPT', '')}),"
        " encoding='utf-8');"
        "sys.exit(0)"
    )
    probe_out = tmp_path / "probe.json"
    os.environ["PROBE_OUT"] = str(probe_out)
    try:
        repository = InMemoryTaskRepository()
        task_id = await _submit(repository, key="env-probe")
        worker = _worker(
            repository,
            tmp_path / "sandbox",
            argv=(
                sys.executable,
                "-c",
                probe_script,
            ),
            worker_id="probe-worker",
        )

        result = await worker.run_once()
    finally:
        os.environ.pop("PROBE_OUT", None)

    assert result is not None
    assert result.status is TaskStatus.completed
    observed = json.loads(probe_out.read_text(encoding="utf-8"))
    workspace = Path(observed["cwd"]).resolve()
    assert observed["sandbox"] == str(workspace)
    assert workspace.parent.name == str(task_id)
    assert workspace.name == "attempt-1"
    assert observed["simulation_id"] == str(task_id)
    assert observed["attempt"] == "1"


async def test_the_solver_only_ever_writes_below_the_attempt_workspace(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    await _submit(repository, key="workspace-containment")
    seen: list[Path] = []

    async def capture(claim: Any, workspace_ref: str) -> Any:
        seen.append(Path(workspace_ref).resolve())
        return None

    repository.attach_workspace = capture  # type: ignore[method-assign]
    worker = _worker(
        repository,
        tmp_path / "sandbox",
        argv=(sys.executable, "-c", "pass"),
        worker_id="containment-worker",
    )

    await worker.run_once()

    assert seen, "the worker must publish the attempt workspace before solving"
    root = (tmp_path / "sandbox").resolve()
    for path in seen:
        assert path.is_relative_to(root)
        assert path != root


async def test_a_solver_that_exits_non_zero_never_completes_the_attempt(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    task_id = await _submit(repository, key="failing-solver")
    worker = _worker(
        repository,
        tmp_path / "sandbox",
        argv=(sys.executable, "-c", "raise SystemExit(4)"),
        worker_id="failing-worker",
    )

    result = await worker.run_once()

    assert result is not None
    assert result.status is not TaskStatus.completed
    state = await repository.get(task_id)
    assert state.status in {
        TaskStatus.retry_wait,
        TaskStatus.failed,
        TaskStatus.cancelled,
    }


async def test_a_missing_solver_executable_fails_the_attempt(tmp_path: Path) -> None:
    repository = InMemoryTaskRepository()
    task_id = await _submit(repository, key="absent-solver")
    worker = _worker(
        repository,
        tmp_path / "sandbox",
        argv=("scn-solver-that-does-not-exist",),
        worker_id="absent-worker",
    )

    result = await worker.run_once()

    assert result is not None
    assert result.status is not TaskStatus.completed
    assert (await repository.get(task_id)).status is not TaskStatus.completed


async def test_a_zero_exit_without_a_result_publishes_no_frames(
    tmp_path: Path,
) -> None:
    """``exit 0`` with nothing written is the anti-pattern the fail-closed rule
    exists to prevent: no frame set may appear, and no result may be claimed."""

    repository = InMemoryTaskRepository()
    worker = _worker(
        repository,
        tmp_path / "sandbox",
        argv=(sys.executable, "-c", "pass"),
        worker_id="empty-worker",
    )

    await worker.run_once()

    assert not list(tmp_path.rglob(SOLVER_RESULT_FILENAME))
    assert not list(tmp_path.rglob(f"{FRAMES_DIRNAME}/frame-*.json"))


# ---------------------------------------------------------------------------
# the physics behind the exit code
# ---------------------------------------------------------------------------


def test_a_full_run_reports_a_real_mass_balanced_result(tmp_path: Path) -> None:
    """A green exit code must be backed by a computed, balanced result."""

    workspace = tmp_path / "attempt"
    workspace.mkdir()
    write_input(workspace, solver_input_document())

    code = cli_main(["--workspace", str(workspace)])

    assert code == EXIT_OK
    result = read_json(workspace / SOLVER_RESULT_FILENAME)
    summary = result["summary"]
    assert summary["frame_count"] == len(result["frames"]) == 6
    assert summary["total_input_m3"] > 0.0
    assert summary["outfall_m3"] > 0.0
    assert summary["final_storage_m3"] > 0.0
    assert summary["max_mass_residual_relative"] <= 1.0e-3
    assert summary["max_courant"] <= 0.5
    assert summary["solver_id"] == "py_mw"
    first = result["frames"][0]
    assert (
        first["payload_sha256"]
        == hashlib.sha256((workspace / first["file"]).read_bytes()).hexdigest()
    )
    frame = read_json(workspace / first["file"])
    assert frame["node_count"] == 4
    assert frame["pipe_count"] == 3
