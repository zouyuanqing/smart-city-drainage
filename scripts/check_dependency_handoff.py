#!/usr/bin/env python3
"""Read-only dependency-config handoff and preservation gate.

The checker validates the handoff against its frozen expected fixture, the base
and live dependency files, the live Settings/.env.example inventory, the exact
Node/npm/lock declarations, the effective post-handoff owner map, the approved
migration handoff, and the active frontend performance entrypoint/fixture
contract. Runtime evidence remains an explicit downstream deferred item; this
checker never creates or pretends to execute it.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib
import json
import subprocess
import sys
import types
import typing
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

EXPECTED_SCHEMA = "dependency-handoff-expectation-v1"
HANDOFF_SCHEMA = "dependency-config-handoff-v1"
HANDOFF_STATUS = "READY_WITH_DEFERRED"
BASE_COMMIT = "ca99e0485fb12fbecbd565e79d81f745f8d4a748"
APPROVED_MIGRATION_ARTIFACT = "artifacts/contracts/approved-migration-0008.json"
APPROVED_MIGRATION_SCHEMA = "approved-migration-v1"
APPROVED_MIGRATION_STATUS = "APPROVED_POST_MIGRATION"
APPROVED_MIGRATION_OWNER = "hydraulic-schema"
FRONTEND_PERFORMANCE_FIXTURE = "frontend/performance/fixtures/twin-map-5k-10k-v1.json"
FRONTEND_PERFORMANCE_RUNNER = "frontend/performance/run-twin-map.mjs"
FRONTEND_PERFORMANCE_SCHEMA = "hydraulic-frontend-performance-browser-v1"
FRONTEND_PERFORMANCE_REPORT_SCHEMA = "hydraulic-frontend-performance-report-v2"
FRONTEND_PERFORMANCE_STATE = "APPROVED_CONTRACT"
FRONTEND_PERFORMANCE_REQUIRED_COMMAND = (
    "node performance/run-twin-map.mjs --fixture "
    "performance/fixtures/twin-map-5k-10k-v1.json --output "
    "<RUN_ID>/g5/frontend-smoke.json --duration-seconds 60"
)
AUTH_IDS = tuple(f"DEP-AUTH-{number:02d}" for number in range(1, 14))
FILE_AUTH_IDS = AUTH_IDS[:-1]

AUTH_FILE_GROUPS: dict[str, tuple[str, ...]] = {
    "DEP-AUTH-01": (".python-version",),
    "DEP-AUTH-02": (".env.example",),
    "DEP-AUTH-03": (
        "backend/requirements-hydraulic.txt",
        "backend/requirements-cpu.txt",
        "backend/requirements.txt",
    ),
    "DEP-AUTH-04": (
        "backend/requirements-hydraulic-py311.lock",
        "backend/requirements-py311-gpu.lock",
        "backend/requirements-py311.lock",
    ),
    "DEP-AUTH-05": ("backend/pyproject.toml",),
    "DEP-AUTH-06": ("backend/app/core/config.py",),
    "DEP-AUTH-07": ("backend/tests/test_simulation_settings.py",),
    "DEP-AUTH-08": ("backend/tests/test_requirements_matrix.py",),
    "DEP-AUTH-09": ("frontend/.node-version",),
    "DEP-AUTH-10": ("frontend/.npmrc",),
    "DEP-AUTH-11": ("frontend/package.json",),
    "DEP-AUTH-12": ("frontend/package-lock.json",),
}
EFFECTIVE_OWNERS = {
    **{
        auth_id: "integration-config"
        for auth_id in (
            "DEP-AUTH-01",
            "DEP-AUTH-02",
            "DEP-AUTH-03",
            "DEP-AUTH-04",
            "DEP-AUTH-05",
            "DEP-AUTH-06",
            "DEP-AUTH-09",
            "DEP-AUTH-10",
            "DEP-AUTH-11",
            "DEP-AUTH-12",
        )
    },
    "DEP-AUTH-07": "dependency-config",
    "DEP-AUTH-08": "dependency-config",
    "DEP-AUTH-13": "dependency-config",
}

AUTHORIZED_SETTINGS = tuple(
    sorted(
        {
            "AUTH_FALLBACK_ADMIN_ENABLED",
            "COURANT_LIMIT",
            "EPA_SWMM_EXECUTABLE",
            "EPA_SWMM_SHA256",
            "EPA_SWMM_VERSION",
            "FORECAST_EVENT_WINDOW_MINUTES",
            "FORECAST_FRESH_MAX_AGE_SECONDS",
            "FORECAST_POLICY_CONFIG_PATH",
            "FORECAST_REPLAY_RETENTION_HOURS",
            "FORECAST_SSE_QUEUE_SIZE",
            "HYDRAULIC_ARTIFACT_ROOT",
            "HYDRAULIC_EXPORT_ROOT",
            "HYDRAULIC_IMPORT_ROOT",
            "HYDRAULIC_SANDBOX_ROOT",
            "HYDRAULIC_SCHEMA_REVISION",
            "HYDRAULIC_SPATIAL_BACKEND",
            "JWT_ALGORITHMS",
            "JWT_AUDIENCE",
            "JWT_ISSUER",
            "MAX_SUBSTEPS_PER_REPORT",
            "NETWORK_INPUT_MAX_BYTES",
            "NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS",
            "NETWORK_JOB_GLOBAL_CONCURRENCY",
            "NETWORK_JOB_HEARTBEAT_SECONDS",
            "NETWORK_JOB_LEASE_SECONDS",
            "NETWORK_JOB_MAX_ATTEMPTS",
            "NETWORK_JOB_RETRY_BACKOFF_SECONDS",
            "NOTIFICATION_MAX_ATTEMPTS",
            "NOWCAST_DEADLINE_SECONDS",
            "NOWCAST_HORIZON_MINUTES",
            "NOWCAST_INTERVAL_MINUTES",
            "NOWCAST_MAX_RETRIES",
            "NOWCAST_RESERVED_SLOT_BORROWING",
            "NOWCAST_RETRY_BACKOFF_SECONDS",
            "NOWCAST_STEP_MINUTES",
            "RATE_LIMIT_FORECAST_READ_PER_MINUTE",
            "RATE_LIMIT_FORECAST_TRANSITION_PER_MINUTE",
            "RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE",
            "RATE_LIMIT_SIMULATION_SUBMIT_PER_MINUTE",
            "RATE_LIMIT_SSE_CONNECT_PER_MINUTE",
            "SIMULATION_FRAME_MAX_BYTES",
            "SIMULATION_OUTPUT_MAX_BYTES",
            "SIMULATION_RESULT_RETENTION_DAYS",
            "SIM_ATTEMPT_TIMEOUT_SECONDS",
            "SIM_CANCEL_POLL_SECONDS",
            "SIM_GLOBAL_CONCURRENCY",
            "SIM_HEARTBEAT_SECONDS",
            "SIM_INPUT_MAX_BYTES",
            "SIM_LEASE_SECONDS",
            "SIM_MAX_ATTEMPTS",
            "SIM_NOWCAST_RESERVED_SLOTS",
            "SIM_RETRY_BACKOFF_SECONDS",
            "SIM_WORKSPACE_MAX_BYTES",
            "SOLVER_DEFAULT_SELECTION",
        }
    )
)
UNAUTHORIZED_SETTINGS = tuple(
    sorted(
        {
            "ACCESS_TOKEN_EXPIRE_MINUTES",
            "ALERT_EMAIL_RECIPIENTS",
            "ALERT_WEBHOOK_URLS",
            "APP_NAME",
            "APP_VERSION",
            "CORS_ORIGINS",
            "DATABASE_URL",
            "DATABASE_URL_SYNC",
            "DB_MAX_OVERFLOW",
            "DB_POOL_SIZE",
            "DEBUG",
            "DEFAULT_MODEL_VERSION",
            "DEPLOYMENT_ENVIRONMENT",
            "FFMPEG_PATH",
            "FFPROBE_PATH",
            "HLS_OUTPUT_DIR",
            "HLS_SEGMENT_TIME",
            "HLS_LIST_SIZE",
            "INFLUXDB_BUCKET",
            "INFLUXDB_ORG",
            "INFLUXDB_TOKEN",
            "INFLUXDB_URL",
            "JWT_ALGORITHM",
            "JWT_PRIVATE_KEY",
            "JWT_PUBLIC_KEY",
            "JWT_SECRET_KEY",
            "LOG_LEVEL",
            "MAX_ACTIVE_INFERENCE_WORKERS",
            "MAX_ACTIVE_STREAMS",
            "MAX_UPLOAD_SIZE_MB",
            "MEDIA_CONNECT_TIMEOUT_SECONDS",
            "MEDIA_READ_TIMEOUT_SECONDS",
            "MODEL_CONFIDENCE_THRESHOLD",
            "MODEL_INFERENCE_DEVICE",
            "MODEL_IOU_THRESHOLD",
            "MODEL_STORAGE_PATH",
            "RATE_LIMIT_PER_MINUTE",
            "REDIS_CHANNEL_ALERTS",
            "REDIS_CHANNEL_MODEL",
            "REDIS_CHANNEL_SENSOR",
            "REDIS_URL",
            "SCREENSHOT_DIR",
            "SMTP_ENABLED",
            "SMTP_FROM",
            "SMTP_HOST",
            "SMTP_PASSWORD",
            "SMTP_PORT",
            "SMTP_USERNAME",
            "SMTP_USE_TLS",
            "STREAM_HEARTBEAT_INTERVAL",
            "WEBHOOK_ENABLED",
        }
    )
)

VERSION_CONTRACT = {
    "lockfileVersion": 3,
    "node": "18.20.8",
    "npm": "10.8.2",
    "packageManager": "npm@10.8.2",
    "python": "3.11",
    "rollupOverride": "4.22.0",
}

FRONTEND_PERFORMANCE_FIXTURE_CONTRACT = {
    "acceptance": {
        "browser": "production-bundled-headless-chromium",
        "note": (
            "The production browser bundle imports ViewportIndex, "
            "SimulationMapController/Canvas, encodeFrame and FrameCache. API "
            "latency is measured against the frozen local fixture API."
        ),
        "scope": "PRD_G5",
        "target_environment": {
            "gpu": "disabled_software_rasterization",
            "node_version": "18.20.8",
            "physical_cores": 4,
            "ram_bytes": 8589934592,
        },
    },
    "data_source": "fixture_api_production_modules_v1",
    "fixture_id": "twin-map-5k-10k-v1",
    "fixture_version": 2,
    "frames": {"count": 289, "duration_minutes": 1440, "interval_minutes": 5},
    "input_counts": {"catchments": 1000, "nodes": 5000, "pipes": 10000},
    "schema": FRONTEND_PERFORMANCE_SCHEMA,
    "seed": 1592614637,
    "thresholds": {
        "max_cache_bytes": 268435456,
        "max_long_task_ms": 200,
        "max_map_api_p95_ms": 500,
        "max_request_animation_frame_p95_ms": 50,
        "min_presented_fps": 25,
        "min_sample_count": 1000,
    },
    "viewport": {"height": 1080, "width": 1920},
    "workload": {
        "duration_seconds": 60,
        "layer_toggles": 120,
        "pan_steps": 3600,
        "playback_frames": 240,
        "selection_changes": 212,
        "zoom_steps": 3600,
    },
}

DEFERRED_ITEMS = (
    {
        "id": "frontend-target-runtime-evidence",
        "path": "artifacts/hydraulic/<RUN_ID>/g0",
        "state": "DEFERRED",
        "owner": "ci-quality-bootstrap",
        "required_command": ("node --version == v18.20.8; npm --version == 10.8.2"),
        "reason": (
            "This read-only handoff checker validates the exact version declarations; "
            "target-runtime evidence belongs to the later G0 execution owner."
        ),
        "blocks": ["G0"],
    },
)

CONSTRAINT_ATTRIBUTES = (
    "gt",
    "ge",
    "lt",
    "le",
    "multiple_of",
    "min_length",
    "max_length",
    "pattern",
    "max_digits",
    "decimal_places",
    "allow_inf_nan",
)


class GateError(RuntimeError):
    """A fail-closed dependency handoff violation."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _approved_migration_reference(repo_root: Path) -> dict[str, str]:
    path = repo_root / PurePosixPath(APPROVED_MIGRATION_ARTIFACT)
    require(path.is_file(), "approved migration artifact is missing")
    artifact = _load_json(path, "approved migration artifact")
    require(
        artifact.get("schema") == APPROVED_MIGRATION_SCHEMA,
        "approved migration schema drifted",
    )
    require(
        artifact.get("status") == APPROVED_MIGRATION_STATUS,
        "approved migration status drifted",
    )
    require(
        artifact.get("phase") == "post-migration",
        "approved migration phase drifted",
    )
    require(
        artifact.get("owner") == APPROVED_MIGRATION_OWNER,
        "approved migration owner drifted",
    )
    head_revision = artifact.get("head_revision")
    require(
        isinstance(head_revision, str) and head_revision,
        "approved migration head_revision is missing",
    )
    return {
        "artifact": APPROVED_MIGRATION_ARTIFACT,
        "head_revision": head_revision,
        "owner": APPROVED_MIGRATION_OWNER,
        "sha256": sha256_file(path),
        "status": APPROVED_MIGRATION_STATUS,
    }


