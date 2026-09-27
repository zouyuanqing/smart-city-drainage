from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import config
from app.core.config import BACKEND_DIR, SUPPORTED_JWT_ALGORITHMS, Settings

CONTRACT_ENVIRONMENT_KEYS = {
    "HYDRAULIC_SPATIAL_BACKEND",
    "HYDRAULIC_SCHEMA_REVISION",
    "HYDRAULIC_IMPORT_ROOT",
    "HYDRAULIC_SANDBOX_ROOT",
    "HYDRAULIC_EXPORT_ROOT",
    "SIM_GLOBAL_CONCURRENCY",
    "SIM_NOWCAST_RESERVED_SLOTS",
    "SIM_ATTEMPT_TIMEOUT_SECONDS",
    "SIM_MAX_ATTEMPTS",
    "SIM_RETRY_BACKOFF_SECONDS",
    "SIM_LEASE_SECONDS",
    "SIM_HEARTBEAT_SECONDS",
    "SIM_CANCEL_POLL_SECONDS",
    "SIM_INPUT_MAX_BYTES",
    "SIM_WORKSPACE_MAX_BYTES",
    "SIMULATION_FRAME_MAX_BYTES",
    "SIMULATION_OUTPUT_MAX_BYTES",
    "SIMULATION_RESULT_RETENTION_DAYS",
    "SOLVER_DEFAULT_SELECTION",
    "EPA_SWMM_EXECUTABLE",
    "EPA_SWMM_VERSION",
    "EPA_SWMM_SHA256",
    "MAX_SUBSTEPS_PER_REPORT",
    "COURANT_LIMIT",
    "NOWCAST_INTERVAL_MINUTES",
    "NOWCAST_HORIZON_MINUTES",
    "NOWCAST_STEP_MINUTES",
    "NOWCAST_DEADLINE_SECONDS",
    "NOWCAST_MAX_RETRIES",
    "NOWCAST_RETRY_BACKOFF_SECONDS",
    "NOWCAST_RESERVED_SLOT_BORROWING",
    "FORECAST_EVENT_WINDOW_MINUTES",
    "FORECAST_FRESH_MAX_AGE_SECONDS",
    "FORECAST_REPLAY_RETENTION_HOURS",
    "FORECAST_SSE_QUEUE_SIZE",
    "HYDRAULIC_ARTIFACT_ROOT",
    "FORECAST_POLICY_CONFIG_PATH",
    "NETWORK_JOB_GLOBAL_CONCURRENCY",
    "NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS",
    "NETWORK_JOB_LEASE_SECONDS",
    "NETWORK_JOB_HEARTBEAT_SECONDS",
    "NETWORK_JOB_MAX_ATTEMPTS",
    "NETWORK_JOB_RETRY_BACKOFF_SECONDS",
    "NETWORK_INPUT_MAX_BYTES",
    "JWT_ALGORITHMS",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "AUTH_FALLBACK_ADMIN_ENABLED",
    "RATE_LIMIT_FORECAST_READ_PER_MINUTE",
    "RATE_LIMIT_FORECAST_TRANSITION_PER_MINUTE",
    "RATE_LIMIT_SIMULATION_SUBMIT_PER_MINUTE",
    "RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE",
    "RATE_LIMIT_SSE_CONNECT_PER_MINUTE",
    "NOTIFICATION_MAX_ATTEMPTS",
}


