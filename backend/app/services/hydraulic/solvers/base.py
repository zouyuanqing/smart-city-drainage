"""Common, dependency-free contracts for hydraulic solvers.

This module deliberately contains no numerical algorithm, database access, or
process launching.  It is the small boundary shared by the worker, the EPA
SWMM adapter, and the pure-Python fallback implementations.  The frozen
hydraulic contract requires a solver to be probed *before* a simulation is
created and then run with the probed identity frozen; a registry can therefore
select a fallback, but a solver itself must never switch identities during a
run.

The types in this file are intentionally permissive at their outer edge.  A
network snapshot and a rainfall request are produced by other modules and are
validated there.  Keeping those values as ``Any`` here prevents the solver
contract from importing ORM or HTTP code, while the adapter still records the
canonical hashes and metadata needed for reproducibility.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
import math
import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, TypeAlias, runtime_checkable

UTC = timezone.utc

# The five identities are stable across API, persistence, logs, and exports.
SOLVER_IDS: tuple[str, ...] = (
    "swmm_kw",
    "swmm_dw",
    "py_mw",
    "py_dw",
    "py_ponding",
)
SWMM_SOLVER_IDS = frozenset({"swmm_kw", "swmm_dw"})
PYTHON_SOLVER_IDS = frozenset(SOLVER_IDS) - SWMM_SOLVER_IDS
DEFAULT_FALLBACK_SOLVER_ID = "py_mw"

# Progress is persisted by the task service.  These boundaries mirror
# IMPLEMENTATION_CONTRACT.md §6.2 and are kept here so a solver cannot emit an
# invalid percentage before the task repository sees it.
PROGRESS_MIN = 0
PROGRESS_MAX = 100


class SolverId(str, Enum):
    """Stable solver identity values from the frozen contract."""

    swmm_kw = "swmm_kw"
    swmm_dw = "swmm_dw"
    py_mw = "py_mw"
    py_dw = "py_dw"
    py_ponding = "py_ponding"


class SolverSelection(str, Enum):
    auto = "auto"
    explicit = "explicit"


class SolverPhase(str, Enum):
    accepted = "accepted"
    validation = "validation"
    solving = "solving"
    parsing = "parsing"
    persisting = "persisting"
    completed = "completed"


class SolverResultStatus(str, Enum):
    verified = "verified"
    warning = "warning"
    failed = "failed"


class SolverErrorCode(str, Enum):
    """Stable error codes used by the adapter and registry."""

    SOLVER_UNAVAILABLE = "SOLVER_UNAVAILABLE"
    SOLVER_VERSION_MISMATCH = "SOLVER_VERSION_MISMATCH"
    UNSUPPORTED_FEATURE = "UNSUPPORTED_FEATURE"
    SOLVER_INPUT_INVALID = "SOLVER_INPUT_INVALID"
    SOLVER_RESULT_INVALID = "SOLVER_RESULT_INVALID"
    SOLVER_EXECUTION_FAILED = "SOLVER_EXECUTION_FAILED"
    SIMULATION_TIMEOUT = "SIMULATION_TIMEOUT"
    SIMULATION_CANCELLED = "SIMULATION_CANCELLED"
    RESULT_PERSIST_FAILED = "RESULT_PERSIST_FAILED"


class SolverContractError(RuntimeError):
    """Base error carrying a stable code and safe structured details."""

    code: str = SolverErrorCode.SOLVER_EXECUTION_FAILED.value

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        solver_id: str | None = None,
        retryable: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.code = code or type(self).code
        self.solver_id = solver_id
        self.retryable = retryable
        self.details = dict(details or {})
        super().__init__(f"{self.code}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "solver_id": self.solver_id,
            "retryable": self.retryable,
            "details": dict(sorted(self.details.items())),
        }


class SolverUnavailableError(SolverContractError):
    code = SolverErrorCode.SOLVER_UNAVAILABLE.value

    def __init__(
        self,
        message: str = "requested solver is unavailable",
        *,
        solver_id: str | None = None,
        reason: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        if reason is not None:
            merged["reason"] = reason
        self.reason = reason or self.code
        super().__init__(
            message,
            solver_id=solver_id,
            retryable=False,
            details=merged,
        )


class SolverVersionMismatchError(SolverUnavailableError):
    code = SolverErrorCode.SOLVER_VERSION_MISMATCH.value


class SolverValidationError(SolverContractError):
    code = SolverErrorCode.UNSUPPORTED_FEATURE.value


class SolverInputError(SolverContractError):
    code = SolverErrorCode.SOLVER_INPUT_INVALID.value


class SolverResultError(SolverContractError):
    code = SolverErrorCode.SOLVER_RESULT_INVALID.value


class SolverExecutionError(SolverContractError):
    code = SolverErrorCode.SOLVER_EXECUTION_FAILED.value


class SolverTimeoutError(SolverContractError):
    code = SolverErrorCode.SIMULATION_TIMEOUT.value


class SolverCancelledError(SolverContractError):
    code = SolverErrorCode.SIMULATION_CANCELLED.value


# Public aliases make migration from older experimental names less surprising.
SolverAdapterError = SolverContractError
HydraulicSolverError = SolverContractError


@dataclass(frozen=True, slots=True)
class SolverCapabilities:
    """Static capabilities advertised by a solver.

    ``validated_scale`` remains ``None`` until a benchmark has actually been
    run.  In particular, a capability declaration is not a performance claim.
    """

    solver_id: str
    version: str
    model_fidelity: str = "unknown"
    supported_features: frozenset[str] = frozenset()
    routing_modes: tuple[str, ...] = ()
    supports_ponding: bool = False
    validated_scale: tuple[int, int, int] | None = None
    max_substeps_per_report: int | None = None
    authoritative: bool = False

    def __post_init__(self) -> None:
        if not self.solver_id:
            raise ValueError("solver_id must not be empty")
        if not self.version:
            raise ValueError("solver version must not be empty")
        if isinstance(self.supported_features, set):
            object.__setattr__(
                self, "supported_features", frozenset(self.supported_features)
            )
        object.__setattr__(self, "routing_modes", tuple(self.routing_modes))

    @property
    def features(self) -> frozenset[str]:
        """Compatibility alias used by a few callers."""

        return self.supported_features

    def supports(self, feature: str) -> bool:
        return feature in self.supported_features

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_id": self.solver_id,
            "version": self.version,
            "model_fidelity": self.model_fidelity,
            "supported_features": sorted(self.supported_features),
            "routing_modes": list(self.routing_modes),
            "supports_ponding": self.supports_ponding,
            "validated_scale": (
                list(self.validated_scale) if self.validated_scale is not None else None
            ),
            "max_substeps_per_report": self.max_substeps_per_report,
            "authoritative": self.authoritative,
        }


@dataclass(frozen=True, slots=True)
class SolverProbe:
    """Result of a non-mutating capability probe.

    ``available`` is a machine fact, not a user preference.  A caller may use
    ``reason`` to explain an explicit rejection or a recorded auto fallback.
    """

    solver_id: str
    version: str
    available: bool
    reason: str = "not_probed"
    capabilities: SolverCapabilities | None = None
    engine_binary_sha256: str | None = None
    executable: str | None = None
    observed_version: str | None = None
    fallback_solver_id: str | None = None
    fallback_reason: str | None = None
    details: Mapping[str, Any] = dataclasses_field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", dict(self.details))
        if self.capabilities is None:
            object.__setattr__(
                self,
                "capabilities",
                SolverCapabilities(solver_id=self.solver_id, version=self.version),
            )

    @property
    def ok(self) -> bool:
        return self.available

    @property
    def usable(self) -> bool:
        return self.available

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_id": self.solver_id,
            "version": self.version,
            "available": self.available,
            "reason": self.reason,
            "capabilities": (
                self.capabilities.to_dict() if self.capabilities is not None else None
            ),
            "engine_binary_sha256": self.engine_binary_sha256,
            "executable": self.executable,
            "observed_version": self.observed_version,
            "fallback_solver_id": self.fallback_solver_id,
            "fallback_reason": self.fallback_reason,
            "details": dict(sorted(self.details.items())),
        }


@dataclass(frozen=True, slots=True)
class SolverSelectionResult:
    """Frozen solver choice made at submission time."""

    selection: SolverSelection
    requested_solver_id: str | None
    selected_solver_id: str
    solver: Any
    probe: SolverProbe
    fallback_reason: str | None = None
    degraded: bool = False
    probes: tuple[SolverProbe, ...] = ()

    @property
    def solver_id(self) -> str:
        return self.selected_solver_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection": self.selection.value,
            "requested_solver_id": self.requested_solver_id,
            "selected_solver_id": self.selected_solver_id,
            "fallback_reason": self.fallback_reason,
            "degraded": self.degraded,
            "probe": self.probe.to_dict(),
            "probes": [probe.to_dict() for probe in self.probes],
        }


@dataclass(frozen=True, slots=True)
class SolverDegradationReport:
    """Auditable explanation of an auto selection or an explicit rejection."""

    requested_solver_id: str | None
    selected_solver_id: str | None
    available: bool
    degraded: bool
    reason: str
    fallback_solver_id: str | None = None
    fallback_reason: str | None = None
    probes: tuple[SolverProbe, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_solver_id": self.requested_solver_id,
            "selected_solver_id": self.selected_solver_id,
            "available": self.available,
            "degraded": self.degraded,
            "reason": self.reason,
            "fallback_solver_id": self.fallback_solver_id,
            "fallback_reason": self.fallback_reason,
            "probes": [probe.to_dict() for probe in self.probes],
        }


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    field: str | None = None
    message: str = ""
    object_ids: tuple[str, ...] = ()
    details: Mapping[str, Any] = dataclasses_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "field": self.field,
            "message": self.message,
            "object_ids": list(self.object_ids),
            "details": dict(sorted(self.details.items())),
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    valid: bool
    issues: tuple[ValidationIssue, ...] = ()
    unsupported_features: tuple[str, ...] = ()
    object_ids: tuple[str, ...] = ()
    details: Mapping[str, Any] = dataclasses_field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(
            self, "unsupported_features", tuple(self.unsupported_features)
        )
        object.__setattr__(self, "object_ids", tuple(self.object_ids))
        object.__setattr__(self, "details", dict(self.details))

    @classmethod
    def ok(cls) -> "ValidationReport":
        return cls(valid=True)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(
            issue for issue in self.issues if issue.code not in {"WARNING", "INFO"}
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "issues": [issue.to_dict() for issue in self.issues],
            "unsupported_features": list(self.unsupported_features),
            "object_ids": list(self.object_ids),
            "details": dict(sorted(self.details.items())),
        }


class _FrozenJSONMapping(dict[Any, Any]):
    """A JSON mapping whose public mutation operations fail closed."""

    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("solver payload mappings are immutable")

    __setitem__ = _immutable  # type: ignore[assignment]
    __delitem__ = _immutable  # type: ignore[assignment]
    clear = _immutable  # type: ignore[assignment]
    pop = _immutable  # type: ignore[assignment]
    popitem = _immutable  # type: ignore[assignment]
    setdefault = _immutable  # type: ignore[assignment]
    update = _immutable  # type: ignore[assignment]


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenJSONMapping(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class SimulationFrame:
    """Solver-facing frame DTO.

    The payload remains a mapping so a fallback solver can use compact column
    arrays.  The sink/repository owns the final schema validation and fencing.
    """

    attempt_id: Any
    simulation_id: Any
    frame_index: int
    valid_at: Any
    payload: Mapping[str, Any]
    solver_id: str
    solver_version: str
    payload_sha256: str | None = None
    target_manifest_sha256: str | None = None
    quality_status: str = SolverResultStatus.verified.value

    def __post_init__(self) -> None:
        if isinstance(self.frame_index, bool) or self.frame_index < 0:
            raise ValueError("frame_index must be a non-negative integer")
        object.__setattr__(self, "payload", _freeze_json(self.payload))
        expected_hash = hash_json(self.payload)
        if self.payload_sha256 is None:
            object.__setattr__(self, "payload_sha256", expected_hash)
        else:
            _require_sha256(self.payload_sha256, "payload_sha256")
            if self.payload_sha256 != expected_hash:
                raise ValueError("payload_sha256 does not match the canonical payload")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": _json_value(self.attempt_id),
            "simulation_id": _json_value(self.simulation_id),
            "frame_index": self.frame_index,
            "valid_at": _json_value(self.valid_at),
            "payload": _json_value(self.payload),
            "solver_id": self.solver_id,
            "solver_version": self.solver_version,
            "payload_sha256": self.payload_sha256,
            "target_manifest_sha256": self.target_manifest_sha256,
            "quality_status": self.quality_status,
        }


# A solver may consume the concrete DTOs from another module.  These aliases
# document the boundary without importing a domain implementation here.
NetworkSnapshot: TypeAlias = Any
SolveRequest: TypeAlias = Any


@dataclass(frozen=True, slots=True)
class SolveSummary:
    """Uniform result returned by every solver implementation."""

    solver_id: str
    solver_version: str
    model_fidelity: str = "unknown"
    engine_binary_sha256: str | None = None
    input_sha256: str | None = None
    network_content_hash: str | None = None
    snapshot_content_hash: str | None = None
    initial_state_hash: str | None = None
    target_manifest_hash: str | None = None
    rainfall_content_hash: str | None = None
    solver_config_hash: str | None = None
    frame_count: int = 0
    first_at: Any = None
    last_at: Any = None
    total_mass_residual_m3: float | None = None
    max_mass_residual_relative: float | None = None
    min_time_step_s: float | None = None
    max_courant: float | None = None
    total_input_m3: float | None = None
    final_storage_m3: float | None = None
    outfall_m3: float | None = None
    infiltration_m3: float | None = None
    overflow_m3: float | None = None
    unsupported_or_degraded_features: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    diagnostics_blob_ref: str | None = None
    result_status: str = SolverResultStatus.warning.value
    fallback_reason: str | None = None
    output_artifacts: Mapping[str, str] = dataclasses_field(default_factory=dict)
    diagnostics: Mapping[str, Any] = dataclasses_field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.solver_id:
            raise ValueError("solver_id must not be empty")
        if not self.solver_version:
            raise ValueError("solver version must not be empty")
        if self.result_status not in {
            SolverResultStatus.verified.value,
            SolverResultStatus.warning.value,
            SolverResultStatus.failed.value,
        }:
            raise ValueError("result_status must be verified, warning, or failed")
        if isinstance(self.frame_count, bool) or self.frame_count < 0:
            raise ValueError("frame_count must be a non-negative integer")
        for name in (
            "total_mass_residual_m3",
            "max_mass_residual_relative",
            "min_time_step_s",
            "max_courant",
            "total_input_m3",
            "final_storage_m3",
            "outfall_m3",
            "infiltration_m3",
            "overflow_m3",
        ):
            value = getattr(self, name)
            if value is not None and not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        object.__setattr__(
            self,
            "unsupported_or_degraded_features",
            tuple(self.unsupported_or_degraded_features),
        )
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "output_artifacts", dict(self.output_artifacts))
        object.__setattr__(self, "diagnostics", dict(self.diagnostics))

    @property
    def quality_status(self) -> str:
        """Alias used by frame and API code."""

        return self.result_status

    @property
    def status(self) -> str:
        return self.result_status

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_id": self.solver_id,
            "solver_version": self.solver_version,
            "model_fidelity": self.model_fidelity,
            "engine_binary_sha256": self.engine_binary_sha256,
            "input_sha256": self.input_sha256,
            "network_content_hash": self.network_content_hash,
            "snapshot_content_hash": self.snapshot_content_hash,
            "initial_state_hash": self.initial_state_hash,
            "target_manifest_hash": self.target_manifest_hash,
            "rainfall_content_hash": self.rainfall_content_hash,
            "solver_config_hash": self.solver_config_hash,
            "frame_count": self.frame_count,
            "first_at": _json_value(self.first_at),
            "last_at": _json_value(self.last_at),
            "total_mass_residual_m3": self.total_mass_residual_m3,
            "max_mass_residual_relative": self.max_mass_residual_relative,
            "min_time_step_s": self.min_time_step_s,
            "max_courant": self.max_courant,
            "total_input_m3": self.total_input_m3,
            "final_storage_m3": self.final_storage_m3,
            "infiltration_m3": self.infiltration_m3,
            "overflow_m3": self.overflow_m3,
            "outfall_m3": self.outfall_m3,
            "unsupported_or_degraded_features": list(
                self.unsupported_or_degraded_features
            ),
            "warnings": list(self.warnings),
            "diagnostics_blob_ref": self.diagnostics_blob_ref,
            "result_status": self.result_status,
            "fallback_reason": self.fallback_reason,
            "output_artifacts": dict(sorted(self.output_artifacts.items())),
            "diagnostics": _json_value(self.diagnostics),
        }


# Compatibility names used by early task prototypes.
SolverResult = SolveSummary


@runtime_checkable
class FrameSink(Protocol):
    """Streaming sink; a solver must not build a complete frame list first."""

    async def write(self, frame: SimulationFrame | Mapping[str, Any]) -> None: ...

    async def finalize(self, summary: SolveSummary) -> None: ...


@dataclass(frozen=True, slots=True)
class RunControl:
    """Cancellation, deadline, progress, and resource controls for one run."""

    deadline_at: datetime | None = None
    cancel_event: asyncio.Event | Any | None = None
    progress_cb: Callable[[int, str], Any] | Callable[[int], Any] | None = None
    workspace_root: Path | str | None = None
    max_workspace_bytes: int = 2 * 1024**3
    max_substeps_per_report: int = 20
    numerical_retry_profile: Mapping[str, Any] = dataclasses_field(default_factory=dict)
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.deadline_at is not None:
            if not isinstance(self.deadline_at, datetime):
                raise TypeError("deadline_at must be a datetime")
            if self.deadline_at.tzinfo is None:
                raise ValueError("deadline_at must be timezone-aware")
            object.__setattr__(self, "deadline_at", self.deadline_at.astimezone(UTC))
        if isinstance(self.workspace_root, str):
            object.__setattr__(self, "workspace_root", Path(self.workspace_root))
        if self.max_workspace_bytes <= 0:
            raise ValueError("max_workspace_bytes must be positive")
        if self.max_substeps_per_report <= 0:
            raise ValueError("max_substeps_per_report must be positive")
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")

    @property
    def cancellation_event(self) -> asyncio.Event | Any | None:
        return self.cancel_event

    @property
    def progress_callback(self) -> Callable[..., Any] | None:
        return self.progress_cb

    def cancelled(self) -> bool:
        event = self.cancel_event
        if event is None:
            return False
        checker = getattr(event, "is_set", None)
        return bool(checker()) if callable(checker) else False

    def check(self) -> None:
        if self.cancelled():
            raise SolverCancelledError("solver run was cancelled")
        if self.deadline_at is not None:
            remaining = (self.deadline_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                raise SolverTimeoutError("solver run deadline has elapsed")

    async def wait_cancelled(self) -> None:
        event = self.cancel_event
        if event is None:
            await asyncio.Event().wait()
            return
        waiter = getattr(event, "wait", None)
        if callable(waiter):
            result = waiter()
            if inspect.isawaitable(result):
                await result
            else:
                while not self.cancelled():
                    await asyncio.sleep(0.05)
        else:
            while not self.cancelled():
                await asyncio.sleep(0.05)

    async def report(self, percent: int, phase: str | SolverPhase) -> None:
        await emit_progress(self.progress_cb, percent, phase)


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    percent: int
    phase: str
    emitted_at: float = dataclasses_field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        object.__setattr__(self, "percent", _validate_progress(self.percent))
        object.__setattr__(self, "phase", _phase_value(self.phase))

    def to_dict(self) -> dict[str, Any]:
        return {"percent": self.percent, "phase": self.phase}


# Compatibility alias used by task/UI prototypes.
SolverProgress = ProgressEvent


_PERSISTENCE_PHASES = {
    "accepted": "accepted",
    "validation": "validating",
    "validating": "validating",
    "solving": "solving",
    "parsing": "quality",
    "quality": "quality",
    "persisting": "persisting",
    "completed": "finalizing",
    "finalizing": "finalizing",
}


def to_persistence_phase(phase: str | SolverPhase) -> str:
    """Map solver vocabulary to the frozen ``simulations.phase`` CHECK values."""

    raw = phase.value if isinstance(phase, SolverPhase) else str(phase).strip()
    if not raw:
        raise ValueError("progress phase must not be empty")
    try:
        return _PERSISTENCE_PHASES[raw]
    except KeyError as exc:
        raise ValueError(f"unsupported solver progress phase: {raw}") from exc


def _phase_value(phase: str | SolverPhase) -> str:
    return to_persistence_phase(phase)


def _validate_progress(percent: int) -> int:
    if isinstance(percent, bool) or not isinstance(percent, int):
        raise TypeError("progress percent must be an integer")
    if percent < PROGRESS_MIN or percent > PROGRESS_MAX:
        raise ValueError("progress percent must be between 0 and 100")
    return percent


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def emit_progress(
    callback: Callable[..., Any] | None,
    percent: int,
    phase: str | SolverPhase,
) -> ProgressEvent:
    """Emit one validated progress event, allowing sync or async callbacks."""

    event = ProgressEvent(percent, _phase_value(phase))
    if callback is None:
        return event
    try:
        value = callback(event.percent, event.phase)
    except TypeError as exc:
        # A one-argument callback is a common test/worker adapter.  Only fall
        # back when the first call failed before producing a value; genuine
        # callback errors must not be hidden.
        try:
            signature = inspect.signature(callback)
            positional = [
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind
                in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
            ]
            has_varargs = any(
                parameter.kind == parameter.VAR_POSITIONAL
                for parameter in signature.parameters.values()
            )
            if len(positional) != 1 and not has_varargs:
                raise exc
        except (TypeError, ValueError):
            raise exc
        value = callback(event.percent)
    await _maybe_await(value)
    return event


class ProgressTracker:
    """Monotonic progress gate used by all solver adapters."""

    def __init__(self, callback: Callable[..., Any] | None = None) -> None:
        self.callback = callback
        self.percent = PROGRESS_MIN
        self.phase = SolverPhase.accepted.value
        self.events: list[ProgressEvent] = []

    async def update(
        self, percent: int, phase: str | SolverPhase
    ) -> ProgressEvent | None:
        normalized = _validate_progress(percent)
        phase_name = _phase_value(phase)
        if normalized < self.percent:
            # The durable task progress is monotonic.  Ignore stale reports from
            # a retry or a late subprocess rather than publishing a regression.
            return None
        self.percent = normalized
        self.phase = phase_name
        event = ProgressEvent(normalized, phase_name)
        self.events.append(event)
        await emit_progress(self.callback, normalized, phase_name)
        return event

    def update_sync(
        self, percent: int, phase: str | SolverPhase
    ) -> ProgressEvent | None:
        normalized = _validate_progress(percent)
        phase_name = _phase_value(phase)
        if normalized < self.percent:
            return None
        self.percent = normalized
        self.phase = phase_name
        event = ProgressEvent(normalized, phase_name)
        self.events.append(event)
        callback = self.callback
        if callback is not None:
            try:
                value = callback(normalized, phase_name)
            except TypeError:
                value = callback(normalized)
            if inspect.isawaitable(value):
                # Synchronous callers cannot await a callback.  Close the
                # coroutine to avoid a warning and make the misuse explicit.
                close = getattr(value, "close", None)
                if callable(close):
                    close()
                raise TypeError("progress callback must be synchronous in update_sync")
        return event


# ----------------------------- canonical hashing -----------------------------


def _json_value(value: Any) -> Any:
    """Convert supported domain values to deterministic JSON primitives."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are not valid canonical JSON")
        # Avoid platform-dependent -0.0 representation.
        return 0.0 if value == 0 else value
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, (Path, uuid.UUID)):
        return str(value)
    if isinstance(value, datetime):
        normalized = (
            value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        )
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return {
            "__bytes_sha256__": hashlib.sha256(value).hexdigest(),
            "__bytes_length__": len(value),
        }
    if isinstance(value, bytearray):
        return _json_value(bytes(value))
    if dataclasses.is_dataclass(value):
        return _json_value(dataclasses.asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_value(model_dump(mode="json"))
        except TypeError:
            return _json_value(model_dump())
    dict_method = getattr(value, "dict", None)
    if callable(dict_method) and not isinstance(value, type):
        try:
            return _json_value(dict_method())
        except TypeError:
            pass
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, (set, frozenset)):
        items = [_json_value(item) for item in value]
        return sorted(items, key=lambda item: canonical_json(item))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    # Last-resort representation for opaque typed DTOs.  It is deliberately
    # explicit rather than silently stringifying an arbitrary object.
    if hasattr(value, "__dataclass_fields__"):
        return _json_value(dataclasses.asdict(value))
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return compact, sorted, UTF-8-safe canonical JSON."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_text(value: str) -> str:
    return hash_bytes(value.encode("utf-8"))


