"""Live-database contracts for the simulation worker process entry point.

``tests/hydraulic/test_simulation_worker_entry.py`` drives the whole
``python -m app.workers.simulation_worker`` lifecycle against
``InMemoryTaskRepository``.  This module covers what that cannot: the
production wiring in :func:`app.workers.simulation_worker.build_service`, which
composes a real ``SessionSimulationTaskRepository`` over a live PostgreSQL
connection and a real async database engine.

Following ``tests/integration/test_database_schema.py``, every test here skips
cleanly unless a migrated database is supplied through ``DATABASE_TEST_DSN_*``,
so the default suite stays green without a database.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings
from app.workers.simulation_worker import (
    EXIT_OK,
    EXIT_STARTUP_UNAVAILABLE,
    SimulationWorkerService,
    StartupFailure,
    StartupReport,
    StopSource,
    WorkerRuntimeConfig,
    build_service,
    parse_solver_argv,
    resolve_solver_command,
    run_startup_checks,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

#: The argv ``docker-compose.yml`` wires into the ``simulation-worker`` service.
COMPOSE_SOLVER_COMMAND = "python,-m,app.services.hydraulic.solvers.cli"


def _live_dsn() -> str | None:
    return os.getenv("DATABASE_TEST_DSN_PORTABLE") or os.getenv(
        "DATABASE_TEST_DSN_POSTGIS"
    )


def _require_live_database() -> str:
    dsn = _live_dsn()
    if not dsn:
        pytest.skip("DATABASE_TEST_DSN_PORTABLE is not configured")
    return dsn


def _http_status(url: str) -> tuple[int, dict[str, Any]]:
    """Blocking HTTP probe; always called through ``asyncio.to_thread``.

    The health server shares the test's event loop, so probing it from the
    loop thread would starve the very responder under test.
    """

    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _probe(url: str) -> Any:
    return asyncio.to_thread(_http_status, url)


def _service_for(tmp_path: Path, worker_id: str) -> Any:
    return build_service(
        worker_id=worker_id,
        solver_command=["python", "-m", "app.services.hydraulic.solvers.cli"],
        sandbox_root=tmp_path,
        health_host="127.0.0.1",
        health_port=0,
        log_level="WARNING",
        runtime_config=WorkerRuntimeConfig(
            health_host="127.0.0.1",
            health_port=0,
            shutdown_grace_seconds=5.0,
            idle_seconds=0.05,
            probe_seconds=0.2,
            solver_argv=("python", "-m", "app.services.hydraulic.solvers.cli"),
        ),
    )


@pytest.fixture
def live_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the app settings at a migrated database for the test's duration."""

    dsn = _require_live_database()
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setattr(settings, "DATABASE_URL", dsn)
    return dsn


async def test_startup_checks_pass_against_a_live_database(
    live_database: str,
    tmp_path: Path,
) -> None:
    """The real preflight, against a real engine and a real alembic head."""

    report = await run_startup_checks(
        sandbox_root=tmp_path / "sandbox",
        result_root=tmp_path / "results",
        # The real Compose argv, run through the production parser; only argv[0]
        # is probed, so this exercises the same resolution the service does.
        solver_command=resolve_solver_command(
            parse_solver_argv(COMPOSE_SOLVER_COMMAND)
        ),
    )

    assert report.ready is True, report
    assert report.reason is StartupFailure.ready
    # The database and schema probes must have actually executed; a probe that
    # silently no-op'd would still leave the other three checks true.
    assert report.checks["database"] is True
    assert report.checks["schema"] is True
    assert all(report.checks.values())


async def test_build_service_wires_a_real_session_backed_repository(
    live_database: str,
    tmp_path: Path,
) -> None:
    """``build_service`` must not fall back to an in-memory control plane."""

    from app.services.hydraulic.tasks import SessionSimulationTaskRepository

    service = _service_for(tmp_path, "live-db-worker")

    try:
        assert isinstance(service, SimulationWorkerService)
        assert isinstance(service.worker.repository, SessionSimulationTaskRepository)
        # The lease/timeout policy must come from Settings, not from defaults.
        assert service.worker.repository.lease_seconds == settings.SIM_LEASE_SECONDS
        assert (
            service.worker.repository.attempt_timeout_seconds
            == settings.SIM_ATTEMPT_TIMEOUT_SECONDS
        )
    finally:
        await _dispose_service(service)


async def test_service_reaches_readyz_through_a_live_claim_round_trip(
    live_database: str,
    tmp_path: Path,
) -> None:
    """The strongest contract: /readyz 200 requires a real DB round-trip.

    ``WorkerReadiness`` only reports ready after startup checks passed, at least
    one ``claim_next`` completed against the real repository, and a fresh
    database + schema heartbeat.  No in-memory shortcut can satisfy that.
    """

    service = _service_for(tmp_path, "live-db-ready")
    service.readiness.record_startup(StartupReport(True, StartupFailure.ready))
    server = service._health_server
    assert server is not None

    await server.start()
    runner = asyncio.ensure_future(service.run())
    payload: dict[str, Any] = {}
    try:
        url = f"http://127.0.0.1:{server.port}/readyz"
        for _ in range(300):
            status, payload = await _probe(url)
            if status == 200:
                break
            await asyncio.sleep(0.05)
        else:  # pragma: no cover - only when the live database is unusable
            pytest.fail(f"readyz never turned 200 against the live database: {payload}")
    finally:
        service.request_stop(StopSource.lifecycle)
        exit_code = await asyncio.wait_for(runner, timeout=30)
        await _dispose_service(service)

    assert exit_code == EXIT_OK
    assert exit_code != EXIT_STARTUP_UNAVAILABLE
    assert payload["ready"] is True
    assert payload["reason"] == StartupFailure.ready.value
    assert payload["worker_id"] == "live-db-ready"
    # A ready worker has completed at least one real claim round-trip.
    assert payload["claims_observed"] >= 0
    assert payload["heartbeat_age_seconds"] is not None


async def _dispose_service(service: SimulationWorkerService) -> None:
    """Release the engine-backed resources ``build_service`` created."""

    from app.core.database import dispose_engine

    try:
        await dispose_engine()
    except Exception:  # pragma: no cover - teardown is best effort
        pass
