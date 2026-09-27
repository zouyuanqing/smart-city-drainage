#!/usr/bin/env python3
"""Fail-closed 5k-node/10k-pipe hydraulic solver benchmark harness.

The harness owns timing, resource limits, streamed-frame accounting, threshold
evaluation, and machine-readable evidence.  It deliberately does not contain a
surrogate hydraulic solver.  A repository-owned production executor must stream
frames through the sink supplied by this process and return durable-publication
and quality evidence.  Missing fixture/executor/dependency is BLOCKED, never
PASS, and never replaced by a synthetic timing loop.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import inspect
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # pragma: no cover - platform import
    import resource
except ImportError:  # Windows has no resource module.
    resource = None  # type: ignore[assignment]

SCHEMA = "hydraulic-solver-benchmark-v1"
REPORT_SCHEMA = "hydraulic-solver-benchmark-report-v1"
SOLVER_ID = "py_mw"
EXPECTED_COUNTS = {
    "nodes": 5000,
    "pipes": 10000,
    "catchments": 1000,
}
EXPECTED_FRAMES = 289
EXPECTED_INTERVAL_MINUTES = 5
EXPECTED_DURATION_MINUTES = 1440
WALL_TIME_LIMIT_SECONDS = 180.0
MASS_RESIDUAL_LIMIT = 1e-4
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_BLOCKED = 2
EXIT_INTERNAL = 3
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_FIXTURE = (
    _REPOSITORY_ROOT
    / "backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json"
)
_DEFAULT_OUTPUT = _REPOSITORY_ROOT / "artifacts/hydraulic/solver.json"


class BenchmarkError(RuntimeError):
    """A benchmark validation, execution, or threshold failure."""

    def __init__(self, message: str, *, exit_code: int = EXIT_FAIL) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def cli_path(value: str | Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    root = Path(__file__).resolve().parents[1]
    if candidate.parts and candidate.parts[0] == root.name:
        return (root.parent / candidate).resolve()
    return (root / candidate).resolve()


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


def require(condition: bool, message: str, *, exit_code: int = EXIT_FAIL) -> None:
    if not condition:
        raise BenchmarkError(message, exit_code=exit_code)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BenchmarkError(
            f"{label} is not finite, readable JSON: {path}: {exc}",
            exit_code=EXIT_BLOCKED,
        ) from exc
    require(
        isinstance(value, dict),
        f"{label} must be a JSON object",
        exit_code=EXIT_BLOCKED,
    )
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _require_file_reference(
    fixture_root: Path,
    reference: Any,
    label: str,
) -> tuple[Path, str]:
    require(
        isinstance(reference, dict),
        f"{label} must be an object",
        exit_code=EXIT_BLOCKED,
    )
    raw_path = reference.get("path")
    expected_hash = reference.get("sha256")
    require(
        isinstance(raw_path, str) and raw_path.strip(),
        f"{label}.path must be a non-empty relative path",
        exit_code=EXIT_BLOCKED,
    )
    require(
        _is_sha256(expected_hash),
        f"{label}.sha256 must be lowercase SHA-256",
        exit_code=EXIT_BLOCKED,
    )
    relative = Path(raw_path)
    require(
        not relative.is_absolute(),
        f"{label}.path must be relative",
        exit_code=EXIT_BLOCKED,
    )
    require(
        ".." not in relative.parts,
        f"{label}.path must not contain '..'",
        exit_code=EXIT_BLOCKED,
    )
    resolved = (fixture_root / relative).resolve()
    try:
        resolved.relative_to(fixture_root)
    except ValueError as exc:
        raise BenchmarkError(
            f"{label}.path escapes the fixture root",
            exit_code=EXIT_BLOCKED,
        ) from exc
    require(
        resolved.is_file(),
        f"{label} file is missing: {raw_path}",
        exit_code=EXIT_BLOCKED,
    )
    actual_hash = sha256_file(resolved)
    require(
        actual_hash == expected_hash,
        f"{label} SHA-256 mismatch",
        exit_code=EXIT_BLOCKED,
    )
    return resolved, actual_hash


def validate_manifest(
    manifest_path: Path,
    *,
    check_runtime: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = manifest_path.resolve()
    try:
        manifest_path.relative_to(Path(__file__).resolve().parents[1])
    except ValueError as exc:
        raise BenchmarkError(
            "fixture manifest is outside repository root",
            exit_code=EXIT_BLOCKED,
        ) from exc
    require(
        manifest_path.is_file(),
        f"fixture manifest is missing: {manifest_path}",
        exit_code=EXIT_BLOCKED,
    )
    manifest = load_json_object(manifest_path, "fixture manifest")
    require(
        manifest.get("schema") == SCHEMA,
        f"fixture schema must be {SCHEMA}",
        exit_code=EXIT_BLOCKED,
    )
    require(
        isinstance(manifest.get("fixture_id"), str) and manifest["fixture_id"].strip(),
        "fixture_id is required",
        exit_code=EXIT_BLOCKED,
    )

    fixture_root = manifest_path.parent.resolve()
    inputs: dict[str, Any] = {}
    for name in (
        "network",
        "rainfall",
        "target_manifest",
        "initial_state",
        "solver_config",
    ):
        path, digest = _require_file_reference(fixture_root, manifest.get(name), name)
        inputs[name] = {"path": path, "sha256": digest}

    counts = manifest.get("input_counts")
    require(
        isinstance(counts, dict),
        "input_counts must be an object",
        exit_code=EXIT_BLOCKED,
    )
    for name, expected in EXPECTED_COUNTS.items():
        actual = counts.get(name)
        require(
            type(actual) is int and actual == expected,
            f"input_counts.{name} must equal {expected}, got {actual!r}",
            exit_code=EXIT_BLOCKED,
        )

    frames = manifest.get("frames")
    require(
        isinstance(frames, dict), "frames must be an object", exit_code=EXIT_BLOCKED
    )
    require(
        type(frames.get("count")) is int and frames["count"] == EXPECTED_FRAMES,
        f"frames.count must equal {EXPECTED_FRAMES}",
        exit_code=EXIT_BLOCKED,
    )
    require(
        type(frames.get("interval_minutes")) is int
        and frames["interval_minutes"] == EXPECTED_INTERVAL_MINUTES,
        f"frames.interval_minutes must equal {EXPECTED_INTERVAL_MINUTES}",
        exit_code=EXIT_BLOCKED,
    )
    require(
        type(frames.get("duration_minutes")) is int
        and frames["duration_minutes"] == EXPECTED_DURATION_MINUTES,
        f"frames.duration_minutes must equal {EXPECTED_DURATION_MINUTES}",
        exit_code=EXIT_BLOCKED,
    )
    try:
        frame_start = datetime.fromisoformat(
            str(frames["start"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchmarkError(
            "frames.start must be ISO-8601", exit_code=EXIT_BLOCKED
        ) from exc
    if frame_start.tzinfo is None:
        frame_start = frame_start.replace(tzinfo=timezone.utc)

    solver = manifest.get("solver")
    require(
        isinstance(solver, dict), "solver must be an object", exit_code=EXIT_BLOCKED
    )
    require(
        solver.get("id") == SOLVER_ID,
        f"solver.id must be {SOLVER_ID}",
        exit_code=EXIT_BLOCKED,
    )
    require(
        isinstance(solver.get("version"), str) and solver["version"].strip(),
        "solver.version is required",
        exit_code=EXIT_BLOCKED,
    )
    require(
        _is_sha256(solver.get("engine_sha256")),
        "solver.engine_sha256 must be lowercase SHA-256",
        exit_code=EXIT_BLOCKED,
    )

    executor = manifest.get("executor")
    require(
        isinstance(executor, dict), "executor must be an object", exit_code=EXIT_BLOCKED
    )
    module_name = executor.get("module")
    qualname = executor.get("qualname")
    require(
        isinstance(module_name, str) and module_name.startswith("app."),
        "executor.module must be a production app.* module",
        exit_code=EXIT_BLOCKED,
    )
    require(
        isinstance(qualname, str) and qualname.strip(),
        "executor.qualname is required",
        exit_code=EXIT_BLOCKED,
    )
    require(
        type(executor.get("async")) is bool,
        "executor.async must be a boolean",
        exit_code=EXIT_BLOCKED,
    )

    if check_runtime:
        version = platform.python_version()
        require(
            version == "3.11.0" or version.startswith("3.11."),
            f"benchmark requires Python 3.11.x, found {version}",
            exit_code=EXIT_BLOCKED,
        )
        available = (
            len(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else os.cpu_count()
        )
        require(
            isinstance(available, int) and available >= 4,
            f"benchmark requires at least four available CPUs, found {available}",
            exit_code=EXIT_BLOCKED,
        )
        for variable in THREAD_ENVIRONMENT_VARIABLES:
            require(
                os.environ.get(variable) == "4",
                f"{variable} must be exactly 4 before benchmark start",
                exit_code=EXIT_BLOCKED,
            )
        require(
            os.environ.get("CUDA_VISIBLE_DEVICES") == "",
            "CUDA_VISIBLE_DEVICES must be empty for the no-GPU benchmark",
            exit_code=EXIT_BLOCKED,
        )

    normalized = {
        "fixture_id": manifest["fixture_id"],
        "frame_start": frame_start,
        "inputs": inputs,
        "executor": executor,
        "solver": solver,
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_path": manifest_path,
    }
    return manifest, normalized


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        require(math.isfinite(value), "frame/result contains a non-finite float")
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump(mode="python"))
    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        return _jsonable(dict_method())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    raise BenchmarkError(
        f"unsupported value in benchmark evidence: {type(value).__name__}"
    )


def _frame_value(frame: Any, name: str) -> Any:
    if isinstance(frame, Mapping):
        return frame.get(name)
    return getattr(frame, name, None)


class BenchmarkFrameSink:
    """Streaming frame sink used by the production executor callback."""

    def __init__(
        self,
        path: Path,
        frame_start: datetime,
        expected_count: int = EXPECTED_FRAMES,
    ) -> None:
        self.path = path
        self.frame_start = frame_start
        self.expected_count = expected_count
        self.attempt_id: str | None = None
        self.frame_count = 0
        self.bytes_written = 0
        self.finalized = False
        self._digest = hashlib.sha256()
        self._handle = path.open("wb")

    async def write(self, frame: Any) -> None:
        require(not self.finalized, "frame received after sink finalization")
        index = _frame_value(frame, "frame_index")
        frame_time_value = _frame_value(frame, "frame_time")
        attempt_id = _frame_value(frame, "attempt_id")
        require(type(index) is int, "frame.frame_index must be an integer")
        require(
            index == self.frame_count,
            f"non-contiguous frame index: expected {self.frame_count}, got {index}",
        )
        require(
            isinstance(attempt_id, str) and attempt_id.strip(),
            "frame.attempt_id is required",
        )
        if self.attempt_id is None:
            self.attempt_id = attempt_id
        require(
            attempt_id == self.attempt_id,
            "all frames in one attempt must have the same attempt_id",
        )
        try:
            frame_time = datetime.fromisoformat(
                str(frame_time_value).replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise BenchmarkError("frame.frame_time must be ISO-8601") from exc
        if frame_time.tzinfo is None:
            frame_time = frame_time.replace(tzinfo=timezone.utc)
        expected_time = self.frame_start + timedelta(
            minutes=EXPECTED_INTERVAL_MINUTES * index
        )
        require(
            frame_time == expected_time,
            f"frame {index} time mismatch: expected {expected_time.isoformat()}, got {frame_time.isoformat()}",
        )
        normalized = _jsonable(frame)
        payload = canonical_json_bytes(normalized) + b"\n"
        self._handle.write(payload)
        self._digest.update(payload)
        self.bytes_written += len(payload)
        self.frame_count += 1
        require(
            self.frame_count <= self.expected_count,
            f"executor emitted more than {self.expected_count} frames",
        )

    async def finalize(self, summary: Any) -> None:
        require(not self.finalized, "sink finalized more than once")
        require(
            self.frame_count == self.expected_count,
            f"executor finalized after {self.frame_count}/{self.expected_count} frames",
        )
        normalized_summary = _jsonable(summary)
        payload = canonical_json_bytes({"summary": normalized_summary}) + b"\n"
        self._handle.write(payload)
        self._digest.update(payload)
        self.bytes_written += len(payload)
        self._handle.flush()
        self.finalized = True

    def persistence_evidence(self) -> dict[str, Any]:
        """Report what this sink actually persisted.

        The benchmark sink writes a local file and fsyncs it; it never writes
        TimescaleDB.  Saying so keeps the G5 gate honest instead of having the
        executor assert ``database_committed`` unconditionally.
        """

        return {
            "database_committed": False,
            "all_frames_committed": self.finalized
            and self.frame_count == self.expected_count,
            "quality_report_committed": False,
            "published": False,
            "persistence_target": "file",
            "detail": (
                "frames were streamed to a local file and fsynced; no frame "
                "reached hydraulic.simulation_frames"
            ),
        }

    def close(self) -> dict[str, Any]:
        if not self._handle.closed:
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self._handle.close()
        return {
            "bytes": self.bytes_written,
            "frame_count": self.frame_count,
            "sha256": self._digest.hexdigest(),
            "finalized": self.finalized,
            "attempt_id": self.attempt_id,
        }


def resolve_executor(
    module_name: str,
    qualname: str,
    *,
    repository_root: Path,
) -> Callable[..., Any]:
    backend_root = (repository_root / "backend").resolve()
    # The harness runs from the repository root, so ``app.*`` is not importable
    # unless the backend package root is on sys.path. The harness knows where
    # that is, so it puts it there itself instead of relying on the caller's
    # PYTHONPATH; the module-location check below still confines the executor
    # to backend/.
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    try:
        module = importlib.import_module(module_name)
    except (ImportError, ModuleNotFoundError) as exc:
        raise BenchmarkError(
            f"production benchmark executor is unavailable: {module_name}: {exc}",
            exit_code=EXIT_BLOCKED,
        ) from exc
    module_path = Path(getattr(module, "__file__", "")).resolve()
    try:
        module_path.relative_to(backend_root)
    except ValueError as exc:
        raise BenchmarkError(
            f"executor module is outside backend/: {module_name}",
            exit_code=EXIT_BLOCKED,
        ) from exc
    target: Any = module
    for part in qualname.split("."):
        if not part:
            break
        target = getattr(target, part, None)
        if target is None:
            raise BenchmarkError(
                f"executor attribute is unavailable: {module_name}:{qualname}",
                exit_code=EXIT_BLOCKED,
            )
    if not callable(target):
        raise BenchmarkError(
            f"executor is not callable: {module_name}:{qualname}",
            exit_code=EXIT_BLOCKED,
        )
    return target


async def _call_executor(
    executor: Callable[..., Any],
    *,
    is_async: bool,
    manifest_path: Path,
    sink: BenchmarkFrameSink,
) -> Any:
    if is_async:
        result = executor(manifest_path, sink)
        if not inspect.isawaitable(result):
            raise BenchmarkError(
                "executor.async=true but callable did not return an awaitable"
            )
        return await result
    if inspect.iscoroutinefunction(executor):
        raise BenchmarkError(
            "executor.async=false but callable is a coroutine function"
        )
    result = executor(manifest_path, sink)
    if inspect.isawaitable(result):
        raise BenchmarkError("executor.async=false but callable returned an awaitable")
    return result


def _validate_execution_result(
    result: Any,
    *,
    sink_metrics: Mapping[str, Any],
    elapsed_wall: float,
    expected_solver_version: str,
) -> dict[str, Any]:
    require(isinstance(result, dict), "executor result must be a mapping")
    normalized = _jsonable(result)
    require(
        normalized.get("attempt_id") == sink_metrics["attempt_id"],
        "executor attempt_id differs from streamed frames",
    )
    require(
        normalized.get("published_attempt_id") == sink_metrics["attempt_id"],
        "executor did not publish the streamed attempt",
    )
    queue_wait = normalized.get("queue_wait_seconds")
    require(
        isinstance(queue_wait, (int, float)) and math.isfinite(float(queue_wait)),
        "executor queue_wait_seconds must be finite",
    )
    queue_wait = float(queue_wait)
    require(
        0 <= queue_wait <= elapsed_wall + 0.001,
        f"executor queue_wait_seconds={queue_wait} is outside measured wall time {elapsed_wall}",
    )
    persistence = normalized.get("persistence")
    require(isinstance(persistence, dict), "executor persistence evidence is required")
    for name in (
        "database_committed",
        "all_frames_committed",
        "quality_report_committed",
        "published",
    ):
        require(
            persistence.get(name) is True, f"executor persistence.{name} must be true"
        )
    quality = normalized.get("quality")
    require(isinstance(quality, dict), "executor quality evidence is required")
    residual = quality.get("mass_balance_relative_residual")
    require(
        isinstance(residual, (int, float)) and math.isfinite(float(residual)),
        "quality.mass_balance_relative_residual must be finite",
    )
    require(
        float(residual) <= MASS_RESIDUAL_LIMIT,
        f"mass balance residual {residual} exceeds {MASS_RESIDUAL_LIMIT}",
    )
    summary = normalized.get("summary")
    require(isinstance(summary, dict), "executor summary evidence is required")
    require(
        summary.get("solver_id") == SOLVER_ID, f"summary.solver_id must be {SOLVER_ID}"
    )
    require(
        summary.get("solver_version") == expected_solver_version,
        "summary.solver_version differs from the fixture solver.version",
    )
    return {
        "attempt_id": sink_metrics["attempt_id"],
        "queue_wait_seconds": queue_wait,
        "worker_wall_time_seconds": elapsed_wall - queue_wait,
        "frame_bytes": sink_metrics["bytes"],
        "frame_count": sink_metrics["frame_count"],
        "frame_sha256": sink_metrics["sha256"],
        "sink_finalized": sink_metrics["finalized"],
        "persistence": persistence,
        "quality": quality,
        "summary": summary,
    }


def verify_declared_scale(
    args: argparse.Namespace,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Cross-check the CLI scenario declaration against the frozen contract.

    The acceptance scale is not negotiable: ``EXPECTED_COUNTS`` and
    ``EXPECTED_DURATION_MINUTES`` are frozen, and the fixture must declare the
    same values.  ``--nodes``/``--pipes``/``--hours`` therefore *state* the
    scenario rather than rescale it.  A declaration that disagrees with either
    the contract or the loaded fixture is BLOCKED, so the flags can never be
    used to make a smaller or shorter run satisfy the gate.
    """

    counts = manifest["input_counts"]
    frames = manifest["frames"]
    declared: dict[str, Any] = {
        "nodes": EXPECTED_COUNTS["nodes"],
        "pipes": EXPECTED_COUNTS["pipes"],
        "hours": EXPECTED_DURATION_MINUTES / 60.0,
    }
    for name, flag in (("nodes", "nodes"), ("pipes", "pipes"), ("hours", "hours")):
        value = getattr(args, flag, None)
        if value is not None:
            declared[name] = value

    for name in ("nodes", "pipes"):
        require(
            declared[name] == EXPECTED_COUNTS[name],
            f"--{name}={declared[name]} does not equal the acceptance contract "
            f"({EXPECTED_COUNTS[name]})",
            exit_code=EXIT_BLOCKED,
        )
        require(
            declared[name] == counts[name],
            f"--{name}={declared[name]} does not equal the fixture "
            f"input_counts.{name} ({counts[name]})",
            exit_code=EXIT_BLOCKED,
        )
    require(
        math.isclose(declared["hours"] * 60.0, EXPECTED_DURATION_MINUTES),
        f"--hours={declared['hours']} does not equal the acceptance contract "
        f"({EXPECTED_DURATION_MINUTES / 60:g}h)",
        exit_code=EXIT_BLOCKED,
    )
    require(
        math.isclose(declared["hours"] * 60.0, float(frames["duration_minutes"])),
        f"--hours={declared['hours']} does not equal the fixture "
        f"frames.duration_minutes ({frames['duration_minutes']})",
        exit_code=EXIT_BLOCKED,
    )
    require(
        EXPECTED_FRAMES
        == frames["duration_minutes"] // frames["interval_minutes"] + 1,
        "the fixture frame count does not follow from its own duration and "
        "interval",
        exit_code=EXIT_BLOCKED,
    )
    return {
        "declared_nodes": declared["nodes"],
        "declared_pipes": declared["pipes"],
        "declared_hours": declared["hours"],
        "declared_from_cli": {
            name: getattr(args, name) is not None for name in ("nodes", "pipes", "hours")
        },
    }


