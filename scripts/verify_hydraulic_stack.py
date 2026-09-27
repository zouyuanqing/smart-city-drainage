#!/usr/bin/env python3
"""Run the frozen hydraulic G0-G6/acceptance checks with real evidence.

The verifier never treats a checklist as execution.  Every child command keeps
its exit code and log, missing prerequisites are BLOCKED, Compose config is not
reported as a live G6/AC-8 result, and acceptance consumes only same-run,
same-commit artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPORT_SCHEMA = "hydraulic-stack-gate-report-v1"
ACCEPTANCE_SCHEMA = "hydraulic-acceptance-report-v1"
BASE_COMMIT = "ca99e0485fb12fbecbd565e79d81f745f8d4a748"
R0_ARTIFACT = "artifacts/contracts/v3-independent-review.json"
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 2
EXIT_INTERNAL = 3
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

G6_COMPOSE_LOCK_FIXTURE = "docker/hydraulic-gates/compose-v1.lock.json"
G6_RESTORE_MANIFEST_FIXTURE = (
    "backend/tests/fixtures/hydraulic/compose/restore-manifest-v1.json"
)
G6_REQUIRED_SERVICES = (
    "backend",
    "frontend",
    "grafana",
    "influxdb",
    "migrate",
    "network-job-worker",
    "nginx",
    "notification-worker",
    "nowcast-worker",
    "outbox-relay",
    "postgres",
    "prometheus",
    "redis",
    "simulation-worker",
)
G6_MIGRATION_GATE_SERVICES = (
    "backend",
    "network-job-worker",
    "nowcast-worker",
    "simulation-worker",
)
G6_SERVICES_WITHOUT_HOST_PORTS = ("influxdb", "postgres", "redis")
G6_SSE_NGINX_DIRECTIVES = (
    "proxy_buffering off",
    "proxy_cache off",
    "proxy_read_timeout 1h",
    'proxy_set_header Connection ""',
)
G6_PROBES: tuple[dict[str, Any], ...] = (
    {
        "name": "backend-health",
        "url": "http://127.0.0.1:8000/health",
        "expected_status": [200],
    },
    {
        "name": "backend-readyz",
        "url": "http://127.0.0.1:8000/readyz",
        "expected_status": [200],
    },
    {
        "name": "nginx-health",
        "url": "http://127.0.0.1:8080/health",
        "expected_status": [200],
    },
)
BACKUP_MANIFEST_SCHEMA_SOURCE = "scripts/backup_manifest.py"
WORKER_READINESS_SOURCE = "backend/app/main.py"
# Used when the script is invoked with no arguments, so a bare
# `python scripts/verify_hydraulic_stack.py` is a valid entry point that still
# writes its evidence to a predictable, repository-relative location.
DEFAULT_EVIDENCE_DIR = ".runtime/evidence/compose-smoke"

GATE_FIXTURES: dict[str, tuple[str, ...]] = {
    "g1": ("backend/tests/fixtures/hydraulic/schema/manifest-v1.json",),
    "g2": (
        "backend/tests/fixtures/hydraulic/network_jobs/v1/manifest.json",
        "backend/tests/fixtures/hydraulic/contracts/idempotency-ttl-v1.json",
        "backend/tests/fixtures/hydraulic/security/jwt-rbac-ratelimit-v1.json",
    ),
    "g3": (
        "backend/tests/fixtures/hydraulic/workers/lifecycle-v1.json",
        "backend/tests/fixtures/hydraulic/calibration/durable-lifecycle-v1.json",
        "backend/tests/fixtures/hydraulic/hydraulics/conservation-stability-v1.json",
    ),
    "g4": (
        "backend/tests/fixtures/hydraulic/forecast/v1/manifest.json",
        "backend/tests/fixtures/hydraulic/realtime/outbox-sse-v1.json",
    ),
    "g6": (G6_COMPOSE_LOCK_FIXTURE, G6_RESTORE_MANIFEST_FIXTURE),
}

GATE_COMMANDS_KEY = "gate_commands"

# The acceptance gate derives every AC's required gates from this frozen
# fixture, so the script cannot carry a weaker second copy of the mapping.
ACCEPTANCE_REQUIREMENTS_FIXTURE = (
    "backend/tests/fixtures/hydraulic/acceptance/v1/acceptance_requirements.json"
)
ACCEPTANCE_CASES = tuple(f"AC-{number}" for number in range(1, 9))
# Gates an AC may require; anything else in the fixture is a fixture bug.
_ACCEPTANCE_GATES = frozenset({"g0", "g1", "g2", "g3", "g4", "g5", "g6", "e2e"})


class GateError(RuntimeError):
    """A verifier input or internal contract error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise GateError(f"{label} is not finite, readable JSON: {path}: {exc}") from exc
    require(isinstance(value, dict), f"{label} must be a JSON object: {path}")
    return value


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _cli_path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate
    workspace_candidate = (root.parent / candidate).resolve()
    try:
        workspace_candidate.relative_to(root)
        return workspace_candidate
    except ValueError:
        return (root / candidate).resolve()


