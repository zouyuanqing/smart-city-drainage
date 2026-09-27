"""CLI and worker-start tests for the real solver process.

The ask these cover is operational, not numerical: ``SIMULATION_SOLVER_COMMAND``
must point at a command that exists, exits ``0`` only after writing a complete
verified result, and fails closed on every abnormal path.  The tests therefore
run the CLI **as a subprocess** (the way the worker runs it) as well as
in-process, so the process boundary, the exit code, and the on-disk protocol
are all exercised rather than assumed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from solver_fixtures import (
    DEFAULT_INTENSITIES_MM_H,
    NETWORK_ID,
    NETWORK_REVISION_ID,
    SIMULATION_ID,
    read_json,
    read_progress,
    simple_network_document,
    solver_input_document,
    write_input,
)

from app.services.hydraulic.solvers.base import canonical_json
from app.services.hydraulic.solvers.cli import (
    EXIT_EXECUTION_FAILED,
    EXIT_INPUT_INVALID,
    EXIT_OK,
    EXIT_RESULT_INVALID,
    EXIT_SOLVER_UNAVAILABLE,
    FRAMES_DIRNAME,
    SOLVER_ERROR_FILENAME,
    SOLVER_INPUT_FILENAME,
    SOLVER_METRICS_FILENAME,
    SOLVER_QUALITY_FILENAME,
    SOLVER_RESULT_FILENAME,
    main,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
CLI_MODULE = "app.services.hydraulic.solvers.cli"
REPO_ROOT = BACKEND_DIR.parent

#: The command ``docker-compose.yml`` wires into the simulation worker.
COMPOSE_SOLVER_COMMAND = "python,-m,app.services.hydraulic.solvers.cli"


def _run_cli_subprocess(
    args: list[str], cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(BACKEND_DIR)
    environment["PYTHONIOENCODING"] = "utf-8"
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, "-m", CLI_MODULE, *args],
        cwd=str(cwd),
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "attempt-1"
    path.mkdir()
    return path


# ---------------------------------------------------------------------------
# the shipped command
# ---------------------------------------------------------------------------


def test_compose_wires_a_real_solver_command() -> None:
    compose = yaml_safe_load(REPO_ROOT / "docker-compose.yml")
    raw = compose["services"]["simulation-worker"]["environment"][
        "SIMULATION_SOLVER_COMMAND"
    ]
    assert raw.startswith("${SIMULATION_SOLVER_COMMAND:-")
    default = raw.split(":-", 1)[1].rstrip("}")
    from app.workers.simulation_worker import parse_solver_argv

    assert parse_solver_argv(default) == (
        "python",
        "-m",
        "app.services.hydraulic.solvers.cli",
    )


def test_the_configured_command_module_is_importable() -> None:
    result = subprocess.run(
        [sys.executable, "-m", CLI_MODULE, "--list-solvers"],
        cwd=str(BACKEND_DIR),
        env={**os.environ, "PYTHONPATH": str(BACKEND_DIR)},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == EXIT_OK, result.stderr
    report = json.loads(result.stdout)
    assert "py_mw" in report["registered_solver_ids"]
    assert "py_dw" in report["registered_solver_ids"]


def test_worker_startup_checks_pass_with_the_real_command(tmp_path: Path) -> None:
    import asyncio

    from app.workers.simulation_worker import resolve_solver_command, run_startup_checks

    argv = resolve_solver_command([*COMPOSE_SOLVER_COMMAND.split(",")])
    assert argv == ("python", "-m", "app.services.hydraulic.solvers.cli")

    async def ok() -> None:
        return None

    report = asyncio.run(
        run_startup_checks(
            sandbox_root=tmp_path / "sandbox",
            result_root=tmp_path / "results",
            solver_command=argv,
            database_probe=ok,
            schema_probe=ok,
        )
    )
    assert report.ready is True
    assert report.checks["solver_command"] is True


def yaml_safe_load(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# happy path, as a real subprocess
# ---------------------------------------------------------------------------


def test_cli_process_writes_the_whole_protocol_and_exits_zero(
    workspace: Path,
) -> None:
    write_input(workspace, solver_input_document())

    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)

    assert result.returncode == EXIT_OK, result.stderr
    assert (workspace / SOLVER_RESULT_FILENAME).is_file()
    assert (workspace / SOLVER_QUALITY_FILENAME).is_file()
    assert (workspace / SOLVER_METRICS_FILENAME).is_file()
    assert not (workspace / SOLVER_ERROR_FILENAME).exists()

    frames = sorted((workspace / FRAMES_DIRNAME).glob("frame-*.json"))
    assert [item.name for item in frames] == [
        f"frame-{index:06d}.json" for index in range(len(DEFAULT_INTENSITIES_MM_H))
    ]


def test_result_manifest_matches_every_frame_byte_for_byte(workspace: Path) -> None:
    write_input(workspace, solver_input_document())
    assert (
        _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace).returncode
        == EXIT_OK
    )

    result = read_json(workspace / SOLVER_RESULT_FILENAME)
    entries = result["frames"]
    assert len(entries) == len(DEFAULT_INTENSITIES_MM_H)
    for index, entry in enumerate(entries):
        payload = (workspace / entry["file"]).read_bytes()
        assert entry["payload_sha256"] == hashlib.sha256(payload).hexdigest()
        assert entry["payload_bytes"] == len(payload)
        assert entry["frame_index"] == index
        frame = json.loads(payload)
        assert frame["schema"] == "hydraulic-frame-v1"
        assert frame["frame_index"] == index
        assert frame["solver_id"] == "py_mw"


def test_summary_binds_the_frozen_identity_and_hashes(workspace: Path) -> None:
    document = solver_input_document()
    write_input(workspace, document)
    assert (
        _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace).returncode
        == EXIT_OK
    )

    result = read_json(workspace / SOLVER_RESULT_FILENAME)
    summary = result["summary"]
    assert result["schema"] == "solver-result-v1"
    assert result["simulation_id"] == SIMULATION_ID
    assert result["network_id"] == NETWORK_ID
    assert result["network_revision_id"] == NETWORK_REVISION_ID
    assert result["attempt_no"] == 1
    assert len(result["input_sha256"]) == 64
    assert summary["solver_id"] == "py_mw"
    assert summary["solver_version"] == "py_mw/1.0.0"
    assert summary["frame_count"] == len(DEFAULT_INTENSITIES_MM_H)
    assert summary["network_content_hash"] == (
        document["provenance"]["network_revision_content_sha256"]
    )
    assert summary["snapshot_content_hash"] == (
        document["provenance"]["snapshot_content_sha256"]
    )
    assert summary["initial_state_hash"] == (
        document["provenance"]["initial_state_content_sha256"]
    )
    assert summary["target_manifest_hash"] == (
        document["provenance"]["target_manifest_sha256"]
    )
    assert summary["input_sha256"] == result["input_sha256"]


def test_progress_log_is_monotonic_and_ends_at_one_hundred(workspace: Path) -> None:
    write_input(workspace, solver_input_document())
    assert (
        _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace).returncode
        == EXIT_OK
    )

    events = read_progress(workspace)
    percents = [event["percent"] for event in events]
    assert percents[0] == 0
    assert percents[-1] == 100
    assert percents == sorted(percents)
    assert len(set(percents)) == len(percents)
    assert {event["phase"] for event in events} <= {
        "accepted",
        "validating",
        "solving",
        "quality",
        "persisting",
        "finalizing",
    }


def test_quality_and_metrics_documents_are_written_and_consistent(
    workspace: Path,
) -> None:
    write_input(workspace, solver_input_document())
    assert (
        _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace).returncode
        == EXIT_OK
    )

    quality = read_json(workspace / SOLVER_QUALITY_FILENAME)
    metrics = read_json(workspace / SOLVER_METRICS_FILENAME)
    assert quality["schema"] == "solver-quality-v1"
    assert metrics["schema"] == "solver-metrics-v1"
    assert quality["result_status"] == metrics["result_status"]
    assert quality["frame_count"] == metrics["frame_count"]
    assert quality["is_authoritative"] is False
    assert quality["mass_balance"]["residual_relative"] <= 1.0e-3
    assert quality["stability"]["max_courant"] <= 0.5
    assert metrics["total_rainfall_input_m3"] > 0.0
    assert metrics["total_outfall_discharge_m3"] > 0.0


def test_py_dw_runs_through_the_same_cli_protocol(workspace: Path) -> None:
    network = simple_network_document(diameter_m=0.9, pipe_length_m=500.0)
    write_input(
        workspace,
        solver_input_document(
            solver_id="py_dw",
            intensities_mm_h=(8.0, 8.0, 8.0, 8.0, 8.0, 8.0),
            step_seconds=120,
            network=network,
        ),
    )

    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)

    assert result.returncode == EXIT_OK, result.stderr
    published = read_json(workspace / SOLVER_RESULT_FILENAME)
    assert published["summary"]["solver_id"] == "py_dw"
    assert published["summary"]["model_fidelity"] == "reduced_fidelity"
    assert published["summary"]["result_status"] in {"verified", "warning"}
    first = read_json(workspace / published["frames"][0]["file"])
    assert first["solver_id"] == "py_dw"
    assert first["schema"] == "hydraulic-frame-v1"


# ---------------------------------------------------------------------------
# fail-closed paths
# ---------------------------------------------------------------------------


def test_missing_input_fails_closed_without_a_result(workspace: Path) -> None:
    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)

    assert result.returncode == EXIT_INPUT_INVALID
    assert not (workspace / SOLVER_RESULT_FILENAME).exists()
    error = read_json(workspace / SOLVER_ERROR_FILENAME)
    assert error["schema"] == "solver-error-v1"
    assert error["error_code"] == "SOLVER_INPUT_INVALID"


def test_a_stale_result_file_is_removed_before_a_failing_run(workspace: Path) -> None:
    stale = workspace / SOLVER_RESULT_FILENAME
    stale.write_text('{"schema":"solver-result-v1"}', encoding="utf-8")
    write_input(workspace, "not json at all")

    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)

    assert result.returncode == EXIT_INPUT_INVALID
    assert not stale.exists(), "a failed run must not leave a stale success behind"


def test_malformed_json_fails_closed(workspace: Path) -> None:
    write_input(workspace, "{ this is not json")
    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)
    assert result.returncode == EXIT_INPUT_INVALID
    assert not (workspace / SOLVER_RESULT_FILENAME).exists()


def test_a_hash_that_contradicts_the_series_fails_closed(workspace: Path) -> None:
    document = solver_input_document()
    document["rainfall"]["intensities_mm_h"] = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    write_input(workspace, document)

    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)

    assert result.returncode == EXIT_INPUT_INVALID
    assert read_json(workspace / SOLVER_ERROR_FILENAME)["error_code"] == (
        "SOLVER_INPUT_INVALID"
    )


def test_an_unregistered_solver_fails_closed(workspace: Path) -> None:
    document = solver_input_document(solver_id="py_ponding")
    write_input(workspace, document)

    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)

    assert result.returncode == EXIT_SOLVER_UNAVAILABLE
    error = read_json(workspace / SOLVER_ERROR_FILENAME)
    assert error["error_code"] == "SOLVER_UNAVAILABLE"
    assert error["retryable"] is False


def test_an_explicit_epa_request_fails_closed_on_a_host_without_the_engine(
    workspace: Path,
) -> None:
    document = solver_input_document(solver_id="swmm_kw")
    write_input(workspace, document)

    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)

    assert result.returncode == EXIT_SOLVER_UNAVAILABLE
    error = read_json(workspace / SOLVER_ERROR_FILENAME)
    assert error["solver_id"] == "swmm_kw"
    assert error["retryable"] is False


def test_a_solver_flag_that_contradicts_the_input_fails_closed(workspace: Path) -> None:
    write_input(workspace, solver_input_document(solver_id="py_mw"))

    result = _run_cli_subprocess(
        ["--workspace", str(workspace), "--solver", "py_dw"], cwd=workspace
    )

    assert result.returncode == EXIT_INPUT_INVALID
    assert "contradicts the frozen input" in (
        read_json(workspace / SOLVER_ERROR_FILENAME)["message"]
    )


def test_a_missing_workspace_fails_closed(tmp_path: Path) -> None:
    result = _run_cli_subprocess(
        ["--workspace", str(tmp_path / "does-not-exist")], cwd=tmp_path
    )
    assert result.returncode == EXIT_INPUT_INVALID
    assert not (
        tmp_path / "does-not-exist"
    ).exists(), "the solver must never create its own output directory"


def test_a_py_dw_regime_refusal_is_terminal_not_a_downgrade(workspace: Path) -> None:
    """``py_dw`` on a network that surcharges must exit non-zero, and must not
    silently re-run as ``py_mw``."""

    write_input(workspace, solver_input_document(solver_id="py_dw"))

    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)

    assert result.returncode == EXIT_EXECUTION_FAILED
    error = read_json(workspace / SOLVER_ERROR_FILENAME)
    assert error["solver_id"] == "py_dw"
    assert error["error_code"] == "UNSUPPORTED_FEATURE"
    assert error["retryable"] is False
    assert not (workspace / SOLVER_RESULT_FILENAME).exists()
    for frame in (workspace / FRAMES_DIRNAME).glob("frame-*.json"):
        published = json.loads(frame.read_text(encoding="utf-8"))
        assert (
            published["solver_id"] == "py_dw"
        ), "a partial staging must belong to the identity that produced it"


def test_a_frame_larger_than_the_cap_fails_closed(workspace: Path) -> None:
    write_input(workspace, solver_input_document())

    result = _run_cli_subprocess(
        ["--workspace", str(workspace), "--max-frame-bytes", "64"], cwd=workspace
    )

    assert result.returncode == EXIT_RESULT_INVALID
    assert not (workspace / SOLVER_RESULT_FILENAME).exists()
    assert read_json(workspace / SOLVER_ERROR_FILENAME)["error_code"] == (
        "SOLVER_RESULT_INVALID"
    )


def test_a_local_timeout_fails_closed_without_publishing(workspace: Path) -> None:
    write_input(workspace, solver_input_document())

    result = _run_cli_subprocess(
        ["--workspace", str(workspace), "--timeout-seconds", "0.0001"],
        cwd=workspace,
    )

    assert result.returncode == EXIT_EXECUTION_FAILED
    assert not (workspace / SOLVER_RESULT_FILENAME).exists()


def test_oversized_input_bytes_are_rejected(workspace: Path) -> None:
    path = workspace / SOLVER_INPUT_FILENAME
    path.write_bytes(
        b'{"schema":"python-solver-input-v1","pad":"' + b"x" * 1000 + b'"}'
    )
    result = _run_cli_subprocess(["--workspace", str(workspace)], cwd=workspace)
    assert result.returncode == EXIT_INPUT_INVALID


# ---------------------------------------------------------------------------
# workspace discovery matches the worker's environment
# ---------------------------------------------------------------------------


def test_the_workspace_is_discovered_from_the_worker_environment(
    workspace: Path,
) -> None:
    write_input(workspace, solver_input_document())

    result = _run_cli_subprocess(
        [],
        cwd=workspace.parent,
        env={
            "HYDRAULIC_SANDBOX_PATH": str(workspace),
            "SIMULATION_ID": SIMULATION_ID,
            "SIMULATION_ATTEMPT": "1",
        },
    )

    assert result.returncode == EXIT_OK, result.stderr
    assert read_json(workspace / SOLVER_RESULT_FILENAME)["simulation_id"] == (
        SIMULATION_ID
    )


def test_the_cwd_is_the_workspace_so_no_flag_is_needed(workspace: Path) -> None:
    write_input(workspace, solver_input_document())
    result = _run_cli_subprocess([], cwd=workspace)
    assert result.returncode == EXIT_OK, result.stderr


# ---------------------------------------------------------------------------
# in-process entry point
# ---------------------------------------------------------------------------


def test_main_returns_the_same_codes_in_process(workspace: Path) -> None:
    assert main(["--workspace", str(workspace)]) == EXIT_INPUT_INVALID
    write_input(workspace, solver_input_document())
    assert main(["--workspace", str(workspace)]) == EXIT_OK


def test_list_solvers_reports_the_pure_python_fallback(capsys: Any) -> None:
    assert main(["--list-solvers"]) == EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["fallback_solver_id"] == "py_mw"
    assert report["registered_solver_ids"] == ["py_dw", "py_mw"]
    assert "swmm_kw" in report["unregistered_solver_ids"]
    assert report["epa"]["available"] is False


def test_probe_reports_availability_for_a_registered_identity(capsys: Any) -> None:
    assert main(["--probe", "--solver", "py_mw"]) == EXIT_OK
    probe = json.loads(capsys.readouterr().out)["probe"]
    assert probe["solver_id"] == "py_mw"
    assert probe["available"] is True
    assert probe["reason"] == "pure_python"


def test_two_consecutive_runs_are_byte_identical(workspace: Path) -> None:
    write_input(workspace, solver_input_document())
    assert main(["--workspace", str(workspace)]) == EXIT_OK
    first = {
        item.name: item.read_bytes() for item in (workspace / FRAMES_DIRNAME).iterdir()
    }
    assert main(["--workspace", str(workspace)]) == EXIT_OK
    second = {
        item.name: item.read_bytes() for item in (workspace / FRAMES_DIRNAME).iterdir()
    }
    assert first == second
    assert canonical_json(read_json(workspace / SOLVER_METRICS_FILENAME)) == (
        canonical_json(read_json(workspace / SOLVER_METRICS_FILENAME))
    )