def peak_rss_bytes() -> int | None:
    if resource is not None:
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return int(value)
        return int(value * 1024)
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
        success = ctypes.windll.psapi.GetProcessMemoryInfo(  # type: ignore[attr-defined]
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.PeakWorkingSetSize) if success else None
    return None


def percentile(values: Sequence[float], quantile: float) -> float:
    require(bool(values), "cannot calculate a percentile of an empty sample")
    require(0 < quantile <= 1, "quantile must be in (0, 1]")
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return float(ordered[index])


def _parse_memory(value: str) -> int:
    """Parse a memory size such as ``8g``, ``8gb``, ``8GiB`` or ``8589934592``.

    The acceptance default is ``--memory 8g``, so a unit without the trailing
    ``b`` has to parse; the old pattern only accepted ``b``-suffixed units and
    rejected the harness's own default.
    """

    match = re.fullmatch(r"([1-9][0-9]*)\s*([a-z]*)", value.strip().lower())
    require(match is not None, f"invalid memory value: {value}")
    amount = int(match.group(1))
    factors = {
        "": 1,
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "ki": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
        "mi": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "gib": 1024**3,
        "gi": 1024**3,
        "t": 1024**4,
        "tb": 1024**4,
        "tib": 1024**4,
        "ti": 1024**4,
    }
    require(match.group(2) in factors, f"invalid memory unit: {value}")
    return amount * factors[match.group(2)]


