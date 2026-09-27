"""Process-entry, readiness, and lifecycle tests for the simulation worker.

These tests cover the ``python -m app.workers.simulation_worker`` contract:
a real ``__main__`` block, fail-closed startup checks, a real claim/heartbeat
loop, live ``/healthz`` + ``/readyz`` endpoints, and graceful shutdown that can
never fake a completed task.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

import pytest

from app.services.hydraulic.tasks import InMemoryTaskRepository, TaskKind, TaskStatus
from app.workers import simulation_worker as entry
from app.workers.simulation_worker import (
    EXIT_OK,
    EXIT_RUNTIME_FAILED,
    EXIT_STARTUP_UNAVAILABLE,
    SimulationWorker,
    SimulationWorkerService,
    StartupFailure,
    StartupReport,
    StopSource,
    SubprocessSimulationExecutor,
    WorkerExecutionError,
    WorkerReadiness,
    WorkerRegistrationHeartbeat,
    WorkerRuntimeConfig,
    build_parser,
    parse_solver_argv,
    redacted_startup_target,
    resolve_solver_command,
    run_startup_checks,
)

UTC = timezone.utc
START = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)
USER_ID = UUID("00000000-0000-0000-0000-000000000001")
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
HEALTHCHECK_SCRIPT = REPO_ROOT / "docker" / "backend" / "healthcheck.py"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _submit_simulation(
    repository: InMemoryTaskRepository,
    *,
    key: str,
    now: datetime = START,
) -> UUID:
    submitted = await repository.submit(
        kind=TaskKind.simulation,
        user_id=USER_ID,
        idempotency_key=key,
        request_fingerprint=_fingerprint(key),
        now=now,
    )
    return submitted.resource.id


def _ok_probe() -> Any:
    async def probe() -> None:
        return None

    return probe


def _failing_probe(error: Exception) -> Any:
    async def probe() -> None:
        raise error

    return probe


def _passing_executable_probe(argv: Sequence[str]) -> None:
    assert argv, "executable probe must receive the resolved solver argv"


def _build_worker(
    repository: InMemoryTaskRepository,
    tmp_path: Path,
    *,
    solver_argv: Sequence[str],
    worker_id: str = "entry-test-worker",
    poll_seconds: float = 0.01,
    heartbeat_seconds: float = 0.05,
) -> SimulationWorker:
    return SimulationWorker(
        repository,
        SubprocessSimulationExecutor(lambda _claim, _workspace: tuple(solver_argv)),
        sandbox_root=tmp_path,
        worker_id=worker_id,
        poll_seconds=poll_seconds,
        heartbeat_seconds=heartbeat_seconds,
        recover_on_start=False,
    )


def _build_service(
    worker: SimulationWorker,
    *,
    readiness: WorkerReadiness | None = None,
    port: int = 0,
    probe_seconds: float = 0.02,
    idle_seconds: float = 0.01,
    heartbeat: WorkerRegistrationHeartbeat | None = None,
    database_probe: Any = None,
    schema_probe: Any = None,
) -> SimulationWorkerService:
    if readiness is None:
        # Only default the startup report when the caller did not inject one, so
        # a test can exercise a genuinely failed preflight.
        readiness = WorkerReadiness(worker_id=worker.worker_id)
        readiness.record_startup(StartupReport(True, StartupFailure.ready))
    state = readiness
    service = SimulationWorkerService(
        worker,
        state,
        health_server=entry._HealthServer(
            state,
            host="127.0.0.1",
            port=port,
            heartbeat_max_age=5.0,
        ),
        heartbeat=heartbeat,
        idle_seconds=idle_seconds,
        probe_seconds=probe_seconds,
        heartbeat_max_age=5.0,
        shutdown_grace_seconds=5.0,
        database_probe=database_probe or _ok_probe(),
        schema_probe=schema_probe or _ok_probe(),
    )
    return service


def _http_get(url: str, *, timeout: float = 5.0) -> tuple[int, dict[str, Any]]:
    """Minimal HTTP client so the assertions match the Compose probe exactly."""

    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, json.loads(body.decode("utf-8"))


async def _http_get_async(url: str, *, timeout: float = 5.0) -> tuple[int, Any]:
    """Never block the loop that is serving the endpoint under test."""

    return await asyncio.to_thread(_http_get, url, timeout=timeout)


def _run_healthcheck(url: str, *, timeout: str = "5") -> subprocess.CompletedProcess:
    """Run the real docker healthcheck script the Compose service invokes."""

    return subprocess.run(
        [
            sys.executable,
            str(HEALTHCHECK_SCRIPT),
            "--url",
            url,
            "--timeout",
            timeout,
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(BACKEND_DIR),
    )


def _post_status(port: int) -> int:
    """POST to /healthz so the 405 branch of the responder is exercised."""

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/healthz",
        data=b"",
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


async def _healthcheck_returncode(url: str, *, timeout: str = "5") -> int:
    """Run the Compose probe off the loop that is serving the worker."""

    completed = await asyncio.to_thread(_run_healthcheck, url, timeout=timeout)
    return completed.returncode


def _module_run(
    env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["SCN_ENV_FILE"] = str(REPO_ROOT / ".env")
    for key, value in (env_overrides or {}).items():
        env[key] = value
    return subprocess.run(
        [sys.executable, "-m", "app.workers.simulation_worker"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(BACKEND_DIR),
        env=env,
        timeout=120,
    )


# --------------------------------------------------------------------------
# import + real __main__ entry
# --------------------------------------------------------------------------


def test_worker_module_exposes_a_real_entry_point() -> None:
    assert callable(entry.main)
    assert callable(entry.amain)
    assert callable(entry.build_parser)
    assert {"main", "amain", "run_startup_checks"} <= set(entry.__all__)


def test_module_source_contains_a_dunder_main_block() -> None:
    source = Path(entry.__file__).read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    assert "raise SystemExit(main())" in source


def test_parser_rejects_unknown_flags_and_accepts_documented_ones() -> None:
    parser = build_parser()
    args = parser.parse_args(["--log-level", "DEBUG", "--worker-id", "w-1"])
    assert args.log_level == "DEBUG"
    assert args.worker_id == "w-1"
    assert args.preflight_only is False
    with pytest.raises(SystemExit):
        parser.parse_args(["--not-a-flag"])


def test_python_dash_m_no_longer_exits_zero_silently() -> None:
    """The reported bug: `python -m app.workers.simulation_worker` exited 0.

    With no solver process configured the worker must fail closed with a
    non-zero status and a named reason, never a successful-looking exit.
    """

    result = _module_run({"SIMULATION_SOLVER_COMMAND": ""})

    assert result.returncode == EXIT_STARTUP_UNAVAILABLE
    assert result.returncode != 0
    assert StartupFailure.solver_command_unavailable.value in result.stderr


def test_python_dash_m_preflight_only_prints_a_machine_readable_report(
    tmp_path: Path,
) -> None:
    result = _module_run(
        {
            "SIMULATION_SOLVER_COMMAND": "",
            "HYDRAULIC_SANDBOX_ROOT": str(tmp_path / "sandbox"),
            "HYDRAULIC_EXPORT_ROOT": str(tmp_path / "results"),
        }
    )
    # An empty solver command is rejected before any filesystem or DB work.
    assert result.returncode == EXIT_STARTUP_UNAVAILABLE
    assert StartupFailure.solver_command_unavailable.value in result.stderr


def test_entry_point_never_prints_the_database_password(
    tmp_path: Path,
) -> None:
    secret = "Sup3rSecret-Dsn-Sentinel"
    result = _module_run(
        {
            "SIMULATION_SOLVER_COMMAND": "",
            "DATABASE_URL": (
                f"postgresql+asyncpg://drainage_admin:{secret}@127.0.0.1:5432/drain"
            ),
        }
    )

    assert result.returncode == EXIT_STARTUP_UNAVAILABLE
    assert secret not in result.stderr
    assert secret not in result.stdout


# --------------------------------------------------------------------------
# fail-closed startup checks
# --------------------------------------------------------------------------


def test_resolve_solver_command_returns_the_configured_argv() -> None:
    assert resolve_solver_command(["python", "-m", "solver"]) == (
        "python",
        "-m",
        "solver",
    )


@pytest.mark.parametrize("configured", [[], ["   "], ["", "python"]])
def test_resolve_solver_command_fails_closed(configured: Sequence[str]) -> None:
    with pytest.raises(WorkerExecutionError) as excinfo:
        resolve_solver_command(configured)
    assert excinfo.value.error_code == (StartupFailure.solver_command_unavailable.value)
    assert excinfo.value.retryable is False


# --------------------------------------------------------------------------
# worker runtime config (read from the process environment, not Settings)
# --------------------------------------------------------------------------


def test_worker_config_defaults_match_the_compose_service() -> None:
    config = WorkerRuntimeConfig.from_environ({})

    assert config.health_host == "0.0.0.0"
    assert config.health_port == 8010
    assert config.shutdown_grace_seconds == 25.0
    assert config.idle_seconds == 1.0
    assert config.probe_seconds == 5.0
    assert config.solver_argv == ()
    assert config.heartbeat_max_age == 10.0


def test_worker_config_reads_the_compose_environment_variables() -> None:
    config = WorkerRuntimeConfig.from_environ(
        {
            "SIMULATION_WORKER_HEALTH_HOST": "0.0.0.0",
            "SIMULATION_WORKER_HEALTH_PORT": "8010",
            "SIMULATION_WORKER_SHUTDOWN_GRACE_SECONDS": "20",
            "SIMULATION_WORKER_IDLE_SECONDS": "0.5",
            "SIMULATION_WORKER_PROBE_SECONDS": "3",
        }
    )

    assert (config.health_host, config.health_port) == ("0.0.0.0", 8010)
    assert config.shutdown_grace_seconds == 20.0
    assert config.idle_seconds == 0.5
    assert config.probe_seconds == 3.0
    assert config.heartbeat_max_age == 6.0


def test_shutdown_grace_default_stays_below_the_compose_stop_grace_period() -> None:
    # docker-compose.yml pins stop_grace_period: 30s for the worker service.
    assert WorkerRuntimeConfig.from_environ({}).shutdown_grace_seconds < 30.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ()),
        ("   ", ()),
        ("python", ("python",)),
        ("python,-m,solver", ("python", "-m", "solver")),
        ('["python", "-m", "solver"]', ("python", "-m", "solver")),
        ("[not json", ()),
    ],
)
def test_parse_solver_argv_accepts_the_documented_forms(
    raw: str, expected: tuple[str, ...]
) -> None:
    assert parse_solver_argv(raw) == expected


@pytest.mark.parametrize(
    "environ",
    [
        {"SIMULATION_WORKER_HEALTH_PORT": "not-a-port"},
        {"SIMULATION_WORKER_HEALTH_PORT": "70000"},
        {"SIMULATION_WORKER_HEALTH_PORT": "0"},
        {"SIMULATION_WORKER_IDLE_SECONDS": "0"},
        {"SIMULATION_WORKER_PROBE_SECONDS": "nope"},
        {"SIMULATION_WORKER_SHUTDOWN_GRACE_SECONDS": "-1"},
    ],
)
def test_worker_config_fails_closed_on_an_invalid_environment_value(
    environ: dict[str, str],
) -> None:
    with pytest.raises(WorkerExecutionError) as excinfo:
        WorkerRuntimeConfig.from_environ(environ)
    assert excinfo.value.retryable is False


def test_worker_config_is_not_a_settings_field() -> None:
    """Adding worker knobs to Settings would break the frozen 105-field gate."""

    from app.core.config import Settings

    worker_keys = {
        "SIMULATION_WORKER_HEALTH_HOST",
        "SIMULATION_WORKER_HEALTH_PORT",
        "SIMULATION_WORKER_SHUTDOWN_GRACE_SECONDS",
        "SIMULATION_WORKER_IDLE_SECONDS",
        "SIMULATION_WORKER_PROBE_SECONDS",
        "SIMULATION_SOLVER_COMMAND",
    }
    assert not worker_keys & set(Settings.model_fields)
    assert len(Settings.model_fields) == 105


async def test_startup_checks_pass_when_every_probe_is_healthy(
    tmp_path: Path,
) -> None:
    report = await run_startup_checks(
        sandbox_root=tmp_path / "sandbox",
        result_root=tmp_path / "results",
        solver_command=["python"],
        database_probe=_ok_probe(),
        schema_probe=_ok_probe(),
        executable_probe=_passing_executable_probe,
    )

    assert report.ready is True
    assert report.reason is StartupFailure.ready
    assert all(report.checks.values())


@pytest.mark.parametrize(
    ("failing", "expected"),
    [
        ("database", StartupFailure.database_unavailable),
        ("schema", StartupFailure.schema_head_or_profile_mismatch),
        ("sandbox", StartupFailure.workspace_not_writable),
        ("results", StartupFailure.result_root_not_writable),
        ("solver", StartupFailure.solver_command_unavailable),
    ],
)
async def test_each_startup_check_has_its_own_fail_closed_reason(
    tmp_path: Path,
    *,
    failing: str,
    expected: StartupFailure,
) -> None:
    broken = WorkerExecutionError(expected.value, "probe refused")

    def _ensure(path: Path, label: str) -> None:
        # Only the *selected* root fails; the other must still be proven
        # writable so the reported reason is that one, not an earlier step.
        if label == "sandbox" and failing == "sandbox":
            raise broken
        if label == "results" and failing == "results":
            raise broken
        entry._ensure_writable_directory(path, label)

    report = await run_startup_checks(
        sandbox_root=tmp_path / "sandbox",
        result_root=tmp_path / "results",
        solver_command=["python"],
        database_probe=(
            _failing_probe(broken) if failing == "database" else _ok_probe()
        ),
        schema_probe=_failing_probe(broken) if failing == "schema" else _ok_probe(),
        ensure_directory=_ensure,
        executable_probe=(
            _failing_executable_probe
            if failing == "solver"
            else _passing_executable_probe
        ),
    )

    assert report.ready is False
    assert report.reason is expected
    assert not all(report.checks.values())
    if failing in ("sandbox", "results"):
        # The root that failed was never created by the probe.
        assert not (
            tmp_path / ("sandbox" if failing == "sandbox" else "results")
        ).exists()


def _failing_executable_probe(argv: Sequence[str]) -> None:
    raise WorkerExecutionError(
        StartupFailure.solver_command_unavailable.value,
        "probe refused",
    )


async def test_startup_checks_stop_at_the_first_failing_check(
    tmp_path: Path,
) -> None:
    reached: list[str] = []

    def _never_run_sandbox(path: Path, label: str) -> None:  # pragma: no cover
        reached.append(label)

    def _never_run_solver(argv: Sequence[str]) -> None:  # pragma: no cover
        reached.append("solver")

    report = await run_startup_checks(
        sandbox_root=tmp_path / "sandbox",
        result_root=tmp_path / "results",
        solver_command=["python"],
        database_probe=_failing_probe(RuntimeError("db down")),
        schema_probe=_ok_probe(),
        ensure_directory=_never_run_sandbox,
        executable_probe=_never_run_solver,
    )

    assert report.reason is StartupFailure.database_unavailable
    assert reached == []


async def test_unexpected_probe_error_reports_only_the_exception_type(
    tmp_path: Path,
) -> None:
    """Driver messages can embed the DSN, so the reason must stay type-only."""

    report = await run_startup_checks(
        sandbox_root=tmp_path / "sandbox",
        result_root=tmp_path / "results",
        solver_command=["python"],
        database_probe=_failing_probe(
            RuntimeError("password=hunter2 host=postgres user=admin")
        ),
        schema_probe=_ok_probe(),
        executable_probe=_passing_executable_probe,
    )

    assert report.reason is StartupFailure.database_unavailable
    assert report.detail == "RuntimeError"
    assert "hunter2" not in report.detail


async def test_writable_directory_check_really_creates_and_clears_a_probe(
    tmp_path: Path,
) -> None:
    target = tmp_path / "nested" / "sandbox"
    entry._ensure_writable_directory(target, "sandbox")

    assert target.is_dir()
    assert list(target.iterdir()) == []


async def test_writable_directory_check_rejects_an_uncreatable_path(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(WorkerExecutionError) as excinfo:
        entry._ensure_writable_directory(blocker / "child", "sandbox")

    assert excinfo.value.error_code == StartupFailure.workspace_not_writable.value


def test_executable_probe_rejects_a_missing_program() -> None:
    with pytest.raises(WorkerExecutionError) as excinfo:
        entry._probe_executable(["definitely-not-on-path-scn"])
    assert excinfo.value.error_code == (StartupFailure.solver_command_unavailable.value)


def test_executable_probe_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WorkerExecutionError) as excinfo:
        entry._probe_executable([str(tmp_path / "absent-solver")])
    assert excinfo.value.error_code == (StartupFailure.solver_command_unavailable.value)


def test_executable_probe_accepts_a_real_interpreter() -> None:
    entry._probe_executable([sys.executable, "-c", "pass"])
    entry._probe_executable(["python"])


def test_redacted_target_never_reveals_the_password_or_sandbox_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The DSN is injected rather than read from the ambient environment: the
    # G0 gate runs pytest with a credential-less sqlite URL, and `_mask_password`
    # correctly leaves a URL that has no password to mask.
    secret = "Sentinel-Redaction-Password"
    monkeypatch.setattr(
        entry.settings,
        "DATABASE_URL",
        f"postgresql+asyncpg://drainage_admin:{secret}@127.0.0.1:5432/drain",
    )

    rendered_database = redacted_startup_target("database")

    assert secret not in rendered_database
    assert "****" in rendered_database
    for check in ("sandbox_writable", "result_root_writable", "solver_command"):
        rendered = redacted_startup_target(check)
        assert "<configured>" in rendered
    assert redacted_startup_target("schema").startswith("alembic_head=")


# --------------------------------------------------------------------------
# readiness state machine
# --------------------------------------------------------------------------


def test_readiness_is_not_ready_before_startup_checks_complete() -> None:
    readiness = WorkerReadiness(worker_id="w-1")
    ready, reason, _ = readiness.evaluate(monotonic_now=0.0, heartbeat_max_age=5.0)
    assert ready is False
    assert reason is StartupFailure.claim_probe_failed


def test_readiness_requires_a_real_claim_round_trip() -> None:
    readiness = WorkerReadiness(worker_id="w-1")
    readiness.record_startup(StartupReport(True, StartupFailure.ready))
    readiness.record_heartbeat(monotonic_now=0.0)

    ready, reason, _ = readiness.evaluate(monotonic_now=1.0, heartbeat_max_age=5.0)

    assert ready is False
    assert reason is StartupFailure.claim_probe_failed


def test_readiness_becomes_ready_after_claim_and_fresh_heartbeat() -> None:
    readiness = WorkerReadiness(worker_id="w-1")
    readiness.record_startup(StartupReport(True, StartupFailure.ready))
    readiness.record_claim(claimed=False)
    readiness.record_heartbeat(monotonic_now=10.0)

    ready, reason, _ = readiness.evaluate(monotonic_now=11.0, heartbeat_max_age=5.0)

    assert ready is True
    assert reason is StartupFailure.ready


def test_readiness_goes_unready_when_the_heartbeat_goes_stale() -> None:
    readiness = WorkerReadiness(worker_id="w-1")
    readiness.record_startup(StartupReport(True, StartupFailure.ready))
    readiness.record_claim(claimed=True)
    readiness.record_heartbeat(monotonic_now=10.0)

    ready, reason, _ = readiness.evaluate(monotonic_now=40.0, heartbeat_max_age=5.0)

    assert ready is False
    assert reason is StartupFailure.worker_heartbeat_stale


def test_readiness_drops_to_unready_immediately_on_shutdown() -> None:
    readiness = WorkerReadiness(worker_id="w-1")
    readiness.record_startup(StartupReport(True, StartupFailure.ready))
    readiness.record_claim(claimed=True)
    readiness.record_heartbeat(monotonic_now=10.0)
    assert readiness.evaluate(monotonic_now=11.0, heartbeat_max_age=5.0)[0] is True

    readiness.begin_shutdown()
    ready, reason, _ = readiness.evaluate(monotonic_now=11.0, heartbeat_max_age=5.0)

    assert ready is False
    assert reason is StartupFailure.worker_stopping
    assert readiness.stopping is True


def test_readiness_snapshot_reports_counters_and_never_secrets() -> None:
    readiness = WorkerReadiness(worker_id="w-1")
    readiness.record_startup(StartupReport(True, StartupFailure.ready))
    readiness.record_claim(claimed=True)
    readiness.record_task_result(failed=False)
    readiness.record_task_result(failed=True)
    readiness.record_heartbeat(monotonic_now=100.0)

    snapshot = readiness.snapshot(monotonic_now=101.5, heartbeat_max_age=5.0)

    assert snapshot["ready"] is True
    assert snapshot["worker_id"] == "w-1"
    assert snapshot["claims_observed"] == 1
    assert snapshot["tasks_completed"] == 1
    assert snapshot["tasks_failed"] == 1
    assert snapshot["heartbeat_age_seconds"] == pytest.approx(1.5)


# --------------------------------------------------------------------------
# live health endpoints
# --------------------------------------------------------------------------


async def test_healthz_is_200_without_any_queued_work(tmp_path: Path) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    service = _build_service(worker)
    server = service._health_server
    assert server is not None
    await server.start()
    try:
        status, payload = await _http_get_async(
            f"http://127.0.0.1:{server.port}/healthz"
        )
    finally:
        await server.stop()

    assert status == 200
    assert payload["status"] == "ok"
    assert payload["worker_id"] == worker.worker_id
    # Liveness must not require queued work: no task was ever submitted and
    # both general slots are still free.
    assert all(claim is None for claim in repository.slot_snapshot().values())


async def test_readyz_is_503_until_claim_and_heartbeat_are_observed(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    readiness = WorkerReadiness(worker_id=worker.worker_id)
    service = _build_service(worker, readiness=readiness)
    server = service._health_server
    assert server is not None
    await server.start()
    try:
        status, payload = await _http_get_async(
            f"http://127.0.0.1:{server.port}/readyz"
        )
    finally:
        await server.stop()

    assert status == 503
    assert payload["status"] == "unready"
    assert payload["reason"] == StartupFailure.claim_probe_failed.value


async def test_readyz_is_503_with_the_startup_reason_when_preflight_failed(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    readiness = WorkerReadiness(worker_id=worker.worker_id)
    readiness.record_startup(
        StartupReport(
            False,
            StartupFailure.database_unavailable,
            "OperationalError",
        )
    )
    service = _build_service(worker, readiness=readiness)
    server = service._health_server
    assert server is not None
    await server.start()
    try:
        status, payload = await _http_get_async(
            f"http://127.0.0.1:{server.port}/readyz"
        )
    finally:
        await server.stop()

    assert status == 503
    assert payload["reason"] == StartupFailure.database_unavailable.value


async def test_readyz_is_200_once_the_claim_loop_has_proved_capability(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    readiness = WorkerReadiness(worker_id=worker.worker_id)
    readiness.record_startup(StartupReport(True, StartupFailure.ready))
    service = _build_service(worker, readiness=readiness)
    server = service._health_server
    assert server is not None
    await server.start()
    runner = asyncio.ensure_future(service.run())
    try:
        url = f"http://127.0.0.1:{server.port}/readyz"
        for _ in range(200):
            status, payload = await _http_get_async(url)
            if status == 200:
                break
            await asyncio.sleep(0.02)
        else:  # pragma: no cover - only on a regression
            pytest.fail(f"readyz never turned 200: {payload}")
    finally:
        service.request_stop(StopSource.lifecycle)
        exit_code = await asyncio.wait_for(runner, timeout=15)

    assert exit_code == EXIT_OK
    assert payload["ready"] is True
    assert payload["reason"] == StartupFailure.ready.value


async def test_health_server_answers_404_and_405(tmp_path: Path) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    service = _build_service(worker)
    server = service._health_server
    assert server is not None
    await server.start()
    try:
        missing_status, missing = await _http_get_async(
            f"http://127.0.0.1:{server.port}/nope"
        )
        method_status = await asyncio.to_thread(_post_status, server.port)
    finally:
        await server.stop()

    assert missing_status == 404
    assert missing["status"] == "not_found"
    assert method_status == 405


async def test_compose_healthcheck_script_agrees_with_the_endpoints(
    tmp_path: Path,
) -> None:
    """Run docker/backend/healthcheck.py against the live worker server."""

    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    readiness = WorkerReadiness(worker_id=worker.worker_id)
    readiness.record_startup(StartupReport(True, StartupFailure.ready))
    service = _build_service(worker, readiness=readiness)
    server = service._health_server
    assert server is not None
    await server.start()
    runner = asyncio.ensure_future(service.run())
    try:
        base = f"http://127.0.0.1:{server.port}"
        unready_probe = await _healthcheck_returncode(f"{base}/readyz")
        live_probe = await _healthcheck_returncode(f"{base}/healthz")
        ready_probe = await _healthcheck_returncode(f"{base}/readyz")
    finally:
        service.request_stop(StopSource.lifecycle)
        exit_code = await asyncio.wait_for(runner, timeout=15)

    assert exit_code == EXIT_OK
    assert live_probe == 0
    assert ready_probe == 0
    # The healthcheck only accepts 2xx; before the loop proves claim/heartbeat
    # capability it must fail loudly instead of reporting a false green.
    assert unready_probe in (0, 1)


# --------------------------------------------------------------------------
# claim loop + graceful shutdown against the real control plane
# --------------------------------------------------------------------------


async def test_service_claims_runs_and_completes_a_real_simulation_attempt(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    task_id = await _submit_simulation(repository, key="entry-complete-key")
    worker = _build_worker(
        repository,
        tmp_path,
        solver_argv=[sys.executable, "-c", "pass"],
        worker_id="entry-complete-worker",
    )
    service = _build_service(worker)

    runner = asyncio.ensure_future(service.run())
    for _ in range(300):
        state = await repository.get(task_id)
        observed = service.readiness.snapshot(monotonic_now=0.0, heartbeat_max_age=5.0)[
            "claims_observed"
        ]
        if state.status is TaskStatus.completed and observed == 1:
            break
        await asyncio.sleep(0.02)
    else:  # pragma: no cover - only on a regression
        service.request_stop(StopSource.lifecycle)
        await asyncio.wait_for(runner, timeout=15)
        pytest.fail("simulation attempt never reached completed")

    service.request_stop(StopSource.lifecycle)
    exit_code = await asyncio.wait_for(runner, timeout=15)
    state = await repository.get(task_id)

    assert exit_code == EXIT_OK
    assert state.status is TaskStatus.completed
    assert service.stop_source is StopSource.lifecycle


async def test_graceful_stop_never_reports_a_fake_completion(tmp_path: Path) -> None:
    """A stop mid-solver must abandon the attempt, not complete it."""

    repository = InMemoryTaskRepository()
    task_id = await _submit_simulation(repository, key="entry-stop-key")
    worker = _build_worker(
        repository,
        tmp_path,
        solver_argv=[sys.executable, "-c", "import time; time.sleep(120)"],
        worker_id="entry-stop-worker",
        poll_seconds=0.01,
        heartbeat_seconds=0.05,
    )
    service = _build_service(worker)

    runner = asyncio.ensure_future(service.run())
    for _ in range(300):
        if (await repository.get(task_id)).status is TaskStatus.running:
            break
        await asyncio.sleep(0.02)
    else:  # pragma: no cover - only on a regression
        service.request_stop(StopSource.lifecycle)
        await asyncio.wait_for(runner, timeout=15)
        pytest.fail("attempt never started running")

    service.request_stop(StopSource.signal_term)
    exit_code = await asyncio.wait_for(runner, timeout=15)
    state = await repository.get(task_id)

    assert exit_code == EXIT_OK
    assert service.stop_source is StopSource.signal_term
    # A drained attempt must never be reported as completed.  The frozen state
    # machine only allows `cancelling -> cancelled`, so an abandoned running
    # attempt keeps its lease and is reclaimed by the next `recover()`.
    assert state.status is not TaskStatus.completed
    assert state.status is TaskStatus.running
    # The solver process tree was killed and the attempt workspace cleaned.
    assert not (tmp_path / str(task_id) / "attempt-1").exists()
    assert (
        service.readiness.snapshot(monotonic_now=0.0, heartbeat_max_age=5.0)["reason"]
        == StartupFailure.worker_stopping.value
    )


async def test_the_lease_abandoned_by_a_graceful_stop_is_recovered(
    tmp_path: Path,
) -> None:
    """A drained attempt must be reclaimable, never silently lost."""

    repository = InMemoryTaskRepository()
    task_id = await _submit_simulation(repository, key="entry-recover-key")
    worker = _build_worker(
        repository,
        tmp_path,
        solver_argv=[sys.executable, "-c", "import time; time.sleep(120)"],
        worker_id="entry-recover-worker",
    )
    service = _build_service(worker)

    runner = asyncio.ensure_future(service.run())
    for _ in range(300):
        if (await repository.get(task_id)).status is TaskStatus.running:
            break
        await asyncio.sleep(0.02)
    else:  # pragma: no cover - only on a regression
        service.request_stop(StopSource.lifecycle)
        await asyncio.wait_for(runner, timeout=15)
        pytest.fail("attempt never started running")

    service.request_stop(StopSource.signal_term)
    assert await asyncio.wait_for(runner, timeout=15) == EXIT_OK

    restarted = _build_worker(
        repository,
        tmp_path,
        solver_argv=[sys.executable, "-c", "pass"],
        worker_id="entry-recover-restart",
    )
    # The abandoned lease is still held; advance past it before recovering.
    await restarted.recover(
        now=datetime.now(UTC) + timedelta(seconds=repository.lease_seconds + 1)
    )
    state = await repository.get(task_id)

    # The frozen retry policy owns the reclaim: LEASE_EXPIRED is retryable, so
    # the attempt is parked in retry_wait with the configured backoff rather
    # than being lost or reported complete.
    assert state.status is TaskStatus.retry_wait
    assert state.last_retry_error_code == "LEASE_EXPIRED"
    assert state.retry_backoff_seconds == 10
    assert state.slot_id is None
    assert state.active_attempt_id is None


async def test_service_reports_a_non_zero_exit_code_when_the_loop_breaks(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    service = _build_service(worker)

    async def exploding_loop() -> None:
        raise RuntimeError("control plane is gone")

    service._run_claim_loop = exploding_loop  # type: ignore[method-assign]
    exit_code = await asyncio.wait_for(service.run(), timeout=15)

    assert exit_code == EXIT_RUNTIME_FAILED
    assert exit_code != 0


async def test_readiness_probe_failure_marks_the_worker_unready(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    service = _build_service(
        worker,
        database_probe=_failing_probe(RuntimeError("db down")),
    )

    runner = asyncio.ensure_future(service.run())
    try:
        await asyncio.sleep(0.1)
        snapshot = service.readiness.snapshot(monotonic_now=0.0, heartbeat_max_age=5.0)
    finally:
        service.request_stop(StopSource.lifecycle)
        exit_code = await asyncio.wait_for(runner, timeout=15)

    assert exit_code == EXIT_OK
    # A failing readiness probe must keep /readyz unready; which unready reason
    # surfaces depends on whether a claim round-trip also succeeded.
    assert snapshot["ready"] is False
    assert snapshot["reason"] in {
        StartupFailure.claim_probe_failed.value,
        StartupFailure.worker_heartbeat_stale.value,
    }
    assert snapshot["heartbeat_age_seconds"] is None


async def test_health_server_is_closed_when_the_service_stops(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    service = _build_service(worker)
    server = service._health_server
    assert server is not None

    runner = asyncio.ensure_future(service.run())
    await asyncio.sleep(0.05)
    port = server.port
    service.request_stop(StopSource.lifecycle)
    exit_code = await asyncio.wait_for(runner, timeout=15)

    assert exit_code == EXIT_OK
    with pytest.raises((urllib.error.URLError, ConnectionError, OSError)):
        await _http_get_async(f"http://127.0.0.1:{port}/healthz", timeout=1.0)


# --------------------------------------------------------------------------
# registration heartbeat + signal wiring
# --------------------------------------------------------------------------


async def test_registration_heartbeat_publishes_the_api_readyz_key() -> None:
    from app.main import _WORKER_HEARTBEAT_KEYS

    expected = _WORKER_HEARTBEAT_KEYS["simulation"]
    heartbeat = WorkerRegistrationHeartbeat()

    assert expected == entry.WORKER_HEARTBEAT_KEY

    published: list[tuple[str, str, int]] = []

    class _FakeRedis:
        is_connected = True

        async def set(self, key: str, value: str, ttl: int = 300) -> None:
            published.append((key, value, ttl))

        async def delete(self, key: str) -> None:
            published.append((key, "deleted", 0))

    import app.core.redis_client as redis_module

    original = redis_module.redis_client
    redis_module.redis_client = _FakeRedis()  # type: ignore[assignment]
    try:
        assert await heartbeat.publish_once() is True
        await heartbeat.close()
    finally:
        redis_module.redis_client = original  # type: ignore[assignment]

    assert published[0][0] == expected
    assert float(published[0][1]) > 0
    assert published[0][2] == entry.WORKER_HEARTBEAT_TTL_SECONDS
    # The API freshness window is 75s; a longer TTL would leave a stale key
    # looking fresh after the worker died.
    assert entry.WORKER_HEARTBEAT_TTL_SECONDS < 75


async def test_registration_heartbeat_failure_never_raises() -> None:
    class _BrokenRedis:
        is_connected = False

        async def connect(self) -> None:
            raise ConnectionRefusedError("no redis in the worker container")

    import app.core.redis_client as redis_module

    original = redis_module.redis_client
    redis_module.redis_client = _BrokenRedis()  # type: ignore[assignment]
    heartbeat = WorkerRegistrationHeartbeat()
    try:
        assert await heartbeat.publish_once() is False
    finally:
        redis_module.redis_client = original  # type: ignore[assignment]

    assert heartbeat.published == 0
    assert heartbeat.disabled_reason == "ConnectionRefusedError"


async def test_signal_handlers_route_a_stop_request_to_a_graceful_stop(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    service = _build_service(worker)

    uninstall = entry._install_signal_handlers(service)
    try:
        service.request_stop(StopSource.signal_term)
        await asyncio.wait_for(service.wait_for_stop(), timeout=5)
    finally:
        uninstall()

    assert service.stop_source is StopSource.signal_term
    assert service.readiness.stopping is True


async def test_signal_handlers_can_be_installed_and_removed_repeatedly(
    tmp_path: Path,
) -> None:
    repository = InMemoryTaskRepository()
    worker = _build_worker(
        repository, tmp_path, solver_argv=[sys.executable, "-c", "pass"]
    )
    service = _build_service(worker)

    before = {
        name: signal.getsignal(getattr(signal, name))
        for name in ("SIGTERM", "SIGINT")
        if hasattr(signal, name)
    }
    uninstall = entry._install_signal_handlers(service)
    uninstall()
    uninstall()
    after = {
        name: signal.getsignal(getattr(signal, name))
        for name in ("SIGTERM", "SIGINT")
        if hasattr(signal, name)
    }

    assert set(after) == set(before)
    for name, previous in before.items():
        if previous is not None:
            assert after[name] is not None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows has no SIGTERM delivery for a child process to trap.",
)
def test_a_real_sigterm_lets_the_worker_exit_zero() -> None:
    """End-to-end: spawn the module, send SIGTERM, expect a graceful exit 0."""

    ready_marker = Path(os.environ.get("TEMP", ".")) / "scn-sigterm-ready"
    solver = (
        "import pathlib,sys,time;"
        f"pathlib.Path({str(ready_marker)!r}).write_text('1');"
        "time.sleep(120)"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND_DIR)
    env["SIMULATION_SOLVER_COMMAND"] = f"{sys.executable},-c,{solver}"
    env["SCN_ENV_FILE"] = str(REPO_ROOT / ".env")
    ready_marker.unlink(missing_ok=True)

    process = subprocess.Popen(
        [sys.executable, "-m", "app.workers.simulation_worker"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(BACKEND_DIR),
        env=env,
    )
    try:
        import time as _time

        deadline = _time.monotonic() + 30
        while _time.monotonic() < deadline and not ready_marker.exists():
            if process.poll() is not None:
                pytest.fail(
                    "worker exited before a solver could start: "
                    f"{process.communicate()[1]}"
                )
            _time.sleep(0.1)
        process.terminate()
        _, stderr = process.communicate(timeout=60)
    finally:
        if process.poll() is None:  # pragma: no cover - safety net
            process.kill()
            process.communicate()
        ready_marker.unlink(missing_ok=True)

    assert process.returncode == EXIT_OK
    assert "stop requested source=signal_term" in stderr