def hash_json(value: Any) -> str:
    """Hash any supported canonical JSON value."""

    return hash_text(canonical_json(value))


def _read_value(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def compute_solver_input_hash(
    request: Any = None,
    snapshot: Any = None,
    *,
    network_content_hash: str | None = None,
    snapshot_content_hash: str | None = None,
    rainfall_content_hash: str | None = None,
    solver_id: str | None = None,
    solver_version: str | None = None,
    solver_config: Any = None,
    solver_config_hash: str | None = None,
    target_manifest_hash: str | None = None,
    initial_state_hash: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
    """Compute the solver input fingerprint from independent hash components.

    The function never hashes an ORM object directly.  Callers may pass the
    already materialized snapshot/request, but the explicit hash arguments are
    authoritative when present.  Network revision, materialized snapshot,
    initial state, target manifest, and solver configuration remain distinct so
    one cannot silently substitute for another.  A schema tag prevents
    accidental collisions with unrelated SHA-256 values.
    """

    request_manifest = _read_value(request, "input_manifest", "solver_input")
    request_network = _read_value(
        request,
        "network_content_hash",
        "network_revision_content_sha256",
        "network_revision_content_hash",
    ) or _read_value(
        request_manifest,
        "network_content_hash",
        "network_revision_content_sha256",
    )
    request_snapshot = _read_value(
        request,
        "snapshot_content_hash",
        "snapshot_content_sha256",
        "materialized_snapshot_sha256",
    ) or _read_value(
        request_manifest,
        "snapshot_content_hash",
        "snapshot_content_sha256",
    )
    request_rainfall = _read_value(
        request,
        "rainfall_content_hash",
        "rainfall_sha256",
    ) or _read_value(request_manifest, "rainfall_content_sha256")
    request_solver = _read_value(request, "solver_id") or _read_value(
        request_manifest,
        "solver_id",
    )
    request_version = _read_value(request, "solver_version", "version") or _read_value(
        request_manifest,
        "solver_version",
    )
    request_config = _read_value(
        request,
        "solver_config",
        "config",
        "parameters",
    ) or _read_value(request_manifest, "solver_config")
    request_config_hash = _read_value(
        request,
        "solver_config_hash",
        "config_hash",
    ) or _read_value(request_manifest, "solver_config_hash")
    request_target = _read_value(
        request,
        "target_manifest_hash",
        "target_manifest_sha256",
        "target_manifest_content_sha256",
    ) or _read_value(request_manifest, "target_manifest_sha256")
    request_initial = _read_value(
        request,
        "initial_state_hash",
        "initial_state_content_sha256",
    ) or _read_value(request_manifest, "initial_state_content_sha256")
    snapshot_network = _read_value(
        snapshot,
        "network_content_hash",
        "network_revision_content_sha256",
        "content_sha256",
    )
    snapshot_content = _read_value(
        snapshot,
        "snapshot_content_hash",
        "snapshot_content_sha256",
    )
    snapshot_target = _read_value(
        snapshot, "target_manifest_sha256", "target_manifest_hash"
    )
    snapshot_initial = _read_value(
        snapshot,
        "initial_state_sha256",
        "initial_state_content_sha256",
    )
    payload: dict[str, Any] = {
        "schema": "hydraulic-solver-input-v1",
        "network_content_hash": network_content_hash
        or request_network
        or snapshot_network,
        "rainfall_content_hash": rainfall_content_hash or request_rainfall,
        "solver_id": solver_id or request_solver,
        "solver_version": solver_version or request_version,
        "solver_config": solver_config if solver_config is not None else request_config,
        "target_manifest_hash": target_manifest_hash
        or request_target
        or snapshot_target,
        "initial_state_hash": initial_state_hash or request_initial or snapshot_initial,
    }
    effective_snapshot_hash = (
        snapshot_content_hash or request_snapshot or snapshot_content
    )
    effective_config_hash = solver_config_hash or request_config_hash
    if effective_snapshot_hash is not None:
        payload["snapshot_content_hash"] = effective_snapshot_hash
    if effective_config_hash is not None:
        payload["solver_config_hash"] = effective_config_hash
    if request_manifest is not None:
        manifest_extra = {
            "schema_version": _read_value(request_manifest, "schema_version"),
            "solver_config_schema_id": _read_value(
                request_manifest,
                "solver_config_schema_id",
            ),
            "solver_config_schema_version": _read_value(
                request_manifest,
                "solver_config_schema_version",
            ),
            "parameter_set_id": _read_value(
                request_manifest,
                "parameter_set_id",
            ),
            "parameter_set_content_sha256": _read_value(
                request_manifest,
                "parameter_set_content_sha256",
            ),
            "parameter_schema_id": _read_value(
                request_manifest,
                "parameter_schema_id",
            ),
            "parameter_schema_version": _read_value(
                request_manifest,
                "parameter_schema_version",
            ),
            "parameter_schema_sha256": _read_value(
                request_manifest,
                "parameter_schema_sha256",
            ),
            "transform_id": _read_value(request_manifest, "transform_id"),
            "solver_crs": _read_value(request_manifest, "solver_crs"),
            "start_at": _read_value(request_manifest, "start_at"),
            "duration_seconds": _read_value(request_manifest, "duration_seconds"),
            "frame_interval_seconds": _read_value(
                request_manifest,
                "frame_interval_seconds",
            ),
            "expected_frame_count": _read_value(
                request_manifest,
                "expected_frame_count",
            ),
            "ponding_enabled": _read_value(request_manifest, "ponding_enabled"),
        }
        if all(value is not None for value in manifest_extra.values()):
            merged_extra = dict(extra or {})
            merged_extra.update(manifest_extra)
            payload["extra"] = merged_extra
    elif extra:
        payload["extra"] = dict(extra)
    return hash_json(payload)


# Short aliases make the intended operation obvious at call sites.
compute_input_hash = compute_solver_input_hash
hash_solver_input = compute_solver_input_hash
input_fingerprint = compute_solver_input_hash


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _require_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be 64 lowercase hexadecimal characters")
    return value


@dataclass(frozen=True, slots=True)
class SolverInputManifest:
    """Complete, immutable identity of one solver invocation.

    This is the preferred input-hash boundary.  The compatibility function
    :func:`compute_solver_input_hash` remains permissive for existing callers,
    while new adapters bind all independent hashes plus the time grid and
    solver configuration through this manifest.
    """

    schema_version: str
    network_revision_content_sha256: str
    snapshot_content_sha256: str
    initial_state_content_sha256: str
    target_manifest_sha256: str
    rainfall_content_sha256: str
    solver_id: str
    solver_version: str
    solver_config_schema_id: str
    solver_config_schema_version: str
    parameter_set_id: str
    parameter_set_content_sha256: str
    parameter_schema_id: str
    parameter_schema_version: str
    parameter_schema_sha256: str
    solver_config: Mapping[str, Any]
    transform_id: str
    solver_crs: str
    start_at: datetime
    duration_seconds: int
    frame_interval_seconds: int
    expected_frame_count: int
    ponding_enabled: bool

    def __post_init__(self) -> None:
        for name in (
            "schema_version",
            "solver_config_schema_id",
            "solver_config_schema_version",
            "parameter_set_id",
            "parameter_schema_id",
            "parameter_schema_version",
            "transform_id",
            "solver_crs",
            "solver_id",
            "solver_version",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.solver_id not in SOLVER_IDS:
            raise ValueError(f"unsupported solver_id: {self.solver_id}")
        for name in (
            "network_revision_content_sha256",
            "snapshot_content_sha256",
            "initial_state_content_sha256",
            "target_manifest_sha256",
            "rainfall_content_sha256",
            "parameter_set_content_sha256",
            "parameter_schema_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if not isinstance(self.start_at, datetime):
            raise TypeError("start_at must be a datetime")
        if self.start_at.tzinfo is None or self.start_at.utcoffset() is None:
            raise ValueError("start_at must be timezone-aware")
        if (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, int)
            or self.duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be a positive integer")
        if (
            isinstance(self.frame_interval_seconds, bool)
            or not isinstance(self.frame_interval_seconds, int)
            or not 1 <= self.frame_interval_seconds <= 300
        ):
            raise ValueError("frame_interval_seconds must be an integer in [1, 300]")
        if self.duration_seconds % self.frame_interval_seconds != 0:
            raise ValueError("duration must align to the frame interval")
        expected = self.duration_seconds // self.frame_interval_seconds + 1
        if (
            isinstance(self.expected_frame_count, bool)
            or not isinstance(self.expected_frame_count, int)
            or self.expected_frame_count != expected
        ):
            raise ValueError(
                "expected_frame_count must include both endpoints of the time grid"
            )
        if not isinstance(self.ponding_enabled, bool):
            raise TypeError("ponding_enabled must be a boolean")
        if not isinstance(self.solver_config, Mapping):
            raise TypeError("solver_config must be a mapping")
        object.__setattr__(self, "start_at", self.start_at.astimezone(UTC))
        object.__setattr__(self, "solver_config", _freeze_json(self.solver_config))

    @property
    def solver_config_hash(self) -> str:
        return hash_json(self.solver_config)

    @property
    def input_sha256(self) -> str:
        return compute_solver_input_hash(
            network_content_hash=self.network_revision_content_sha256,
            snapshot_content_hash=self.snapshot_content_sha256,
            rainfall_content_hash=self.rainfall_content_sha256,
            solver_id=self.solver_id,
            solver_version=self.solver_version,
            solver_config=self.solver_config,
            solver_config_hash=self.solver_config_hash,
            target_manifest_hash=self.target_manifest_sha256,
            initial_state_hash=self.initial_state_content_sha256,
            extra={
                "schema_version": self.schema_version,
                "solver_config_schema_id": self.solver_config_schema_id,
                "solver_config_schema_version": self.solver_config_schema_version,
                "parameter_set_id": self.parameter_set_id,
                "parameter_set_content_sha256": self.parameter_set_content_sha256,
                "parameter_schema_id": self.parameter_schema_id,
                "parameter_schema_version": self.parameter_schema_version,
                "parameter_schema_sha256": self.parameter_schema_sha256,
                "transform_id": self.transform_id,
                "solver_crs": self.solver_crs,
                "start_at": self.start_at,
                "duration_seconds": self.duration_seconds,
                "frame_interval_seconds": self.frame_interval_seconds,
                "expected_frame_count": self.expected_frame_count,
                "ponding_enabled": self.ponding_enabled,
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "network_revision_content_sha256": self.network_revision_content_sha256,
            "snapshot_content_sha256": self.snapshot_content_sha256,
            "initial_state_content_sha256": self.initial_state_content_sha256,
            "target_manifest_sha256": self.target_manifest_sha256,
            "rainfall_content_sha256": self.rainfall_content_sha256,
            "solver_id": self.solver_id,
            "solver_version": self.solver_version,
            "solver_config_schema_id": self.solver_config_schema_id,
            "solver_config_schema_version": self.solver_config_schema_version,
            "parameter_set_id": self.parameter_set_id,
            "parameter_set_content_sha256": self.parameter_set_content_sha256,
            "parameter_schema_id": self.parameter_schema_id,
            "parameter_schema_version": self.parameter_schema_version,
            "parameter_schema_sha256": self.parameter_schema_sha256,
            "solver_config": _json_value(self.solver_config),
            "solver_config_hash": self.solver_config_hash,
            "transform_id": self.transform_id,
            "solver_crs": self.solver_crs,
            "start_at": _json_value(self.start_at),
            "duration_seconds": self.duration_seconds,
            "frame_interval_seconds": self.frame_interval_seconds,
            "expected_frame_count": self.expected_frame_count,
            "ponding_enabled": self.ponding_enabled,
            "input_sha256": self.input_sha256,
        }


SolverInput = SolverInputManifest


# ------------------------------- registry ----------------------------------


def _coerce_capabilities(
    value: Any,
    *,
    solver_id: str,
    version: str,
) -> SolverCapabilities | None:
    if value is None or isinstance(value, SolverCapabilities):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        data.setdefault("solver_id", solver_id)
        data.setdefault("version", version)
        features = data.pop("supported_features", data.pop("features", ()))
        if isinstance(features, str):
            features = (features,)
        data["supported_features"] = frozenset(str(item) for item in features)
        if "routing_modes" in data:
            data["routing_modes"] = tuple(str(item) for item in data["routing_modes"])
        try:
            return SolverCapabilities(**data)
        except (TypeError, ValueError):
            return SolverCapabilities(solver_id=solver_id, version=version)
    return None


async def _probe_solver(solver: Any) -> SolverProbe:
    solver_id = str(getattr(solver, "solver_id", "unknown"))
    version = str(getattr(solver, "version", "unknown"))
    probe_method = getattr(solver, "probe", None)
    if not callable(probe_method):
        return SolverProbe(
            solver_id=solver_id,
            version=version,
            available=False,
            reason="probe_required",
        )
    try:
        value = probe_method()
        value = await _maybe_await(value)
    except SolverContractError as exc:
        return SolverProbe(
            solver_id=solver_id,
            version=version,
            available=False,
            reason=str(getattr(exc, "reason", exc.code)),
            details=exc.to_dict(),
        )
    except Exception as exc:  # probe must never crash selection
        return SolverProbe(
            solver_id=solver_id,
            version=version,
            available=False,
            reason="probe_failed",
            details={"error_type": type(exc).__name__, "error": str(exc)},
        )
    if isinstance(value, SolverProbe):
        return value
    if isinstance(value, Mapping):
        data = dict(value)
        data.setdefault("solver_id", solver_id)
        data.setdefault("version", version)
        try:
            probe_solver_id = str(data.pop("solver_id"))
            probe_version = str(data.pop("version"))
            capabilities = _coerce_capabilities(
                data.pop("capabilities", None),
                solver_id=probe_solver_id,
                version=probe_version,
            )
            return SolverProbe(
                solver_id=probe_solver_id,
                version=probe_version,
                available=bool(data.pop("available", data.pop("usable", True))),
                reason=str(data.pop("reason", "probed")),
                capabilities=capabilities,
                engine_binary_sha256=data.pop("engine_binary_sha256", None),
                executable=data.pop("executable", None),
                observed_version=data.pop("observed_version", None),
                fallback_solver_id=data.pop("fallback_solver_id", None),
                fallback_reason=data.pop("fallback_reason", None),
                details=data,
            )
        except (TypeError, ValueError):
            return SolverProbe(
                solver_id=solver_id,
                version=version,
                available=False,
                reason="invalid_probe",
                details={"probe": value},
            )
    return SolverProbe(
        solver_id=solver_id,
        version=version,
        available=bool(value),
        reason="probed" if value else "unavailable",
    )


class SolverRegistry:
    """Registry and submission-time solver selector.

    The registry owns only selection.  It never catches a run-time failure and
    substitutes another solver; callers must retain the returned selection for
    the lifetime of a simulation.
    """

    def __init__(
        self,
        solvers: Iterable[Any] | None = None,
        *,
        fallback_solver_id: str = DEFAULT_FALLBACK_SOLVER_ID,
    ) -> None:
        self._solvers: dict[str, Any] = {}
        raw_fallback = getattr(fallback_solver_id, "value", fallback_solver_id)
        self.fallback_solver_id = str(raw_fallback)
        for solver in solvers or ():
            self.register(solver)

    def register(self, solver: Any, *, replace: bool = False) -> Any:
        raw_solver_id = getattr(solver, "solver_id", "")
        solver_id = str(getattr(raw_solver_id, "value", raw_solver_id)).strip()
        if not solver_id:
            raise ValueError("solver must expose a non-empty solver_id")
        if solver_id not in SOLVER_IDS:
            raise ValueError(f"unsupported solver_id: {solver_id}")
        if solver_id in self._solvers and not replace:
            raise ValueError(f"solver already registered: {solver_id}")
        self._solvers[solver_id] = solver
        return solver

    register_solver = register

    def unregister(self, solver_id: str) -> Any:
        try:
            return self._solvers.pop(solver_id)
        except KeyError as exc:
            raise KeyError(f"solver is not registered: {solver_id}") from exc

    def get(self, solver_id: str) -> Any:
        try:
            return self._solvers[solver_id]
        except KeyError as exc:
            raise SolverUnavailableError(
                f"solver is not registered: {solver_id}",
                solver_id=solver_id,
                reason="not_registered",
            ) from exc

    get_solver = get

    def __contains__(self, solver_id: object) -> bool:
        return solver_id in self._solvers

    def __len__(self) -> int:
        return len(self._solvers)

    @property
    def solver_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._solvers))

    def solvers(self) -> tuple[Any, ...]:
        return tuple(self._solvers[solver_id] for solver_id in self.solver_ids)

    async def probe(self, solver_id: str) -> SolverProbe:
        return await _probe_solver(self.get(solver_id))

    def _candidate_ids(
        self,
        mode: SolverSelection,
        requested_solver_id: str | None,
    ) -> tuple[str, ...]:
        if mode is SolverSelection.explicit:
            if not requested_solver_id:
                raise ValueError("explicit selection requires requested_solver_id")
            return (requested_solver_id,)
        if requested_solver_id is not None:
            if requested_solver_id not in SOLVER_IDS:
                raise ValueError(f"unsupported solver_id: {requested_solver_id}")
            candidates = [requested_solver_id]
            if self.fallback_solver_id not in candidates:
                candidates.append(self.fallback_solver_id)
            return tuple(candidates)
        candidates = ["swmm_kw"]
        if self.fallback_solver_id not in candidates:
            candidates.append(self.fallback_solver_id)
        return tuple(candidates)

    async def select(
        self,
        selection: SolverSelection | str = SolverSelection.auto,
        requested_solver_id: str | None = None,
        *,
        solver_id: str | None = None,
    ) -> SolverSelectionResult:
        """Freeze a solver choice, with explicit and auto semantics separated.

        ``explicit`` never degrades.  ``auto`` probes a requested solver first;
        when it is unavailable it may freeze the registered Python fallback,
        while the no-request path prefers ``swmm_kw`` before ``py_mw``.  The
        returned identity is immutable for the lifetime of a simulation.
        """

        if requested_solver_id is not None:
            raw_requested = getattr(requested_solver_id, "value", requested_solver_id)
            requested_solver_id = str(raw_requested)
        if solver_id is not None:
            raw_solver_id = getattr(solver_id, "value", solver_id)
            solver_id = str(raw_solver_id)
            if requested_solver_id is not None and requested_solver_id != solver_id:
                raise ValueError("solver_id and requested_solver_id disagree")
            requested_solver_id = solver_id
        try:
            mode = (
                selection
                if isinstance(selection, SolverSelection)
                else SolverSelection(str(selection))
            )
        except ValueError as exc:
            raise ValueError("solver selection must be auto or explicit") from exc
        candidates = self._candidate_ids(mode, requested_solver_id)
        probes: list[SolverProbe] = []
        for candidate in candidates:
            if candidate not in self._solvers:
                probes.append(
                    SolverProbe(
                        solver_id=candidate,
                        version="unregistered",
                        available=False,
                        reason="not_registered",
                    )
                )
                continue
            probes.append(await _probe_solver(self._solvers[candidate]))

        if mode is SolverSelection.explicit:
            probe = probes[0]
            if not probe.available:
                raise SolverUnavailableError(
                    f"explicit solver {requested_solver_id!r} is "
                    f"unavailable: {probe.reason}",
                    solver_id=requested_solver_id,
                    reason=probe.reason,
                    details={
                        "probes": [item.to_dict() for item in probes],
                    },
                )
            assert requested_solver_id is not None
            return SolverSelectionResult(
                selection=mode,
                requested_solver_id=requested_solver_id,
                selected_solver_id=requested_solver_id,
                solver=self._solvers[requested_solver_id],
                probe=probe,
                probes=tuple(probes),
            )

        for index, probe in enumerate(probes):
            if not probe.available:
                continue
            candidate = candidates[index]
            degraded = index > 0
            if degraded:
                previous = probes[index - 1]
                fallback_reason = (
                    f"requested_solver_unavailable:{requested_solver_id};"
                    f"reason:{previous.reason};selected:{candidate}"
                    if requested_solver_id
                    else f"preferred_solver_unavailable:{previous.reason};"
                    f"selected:{candidate}"
                )
            else:
                fallback_reason = None
            return SolverSelectionResult(
                selection=mode,
                requested_solver_id=requested_solver_id,
                selected_solver_id=candidate,
                solver=self._solvers[candidate],
                probe=probe,
                fallback_reason=fallback_reason,
                degraded=degraded,
                probes=tuple(probes),
            )

        requested_probe = next(
            (probe for probe in probes if probe.solver_id == requested_solver_id),
            probes[0] if probes else None,
        )
        raise SolverUnavailableError(
            "no available solver for the requested selection",
            solver_id=requested_solver_id,
            reason=requested_probe.reason if requested_probe else "not_registered",
            details={
                "requested_solver_id": requested_solver_id,
                "candidates": list(candidates),
                "probes": [probe.to_dict() for probe in probes],
            },
        )

    resolve = select
    select_solver = select

    async def capability_report(
        self,
        selection: SolverSelection | str = SolverSelection.auto,
        requested_solver_id: str | None = None,
    ) -> SolverDegradationReport:
        """Return every probe and the auditable auto-selection outcome."""

        try:
            selected = await self.select(selection, requested_solver_id)
        except SolverUnavailableError as exc:
            probes: list[SolverProbe] = []
            for item in exc.details.get("probes", ()):
                if isinstance(item, SolverProbe):
                    probes.append(item)
                elif isinstance(item, Mapping):
                    data = dict(item)
                    probe_solver_id = str(data.get("solver_id", "unknown"))
                    probe_version = str(data.get("version", "unknown"))
                    capabilities = _coerce_capabilities(
                        data.get("capabilities"),
                        solver_id=probe_solver_id,
                        version=probe_version,
                    )
                    probes.append(
                        SolverProbe(
                            solver_id=probe_solver_id,
                            version=probe_version,
                            available=bool(data.get("available", False)),
                            reason=str(data.get("reason", "unavailable")),
                            capabilities=capabilities,
                            engine_binary_sha256=data.get("engine_binary_sha256"),
                            executable=data.get("executable"),
                            observed_version=data.get("observed_version"),
                            fallback_solver_id=data.get("fallback_solver_id"),
                            fallback_reason=data.get("fallback_reason"),
                            details={
                                key: value
                                for key, value in data.items()
                                if key
                                not in {
                                    "solver_id",
                                    "version",
                                    "available",
                                    "reason",
                                    "capabilities",
                                    "engine_binary_sha256",
                                    "executable",
                                    "observed_version",
                                    "fallback_solver_id",
                                    "fallback_reason",
                                }
                            },
                        )
                    )
            is_auto = selection in {
                SolverSelection.auto,
                SolverSelection.auto.value,
            }
            return SolverDegradationReport(
                requested_solver_id=requested_solver_id,
                selected_solver_id=None,
                available=False,
                degraded=False,
                reason=exc.code,
                fallback_solver_id=self.fallback_solver_id if is_auto else None,
                fallback_reason=exc.reason,
                probes=tuple(probes),
            )
        probes = list(selected.probes or (selected.probe,))
        return SolverDegradationReport(
            requested_solver_id=selected.requested_solver_id,
            selected_solver_id=selected.selected_solver_id,
            available=True,
            degraded=selected.degraded,
            reason="selected" if not selected.degraded else "fallback_selected",
            fallback_solver_id=(
                selected.selected_solver_id if selected.degraded else None
            ),
            fallback_reason=selected.fallback_reason,
            probes=tuple(probes),
        )

    probe_all = capability_report
    report = capability_report
    list_capabilities = capability_report


# A process-local registry is useful for dependency injection, but remains
# empty until an application registers concrete solvers.  This avoids importing
# an optional native engine or blocking Windows startup at import time.
default_registry = SolverRegistry()
SOLVER_REGISTRY = default_registry
register_solver = default_registry.register
resolve_solver = default_registry.select


def create_solver_registry(
    solvers: Iterable[Any] | None = None,
    *,
    fallback_solver_id: str = DEFAULT_FALLBACK_SOLVER_ID,
) -> SolverRegistry:
    """Create an isolated registry without importing optional solver engines."""

    return SolverRegistry(solvers, fallback_solver_id=fallback_solver_id)


@runtime_checkable
class HydraulicSolver(Protocol):
    """Runtime-checkable structural type for all solver implementations."""

    solver_id: str
    version: str

    async def probe(self) -> SolverProbe: ...

    def capabilities(self) -> SolverCapabilities: ...

    def validate(
        self,
        snapshot: NetworkSnapshot,
        request: SolveRequest,
    ) -> ValidationReport: ...

    async def run(
        self,
        request: SolveRequest,
        snapshot: NetworkSnapshot,
        frame_sink: FrameSink,
        control: RunControl,
    ) -> SolveSummary: ...


# The frozen implementation contract calls the same boundary SolverAdapter.
SolverAdapter = HydraulicSolver


__all__ = [
    "DEFAULT_FALLBACK_SOLVER_ID",
    "FrameSink",
    "HydraulicSolver",
    "NetworkSnapshot",
    "ProgressEvent",
    "ProgressTracker",
    "PYTHON_SOLVER_IDS",
    "RunControl",
    "SOLVER_IDS",
    "SOLVER_REGISTRY",
    "SWMM_SOLVER_IDS",
    "SimulationFrame",
    "SolveRequest",
    "SolveSummary",
    "SolverAdapter",
    "SolverAdapterError",
    "SolverCapabilities",
    "SolverCancelledError",
    "SolverContractError",
    "SolverDegradationReport",
    "SolverErrorCode",
    "SolverExecutionError",
    "SolverId",
    "SolverInput",
    "SolverInputError",
    "SolverInputManifest",
    "SolverPhase",
    "SolverProbe",
    "SolverRegistry",
    "SolverResult",
    "SolverResultError",
    "SolverResultStatus",
    "SolverSelection",
    "SolverSelectionResult",
    "SolverTimeoutError",
    "SolverUnavailableError",
    "SolverValidationError",
    "SolverVersionMismatchError",
    "ValidationIssue",
    "ValidationReport",
    "canonical_json",
    "compute_input_hash",
    "compute_solver_input_hash",
    "create_solver_registry",
    "default_registry",
    "emit_progress",
    "hash_bytes",
    "hash_json",
    "hash_solver_input",
    "hash_text",
    "input_fingerprint",
    "register_solver",
    "resolve_solver",
    "to_persistence_phase",
]