THREAD_ENVIRONMENT_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def pin_execution_environment(threads: int) -> dict[str, str]:
    """Pin the frozen no-GPU, ``threads``-way BLAS/OpenMP environment.

    ``validate_manifest`` requires these variables to already hold the frozen
    values, but neither the acceptance gate nor ``verify_hydraulic_stack.py``
    sets them, so demanding them from the caller made the benchmark BLOCKED
    before it ran. The harness owns the environment it then asserts on: it
    forces the values here, and the assertion in ``validate_manifest`` still
    fails closed if the pinning did not take effect.
    """

    for variable in THREAD_ENVIRONMENT_VARIABLES:
        os.environ[variable] = str(threads)
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    return {
        **{variable: os.environ[variable] for variable in THREAD_ENVIRONMENT_VARIABLES},
        "CUDA_VISIBLE_DEVICES": os.environ["CUDA_VISIBLE_DEVICES"],
    }


def configure_resources(cpus: int, memory: str, threads: int) -> dict[str, Any]:
    require(cpus == 4, "acceptance benchmark requires --cpus 4")
    require(memory.lower() == "8g", "acceptance benchmark requires --memory 8g")
    require(threads == 4, "acceptance benchmark requires --threads 4")
    pinned = pin_execution_environment(threads)

    affinity_before = None
    affinity_after = None
    if hasattr(os, "sched_setaffinity") and hasattr(os, "sched_getaffinity"):
        affinity_before = sorted(os.sched_getaffinity(0))
        selected = set(affinity_before[:cpus])
        require(
            len(selected) == cpus,
            "fewer than four CPUs are available in the current affinity mask",
        )
        os.sched_setaffinity(0, selected)
        affinity_after = sorted(os.sched_getaffinity(0))

    memory_bytes = _parse_memory(memory)
    address_limit_applied = False
    if (
        resource is not None
        and hasattr(resource, "setrlimit")
        and hasattr(resource, "RLIMIT_AS")
    ):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            address_limit_applied = True
        except (OSError, ValueError) as exc:
            raise BenchmarkError(
                f"cannot apply the requested 8 GiB address-space limit: {exc}",
                exit_code=EXIT_BLOCKED,
            ) from exc
    return {
        "requested_cpus": cpus,
        "requested_memory_bytes": memory_bytes,
        "requested_threads": threads,
        "pinned_environment": pinned,
        "affinity_before": affinity_before,
        "affinity_after": affinity_after,
        "address_space_limit_applied": address_limit_applied,
    }