def _verify_approved_migration_reference(
    repo_root: Path,
    reference: Any,
) -> dict[str, str]:
    require(isinstance(reference, dict), "handoff requires approved_migration")
    require(
        set(reference) == {"artifact", "head_revision", "owner", "sha256", "status"},
        "invalid approved_migration shape",
    )
    expected = _approved_migration_reference(repo_root)
    require(reference == expected, "approved migration reference drifted")
    return expected


def _frontend_performance_contract_snapshot(repo_root: Path) -> dict[str, Any]:
    fixture_path = repo_root / PurePosixPath(FRONTEND_PERFORMANCE_FIXTURE)
    runner_path = repo_root / PurePosixPath(FRONTEND_PERFORMANCE_RUNNER)
    require(fixture_path.is_file(), "approved frontend performance fixture is missing")
    require(runner_path.is_file(), "approved frontend performance runner is missing")
    return {
        "acceptance_scope": "PRD_G5",
        "entrypoint": {
            "command": "node performance/run-twin-map.mjs",
            "path": "frontend/package.json#scripts.test:perf:twin",
            "required_command": FRONTEND_PERFORMANCE_REQUIRED_COMMAND,
        },
        "fixture": {
            "fixture_id": "twin-map-5k-10k-v1",
            "path": FRONTEND_PERFORMANCE_FIXTURE,
            "sha256": sha256_file(fixture_path),
        },
        "report_schema": FRONTEND_PERFORMANCE_REPORT_SCHEMA,
        "runner": {
            "path": FRONTEND_PERFORMANCE_RUNNER,
            "sha256": sha256_file(runner_path),
        },
        "schema": FRONTEND_PERFORMANCE_SCHEMA,
        "state": FRONTEND_PERFORMANCE_STATE,
    }