def evidence_path(root: Path, value: str | Path) -> Path:
    candidate = _cli_path(root, value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise GateError(f"evidence directory escapes repository root: {value}") from exc
    return candidate


def repo_path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise GateError(f"path escapes repository root: {value}") from exc
    return resolved


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("git")
    if executable is None:
        if check:
            raise GateError("git executable is unavailable")
        return subprocess.CompletedProcess(["git", *args], 127, "", "git unavailable")
    completed = subprocess.run(
        [executable, "-C", str(root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and completed.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed


def _summary_lines(text: str, limit: int = 20) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def run_command(
    *,
    root: Path,
    evidence_dir: Path,
    name: str,
    argv: Sequence[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: int = 300,
    blocked_exit_codes: frozenset[int] = frozenset(),
    sensitive_stdout: bool = False,
) -> dict[str, Any]:
    logs_dir = evidence_dir / "commands"
    logs_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "command"
    stdout_path = logs_dir / f"{safe_name}.stdout.log"
    stderr_path = logs_dir / f"{safe_name}.stderr.log"
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    started_at = utc_now()
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(cwd or root),
            env=process_env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        stdout = completed.stdout
        stderr = completed.stderr
        return_code = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        return_code = 124
        timed_out = True
    duration = time.perf_counter() - started
    if sensitive_stdout and stdout:
        stdout_log = (
            f"[sensitive stdout redacted]\n"
            f"bytes={len(stdout.encode('utf-8'))}\n"
            f"sha256={sha256_bytes(stdout.encode('utf-8'))}\n"
        )
    else:
        stdout_log = stdout
    stdout_path.write_text(stdout_log, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    if return_code == 0:
        status = "PASS"
    elif return_code in blocked_exit_codes:
        status = "BLOCKED"
    else:
        status = "FAIL"
    return {
        "name": name,
        "status": status,
        "command": list(argv),
        "cwd": str((cwd or root).resolve()),
        "exit_code": return_code,
        "timed_out": timed_out,
        "duration_seconds": round(duration, 6),
        "started_at": started_at,
        "stdout_log": str(stdout_path.relative_to(root)),
        "stderr_log": str(stderr_path.relative_to(root)),
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
        "stdout_summary": _summary_lines(
            stdout if not sensitive_stdout else "[sensitive stdout redacted]"
        ),
        "stderr_summary": _summary_lines(stderr),
        "_stdout": stdout,
        "_stderr": stderr,
    }


def public_step(step: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in step.items() if not key.startswith("_")}


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)
    return sha256_bytes(payload.encode("utf-8"))


def physical_cores() -> int | None:
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.is_file():
        return None
    cores: set[tuple[str, str]] = set()
    current: dict[str, str] = {}
    for line in [
        *cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines(),
        "",
    ]:
        if line:
            key, separator, value = line.partition(":")
            if separator:
                current[key.strip()] = value.strip()
            continue
        if current.get("physical id") and current.get("core id"):
            cores.add((current["physical id"], current["core id"]))
        current = {}
    return len(cores) or None


def total_ram_bytes() -> int | None:
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def node_version(root: Path) -> str | None:
    executable = shutil.which("node")
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def npm_version(root: Path) -> str | None:
    executable = shutil.which("npm")
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def metadata(root: Path) -> dict[str, Any]:
    lock = root / "backend" / "requirements-py311.lock"
    return {
        "os": platform.platform(),
        "cpu_model": platform.processor() or None,
        "physical_cores": physical_cores(),
        "logical_cpus": os.cpu_count(),
        "ram_bytes": total_ram_bytes(),
        "python_version": platform.python_version(),
        "node_version": node_version(root),
        "npm_version": npm_version(root),
        "dependency_lock_sha256": sha256_file(lock) if lock.is_file() else None,
        "git_commit": git(root, "rev-parse", "HEAD", check=False).stdout.strip()
        or None,
        "git_dirty": bool(
            git(root, "status", "--porcelain", check=False).stdout.strip()
        ),
    }


def command_name(tool: str) -> str:
    if os.name == "nt":
        return f"{tool}.cmd" if tool == "npm" else tool
    return tool


def fixture_step(
    *,
    root: Path,
    fixture_paths: Sequence[str],
    gate: str,
) -> dict[str, Any]:
    missing = [
        path for path in fixture_paths if not (root / PurePosixPath(path)).is_file()
    ]
    if missing:
        return {
            "name": f"{gate}.fixtures",
            "status": "BLOCKED",
            "command": [],
            "cwd": str(root),
            "exit_code": 2,
            "duration_seconds": 0.0,
            "started_at": utc_now(),
            "stdout_log": None,
            "stderr_log": None,
            "stdout_sha256": None,
            "stderr_sha256": None,
            "stdout_summary": [],
            "stderr_summary": [f"missing frozen fixtures: {', '.join(missing)}"],
            "missing_fixtures": missing,
        }
    return {
        "name": f"{gate}.fixtures",
        "status": "PASS",
        "command": [],
        "cwd": str(root),
        "exit_code": 0,
        "duration_seconds": 0.0,
        "started_at": utc_now(),
        "stdout_log": None,
        "stderr_log": None,
        "stdout_sha256": None,
        "stderr_sha256": None,
        "stdout_summary": [f"all {len(fixture_paths)} frozen fixtures exist"],
        "stderr_summary": [],
        "fixture_sha256": {
            path: sha256_file(root / PurePosixPath(path)) for path in fixture_paths
        },
    }


def optional_manifest_commands(
    root: Path,
    manifest_path: Path,
) -> list[list[str]]:
    value = load_json(manifest_path, f"{manifest_path} gate manifest")
    raw = value.get(GATE_COMMANDS_KEY, [])
    require(
        isinstance(raw, list), f"{manifest_path}:{GATE_COMMANDS_KEY} must be an array"
    )
    commands: list[list[str]] = []
    for index, command in enumerate(raw):
        require(
            isinstance(command, list)
            and command
            and all(isinstance(part, str) for part in command),
            f"{manifest_path}:{GATE_COMMANDS_KEY}[{index}] must be argv",
        )
        executable = command[0]
        if executable in {"python", "pytest", "node", "npm", "docker"}:
            command = [command_name(executable), *command[1:]]
        resolved = shutil.which(command[0])
        require(
            resolved is not None,
            f"gate command executable is unavailable: {command[0]}",
        )
        command[0] = resolved
        commands.append(command)
    return commands


def enforce_version_step(step: dict[str, Any], expected: str) -> None:
    actual = str(step.get("_stdout", "")).strip()
    if step.get("exit_code") == 0 and actual == expected:
        step["status"] = "PASS"
        step["stderr_summary"] = []
        return
    step["status"] = "FAIL"
    step["exit_code"] = 1
    step["stderr_summary"] = [
        f"expected {expected}, observed {actual or '<unavailable>'}"
    ]


def gate_g0(root: Path, evidence_dir: Path) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    python = sys.executable
    steps.append(
        run_command(
            root=root,
            evidence_dir=evidence_dir,
            name="g0-python-version",
            argv=[
                python,
                "-c",
                "import sys; print(sys.version); raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)",
            ],
            timeout_seconds=30,
        )
    )
    node_version_step = run_command(
        root=root,
        evidence_dir=evidence_dir,
        name="g0-node-version",
        argv=[command_name("node"), "--version"],
        timeout_seconds=30,
    )
    enforce_version_step(node_version_step, "v18.20.8")
    steps.append(node_version_step)
    npm_version_step = run_command(
        root=root,
        evidence_dir=evidence_dir,
        name="g0-npm-version",
        argv=[command_name("npm"), "--version"],
        timeout_seconds=30,
    )
    enforce_version_step(npm_version_step, "10.8.2")
    steps.append(npm_version_step)
    steps.append(
        run_command(
            root=root,
            evidence_dir=evidence_dir,
            name="g0-r0-migration-gate",
            argv=[
                python,
                "scripts/check_migration_gate.py",
                "--review-artifact",
                R0_ARTIFACT,
                "--base-ref",
                BASE_COMMIT,
                "--versions-dir",
                "backend/alembic/versions",
            ],
            timeout_seconds=120,
            blocked_exit_codes=frozenset({2}),
        )
    )
    steps.append(
        run_command(
            root=root,
            evidence_dir=evidence_dir,
            name="g0-contract-ownership",
            argv=[
                python,
                "scripts/check_contract_ownership.py",
                "--contract",
                "docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md",
                "--phase",
                "post-handoff",
            ],
            timeout_seconds=120,
            blocked_exit_codes=frozenset({2}),
        )
    )
    steps.append(
        run_command(
            root=root,
            evidence_dir=evidence_dir,
            name="g0-dependency-handoff",
            argv=[
                python,
                "scripts/check_dependency_handoff.py",
                "--handoff",
                "artifacts/contracts/dependency-config-handoff.json",
                "--expected",
                "backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json",
                "--python-version-file",
                ".python-version",
                "--node-version-file",
                "frontend/.node-version",
                "--npmrc",
                "frontend/.npmrc",
                "--package",
                "frontend/package.json",
                "--lock",
                "frontend/package-lock.json",
                "--env-example",
                ".env.example",
            ],
            timeout_seconds=180,
            blocked_exit_codes=frozenset({2}),
        )
    )
    backend = root / "backend"
    steps.append(
        run_command(
            root=root,
            evidence_dir=evidence_dir,
            name="g0-backend-pytest",
            argv=[python, "-m", "pytest", "tests/", "-v", "--tb=short"],
            cwd=backend,
            env={
                "DATABASE_URL": "sqlite+aiosqlite:///./test.db",
                "REDIS_URL": "redis://127.0.0.1:1/0",
                "INFLUXDB_URL": "http://127.0.0.1:1",
                "JWT_SECRET_KEY": "g0-test-secret-not-for-production",
                "PYTHONPATH": str(backend),
            },
            timeout_seconds=900,
        )
    )
    lint_commands = (
        ("g0-backend-black", ("-m", "black", "--check", "app", "tests")),
        ("g0-backend-isort", ("-m", "isort", "--check-only", "app", "tests")),
        ("g0-backend-flake8", ("-m", "flake8", "app", "tests")),
        ("g0-backend-mypy", ("-m", "mypy", "app", "--ignore-missing-imports")),
    )
    for name, arguments in lint_commands:
        steps.append(
            run_command(
                root=root,
                evidence_dir=evidence_dir,
                name=name,
                argv=[python, *arguments],
                cwd=backend,
                timeout_seconds=600,
            )
        )
    startup_code = (
        "from fastapi.testclient import TestClient; "
        "from app.main import app; "
        "client = TestClient(app); "
        "client.__enter__(); "
        "response = client.get('/health'); "
        "print(response.status_code, response.text); "
        "assert response.status_code == 200; "
        "client.__exit__(None, None, None)"
    )
    steps.append(
        run_command(
            root=root,
            evidence_dir=evidence_dir,
            name="g0-backend-lifespan",
            argv=[python, "-c", startup_code],
            cwd=backend,
            env={
                "DATABASE_URL": "sqlite+aiosqlite:///./test.db",
                "REDIS_URL": "redis://127.0.0.1:1/0",
                "INFLUXDB_URL": "http://127.0.0.1:1",
                "JWT_SECRET_KEY": "g0-test-secret-not-for-production",
                "PYTHONPATH": str(backend),
            },
            timeout_seconds=120,
        )
    )
    frontend = root / "frontend"
    npm = command_name("npm")
    steps.append(
        run_command(
            root=root,
            evidence_dir=evidence_dir,
            name="g0-frontend-npm-ci",
            argv=[npm, "ci"],
            cwd=frontend,
            timeout_seconds=900,
        )
    )
    for name, command in (
        ("g0-frontend-vitest", [npm, "test"]),
        ("g0-frontend-lint", [npm, "run", "lint"]),
        ("g0-frontend-format", [npm, "run", "format:check"]),
        ("g0-frontend-build", [npm, "run", "build"]),
    ):
        steps.append(
            run_command(
                root=root,
                evidence_dir=evidence_dir,
                name=name,
                argv=command,
                cwd=frontend,
                timeout_seconds=900,
            )
        )
    for relative in (
        "frontend/performance/run-twin-map.mjs",
        "frontend/e2e/run-ac-smoke.mjs",
    ):
        steps.append(
            run_command(
                root=root,
                evidence_dir=evidence_dir,
                name=f"g0-node-check-{PurePosixPath(relative).stem}",
                argv=[command_name("node"), "--check", relative],
                timeout_seconds=60,
            )
        )
    return steps


def gate_domain(root: Path, evidence_dir: Path, gate: str) -> list[dict[str, Any]]:
    fixture_paths = GATE_FIXTURES[gate]
    fixture_result = fixture_step(root=root, fixture_paths=fixture_paths, gate=gate)
    steps = [fixture_result]
    if fixture_result["status"] != "PASS":
        return steps
    manifest_path = root / fixture_paths[0]
    try:
        commands = optional_manifest_commands(root, manifest_path)
    except GateError as exc:
        steps.append(
            {
                "name": f"{gate}.manifest-validation",
                "status": "BLOCKED",
                "command": [],
                "cwd": str(root),
                "exit_code": 2,
                "duration_seconds": 0.0,
                "started_at": utc_now(),
                "stdout_log": None,
                "stderr_log": None,
                "stdout_sha256": None,
                "stderr_sha256": None,
                "stdout_summary": [],
                "stderr_summary": [str(exc)],
            }
        )
        return steps
    if not commands:
        steps.append(
            {
                "name": f"{gate}.harness",
                "status": "BLOCKED",
                "command": [],
                "cwd": str(root),
                "exit_code": 2,
                "duration_seconds": 0.0,
                "started_at": utc_now(),
                "stdout_log": None,
                "stderr_log": None,
                "stdout_sha256": None,
                "stderr_sha256": None,
                "stdout_summary": [],
                "stderr_summary": [
                    f"{manifest_path} does not define executable {GATE_COMMANDS_KEY}; static fixture presence is not gate evidence"
                ],
            }
        )
        return steps
    for index, command in enumerate(commands):
        steps.append(
            run_command(
                root=root,
                evidence_dir=evidence_dir,
                name=f"{gate}.harness-{index + 1}",
                argv=command,
                timeout_seconds=1800,
            )
        )
    return steps


def gate_g5(root: Path, evidence_dir: Path) -> list[dict[str, Any]]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    solver_fixture = (
        root / "backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json"
    )
    frontend_fixture = root / "frontend/performance/fixtures/twin-map-5k-10k-v1.json"
    steps: list[dict[str, Any]] = []
    steps.append(
        fixture_step(
            root=root,
            fixture_paths=(
                "backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json",
                "backend/tests/fixtures/performance/api/control-plane-v1.json",
                "backend/tests/fixtures/performance/nowcast/30-cycle-v1.json",
                "frontend/performance/fixtures/twin-map-5k-10k-v1.json",
            ),
            gate="g5",
        )
    )
    if solver_fixture.is_file():
        steps.append(
            run_command(
                root=root,
                evidence_dir=evidence_dir,
                name="g5-solver-5k-10k",
                argv=[
                    sys.executable,
                    "scripts/benchmark_hydraulic.py",
                    "--fixture",
                    "backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json",
                    "--warmup",
                    "1",
                    "--repeats",
                    "3",
                    "--cpus",
                    "4",
                    "--memory",
                    "8g",
                    "--threads",
                    "4",
                    "--output",
                    str(evidence_dir / "solver.json"),
                ],
                timeout_seconds=1200,
                blocked_exit_codes=frozenset({2}),
            )
        )
    if frontend_fixture.is_file():
        steps.append(
            run_command(
                root=root,
                evidence_dir=evidence_dir,
                name="g5-frontend-map-smoke",
                argv=[
                    command_name("node"),
                    "frontend/performance/run-twin-map.mjs",
                    "--fixture",
                    "frontend/performance/fixtures/twin-map-5k-10k-v1.json",
                    "--duration-seconds",
                    "60",
                    "--output",
                    str(evidence_dir / "frontend-smoke.json"),
                ],
                timeout_seconds=300,
                blocked_exit_codes=frozenset({2}),
            )
        )
    for relative in (
        "scripts/benchmark_hydraulic_api.py",
        "scripts/benchmark_hydraulic_nowcast.py",
    ):
        steps.append(
            {
                "name": f"g5-required-harness-{PurePosixPath(relative).name}",
                "status": "PASS" if (root / relative).is_file() else "BLOCKED",
                "command": [],
                "cwd": str(root),
                "exit_code": 0 if (root / relative).is_file() else 2,
                "duration_seconds": 0.0,
                "started_at": utc_now(),
                "stdout_log": None,
                "stderr_log": None,
                "stdout_sha256": None,
                "stderr_sha256": None,
                "stdout_summary": [],
                "stderr_summary": (
                    []
                    if (root / relative).is_file()
                    else [f"frozen G5 harness is missing: {relative}"]
                ),
            }
        )
    package = root / "frontend/package.json"
    if package.is_file():
        package_value = load_json(package, "frontend/package.json")
        expected_script = "node performance/run-twin-map.mjs"
        actual_script = package_value.get("scripts", {}).get("test:perf:twin")
        steps.append(
            {
                "name": "g5-frozen-frontend-entrypoint",
                "status": "PASS" if actual_script == expected_script else "BLOCKED",
                "command": [],
                "cwd": str(root),
                "exit_code": 0 if actual_script == expected_script else 2,
                "duration_seconds": 0.0,
                "started_at": utc_now(),
                "stdout_log": None,
                "stderr_log": None,
                "stdout_sha256": None,
                "stderr_sha256": None,
                "stdout_summary": [],
                "stderr_summary": (
                    []
                    if actual_script == expected_script
                    else [
                        "frontend/package.json scripts.test:perf:twin must equal "
                        f"{expected_script!r}; the algorithm smoke cannot replace the frozen browser entrypoint"
                    ]
                ),
            }
        )
    return steps


def _compose_env(profile: str) -> dict[str, str]:
    return {
        "HYDRAULIC_SPATIAL_BACKEND": profile,
        "POSTGRES_PASSWORD": "ci-postgres-secret",
        "REDIS_PASSWORD": "ci-redis-secret",
        "INFLUXDB_PASSWORD": "ci-influx-secret",
        "INFLUXDB_TOKEN": "ci-influx-token-secret",
        "DOCKER_INFLUXDB_ADMIN_TOKEN": "ci-influx-token-secret",
        "DATABASE_URL": (
            "postgresql+asyncpg://drainage_admin:ci-postgres-secret@postgres:5432/"
            "smart_drainage"
        ),
        "DATABASE_URL_SYNC": (
            "postgresql://drainage_admin:ci-postgres-secret@postgres:5432/"
            "smart_drainage"
        ),
        "INFLUXDB_ORG": "smart-city",
        "INFLUXDB_BUCKET": "sensor_data",
        "REDIS_URL": "redis://:ci-redis-secret@redis:6379/0",
        "JWT_SECRET_KEY": "ci-jwt-secret-at-least-32-characters",
        "JWT_PRIVATE_KEY": "ci-placeholder-private-key",
        "JWT_PUBLIC_KEY": "ci-placeholder-public-key",
        "JWT_ISSUER": "smart-city-drainage",
        "JWT_AUDIENCE": "smart-city-api",
        "GRAFANA_ADMIN_PASSWORD": "ci-grafana-secret",
    }


def _compose_command(root: Path, profile: str, *arguments: str) -> list[str]:
    docker = shutil.which("docker")
    require(docker is not None, "docker executable is unavailable")
    return [
        docker,
        "compose",
        # Without an explicit project name Compose derives one from the
        # directory name, so `down -v` would destroy whatever volumes the
        # developer's own default project holds.  Every gate command is bound
        # to its own throwaway project.
        "--project-name",
        _compose_project_name(profile),
        "--profile",
        "production",
        "--profile",
        "monitoring",
        "-f",
        "docker-compose.yml",
        *arguments,
    ]


def _compose_project_name(profile: str) -> str:
    return f"hydraulic-gate-{profile}"


def _compose_static_failures(
    root: Path,
    config: Mapping[str, Any],
    profile: str,
) -> list[str]:
    failures: list[str] = []
    services = config.get("services")
    if not isinstance(services, dict):
        return ["compose config has no services object"]
    missing = sorted(set(G6_REQUIRED_SERVICES) - set(services))
    if missing:
        failures.append(f"missing target services: {', '.join(missing)}")

    for name, service in services.items():
        if not isinstance(service, dict):
            failures.append(f"service {name} is not an object")
            continue
        image = service.get("image")
        if isinstance(image, str) and not service.get("build"):
            if "@sha256:" not in image:
                failures.append(f"service {name} image is not digest-pinned: {image}")
        if name in G6_SERVICES_WITHOUT_HOST_PORTS and "ports" in service:
            failures.append(
                f"service {name} publishes a host port in the default profile"
            )

    postgres = services.get("postgres")
    if isinstance(postgres, dict):
        image = str(postgres.get("image", ""))
        if profile == "postgis" and "postgis" not in image.lower():
            failures.append("postgis profile does not select an explicit PostGIS image")
        if profile == "portable" and "postgis" in image.lower():
            failures.append("portable profile must not use a PostGIS image")
    environment = config.get("services", {}).get("backend", {}).get("environment", {})
    if isinstance(environment, dict):
        if environment.get("HYDRAULIC_SPATIAL_BACKEND") != profile:
            failures.append(
                "backend HYDRAULIC_SPATIAL_BACKEND does not match the explicit gate profile"
            )
    else:
        failures.append("backend environment is missing")

    migrate = services.get("migrate")
    if not isinstance(migrate, dict):
        failures.append("migrate service is missing")
    else:
        command = migrate.get("command")
        normalized_command = command if isinstance(command, list) else []
        if (
            "alembic" not in normalized_command
            or "upgrade" not in normalized_command
            or "head" not in normalized_command
        ):
            failures.append("migrate command is not alembic upgrade head")
    for name in G6_MIGRATION_GATE_SERVICES:
        service = services.get(name)
        if not isinstance(service, dict):
            continue
        depends = service.get("depends_on", {})
        migrate_condition = (
            depends.get("migrate", {}).get("condition")
            if isinstance(depends, dict)
            else None
        )
        if migrate_condition != "service_completed_successfully":
            failures.append(
                f"{name} does not wait for migrate service_completed_successfully"
            )

    compose_text_path = root / "docker-compose.yml"
    if compose_text_path.is_file():
        compose_text = compose_text_path.read_text(encoding="utf-8", errors="replace")
        secret_pattern = re.compile(
            r"(?im)^\s*(?:[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN)|DATABASE_URL|REDIS_URL):\s*(?!\$\{)(?!\"\s*\$)(\S.*)$"
        )
        for match in secret_pattern.finditer(compose_text):
            failures.append(
                f"hard-coded secret/default in docker-compose.yml: {match.group(1).strip()}"
            )
    init_sql = root / "docker/init-db.sql"
    if init_sql.is_file():
        sql = init_sql.read_text(encoding="utf-8", errors="replace")
        for marker in ("CREATE TABLE", "create_hypertable", "INSERT INTO"):
            if re.search(marker, sql, flags=re.IGNORECASE):
                failures.append(
                    f"docker/init-db.sql contains forbidden business schema marker: {marker}"
                )
    nginx_text = ""
    for path in (root / "docker/nginx").rglob("*.conf"):
        nginx_text += "\n" + path.read_text(encoding="utf-8", errors="replace")
    for directive in G6_SSE_NGINX_DIRECTIVES:
        if directive not in nginx_text:
            failures.append(f"nginx SSE contract is missing: {directive}")
    return failures


def _fixture_content_hash(value: Mapping[str, Any]) -> str:
    body = {key: item for key, item in value.items() if key != "content_sha256"}
    return sha256_bytes(canonical_json_bytes(body))


def _source_literal(path: Path, pattern: str, label: str) -> str | None:
    """Read one real literal out of a repository source file."""

    if not path.is_file():
        return None
    match = re.search(pattern, path.read_text(encoding="utf-8", errors="replace"))
    if match is None:
        return None
    return match.group(1)


def _worker_heartbeat_keys(root: Path) -> dict[str, str] | None:
    """Parse the real readiness heartbeat registry from the API module."""

    text_path = root / WORKER_READINESS_SOURCE
    if not text_path.is_file():
        return None
    text = text_path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"_WORKER_HEARTBEAT_KEYS\s*=\s*\{(.*?)\}", text, re.DOTALL)
    if match is None:
        return None
    entries = re.findall(r'"([^"]+)":\s*"([^"]+)"', match.group(1))
    return dict(entries) if entries else None


def g6_fixture_contract_failures(
    root: Path,
    config: Mapping[str, Any],
    profile: str,
) -> list[str]:
    """Cross-check the frozen G6 fixtures against the gate and the real code.

    A fixture that only exists is not evidence: the compose lock must still
    describe the same services, probes, image policy and worker readiness
    contract the gate enforces, and the restore manifest must still be an
    unpopulated contract rather than a claimed observation.
    """

    failures: list[str] = []
    lock_path = root / PurePosixPath(G6_COMPOSE_LOCK_FIXTURE)
    restore_path = root / PurePosixPath(G6_RESTORE_MANIFEST_FIXTURE)
    services = (
        config.get("services") if isinstance(config.get("services"), dict) else {}
    )

    if lock_path.is_file():
        try:
            lock = load_json(lock_path, "compose lock fixture")
        except GateError as exc:
            failures.append(str(exc))
        else:
            failures.extend(_compose_lock_failures(root, lock, services, profile))
    if restore_path.is_file():
        try:
            restore = load_json(restore_path, "restore manifest fixture")
        except GateError as exc:
            failures.append(str(exc))
        else:
            failures.extend(_restore_manifest_failures(root, restore))
    return failures


def _compose_lock_failures(
    root: Path,
    lock: Mapping[str, Any],
    services: Mapping[str, Any],
    profile: str,
) -> list[str]:
    failures: list[str] = []
    if lock.get("schema") != "hydraulic-compose-lock-v1":
        failures.append(
            "compose lock schema must be hydraulic-compose-lock-v1, got "
            f"{lock.get('schema')!r}"
        )
    declared_hash = lock.get("content_sha256")
    if not isinstance(declared_hash, str) or not SHA256_RE.fullmatch(declared_hash):
        failures.append("compose lock content_sha256 must be lowercase SHA-256")
    elif _fixture_content_hash(lock) != declared_hash:
        failures.append("compose lock content_sha256 does not match its canonical body")
    if lock.get("evidence_state") != "NOT_RUN":
        failures.append("compose lock evidence_state must stay NOT_RUN")
    contract = lock.get("contract")
    if not isinstance(contract, dict):
        return failures + ["compose lock contract must be an object"]

    expected: tuple[tuple[str, Any, str], ...] = (
        ("required_services", list(G6_REQUIRED_SERVICES), "gate required services"),
        (
            "migration_gate_services",
            list(G6_MIGRATION_GATE_SERVICES),
            "gate migration dependency services",
        ),
        (
            "services_without_host_ports",
            list(G6_SERVICES_WITHOUT_HOST_PORTS),
            "services",
        ),
        ("sse_nginx_directives", list(G6_SSE_NGINX_DIRECTIVES), "nginx SSE directives"),
    )
    for name, gate_value, label in expected:
        if contract.get(name) != gate_value:
            failures.append(
                f"compose lock {name} does not match the {label} this gate enforces"
            )
    migrate = contract.get("migrate")
    if not isinstance(migrate, dict) or migrate.get("command") != [
        "alembic",
        "upgrade",
        "head",
    ]:
        failures.append("compose lock migrate command must be alembic upgrade head")
    health = contract.get("health_contract")
    if not isinstance(health, dict):
        failures.append("compose lock health_contract must be an object")
    else:
        if health.get("probes") != [dict(item) for item in G6_PROBES]:
            failures.append("compose lock probes do not match the gate HTTP probes")
        real_heartbeats = _worker_heartbeat_keys(root)
        if real_heartbeats is None:
            failures.append(
                f"compose lock worker heartbeat keys could not be re-read from "
                f"{WORKER_READINESS_SOURCE}"
            )
        elif health.get("worker_heartbeat_keys") != real_heartbeats:
            failures.append(
                "compose lock worker heartbeat keys drifted from "
                f"{WORKER_READINESS_SOURCE}"
            )
        window = re.search(
            r"now\s*-\s*observed\s*<=\s*([0-9.]+)", _worker_window_source(root)
        )
        if window is not None:
            if health.get("worker_heartbeat_window_seconds") != float(window.group(1)):
                failures.append(
                    "compose lock worker heartbeat window drifted from the readiness check"
                )
    pinned = contract.get("digest_pinned_images")
    if not isinstance(pinned, dict) or not pinned:
        failures.append("compose lock digest_pinned_images must be a non-empty object")
    else:
        marker = contract.get("digest_pin_marker")
        for service, image in sorted(pinned.items()):
            if not isinstance(image, str) or not isinstance(marker, str):
                failures.append(f"compose lock pinned image for {service} is invalid")
                continue
            if marker not in image:
                failures.append(
                    f"compose lock pinned image for {service} is not digest-pinned"
                )
            if service not in G6_REQUIRED_SERVICES:
                failures.append(
                    f"compose lock pins an image for a non-required service: {service}"
                )
        failures.extend(_pinned_image_failures(contract, services, pinned, profile))
    local_services = contract.get("local_image_services")
    if not isinstance(local_services, list) or not local_services:
        failures.append("compose lock local_image_services must be a non-empty array")
    else:
        unknown = sorted(set(local_services) - set(G6_REQUIRED_SERVICES))
        if unknown:
            failures.append(
                f"compose lock local_image_services names non-required services: {unknown}"
            )
    return failures


def _worker_window_source(root: Path) -> str:
    path = root / WORKER_READINESS_SOURCE
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _pinned_image_failures(
    contract: Mapping[str, Any],
    services: Mapping[str, Any],
    pinned: Mapping[str, Any],
    profile: str,
) -> list[str]:
    """The rendered Compose image must match the lock, per profile."""

    failures: list[str] = []
    for service, expected in sorted(pinned.items()):
        rendered = services.get(service)
        if not isinstance(rendered, dict):
            continue
        image = rendered.get("image")
        if service == "postgres" and profile == "postgis":
            # The postgis profile must select a different, PostGIS-bearing image
            # while staying digest-pinned; the portable default does not apply.
            if image == expected:
                failures.append(
                    "postgis profile renders the portable default postgres image"
                )
            continue
        if image != expected:
            failures.append(
                f"compose image drift for {service}: rendered {image!r} does not "
                f"match the frozen lock {expected!r}"
            )
    return failures


def _restore_manifest_failures(
    root: Path,
    restore: Mapping[str, Any],
) -> list[str]:
    failures: list[str] = []
    if restore.get("schema") != "hydraulic-compose-restore-manifest-v1":
        failures.append(
            "restore manifest schema must be hydraulic-compose-restore-manifest-v1, got "
            f"{restore.get('schema')!r}"
        )
    declared_hash = restore.get("content_sha256")
    if not isinstance(declared_hash, str) or not SHA256_RE.fullmatch(declared_hash):
        failures.append("restore manifest content_sha256 must be lowercase SHA-256")
    elif _fixture_content_hash(restore) != declared_hash:
        failures.append(
            "restore manifest content_sha256 does not match its canonical body"
        )
    if restore.get("evidence_state") != "NOT_RUN":
        failures.append("restore manifest evidence_state must stay NOT_RUN")
    observed = restore.get("observed_results")
    if not isinstance(observed, dict):
        failures.append("restore manifest observed_results must be an object")
    else:
        populated = sorted(
            key
            for key, value in observed.items()
            if key != "note" and value is not None
        )
        if populated:
            failures.append(
                "restore manifest must not ship populated observed results: "
                f"{populated}"
            )
    archive = restore.get("archive_contract")
    if not isinstance(archive, dict):
        return failures + ["restore manifest archive_contract must be an object"]
    declared_schema = _source_literal(
        root / BACKUP_MANIFEST_SCHEMA_SOURCE,
        r'MANIFEST_SCHEMA\s*=\s*"([^"]+)"',
        "backup manifest schema",
    )
    if declared_schema is None:
        failures.append(
            f"backup manifest schema could not be re-read from {BACKUP_MANIFEST_SCHEMA_SOURCE}"
        )
    elif archive.get("manifest_schema") != declared_schema:
        failures.append(
            "restore manifest manifest_schema drifted from "
            f"{BACKUP_MANIFEST_SCHEMA_SOURCE} ({declared_schema})"
        )
    components = archive.get("components")
    if components != ["influx", "postgres", "redis"]:
        failures.append(
            "restore manifest components must be the full supported component set"
        )
    assertions = restore.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        failures.append("restore manifest must declare at least one assertion")
    else:
        identifiers = [
            str(item.get("id")) for item in assertions if isinstance(item, dict)
        ]
        if len(identifiers) != len(set(identifiers)) or any(
            not item.get("requirement") or not item.get("predicate")
            for item in assertions
            if isinstance(item, dict)
        ):
            failures.append(
                "restore manifest assertions need unique ids and a requirement plus predicate"
            )
    return failures


def _probe_status(
    status_ok: bool,
    *,
    name: str,
    url: str,
    status: int,
    expected: set[int],
    started: float,
    body: str,
    required_fields: Mapping[str, Any] | None,
) -> tuple[str, list[str]]:
    """Grade a probe on its status code and, optionally, its JSON body.

    A readiness endpoint that answers 200 while reporting
    ``RUNTIME_NOT_READY`` is not ready.  ``required_fields`` states the body
    contract so that failure mode cannot pass on the status code alone.
    """

    reasons: list[str] = [] if status_ok else [f"status {status} not in {sorted(expected)}"]
    observed: dict[str, Any] | None = None
    if required_fields:
        try:
            decoded = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            reasons.append("body is not JSON")
        else:
            if isinstance(decoded, dict):
                observed = decoded
                for field, want in required_fields.items():
                    value: Any = decoded.get(field)
                    if isinstance(want, list):
                        if value not in want:
                            reasons.append(f"{field}={value!r} not in {want!r}")
                    elif value != want:
                        reasons.append(f"{field}={value!r} != {want!r}")
            else:
                reasons.append("body is not a JSON object")
    return ("PASS" if not reasons else "FAIL"), reasons


def _http_probe(
    name: str,
    url: str,
    expected: set[int],
    timeout: int = 20,
    *,
    required_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read(4096).decode("utf-8", errors="replace")
        verdict, reasons = _probe_status(
            status in expected,
            name=name,
            url=url,
            status=status,
            expected=expected,
            started=started,
            body=body,
            required_fields=required_fields,
        )
        return {
            "name": name,
            "status": verdict,
            "reasons": reasons,
            "url": url,
            "http_status": status,
            "expected_status": sorted(expected),
            "duration_seconds": round(time.perf_counter() - started, 6),
            "body_summary": body[:500],
        }
    except HTTPError as exc:
        return {
            "name": name,
            "status": "PASS" if exc.code in expected else "FAIL",
            "url": url,
            "http_status": exc.code,
            "expected_status": sorted(expected),
            "duration_seconds": round(time.perf_counter() - started, 6),
            "body_summary": exc.read(4096).decode("utf-8", errors="replace")[:500],
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "name": name,
            "status": "FAIL",
            "url": url,
            "http_status": None,
            "expected_status": sorted(expected),
            "duration_seconds": round(time.perf_counter() - started, 6),
            "error": str(exc),
        }


def _compose_static_and_fixture_steps(
    root: Path, evidence_dir: Path, profile: str, gate: str
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Render the Compose config, apply the static contract checks and the frozen
    G6 fixture check.  Shared by `g6` and the live `smoke` gate so both verify the
    identical contracts instead of drifting apart."""
    environment = _compose_env(profile)
    steps: list[dict[str, Any]] = []
    static_failures: list[str] = []
    try:
        config_quiet = run_command(
            root=root,
            evidence_dir=evidence_dir,
            name=f"{gate}-{profile}-compose-config-quiet",
            argv=_compose_command(root, profile, "config", "--quiet"),
            env=environment,
            timeout_seconds=120,
        )
        steps.append(config_quiet)
        config_json = run_command(
            root=root,
            evidence_dir=evidence_dir,
            name=f"{gate}-{profile}-compose-config-json",
            argv=_compose_command(root, profile, "config", "--format", "json"),
            env=environment,
            timeout_seconds=120,
            sensitive_stdout=True,
        )
        steps.append(config_json)
        if config_json["exit_code"] == 0:
            try:
                rendered = json.loads(config_json["_stdout"])
            except json.JSONDecodeError as exc:
                static_failures.append(
                    f"docker compose config emitted invalid JSON: {exc}"
                )
            else:
                static_failures.extend(
                    _compose_static_failures(root, rendered, profile)
                )
                static_failures.extend(
                    g6_fixture_contract_failures(root, rendered, profile)
                )
        else:
            static_failures.append("docker compose config did not emit renderable JSON")
    except GateError as exc:
        static_failures.append(str(exc))
    static_step = {
        "name": f"{gate}-{profile}-static-contract",
        "status": "FAIL" if static_failures else "PASS",
        "command": [],
        "cwd": str(root),
        "exit_code": 1 if static_failures else 0,
        "duration_seconds": 0.0,
        "started_at": utc_now(),
        "stdout_log": None,
        "stderr_log": None,
        "stdout_sha256": None,
        "stderr_sha256": None,
        "stdout_summary": [],
        "stderr_summary": static_failures,
    }
    steps.append(static_step)
    steps.append(
        fixture_step(
            root=root, fixture_paths=GATE_FIXTURES["g6"], gate=f"{gate}-{profile}"
        )
    )
    return steps, static_failures, static_step


def _http_probe_step(
    name: str, root: Path, probes: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    passed = bool(probes) and all(item["status"] == "PASS" for item in probes)
    return {
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "command": [],
        "cwd": str(root),
        "exit_code": 0 if passed else 1,
        "duration_seconds": round(
            sum(item["duration_seconds"] for item in probes), 6
        ),
        "started_at": utc_now(),
        "stdout_log": None,
        "stderr_log": None,
        "stdout_sha256": None,
        "stderr_sha256": None,
        "stdout_summary": [],
        "stderr_summary": [item for item in probes if item["status"] != "PASS"],
        "probes": list(probes),
    }


def gate_g6(
    root: Path,
    evidence_dir: Path,
    profile: str,
    mode: str,
    live_timeout_seconds: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    require(
        profile in {"postgis", "portable"}, "G6 requires --profile postgis|portable"
    )
    require(mode in {"config", "live"}, "G6 --mode must be config or live")
    environment = _compose_env(profile)
    probes: list[dict[str, Any]] = []
    steps, static_failures, static_step = _compose_static_and_fixture_steps(
        root, evidence_dir, profile, "g6"
    )
    fixture_result = steps[-1]
    if mode == "live" and not static_failures and fixture_result["status"] == "PASS":
        steps.append(
            run_command(
                root=root,
                evidence_dir=evidence_dir,
                name=f"g6-{profile}-down-fresh",
                argv=_compose_command(root, profile, "down", "-v", "--remove-orphans"),
                env=environment,
                timeout_seconds=300,
            )
        )
        steps.append(
            run_command(
                root=root,
                evidence_dir=evidence_dir,
                name=f"g6-{profile}-up-build-wait",
                argv=_compose_command(root, profile, "up", "--build", "-d", "--wait"),
                env=environment,
                timeout_seconds=live_timeout_seconds,
            )
        )
        steps.append(
            run_command(
                root=root,
                evidence_dir=evidence_dir,
                name=f"g6-{profile}-compose-ps",
                argv=_compose_command(root, profile, "ps", "--all"),
                env=environment,
                timeout_seconds=120,
            )
        )
        probes.extend(
            [
                _http_probe(
                    "backend-health",
                    "http://127.0.0.1:8000/health",
                    {200},
                    required_fields={"status": "healthy"},
                ),
                # /readyz answering 200 is not enough: a payload that reports
                # RUNTIME_NOT_READY or an unavailable spatial backend means the
                # stack is not actually serving, and the gate must say so.
                _http_probe(
                    "backend-readyz",
                    "http://127.0.0.1:8000/readyz",
                    {200},
                    required_fields={"ready": True, "code": ["OK", None]},
                ),
                _http_probe(
                    "nginx-health",
                    "http://127.0.0.1:8080/health",
                    {200},
                ),
                # AC-8's evidence list names API *and* SSE; the previous probe
                # set never touched the event stream at all.
                _http_probe(
                    "backend-sse-events",
                    "http://127.0.0.1:8000/api/sse/events",
                    {200, 307, 401, 403},
                ),
                _http_probe(
                    "backend-forecast-api",
                    "http://127.0.0.1:8000/api/forecast/latest",
                    {200, 401, 403},
                ),
            ]
        )
        steps.append(
            _http_probe_step(f"g6-{profile}-http-probes", root, probes)
        )
    elif mode == "live":
        static_failures.append(
            "live G6 was not started because config/static or frozen fixture prerequisites failed"
        )
        static_step["status"] = "FAIL"
        static_step["exit_code"] = 1
        static_step["stderr_summary"] = static_failures
    return steps, probes, static_failures


def gate_smoke(
    root: Path, evidence_dir: Path, profile: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Verify a Compose stack that is ALREADY running.

    This is the live business smoke for a caller that has just executed its own
    `docker compose up -d --build` (the acceptance gate does exactly that between
    bringing the stack up and tearing it down).  It therefore never runs `down`
    or `up` itself: doing so under a different Compose project name is what
    collides with the caller's stack on host port 8000 and leaves a second,
    default-named project holding that port for the next run.

    The verification itself is not reduced: the Compose static contract, the
    frozen G6 fixture contract and the same G6_PROBES live endpoints that the
    `g6` live gate uses all still run, and any failure fails the gate.
    """
    require(
        profile in {"postgis", "portable"},
        "smoke requires --profile postgis|portable",
    )
    steps, static_failures, static_step = _compose_static_and_fixture_steps(
        root, evidence_dir, profile, "smoke"
    )
    fixture_result = steps[-1]
    if static_failures or fixture_result["status"] != "PASS":
        static_failures.append(
            "live smoke was not started because config/static or frozen fixture "
            "prerequisites failed"
        )
        static_step["status"] = "FAIL"
        static_step["exit_code"] = 1
        static_step["stderr_summary"] = static_failures
        return steps, [], static_failures
    probes = [
        _http_probe(item["name"], item["url"], set(item["expected_status"]))
        for item in G6_PROBES
    ]
    steps.append(_http_probe_step("smoke-http-probes", root, probes))
    return steps, probes, static_failures


def build_report(
    root: Path,
    args: argparse.Namespace,
    command: Sequence[str],
) -> dict[str, Any]:
    evidence_dir = evidence_path(root, args.evidence_dir)
    run_id = (
        args.run_id
        or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{git(root, 'rev-parse', '--short=8', 'HEAD', check=False).stdout.strip() or 'unknown'}"
    )
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "run_id": run_id,
        "utc_timestamp": utc_now(),
        "command": list(command),
        "gate": args.gate,
        "profile": args.profile,
        "mode": (
            args.mode
            if args.gate == "g6"
            else ("running" if args.gate == "smoke" else "full")
        ),
        "commit": git(root, "rev-parse", "HEAD", check=False).stdout.strip() or None,
        "dirty_state": bool(
            git(root, "status", "--porcelain", check=False).stdout.strip()
        ),
        "environment": metadata(root),
        "evidence_dir": str(evidence_dir),
        "status": "RUNNING",
        "exit_code": EXIT_INTERNAL,
        "steps": [],
        "probes": [],
        "static_failures": [],
        "scope_note": (
            "Live business smoke of an already running Compose stack: Compose "
            "static contract, frozen G6 fixtures and live G6_PROBES endpoints."
            if args.gate == "smoke"
            else "Compose config is a configuration check only; it is not live G6 or AC-8 evidence."
        ),
    }
    if args.gate == "g0":
        report["steps"] = gate_g0(root, evidence_dir)
    elif args.gate in {"g1", "g2", "g3", "g4"}:
        report["steps"] = gate_domain(root, evidence_dir, args.gate)
    elif args.gate == "g5":
        report["steps"] = gate_g5(root, evidence_dir)
    elif args.gate == "g6":
        require(args.profile is not None, "G6 requires --profile")
        report["steps"], report["probes"], report["static_failures"] = gate_g6(
            root,
            evidence_dir,
            args.profile,
            args.mode,
            args.live_timeout_seconds,
        )
    elif args.gate == "smoke":
        require(args.profile is not None, "smoke requires --profile")
        report["steps"], report["probes"], report["static_failures"] = gate_smoke(
            root, evidence_dir, args.profile
        )
    else:
        raise GateError(f"unsupported gate: {args.gate}")

    statuses = {step["status"] for step in report["steps"]}
    if "FAIL" in statuses or report["static_failures"]:
        report["status"] = "FAIL"
        report["exit_code"] = EXIT_FAIL
    elif "BLOCKED" in statuses:
        report["status"] = "BLOCKED"
        report["exit_code"] = EXIT_BLOCKED
    else:
        report["status"] = "PASS"
        report["exit_code"] = EXIT_PASS
    return report


def acceptance_requirements(root: Path) -> dict[str, tuple[str, ...]]:
    """Read each AC's required gates from the frozen requirements fixture.

    The AC->gate mapping has exactly one source of truth: the frozen
    ``acceptance_requirements.json``.  A second hardcoded copy in this script
    silently weakened every gate it omitted, which let an AC be judged PASS
    while a required artifact (most often G6's two profile runs) was absent.
    A missing or malformed fixture blocks the gate rather than falling back to
    a weaker built-in list.
    """

    path = root / PurePosixPath(ACCEPTANCE_REQUIREMENTS_FIXTURE)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(
            f"acceptance requirements fixture is unreadable: {path} ({exc})"
        ) from exc
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, dict) or not cases:
        raise GateError(f"acceptance requirements fixture has no cases: {path}")
    requirements: dict[str, tuple[str, ...]] = {}
    for case_name, case in cases.items():
        if not isinstance(case, dict):
            raise GateError(f"acceptance case {case_name} is not an object")
        gates = case.get("required_gates")
        if not isinstance(gates, list) or not gates or not all(
            isinstance(gate, str) and gate in _ACCEPTANCE_GATES for gate in gates
        ):
            raise GateError(
                f"acceptance case {case_name} has invalid required_gates: {gates!r}"
            )
        requirements[_ac_name(case_name)] = tuple(gates)
    valid = {_ac_name(case) for case in ACCEPTANCE_CASES}
    unknown = sorted(set(requirements) - valid)
    if unknown:
        raise GateError(f"acceptance fixture declares unknown cases: {unknown}")
    missing = sorted(valid - set(requirements))
    if missing:
        raise GateError(f"acceptance fixture is missing cases: {missing}")
    return requirements


def _ac_name(case_name: str) -> str:
    """``AC-3`` -> ``ac3``; the CLI's --ac spelling."""

    return case_name.strip().lower().replace("-", "")


def _acceptance_artifact(
    root: Path,
    path: Path,
    expected_run_id: str,
    expected_commit: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, f"missing artifact: {path}"
    try:
        value = load_json(path, "acceptance artifact")
    except GateError as exc:
        return None, str(exc)
    if value.get("run_id") != expected_run_id:
        return None, f"cross-run artifact: {path}"
    if expected_commit and value.get("commit") != expected_commit:
        return None, f"cross-commit artifact: {path}"
    if value.get("status") != "PASS" or value.get("exit_code") != 0:
        return None, f"non-PASS artifact: {path}"
    return value, None


def gate_acceptance(
    root: Path,
    evidence_dir: Path,
    ac_names: Sequence[str],
    run_id: str,
    command: Sequence[str],
) -> dict[str, Any]:
    commit = git(root, "rev-parse", "HEAD", check=False).stdout.strip() or None
    base = evidence_dir.parent
    required_paths = {
        "g0": (base / "g0" / "result.json",),
        "g1": (
            base / "g1" / "postgis" / "result.json",
            base / "g1" / "portable" / "result.json",
        ),
        "g2": (base / "g2" / "result.json",),
        "g3": (base / "g3" / "result.json",),
        "g4": (base / "g4" / "result.json",),
        "g5": (base / "g5" / "result.json",),
        "g6": (
            base / "g6" / "postgis" / "result.json",
            base / "g6" / "portable" / "result.json",
        ),
        "e2e": (base / "e2e" / "ac-smoke.json",),
    }
    artifacts: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, list[str]] = {}
    for name, paths in required_paths.items():
        artifacts[name] = []
        errors[name] = []
        for path in paths:
            artifact, error = _acceptance_artifact(root, path, run_id, commit)
            if error:
                errors[name].append(error)
            elif artifact:
                artifacts[name].append(artifact)

    ac_requirements = acceptance_requirements(root)
    results: list[dict[str, Any]] = []
    for ac_name in ac_names:
        required = ac_requirements[ac_name]
        missing = [gate for gate in required if not artifacts[gate]]
        reasons = [reason for gate in required for reason in errors[gate]]
        status = "PASS" if not missing and not reasons else "BLOCKED"
        results.append(
            {
                "ac": ac_name,
                "status": status,
                "required_gates": list(required),
                "missing_or_invalid_artifacts": missing + reasons,
                "artifact_paths": [
                    str(path)
                    for gate in required
                    for path in required_paths[gate]
                    if path.is_file()
                ],
            }
        )
    if any(item["status"] == "BLOCKED" for item in results):
        overall = "BLOCKED"
        exit_code = EXIT_BLOCKED
    else:
        overall = "PASS"
        exit_code = EXIT_PASS
    return {
        "schema": ACCEPTANCE_SCHEMA,
        "run_id": run_id,
        "utc_timestamp": utc_now(),
        "command": list(command),
        "commit": commit,
        "dirty_state": bool(
            git(root, "status", "--porcelain", check=False).stdout.strip()
        ),
        "environment": metadata(root),
        "evidence_dir": str(evidence_dir),
        "status": overall,
        "exit_code": exit_code,
        "acceptance": results,
        "artifacts": {
            name: [str(path) for path in paths if path.is_file()]
            for name, paths in required_paths.items()
        },
        "artifact_errors": errors,
    }


def finalise_report(report: Mapping[str, Any], evidence_dir: Path) -> tuple[Path, str]:
    result_path = evidence_dir / "result.json"
    file_hash = atomic_write_json(result_path, report)
    sidecar = evidence_dir / "result.json.sha256"
    sidecar.write_text(f"{file_hash}  result.json\n", encoding="ascii")
    return result_path, file_hash


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gate",
        default="smoke",
        choices=("g0", "g1", "g2", "g3", "g4", "g5", "g6", "smoke", "acceptance"),
        help=(
            "gate to run.  The default is 'smoke': a live business smoke of an "
            "ALREADY running Compose stack, which is what a caller that just ran "
            "`docker compose up -d --build` needs.  It verifies the same Compose "
            "static contract, frozen G6 fixtures and G6_PROBES live endpoints as "
            "the g6 live gate, and never creates or tears down a stack of its own."
        ),
    )
    parser.add_argument(
        "--profile",
        default="portable",
        choices=("postgis", "portable"),
        help="spatial backend profile (default: portable, the Compose default)",
    )
    parser.add_argument(
        "--evidence-dir",
        default=DEFAULT_EVIDENCE_DIR,
        help=f"evidence directory under the repository root (default: {DEFAULT_EVIDENCE_DIR})",
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--ac",
        default="ac1,ac2,ac3,ac4,ac5,ac6,ac7,ac8",
        help="comma-separated acceptance IDs (default: all)",
    )
    parser.add_argument(
        "--mode",
        choices=("config", "live"),
        default="live",
        help="G6 mode; config validates rendering/static topology only",
    )
    parser.add_argument("--live-timeout-seconds", type=int, default=1800)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    command = [sys.executable, str(Path(__file__).resolve()), *raw_argv]
    root = repo_root()
    try:
        if args.gate == "acceptance":
            ac_names = tuple(
                part.strip().lower() for part in args.ac.split(",") if part.strip()
            )
            valid = tuple(_ac_name(case) for case in ACCEPTANCE_CASES)
            invalid = sorted(set(ac_names) - set(valid))
            require(not invalid, f"invalid AC ids: {invalid}")
            require(bool(ac_names), "acceptance requires at least one AC id")
            run_id = (
                args.run_id
                or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{git(root, 'rev-parse', '--short=8', 'HEAD', check=False).stdout.strip() or 'unknown'}"
            )
            evidence_dir = evidence_path(root, args.evidence_dir)
            report = gate_acceptance(root, evidence_dir, ac_names, run_id, command)
        else:
            report = build_report(root, args, command)
        result_path, artifact_hash = finalise_report(
            report, evidence_path(root, args.evidence_dir)
        )
    except (GateError, OSError, ValueError, TypeError) as exc:
        evidence_dir = evidence_path(root, args.evidence_dir)
        report = {
            "schema": ACCEPTANCE_SCHEMA if args.gate == "acceptance" else REPORT_SCHEMA,
            "run_id": args.run_id or "unknown",
            "utc_timestamp": utc_now(),
            "command": command,
            "gate": args.gate,
            "profile": args.profile,
            "status": "FAIL",
            "exit_code": EXIT_FAIL,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(limit=20),
            },
        }
        result_path, artifact_hash = finalise_report(report, evidence_dir)
    public_report = dict(report)
    if "steps" in public_report:
        public_report["steps"] = [public_step(step) for step in public_report["steps"]]
    print(
        json.dumps(public_report, ensure_ascii=False, sort_keys=True, allow_nan=False)
    )
    print(f"HYDRAULIC_STACK_STATUS={report['status']}")
    print(f"HYDRAULIC_STACK_EXIT={report['exit_code']}")
    print(f"HYDRAULIC_STACK_RESULT={result_path}")
    print(f"HYDRAULIC_STACK_ARTIFACT_SHA256={artifact_hash}")
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