def test_missing_optional_swmm_uses_python_mw(monkeypatch) -> None:
    for name in (
        "SOLVER_DEFAULT_SELECTION",
        "EPA_SWMM_EXECUTABLE",
        "EPA_SWMM_VERSION",
        "EPA_SWMM_SHA256",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.SOLVER_DEFAULT_SELECTION == "auto"
    assert settings.resolve_auto_solver() == "py_mw"
    assert settings.resolve_auto_solver("swmm_dw") == "py_mw"


def test_configured_swmm_is_preferred_in_auto_mode(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "swmm5.exe"
    executable.write_bytes(b"fake swmm engine")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    monkeypatch.setenv("EPA_SWMM_EXECUTABLE", str(executable))
    monkeypatch.setenv("EPA_SWMM_VERSION", "5.2.4")
    monkeypatch.setenv("EPA_SWMM_SHA256", digest)

    settings = Settings(_env_file=None)

    assert settings.epa_swmm_configured is True
    assert settings.epa_swmm_available is True
    assert settings.probe_epa_swmm()["reason"] == "verified"
    assert settings.resolve_auto_solver() == "swmm_kw"
    assert settings.resolve_auto_solver("swmm_dw") == "swmm_dw"


def test_missing_swmm_executable_falls_back_to_python_mw(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("EPA_SWMM_EXECUTABLE", str(tmp_path / "does-not-exist.exe"))
    monkeypatch.setenv("EPA_SWMM_VERSION", "5.2.4")
    monkeypatch.setenv("EPA_SWMM_SHA256", "a" * 64)

    settings = Settings(_env_file=None)

    assert settings.epa_swmm_configured is True
    assert settings.epa_swmm_available is False
    assert settings.probe_epa_swmm()["reason"] == "executable_not_found"
    assert settings.resolve_auto_solver() == "py_mw"
    assert settings.resolve_auto_solver("swmm_kw") == "py_mw"


def test_swmm_sha256_mismatch_falls_back_to_python_mw(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "swmm5.exe"
    executable.write_bytes(b"tampered swmm engine")
    monkeypatch.setenv("EPA_SWMM_EXECUTABLE", str(executable))
    monkeypatch.setenv("EPA_SWMM_VERSION", "5.2.4")
    monkeypatch.setenv("EPA_SWMM_SHA256", hashlib.sha256(b"other").hexdigest())

    settings = Settings(_env_file=None)

    assert settings.epa_swmm_available is False
    probe = settings.probe_epa_swmm()
    assert probe["reason"] == "sha256_mismatch"
    assert probe["observed_sha256"] != settings.EPA_SWMM_SHA256
    assert settings.resolve_auto_solver() == "py_mw"


def test_invalid_swmm_version_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("EPA_SWMM_EXECUTABLE", r"C:\\Program Files\\EPA SWMM\\swmm5.exe")
    monkeypatch.setenv("EPA_SWMM_VERSION", "latest")
    monkeypatch.setenv("EPA_SWMM_SHA256", "a" * 64)

    with pytest.raises(ValidationError, match="EPA_SWMM_VERSION"):
        Settings(_env_file=None)


def test_simulation_environment_is_typed(monkeypatch) -> None:
    monkeypatch.setenv("SIM_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("SIM_RETRY_BACKOFF_SECONDS", "2, 5")
    monkeypatch.setenv("NOWCAST_HORIZON_MINUTES", "120")
    monkeypatch.setenv("NOWCAST_STEP_MINUTES", "5")
    monkeypatch.setenv("FORECAST_FRESH_MAX_AGE_SECONDS", "900")

    settings = Settings(_env_file=None)

    assert settings.SIM_MAX_ATTEMPTS == 3
    assert settings.SIM_RETRY_BACKOFF_SECONDS == [2, 5]
    assert settings.NOWCAST_HORIZON_MINUTES == 120
    assert settings.NOWCAST_STEP_MINUTES == 5
    assert settings.FORECAST_FRESH_MAX_AGE_SECONDS == 900


@pytest.mark.parametrize(
    ("environment", "error_field"),
    [
        ({"SIM_GLOBAL_CONCURRENCY": "1"}, "SIM_GLOBAL_CONCURRENCY"),
        ({"SIM_GLOBAL_CONCURRENCY": "3"}, "SIM_GLOBAL_CONCURRENCY"),
        (
            {"SIM_GLOBAL_CONCURRENCY": "2", "SIM_NOWCAST_RESERVED_SLOTS": "2"},
            "SIM_NOWCAST_RESERVED_SLOTS",
        ),
        ({"SIM_ATTEMPT_TIMEOUT_SECONDS": "601"}, "SIM_ATTEMPT_TIMEOUT_SECONDS"),
        ({"SIM_CANCEL_POLL_SECONDS": "2"}, "SIM_CANCEL_POLL_SECONDS"),
        (
            {
                "SIM_MAX_ATTEMPTS": "4",
                "SIM_RETRY_BACKOFF_SECONDS": "[1, 2, 3]",
            },
            "SIM_MAX_ATTEMPTS",
        ),
        (
            {
                "NETWORK_JOB_MAX_ATTEMPTS": "4",
                "NETWORK_JOB_RETRY_BACKOFF_SECONDS": "[1, 2, 3]",
            },
            "NETWORK_JOB_MAX_ATTEMPTS",
        ),
        (
            {"NETWORK_JOB_GLOBAL_CONCURRENCY": "3"},
            "NETWORK_JOB_GLOBAL_CONCURRENCY",
        ),
        ({"SIM_WORKSPACE_MAX_BYTES": "2147483649"}, "SIM_WORKSPACE_MAX_BYTES"),
        (
            {"SIMULATION_FRAME_MAX_BYTES": "67108865"},
            "SIMULATION_FRAME_MAX_BYTES",
        ),
        (
            {"SIMULATION_OUTPUT_MAX_BYTES": "8589934593"},
            "SIMULATION_OUTPUT_MAX_BYTES",
        ),
        ({"NOWCAST_HORIZON_MINUTES": "121"}, "NOWCAST_HORIZON_MINUTES"),
        ({"NOWCAST_DEADLINE_SECONDS": "181"}, "NOWCAST_DEADLINE_SECONDS"),
        (
            {"NOWCAST_RESERVED_SLOT_BORROWING": "true"},
            "NOWCAST_RESERVED_SLOT_BORROWING",
        ),
        ({"SIM_MAX_ATTEMPTS": "4"}, "SIM_MAX_ATTEMPTS"),
        ({"NOWCAST_MAX_RETRIES": "1"}, "NOWCAST_RETRY_BACKOFF_SECONDS"),
        ({"NETWORK_JOB_HEARTBEAT_SECONDS": "30"}, "NETWORK_JOB_HEARTBEAT_SECONDS"),
        ({"SIM_INPUT_MAX_BYTES": "20971521"}, "SIM_INPUT_MAX_BYTES"),
        ({"SOLVER_DEFAULT_SELECTION": "explicit"}, "EPA_SWMM_EXECUTABLE"),
        ({"EPA_SWMM_SHA256": "not-a-sha256"}, "EPA_SWMM_SHA256"),
        ({"JWT_ALGORITHMS": "[]"}, "JWT_ALGORITHMS"),
        ({"JWT_ALGORITHMS": '["HS256", "none"]'}, "JWT_ALGORITHMS"),
        ({"JWT_ALGORITHMS": '["HS256", "HS256"]'}, "JWT_ALGORITHMS"),
        (
            {"JWT_ALGORITHM": "HS512", "JWT_ALGORITHMS": '["HS256", "RS256"]'},
            "JWT_ALGORITHM",
        ),
    ],
)
def test_invalid_simulation_settings_are_rejected(
    monkeypatch, environment: dict[str, str], error_field: str
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match=error_field):
        Settings(_env_file=None)


def test_retry_backoff_must_be_strictly_increasing(monkeypatch) -> None:
    monkeypatch.setenv("SIM_RETRY_BACKOFF_SECONDS", "[60, 10]")

    with pytest.raises(ValidationError, match="SIM_RETRY_BACKOFF_SECONDS"):
        Settings(_env_file=None)


def test_env_example_contains_every_contract_variable() -> None:
    env_example = Path(__file__).parents[2] / ".env.example"
    configured_keys = {
        line.split("=", 1)[0]
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    assert CONTRACT_ENVIRONMENT_KEYS <= configured_keys
    Settings(_env_file=env_example)


def test_compose_only_dotenv_keys_do_not_leak_into_application_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DATABASE_URL=postgresql+asyncpg://user:pass@db:5432/app\n"
        "POSTGRES_PASSWORD=compose-only\n"
        "REDIS_PASSWORD=compose-only\n"
        "INFLUXDB_PASSWORD=compose-only\n"
        "GRAFANA_ADMIN_PASSWORD=compose-only\n"
        "PIP_INDEX_URL=https://packages.invalid/simple\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=env_file)

    assert settings.DATABASE_URL.endswith("@db:5432/app")
    assert not hasattr(settings, "POSTGRES_PASSWORD")
    assert not hasattr(settings, "GRAFANA_ADMIN_PASSWORD")


def test_unknown_dotenv_key_is_still_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SETTINGS_TYPO=value\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="settings_typo"):
        Settings(_env_file=env_file)


def test_production_rejects_fallback_admin(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_ISSUER", "test-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "test-audience")
    monkeypatch.setenv("AUTH_FALLBACK_ADMIN_ENABLED", "true")

    with pytest.raises(ValidationError, match="AUTH_FALLBACK_ADMIN_ENABLED"):
        Settings(_env_file=None)


def test_fallback_admin_is_never_allowed_in_production(monkeypatch) -> None:
    # Bypass the Settings fail-closed check to prove the runtime gate is
    # independent of the validation layer.
    settings = Settings(_env_file=None)
    settings.DEPLOYMENT_ENVIRONMENT = "production"
    settings.AUTH_FALLBACK_ADMIN_ENABLED = True

    assert settings.auth_fallback_admin_allowed is False


def test_fallback_admin_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "development")

    assert Settings(_env_file=None).auth_fallback_admin_allowed is False
    assert (
        Settings(
            _env_file=None, AUTH_FALLBACK_ADMIN_ENABLED=True
        ).auth_fallback_admin_allowed
        is True
    )


def test_production_requires_issuer_audience_and_rs256(monkeypatch) -> None:
    for name, value in {
        "DEPLOYMENT_ENVIRONMENT": "production",
        "JWT_SECRET_KEY": "a-random-production-secret",
        "INFLUXDB_TOKEN": "a-random-influx-token",
        "DATABASE_URL": "postgresql+asyncpg://user:real-password@db:5432/app",
        "DATABASE_URL_SYNC": "postgresql://user:real-password@db:5432/app",
        "REDIS_URL": "redis://:real-password@redis:6379/0",
    }.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match="JWT_ISSUER"):
        Settings(_env_file=None)

    monkeypatch.setenv("JWT_ISSUER", "smart-city-drainage")
    with pytest.raises(ValidationError, match="JWT_AUDIENCE"):
        Settings(_env_file=None)

    monkeypatch.setenv("JWT_AUDIENCE", "smart-city-api")
    with pytest.raises(ValidationError, match="RS256"):
        Settings(_env_file=None, JWT_ALGORITHMS=["HS256"])

    with pytest.raises(ValidationError, match="RS256"):
        Settings(
            _env_file=None,
            JWT_ALGORITHMS=["RS256", "HS256"],
            JWT_ALGORITHM="HS256",
        )

    production = Settings(
        _env_file=None,
        JWT_ALGORITHMS=["RS256"],
        JWT_ALGORITHM="RS256",
        JWT_PRIVATE_KEY="private-key",
        JWT_PUBLIC_KEY="public-key",
    )
    assert production.JWT_ALGORITHMS == ["RS256"]


def test_jwt_allowlist_matches_python_jose_algorithms() -> None:
    from jose.constants import Algorithms

    assert SUPPORTED_JWT_ALGORITHMS <= Algorithms.SUPPORTED
    assert "none" not in SUPPORTED_JWT_ALGORITHMS


def test_default_env_file_is_anchored_to_the_project_root(
    tmp_path, monkeypatch
) -> None:
    root_env = tmp_path / ".env"
    backend_env = tmp_path / "backend" / ".env"
    root_env.write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")
    backend_env.parent.mkdir(parents=True, exist_ok=True)
    backend_env.write_text("LOG_LEVEL=WARNING\n", encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT_ENV_FILE", root_env)
    monkeypatch.setattr(config, "BACKEND_ENV_FILE", backend_env)
    monkeypatch.delenv("SCN_ENV_FILE", raising=False)

    assert config.resolve_env_file() == root_env

    root_env.unlink()
    assert config.resolve_env_file() == backend_env

    backend_env.unlink()
    assert config.resolve_env_file() is None

    override = tmp_path / "mounted.env"
    override.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")
    monkeypatch.setenv("SCN_ENV_FILE", str(override))
    assert config.resolve_env_file() == override

    # The class model no longer hard-codes a cwd-relative ".env".
    assert Settings.model_config["env_file"] is None


def test_root_env_file_is_loaded_from_the_backend_working_directory(
    tmp_path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        'JWT_ISSUER=from-root-env\nJWT_ALGORITHMS=["HS256"]\n', encoding="utf-8"
    )
    monkeypatch.setattr(config, "PROJECT_ROOT_ENV_FILE", env_file)
    monkeypatch.setattr(config, "BACKEND_ENV_FILE", tmp_path / "backend" / ".env")
    monkeypatch.delenv("SCN_ENV_FILE", raising=False)
    monkeypatch.delenv("JWT_ISSUER", raising=False)
    monkeypatch.delenv("JWT_ALGORITHMS", raising=False)
    # README starts the server from `backend/`: the root file must still load.
    monkeypatch.chdir(BACKEND_DIR)

    settings = Settings()

    assert settings.JWT_ISSUER == "from-root-env"
    assert settings.JWT_ALGORITHMS == ["HS256"]


def test_env_file_resolution_is_independent_of_the_process_working_directory(
    tmp_path, monkeypatch
) -> None:
    nested = tmp_path / "nested" / "cwd"
    nested.mkdir(parents=True)
    env_file = tmp_path / "mounted.env"
    env_file.write_text("LOG_LEVEL=WARNING\n", encoding="utf-8")
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    code = "from app.core.config import Settings; print(Settings().LOG_LEVEL)"
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=nested,
        env={
            **os.environ,
            "PYTHONPATH": str(BACKEND_DIR),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND_DIR),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SCN_ENV_FILE": str(env_file),
    }
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=nested,
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "WARNING"


def test_python_requirements_are_pinned_and_swmm_stays_external() -> None:
    backend = Path(__file__).parents[1]
    requirement_files = [
        backend / "requirements.txt",
        backend / "requirements-cpu.txt",
        backend / "requirements-hydraulic.txt",
    ]
    requirement_lines = [
        line.strip()
        for path in requirement_files
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert all("==" in line for line in requirement_lines)
    assert not any("swmm-toolkit" in line.lower() for line in requirement_lines)
    assert not any("py-swmm" in line.lower() for line in requirement_lines)

    hydraulic_lines = [
        line.strip().lower()
        for line in (backend / "requirements-hydraulic.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any(
        package in line
        for line in hydraulic_lines
        for package in ("torch", "opencv", "onnxruntime", "hiredis", "swmm")
    )

    pyproject = tomllib.loads((backend / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"]["optional-dependencies"]
    assert "numpy==1.26.4 ; python_version < '3.13'" in extras["hydraulic"]
    assert "numpy==2.3.4 ; python_version >= '3.13'" in extras["hydraulic"]
    assert "Pillow==10.2.0 ; python_version < '3.13'" in extras["hydraulic"]
    assert "Pillow==12.1.0 ; python_version >= '3.13'" in extras["hydraulic"]
    assert extras["swmm"] == []

    for lock_name in (
        "requirements-py311.lock",
        "requirements-py311-gpu.lock",
        "requirements-hydraulic-py311.lock",
    ):
        lock_text = (backend / lock_name).read_text(encoding="utf-8")
        assert "numpy==1.26.4" in lock_text
        assert "--hash=sha256:" in lock_text
        assert "swmm-toolkit" not in lock_text.lower()
        assert "py-swmm" not in lock_text.lower()

    hydraulic_lock = (backend / "requirements-hydraulic-py311.lock").read_text(
        encoding="utf-8"
    )
    assert "torch==" not in hydraulic_lock
    assert "opencv" not in hydraulic_lock
    assert "onnxruntime" not in hydraulic_lock
    assert "swmm" not in hydraulic_lock.lower()