def _verify_frontend_performance_contract(
    repo_root: Path,
    contract: Any,
) -> dict[str, Any]:
    require(isinstance(contract, dict), "handoff requires frontend_performance")
    expected = _frontend_performance_contract_snapshot(repo_root)
    require(contract == expected, "frontend performance contract drifted")
    fixture_path = _repo_path(repo_root, FRONTEND_PERFORMANCE_FIXTURE)
    fixture = _load_json(fixture_path, "frontend performance fixture")
    require(
        fixture == FRONTEND_PERFORMANCE_FIXTURE_CONTRACT,
        "frontend performance fixture semantics drifted",
    )
    acceptance = fixture.get("acceptance", {})
    require(
        acceptance.get("scope") == "PRD_G5"
        and acceptance.get("browser") == "production-bundled-headless-chromium"
        and acceptance.get("target_environment")
        == {
            "gpu": "disabled_software_rasterization",
            "node_version": "18.20.8",
            "physical_cores": 4,
            "ram_bytes": 8589934592,
        },
        "frontend performance fixture must preserve the target browser contract",
    )
    runner_path = _repo_path(repo_root, FRONTEND_PERFORMANCE_RUNNER)
    runner = runner_path.read_text(encoding="utf-8")
    for token in (
        f"const FIXTURE_SCHEMA = '{FRONTEND_PERFORMANCE_SCHEMA}'",
        f"const REPORT_SCHEMA = '{FRONTEND_PERFORMANCE_REPORT_SCHEMA}'",
        "const EXPECTED_COUNTS = Object.freeze({ nodes: 5000, pipes: 10000, catchments: 1000 })",
        "const EXPECTED_FRAMES = 289",
        "acceptance_scope: options.diagnostic ? 'DIAGNOSTIC_ONLY' : 'PRD_G5'",
        "prd_gate_status: 'NOT_RUN'",
    ):
        require(
            token in runner,
            f"frontend performance runner contract token missing: {token}",
        )
    return expected