def git_output(repository_root: Path, *args: str) -> str | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    completed = subprocess.run(
        [executable, "-C", str(repository_root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def linux_cpu_details() -> tuple[str | None, int | None]:
    cpuinfo = Path("/proc/cpuinfo")
    if not cpuinfo.is_file():
        return None, None
    model: str | None = None
    physical: set[tuple[str, str]] = set()
    current: dict[str, str] = {}
    for raw_line in [
        *cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines(),
        "",
    ]:
        if raw_line:
            if ":" in raw_line:
                key, value = raw_line.split(":", 1)
                current[key.strip()] = value.strip()
            continue
        if current:
            model = model or current.get("model name")
            physical_id = current.get("physical id")
            core_id = current.get("core id")
            if physical_id is not None and core_id is not None:
                physical.add((physical_id, core_id))
        current = {}
    return model, len(physical) or None


def total_ram_bytes() -> int | None:
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, ValueError, OSError):
        return None


def environment_metadata(repository_root: Path) -> dict[str, Any]:
    cpu_model, physical_cores = linux_cpu_details()
    lock_path = repository_root / "backend" / "requirements-py311.lock"
    return {
        "os": platform.platform(),
        "cpu_model": cpu_model,
        "physical_cores": physical_cores,
        "logical_cpus": os.cpu_count(),
        "ram_bytes": total_ram_bytes(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "dependency_lock_sha256": (
            sha256_file(lock_path) if lock_path.is_file() else None
        ),
        "git_commit": git_output(repository_root, "rev-parse", "HEAD"),
        "git_dirty": bool(git_output(repository_root, "status", "--porcelain")),
    }


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


def base_report(
    *,
    run_id: str,
    command: Sequence[str],
    output_path: Path,
    status: str,
    exit_code: int,
) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[1]
    return {
        "schema": REPORT_SCHEMA,
        "run_id": run_id,
        "utc_timestamp": utc_now(),
        "command": list(command),
        "output": str(output_path),
        "status": status,
        "exit_code": exit_code,
        "benchmark_executed": False,
        "acceptance_scope": "py_mw_5k_10k_24h",
        "environment": environment_metadata(repository_root),
    }


def finalise_report(report: dict[str, Any], output_path: Path) -> str:
    evidence_hash = sha256_bytes(canonical_json_bytes(report))
    report["evidence_sha256"] = evidence_hash
    file_hash = atomic_write_json(output_path, report)
    report["artifact_sha256"] = file_hash
    # Rewrite once with the file hash is intentionally omitted: embedding a
    # file's own digest would be self-referential. The sidecar carries it.
    sidecar = output_path.with_suffix(output_path.suffix + ".sha256")
    sidecar.write_text(f"{file_hash}  {output_path.name}\n", encoding="ascii")
    return file_hash


def run_benchmark(args: argparse.Namespace, command: Sequence[str]) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[1]
    output_path = cli_path(args.output)
    run_id = (
        args.run_id
        or f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{git_output(repository_root, 'rev-parse', '--short=8', 'HEAD') or 'unknown'}"
    )
    report = base_report(
        run_id=run_id,
        command=command,
        output_path=output_path,
        status="RUNNING",
        exit_code=EXIT_INTERNAL,
    )
    try:
        pinned_environment = pin_execution_environment(args.threads)
        manifest, normalized = validate_manifest(
            cli_path(args.fixture), check_runtime=True
        )
        declared_scale = verify_declared_scale(args, manifest)
        report["environment"] = {
            **report.get("environment", {}),
            "pinned_environment": pinned_environment,
        }
        report["fixture"] = {
            "fixture_id": normalized["fixture_id"],
            "path": str(cli_path(args.fixture)),
            "sha256": normalized["manifest_sha256"],
            "input_counts": dict(EXPECTED_COUNTS),
            "frame_count": EXPECTED_FRAMES,
            "declared_scale": declared_scale,
            "input_sha256": {
                name: details["sha256"]
                for name, details in normalized["inputs"].items()
            },
        }
        report["solver"] = dict(normalized["solver"])
        report["threshold"] = {
            "max_worker_wall_time_seconds": WALL_TIME_LIMIT_SECONDS,
            "max_mass_balance_relative_residual": MASS_RESIDUAL_LIMIT,
            "warmup_runs": args.warmup,
            "measured_runs": args.repeats,
        }
        if args.validate_only:
            report["status"] = "VALIDATED"
            report["exit_code"] = EXIT_PASS
            report["validation_only"] = True
            return report

        require(args.warmup == 1, "--warmup must be exactly 1")
        require(args.repeats == 3, "--repeats must be exactly 3")
        report["resources"] = configure_resources(args.cpus, args.memory, args.threads)
        executor = resolve_executor(
            normalized["executor"]["module"],
            normalized["executor"]["qualname"],
            repository_root=repository_root,
        )

        async def execute_all() -> list[dict[str, Any]]:
            records: list[dict[str, Any]] = []
            total = args.warmup + args.repeats
            with tempfile.TemporaryDirectory(
                prefix="hydraulic-benchmark-"
            ) as temporary:
                for index in range(total):
                    phase = "warmup" if index < args.warmup else "measured"
                    sink_path = Path(temporary) / f"{phase}-{index}.jsonl"
                    sink = BenchmarkFrameSink(
                        sink_path,
                        normalized["frame_start"],
                    )
                    cpu_start = time.process_time()
                    wall_start = time.perf_counter()
                    try:
                        raw_result = await _call_executor(
                            executor,
                            is_async=normalized["executor"]["async"],
                            manifest_path=normalized["manifest_path"],
                            sink=sink,
                        )
                        wall_elapsed = time.perf_counter() - wall_start
                        cpu_elapsed = time.process_time() - cpu_start
                        sink_metrics = sink.close()
                        require(
                            sink_metrics["finalized"],
                            "executor must call sink.finalize(summary)",
                        )
                        execution = _validate_execution_result(
                            raw_result,
                            sink_metrics=sink_metrics,
                            elapsed_wall=wall_elapsed,
                            expected_solver_version=normalized["solver"]["version"],
                        )
                    finally:
                        sink.close()
                    execution.update(
                        {
                            "phase": phase,
                            "run_index": index,
                            "wall_time_seconds": wall_elapsed,
                            "cpu_time_seconds": cpu_elapsed,
                            "peak_rss_bytes_after_run": peak_rss_bytes(),
                        }
                    )
                    records.append(execution)
            return records

        records = asyncio.run(execute_all())
        report["benchmark_executed"] = True
        report["runs"] = records
        measured = [record for record in records if record["phase"] == "measured"]
        wall_times = [float(record["worker_wall_time_seconds"]) for record in measured]
        residuals = [
            float(record["quality"]["mass_balance_relative_residual"])
            for record in measured
        ]
        report["measurements"] = {
            "worker_wall_time_p95_seconds": percentile(wall_times, 0.95),
            "worker_wall_time_max_seconds": max(wall_times),
            "mass_balance_relative_residual_max": max(residuals),
            "frame_bytes_max": max(int(record["frame_bytes"]) for record in measured),
            "peak_rss_bytes_max": (
                max(
                    int(record["peak_rss_bytes_after_run"])
                    for record in measured
                    if record["peak_rss_bytes_after_run"] is not None
                )
                if any(
                    record["peak_rss_bytes_after_run"] is not None
                    for record in measured
                )
                else None
            ),
        }
        passed = (
            len(measured) == 3
            and report["measurements"]["worker_wall_time_max_seconds"]
            <= WALL_TIME_LIMIT_SECONDS
            and report["measurements"]["mass_balance_relative_residual_max"]
            <= MASS_RESIDUAL_LIMIT
        )
        report["status"] = "PASS" if passed else "FAIL"
        report["exit_code"] = EXIT_PASS if passed else EXIT_FAIL
        return report
    except BenchmarkError as exc:
        report["status"] = "BLOCKED" if exc.exit_code == EXIT_BLOCKED else "FAIL"
        report["exit_code"] = exc.exit_code
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        return report
    except Exception as exc:  # pragma: no cover - final fail-closed boundary
        report["status"] = "FAIL"
        report["exit_code"] = EXIT_INTERNAL
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=20),
        }
        return report


def _self_test() -> int:
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix="benchmark-self-test-",
        dir=Path(__file__).resolve().parents[1],
    ) as temporary:
        root = Path(temporary)
        references: dict[str, Any] = {}
        for name in (
            "network",
            "rainfall",
            "target_manifest",
            "initial_state",
            "solver_config",
        ):
            path = root / f"{name}.json"
            path.write_text(f'{{"name":"{name}"}}\n', encoding="utf-8")
            references[name] = {"path": path.name, "sha256": sha256_file(path)}
        manifest = {
            "schema": SCHEMA,
            "fixture_id": "self-test",
            "input_counts": EXPECTED_COUNTS,
            "frames": {
                "count": EXPECTED_FRAMES,
                "start": "2026-01-01T00:00:00Z",
                "interval_minutes": EXPECTED_INTERVAL_MINUTES,
                "duration_minutes": EXPECTED_DURATION_MINUTES,
            },
            "solver": {
                "id": SOLVER_ID,
                "version": "self-test",
                "engine_sha256": "0" * 64,
            },
            "executor": {
                "module": "app.services.hydraulic.benchmark",
                "qualname": "run_once",
                "async": True,
            },
            **references,
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        _, normalized = validate_manifest(manifest_path, check_runtime=False)
        checks.append({"name": "valid_manifest", "status": "PASS"})
        checks.append(
            {
                "name": "hash_validation",
                "status": (
                    "PASS" if normalized["inputs"]["network"]["sha256"] else "FAIL"
                ),
            }
        )
        manifest["network"]["sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        try:
            validate_manifest(manifest_path, check_runtime=False)
        except BenchmarkError:
            checks.append({"name": "hash_drift_rejected", "status": "PASS"})
        else:
            checks.append({"name": "hash_drift_rejected", "status": "FAIL"})
        checks.append(
            {
                "name": "p95_nearest_rank",
                "status": (
                    "PASS" if percentile([1.0, 2.0, 3.0], 0.95) == 3.0 else "FAIL"
                ),
            }
        )
    status = "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL"
    print(
        json.dumps(
            {
                "schema": "hydraulic-solver-benchmark-self-test-v1",
                "status": status,
                "checks": checks,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return EXIT_PASS if status == "PASS" else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        default=str(_DEFAULT_FIXTURE),
        help="frozen solver manifest (defaults to the 5k/10k fixture)",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        help=(
            "declare the scenario node count; it must equal both the frozen "
            f"fixture and the acceptance contract ({EXPECTED_COUNTS['nodes']})"
        ),
    )
    parser.add_argument(
        "--pipes",
        type=int,
        help=(
            "declare the scenario pipe-segment count; it must equal both the "
            "frozen fixture and the acceptance contract "
            f"({EXPECTED_COUNTS['pipes']})"
        ),
    )
    parser.add_argument(
        "--hours",
        type=float,
        help=(
            "declare the simulated horizon in hours; it must equal both the "
            "frozen fixture and the acceptance contract "
            f"({EXPECTED_DURATION_MINUTES / 60:g}h)"
        ),
    )
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--cpus", type=int, default=4)
    parser.add_argument("--memory", default="8g")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        help="benchmark report path (defaults to artifacts/hydraulic/solver.json)",
    )
    parser.add_argument("--run-id")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the complete fixture/runtime contract without executing the solver",
    )
    parser.add_argument("--self-test", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        *(list(argv) if argv is not None else sys.argv[1:]),
    ]
    if args.self_test:
        return _self_test()
    report = run_benchmark(args, command)
    output_path = cli_path(args.output)
    artifact_hash = finalise_report(report, output_path)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
    print(f"HYDRAULIC_BENCHMARK_STATUS={report['status']}")
    print(f"HYDRAULIC_BENCHMARK_EXIT={report['exit_code']}")
    print(f"HYDRAULIC_BENCHMARK_ARTIFACT_SHA256={artifact_hash}")
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