def _git(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GateError(f"git {' '.join(args)} failed: {detail}")
    return completed


def _git_blob(repo_root: Path, base_commit: str, relative: str) -> bytes | None:
    completed = _git(
        repo_root,
        "show",
        f"{base_commit}:{relative}",
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else None


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateError(f"{label} is not readable JSON: {path}: {exc}") from exc
    require(isinstance(value, dict), f"{label} must be a JSON object: {path}")
    return value


def _cli_path(repo_root: Path, value: str) -> Path:
    """Accept both repository-relative and workspace-relative CLI paths."""

    root = repo_root.resolve()
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    workspace_candidate = (root.parent / candidate).resolve()
    try:
        return workspace_candidate.relative_to(root)
    except ValueError:
        return candidate


def _repo_path(repo_root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise GateError(f"path escapes repository root: {value}") from exc
    return resolved


def _canonical_type(annotation: Any) -> str:
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)
    if origin is typing.Literal:
        values = ", ".join(repr(value) for value in arguments)
        return f"Literal[{values}]"
    if origin in {list, typing.List}:
        return f"list[{_canonical_type(arguments[0])}]"
    if origin in {dict, typing.Dict}:
        return (
            f"dict[{_canonical_type(arguments[0])}," f"{_canonical_type(arguments[1])}]"
        )
    if origin is typing.Union or origin is types.UnionType:
        return " | ".join(_canonical_type(argument) for argument in arguments)
    if annotation is type(None):
        return "None"
    if annotation is Ellipsis:
        return "..."
    if isinstance(annotation, type):
        return annotation.__name__
    return str(annotation)


def _canonical_default(field: Any) -> Any:
    try:
        value = field.get_default(call_default_factory=True)
    except TypeError:
        value = field.get_default()
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
    except (TypeError, ValueError):
        if value is None:
            return None
        return repr(value)


def _constraints(field: Any) -> list[str]:
    values: list[str] = []
    for metadata in field.metadata:
        attributes = [
            f"{name}={getattr(metadata, name)!r}"
            for name in CONSTRAINT_ATTRIBUTES
            if hasattr(metadata, name)
        ]
        values.append(f"{type(metadata).__name__}({','.join(attributes)})")
    return sorted(set(values))


def _parse_env_example(path: Path) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, raw_value = line.split("=", 1)
        name = name.strip()
        require(name not in values, f"duplicate .env.example key: {name}")
        raw_value = raw_value.strip()
        if raw_value.lower() in {"true", "false"}:
            values[name] = raw_value.lower() == "true"
            continue
        try:
            values[name] = json.loads(raw_value)
        except json.JSONDecodeError:
            values[name] = raw_value
    return values


def _decorator_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _literal_strings(node: ast.expr) -> tuple[str, ...]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return tuple(
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        )
    return ()


def _settings_validators(source: str) -> dict[str, list[str]]:
    tree = ast.parse(source)
    settings_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Settings"
    )
    field_tokens: dict[str, set[str]] = {}
    model_tokens: set[str] = set()
    for node in settings_node.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            name = _decorator_name(decorator)
            if name not in {"field_validator", "model_validator"}:
                continue
            call = decorator if isinstance(decorator, ast.Call) else None
            mode = "after"
            if call is not None:
                for keyword in call.keywords:
                    if keyword.arg == "mode":
                        modes = _literal_strings(keyword.value)
                        mode = modes[0] if modes else "after"
            if name == "model_validator":
                model_tokens.add(f"Settings:model:{node.name}")
                continue
            if call is None:
                continue
            field_names: list[str] = []
            for argument in call.args:
                field_names.extend(_literal_strings(argument))
            for field_name in field_names:
                field_tokens.setdefault(field_name, set()).add(
                    f"{field_name}:{mode}:{node.name}"
                )
    return {
        field_name: sorted(tokens | model_tokens)
        for field_name, tokens in field_tokens.items()
    }


def _invalid_case(
    environment: Mapping[str, Any], error_contains: str | None = None
) -> dict[str, Any]:
    case: dict[str, Any] = {"environment": dict(sorted(environment.items()))}
    if error_contains is not None:
        case["error_contains"] = error_contains
    return case


INVALID_CASES: dict[str, list[dict[str, Any]]] = {
    "COURANT_LIMIT": [_invalid_case({"COURANT_LIMIT": "1.1"}, "COURANT_LIMIT")],
    "EPA_SWMM_SHA256": [
        _invalid_case({"EPA_SWMM_SHA256": "not-a-sha256"}, "EPA_SWMM_SHA256")
    ],
    "EPA_SWMM_VERSION": [
        _invalid_case({"EPA_SWMM_VERSION": "latest"}, "EPA_SWMM_VERSION")
    ],
    "FORECAST_EVENT_WINDOW_MINUTES": [
        _invalid_case(
            {"FORECAST_EVENT_WINDOW_MINUTES": "0"}, "FORECAST_EVENT_WINDOW_MINUTES"
        )
    ],
    "FORECAST_FRESH_MAX_AGE_SECONDS": [
        _invalid_case(
            {"FORECAST_FRESH_MAX_AGE_SECONDS": "0"}, "FORECAST_FRESH_MAX_AGE_SECONDS"
        )
    ],
    "FORECAST_REPLAY_RETENTION_HOURS": [
        _invalid_case(
            {"FORECAST_REPLAY_RETENTION_HOURS": "0"}, "FORECAST_REPLAY_RETENTION_HOURS"
        )
    ],
    "FORECAST_SSE_QUEUE_SIZE": [
        _invalid_case({"FORECAST_SSE_QUEUE_SIZE": "0"}, "FORECAST_SSE_QUEUE_SIZE")
    ],
    "HYDRAULIC_SPATIAL_BACKEND": [
        _invalid_case(
            {"HYDRAULIC_SPATIAL_BACKEND": "mysql"}, "HYDRAULIC_SPATIAL_BACKEND"
        )
    ],
    "JWT_ALGORITHMS": [
        _invalid_case({"JWT_ALGORITHMS": "[]"}, "JWT_ALGORITHMS"),
        _invalid_case({"JWT_ALGORITHMS": '["HS256", "none"]'}, "JWT_ALGORITHMS"),
        _invalid_case({"JWT_ALGORITHMS": '["HS256", "HS256"]'}, "JWT_ALGORITHMS"),
    ],
    "MAX_SUBSTEPS_PER_REPORT": [
        _invalid_case({"MAX_SUBSTEPS_PER_REPORT": "0"}, "MAX_SUBSTEPS_PER_REPORT")
    ],
    "NETWORK_INPUT_MAX_BYTES": [
        _invalid_case(
            {"NETWORK_INPUT_MAX_BYTES": "20971521"}, "NETWORK_INPUT_MAX_BYTES"
        )
    ],
    "NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS": [
        _invalid_case(
            {"NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS": "601"},
            "NETWORK_JOB_ATTEMPT_TIMEOUT_SECONDS",
        )
    ],
    "NETWORK_JOB_GLOBAL_CONCURRENCY": [
        _invalid_case(
            {"NETWORK_JOB_GLOBAL_CONCURRENCY": "3"}, "NETWORK_JOB_GLOBAL_CONCURRENCY"
        )
    ],
    "NETWORK_JOB_HEARTBEAT_SECONDS": [
        _invalid_case(
            {"NETWORK_JOB_HEARTBEAT_SECONDS": "30"},
            "NETWORK_JOB_HEARTBEAT_SECONDS",
        )
    ],
    "NETWORK_JOB_LEASE_SECONDS": [
        _invalid_case({"NETWORK_JOB_LEASE_SECONDS": "0"}, "NETWORK_JOB_LEASE_SECONDS")
    ],
    "NETWORK_JOB_MAX_ATTEMPTS": [
        _invalid_case({"NETWORK_JOB_MAX_ATTEMPTS": "4"}, "NETWORK_JOB_MAX_ATTEMPTS")
    ],
    "NETWORK_JOB_RETRY_BACKOFF_SECONDS": [
        _invalid_case(
            {"NETWORK_JOB_RETRY_BACKOFF_SECONDS": "[60, 10]"},
            "NETWORK_JOB_RETRY_BACKOFF_SECONDS",
        )
    ],
    "NOTIFICATION_MAX_ATTEMPTS": [
        _invalid_case({"NOTIFICATION_MAX_ATTEMPTS": "0"}, "NOTIFICATION_MAX_ATTEMPTS")
    ],
    "NOWCAST_DEADLINE_SECONDS": [
        _invalid_case({"NOWCAST_DEADLINE_SECONDS": "181"}, "NOWCAST_DEADLINE_SECONDS")
    ],
    "NOWCAST_HORIZON_MINUTES": [
        _invalid_case({"NOWCAST_HORIZON_MINUTES": "121"}, "NOWCAST_HORIZON_MINUTES")
    ],
    "NOWCAST_INTERVAL_MINUTES": [
        _invalid_case({"NOWCAST_INTERVAL_MINUTES": "0"}, "NOWCAST_INTERVAL_MINUTES")
    ],
    "NOWCAST_MAX_RETRIES": [
        _invalid_case({"NOWCAST_MAX_RETRIES": "3"}, "NOWCAST_RETRY_BACKOFF_SECONDS")
    ],
    "NOWCAST_RESERVED_SLOT_BORROWING": [
        _invalid_case(
            {"NOWCAST_RESERVED_SLOT_BORROWING": "true"},
            "NOWCAST_RESERVED_SLOT_BORROWING",
        )
    ],
    "NOWCAST_RETRY_BACKOFF_SECONDS": [
        _invalid_case(
            {"NOWCAST_RETRY_BACKOFF_SECONDS": "[15, 5]"},
            "NOWCAST_RETRY_BACKOFF_SECONDS",
        )
    ],
    "NOWCAST_STEP_MINUTES": [
        _invalid_case({"NOWCAST_STEP_MINUTES": "6"}, "NOWCAST_STEP_MINUTES")
    ],
    "RATE_LIMIT_FORECAST_READ_PER_MINUTE": [
        _invalid_case(
            {"RATE_LIMIT_FORECAST_READ_PER_MINUTE": "0"},
            "RATE_LIMIT_FORECAST_READ_PER_MINUTE",
        )
    ],
    "RATE_LIMIT_FORECAST_TRANSITION_PER_MINUTE": [
        _invalid_case(
            {"RATE_LIMIT_FORECAST_TRANSITION_PER_MINUTE": "0"},
            "RATE_LIMIT_FORECAST_TRANSITION_PER_MINUTE",
        )
    ],
    "RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE": [
        _invalid_case(
            {"RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE": "0"},
            "RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE",
        )
    ],
    "RATE_LIMIT_SIMULATION_SUBMIT_PER_MINUTE": [
        _invalid_case(
            {"RATE_LIMIT_SIMULATION_SUBMIT_PER_MINUTE": "0"},
            "RATE_LIMIT_SIMULATION_SUBMIT_PER_MINUTE",
        )
    ],
    "RATE_LIMIT_SSE_CONNECT_PER_MINUTE": [
        _invalid_case(
            {"RATE_LIMIT_SSE_CONNECT_PER_MINUTE": "0"},
            "RATE_LIMIT_SSE_CONNECT_PER_MINUTE",
        )
    ],
    "SIMULATION_FRAME_MAX_BYTES": [
        _invalid_case(
            {"SIMULATION_FRAME_MAX_BYTES": "67108865"}, "SIMULATION_FRAME_MAX_BYTES"
        )
    ],
    "SIMULATION_OUTPUT_MAX_BYTES": [
        _invalid_case(
            {"SIMULATION_OUTPUT_MAX_BYTES": "8589934593"}, "SIMULATION_OUTPUT_MAX_BYTES"
        )
    ],
    "SIMULATION_RESULT_RETENTION_DAYS": [
        _invalid_case(
            {"SIMULATION_RESULT_RETENTION_DAYS": "0"},
            "SIMULATION_RESULT_RETENTION_DAYS",
        )
    ],
    "SIM_ATTEMPT_TIMEOUT_SECONDS": [
        _invalid_case(
            {"SIM_ATTEMPT_TIMEOUT_SECONDS": "601"}, "SIM_ATTEMPT_TIMEOUT_SECONDS"
        )
    ],
    "SIM_CANCEL_POLL_SECONDS": [
        _invalid_case({"SIM_CANCEL_POLL_SECONDS": "2"}, "SIM_CANCEL_POLL_SECONDS")
    ],
    "SIM_GLOBAL_CONCURRENCY": [
        _invalid_case({"SIM_GLOBAL_CONCURRENCY": "1"}, "SIM_GLOBAL_CONCURRENCY"),
        _invalid_case({"SIM_GLOBAL_CONCURRENCY": "3"}, "SIM_GLOBAL_CONCURRENCY"),
    ],
    "SIM_HEARTBEAT_SECONDS": [
        _invalid_case({"SIM_HEARTBEAT_SECONDS": "30"}, "SIM_HEARTBEAT_SECONDS")
    ],
    "SIM_INPUT_MAX_BYTES": [
        _invalid_case({"SIM_INPUT_MAX_BYTES": "20971521"}, "SIM_INPUT_MAX_BYTES")
    ],
    "SIM_LEASE_SECONDS": [
        _invalid_case({"SIM_LEASE_SECONDS": "0"}, "SIM_LEASE_SECONDS")
    ],
    "SIM_MAX_ATTEMPTS": [_invalid_case({"SIM_MAX_ATTEMPTS": "4"}, "SIM_MAX_ATTEMPTS")],
    "SIM_NOWCAST_RESERVED_SLOTS": [
        _invalid_case({"SIM_NOWCAST_RESERVED_SLOTS": "2"}, "SIM_NOWCAST_RESERVED_SLOTS")
    ],
    "SIM_RETRY_BACKOFF_SECONDS": [
        _invalid_case(
            {"SIM_RETRY_BACKOFF_SECONDS": "[60, 10]"},
            "SIM_RETRY_BACKOFF_SECONDS",
        )
    ],
    "SIM_WORKSPACE_MAX_BYTES": [
        _invalid_case(
            {"SIM_WORKSPACE_MAX_BYTES": "2147483649"}, "SIM_WORKSPACE_MAX_BYTES"
        )
    ],
    "SOLVER_DEFAULT_SELECTION": [
        _invalid_case(
            {"SOLVER_DEFAULT_SELECTION": "explicit"}, "SOLVER_DEFAULT_SELECTION"
        )
    ],
}


def _load_settings(repo_root: Path) -> tuple[Any, str]:
    backend = repo_root / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    module = importlib.import_module("app.core.config")
    source = (backend / "app/core/config.py").read_text(encoding="utf-8")
    return module.Settings, source


def _validate_invalid_cases(settings_type: Any, field_names: Iterable[str]) -> None:
    from pydantic import ValidationError

    names = set(field_names)
    original = {name: __import__("os").environ.get(name) for name in names}
    try:
        for field_name, cases in INVALID_CASES.items():
            if field_name not in AUTHORIZED_SETTINGS:
                continue
            for index, case in enumerate(cases):
                for name in names:
                    __import__("os").environ.pop(name, None)
                for name, value in case["environment"].items():
                    __import__("os").environ[name] = str(value)
                try:
                    settings_type(_env_file=None)
                except ValidationError as exc:
                    expected = case.get("error_contains")
                    if expected is not None:
                        require(
                            expected in str(exc),
                            (
                                f"invalid case {field_name}[{index}] did not "
                                f"mention {expected}"
                            ),
                        )
                except Exception as exc:
                    raise GateError(
                        f"invalid case {field_name}[{index}] raised "
                        f"non-ValidationError: {exc}"
                    ) from exc
                else:
                    raise GateError(f"invalid case {field_name}[{index}] was accepted")
    finally:
        import os

        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _live_settings_contract(repo_root: Path, env_example: Path) -> dict[str, Any]:
    settings_type, source = _load_settings(repo_root)
    fields = settings_type.model_fields
    env_values = _parse_env_example(env_example)
    field_names = tuple(sorted(fields))
    require(
        field_names == tuple(sorted(AUTHORIZED_SETTINGS + UNAUTHORIZED_SETTINGS)),
        "Settings registry does not contain exactly the frozen 105 fields",
    )
    require(
        len(field_names) == 105,
        f"expected 105 Settings fields, found {len(field_names)}",
    )
    require(
        tuple(sorted(env_values)) == field_names,
        ".env.example keys do not exactly match Settings.model_fields",
    )
    require(
        not set(AUTHORIZED_SETTINGS) & set(UNAUTHORIZED_SETTINGS),
        "authorized and preserve-only Settings overlap",
    )
    require(
        len(AUTHORIZED_SETTINGS) == 54,
        "authorized Settings registry must contain 54 fields",
    )
    require(
        len(UNAUTHORIZED_SETTINGS) == 51,
        "preserve-only Settings registry must contain 51 fields",
    )

    validators = _settings_validators(source)
    fields_contract: list[dict[str, Any]] = []
    for name in AUTHORIZED_SETTINGS:
        field = fields[name]
        fields_contract.append(
            {
                "authorized": True,
                "constraints": _constraints(field),
                "default": _canonical_default(field),
                "env_example": env_values[name],
                "invalid_cases": copy.deepcopy(INVALID_CASES.get(name, [])),
                "name": name,
                "type": _canonical_type(field.annotation),
                "validators": validators.get(
                    name, ["Settings:model:validate_simulation_contract"]
                ),
            }
        )
    unauthorized_contract = [
        {"authorized": False, "name": name, "preserve_only": True}
        for name in UNAUTHORIZED_SETTINGS
    ]
    _validate_invalid_cases(settings_type, field_names)
    return {
        "fields": fields_contract,
        "field_count": 54,
        "unauthorized_field_count": 51,
        "unauthorized_fields": unauthorized_contract,
    }


def _file_snapshot(repo_root: Path, base_commit: str) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for auth_id in FILE_AUTH_IDS:
        entries: list[dict[str, Any]] = []
        for relative in sorted(AUTH_FILE_GROUPS[auth_id]):
            live_path = repo_root / PurePosixPath(relative)
            require(
                live_path.is_file(),
                f"authorized dependency file is missing: {relative}",
            )
            base_blob = _git_blob(repo_root, base_commit, relative)
            entries.append(
                {
                    "base_sha256": (
                        sha256_bytes(base_blob) if base_blob is not None else None
                    ),
                    "base_state": "PRESENT" if base_blob is not None else "ABSENT",
                    "path": relative,
                    "result_sha256": sha256_file(live_path),
                }
            )
        files[auth_id] = {"paths": entries}
    return files


def build_expected_contract_snapshot(
    repo_root: Path,
    base_commit: str = BASE_COMMIT,
    env_example: Path | None = None,
) -> dict[str, Any]:
    """Build the frozen expectation payload without writing any file.

    This pure helper is used to materialize the committed expected fixture once;
    the CLI never calls it as a substitute for live validation.
    """

    root = repo_root.resolve()
    env_path = env_example or root / ".env.example"
    return {
        "approved_migration": _approved_migration_reference(root),
        "auth_ids": list(AUTH_IDS),
        "base_commit": base_commit,
        "deferred": copy.deepcopy(list(DEFERRED_ITEMS)),
        "effective_owners": EFFECTIVE_OWNERS,
        "files": _file_snapshot(root, base_commit),
        "frontend_performance": _frontend_performance_contract_snapshot(root),
        "phase": "post-handoff",
        "schema": EXPECTED_SCHEMA,
        "settings": _live_settings_contract(root, env_path),
        "status": HANDOFF_STATUS,
        "versions": copy.deepcopy(VERSION_CONTRACT),
    }


def _parse_npmrc(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        require("=" in line, f"invalid .npmrc line: {raw_line}")
        key, value = line.split("=", 1)
        entries.append((key.strip().lower(), value.strip()))
    return entries


def _verify_expected_and_handoff(
    expected: dict[str, Any],
    handoff: dict[str, Any],
) -> None:
    require(
        expected.get("schema") == EXPECTED_SCHEMA, "unexpected expected fixture schema"
    )
    require(
        handoff.get("schema") == HANDOFF_SCHEMA, "unexpected dependency handoff schema"
    )
    for key in (
        "approved_migration",
        "auth_ids",
        "base_commit",
        "deferred",
        "effective_owners",
        "files",
        "frontend_performance",
        "phase",
        "settings",
        "status",
        "versions",
    ):
        require(
            handoff.get(key) == expected.get(key),
            f"handoff differs from expected fixture: {key}",
        )
    require(
        tuple(handoff["auth_ids"]) == AUTH_IDS,
        "handoff auth_ids must be exactly DEP-AUTH-01..13",
    )
    require(
        tuple(handoff["files"]) == FILE_AUTH_IDS,
        "handoff files must be exactly DEP-AUTH-01..12",
    )
    require(handoff["phase"] == "post-handoff", "handoff phase must be post-handoff")
    require(
        handoff["status"] == HANDOFF_STATUS, f"handoff status must be {HANDOFF_STATUS}"
    )


def _verify_expected_reference(
    repo_root: Path,
    handoff: dict[str, Any],
    expected_path: Path,
) -> None:
    reference = handoff.get("expected_fixture")
    require(isinstance(reference, dict), "handoff requires expected_fixture")
    require(set(reference) == {"path", "sha256"}, "invalid expected_fixture shape")
    relative = expected_path.relative_to(repo_root.resolve()).as_posix()
    require(reference["path"] == relative, "handoff expected_fixture path drifted")
    require(
        reference["sha256"] == sha256_file(expected_path),
        "handoff expected_fixture SHA-256 mismatch",
    )


def _verify_files(
    repo_root: Path,
    handoff: dict[str, Any],
    base_commit: str,
) -> None:
    for auth_id in FILE_AUTH_IDS:
        entry = handoff["files"][auth_id]
        require(set(entry) == {"paths"}, f"unexpected keys for {auth_id}")
        paths = tuple(item["path"] for item in entry["paths"])
        require(
            paths == tuple(sorted(AUTH_FILE_GROUPS[auth_id])),
            f"wrong paths for {auth_id}",
        )
        for item in entry["paths"]:
            relative = item["path"]
            require(
                set(item) == {"base_sha256", "base_state", "path", "result_sha256"},
                f"invalid file entry: {relative}",
            )
            live = repo_root / PurePosixPath(relative)
            require(live.is_file(), f"live dependency file is missing: {relative}")
            base_blob = _git_blob(repo_root, base_commit, relative)
            expected_state = "PRESENT" if base_blob is not None else "ABSENT"
            require(
                item["base_state"] == expected_state, f"base state mismatch: {relative}"
            )
            expected_base_hash = (
                sha256_bytes(base_blob) if base_blob is not None else None
            )
            require(
                item["base_sha256"] == expected_base_hash,
                f"base SHA-256 mismatch: {relative}",
            )
            require(
                item["result_sha256"] == sha256_file(live),
                f"result SHA-256 mismatch: {relative}",
            )


def _verify_versions(
    repo_root: Path,
    handoff: dict[str, Any],
    python_version_file: Path,
    node_version_file: Path,
    npmrc_file: Path,
    package_file: Path,
    lock_file: Path,
) -> None:
    versions = handoff["versions"]
    require(versions == VERSION_CONTRACT, "handoff version contract drifted")
    require(
        python_version_file.read_text(encoding="utf-8").strip() == "3.11",
        ".python-version must be exactly 3.11",
    )
    require(
        node_version_file.read_text(encoding="utf-8").strip() == "18.20.8",
        "frontend/.node-version must be exactly 18.20.8",
    )
    engine_entries = [
        value for key, value in _parse_npmrc(npmrc_file) if key == "engine-strict"
    ]
    require(
        len(engine_entries) == 1 and engine_entries[0].lower() == "true",
        ".npmrc must contain exactly one effective engine-strict=true",
    )

    package = _load_json(package_file, "package.json")
    require(
        package.get("engines") == {"node": "18.20.8", "npm": "10.8.2"},
        "package.json engines drifted",
    )
    require(
        package.get("packageManager") == "npm@10.8.2",
        "packageManager must be npm@10.8.2",
    )
    require(
        package.get("overrides", {}).get("rollup") == "4.22.0",
        "package.json rollup override drifted",
    )
    scripts = package.get("scripts")
    require(isinstance(scripts, dict), "package.json scripts must be an object")
    require(
        scripts.get("test:perf:twin") == "node performance/run-twin-map.mjs",
        "frontend performance entrypoint drifted",
    )
    require(
        scripts.get("test:perf") == "node performance/run-twin-map.mjs",
        "frontend performance compatibility entrypoint drifted",
    )

    base_blob = _git_blob(repo_root, BASE_COMMIT, "frontend/package.json")
    require(base_blob is not None, "base frontend/package.json is unavailable")
    base_package = json.loads(base_blob.decode("utf-8"))
    require(
        {
            key: value
            for key, value in scripts.items()
            if key not in {"test:perf", "test:perf:twin"}
        }
        == base_package.get("scripts"),
        "package scripts changed other than the approved performance entrypoints",
    )

    lock = _load_json(lock_file, "package-lock.json")
    require(
        type(lock.get("lockfileVersion")) is int and lock["lockfileVersion"] == 3,
        "package-lock lockfileVersion must be number 3",
    )
    root_lock = lock.get("packages", {}).get("")
    require(isinstance(root_lock, dict), "package-lock packages[''] is missing")
    for key in ("dependencies", "devDependencies", "engines"):
        require(
            root_lock.get(key) == package.get(key),
            f"package-lock root {key} differs from package.json",
        )


def _verify_deferred(repo_root: Path, handoff: dict[str, Any]) -> list[str]:
    require(
        handoff["deferred"] == list(DEFERRED_ITEMS),
        "deferred items differ from the frozen handoff",
    )
    return [item["id"] for item in handoff["deferred"]]


def _verify_r0_reference(repo_root: Path, handoff: dict[str, Any]) -> dict[str, str]:
    reference = handoff.get("r0_review")
    require(isinstance(reference, dict), "handoff requires r0_review")
    require(
        set(reference) == {"artifact", "document_bundle_sha256", "verdict"},
        "invalid r0_review shape",
    )
    require(
        reference["verdict"] == "R0_DESIGN_PASS",
        "handoff R0 verdict must be R0_DESIGN_PASS",
    )
    artifact_path = _repo_path(repo_root, reference["artifact"])
    require(artifact_path.is_file(), "handoff R0 artifact is missing")
    artifact = _load_json(artifact_path, "R0 artifact")
    require(
        artifact.get("verdict") == reference["verdict"],
        "handoff and R0 artifact verdicts differ",
    )
    require(
        artifact.get("base_commit") == BASE_COMMIT,
        "handoff and R0 artifact base commits differ",
    )
    require(
        artifact.get("document_bundle_sha256") == reference["document_bundle_sha256"],
        "handoff and R0 artifact bundle hashes differ",
    )
    return {key: str(value) for key, value in reference.items()}


def validate_dependency_handoff(
    repo_root: Path,
    handoff_path: Path,
    expected_path: Path,
    python_version_file: Path,
    node_version_file: Path,
    npmrc_file: Path,
    package_file: Path,
    lock_file: Path,
    env_example: Path,
) -> dict[str, Any]:
    """Validate the handoff without writing files or running package scripts."""

    root = repo_root.resolve()
    handoff_file = _repo_path(root, handoff_path)
    expected_file = _repo_path(root, expected_path)
    handoff = _load_json(handoff_file, "dependency handoff")
    expected = _load_json(expected_file, "expected dependency fixture")

    _verify_expected_and_handoff(expected, handoff)
    _verify_expected_reference(root, handoff, expected_file)
    require(
        handoff["base_commit"] == BASE_COMMIT,
        f"handoff base_commit must be {BASE_COMMIT}",
    )
    resolved_base = (
        _git(root, "rev-parse", "--verify", f"{handoff['base_commit']}^{{commit}}")
        .stdout.decode()
        .strip()
    )
    require(
        resolved_base == handoff["base_commit"],
        "handoff base commit cannot be resolved",
    )
    _verify_files(root, handoff, handoff["base_commit"])
    _verify_versions(
        root,
        handoff,
        _repo_path(root, python_version_file),
        _repo_path(root, node_version_file),
        _repo_path(root, npmrc_file),
        _repo_path(root, package_file),
        _repo_path(root, lock_file),
    )
    live_settings = _live_settings_contract(root, _repo_path(root, env_example))
    require(
        handoff["settings"] == live_settings,
        "handoff Settings contract differs from live Settings/.env.example",
    )
    require(
        handoff["settings"] == expected["settings"],
        "handoff Settings contract differs from expected fixture",
    )
    approved_migration = _verify_approved_migration_reference(
        root,
        handoff.get("approved_migration"),
    )
    frontend_performance = _verify_frontend_performance_contract(
        root,
        handoff.get("frontend_performance"),
    )
    deferred = _verify_deferred(root, handoff)
    r0_reference = _verify_r0_reference(root, handoff)

    return {
        "status": HANDOFF_STATUS,
        "base_commit": handoff["base_commit"],
        "approved_migration": approved_migration,
        "auth_ids": list(handoff["auth_ids"]),
        "settings_counts": {"authorized": 54, "preserve_only": 51},
        "versions": handoff["versions"],
        "deferred": deferred,
        "frontend_performance": frontend_performance,
        "g0_g5_blocked_by_deferred": True,
        "r0_review": r0_reference,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--python-version-file", required=True)
    parser.add_argument("--node-version-file", required=True)
    parser.add_argument("--npmrc", required=True)
    parser.add_argument("--package", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--env-example", required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        result = validate_dependency_handoff(
            repo_root,
            _cli_path(repo_root, args.handoff),
            _cli_path(repo_root, args.expected),
            _cli_path(repo_root, args.python_version_file),
            _cli_path(repo_root, args.node_version_file),
            _cli_path(repo_root, args.npmrc),
            _cli_path(repo_root, args.package),
            _cli_path(repo_root, args.lock),
            _cli_path(repo_root, args.env_example),
        )
    except (GateError, OSError, ValueError, TypeError) as exc:
        print(f"DEPENDENCY_HANDOFF=FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    print("DEPENDENCY_HANDOFF=PASS_WITH_DEFERRED")
    for item in result["deferred"]:
        print(f"DEFERRED={item}")
    print("G0_G5_BLOCKED_BY_DEFERRED=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
