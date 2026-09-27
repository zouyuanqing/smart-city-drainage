"""``py_mw``: the pure-Python motion-wave solver adapter and its I/O protocol.

This module is the production entry point for the reduced-fidelity fallback
solver.  It owns three things:

1. the **frozen input schema** (:data:`PYTHON_SOLVER_INPUT_SCHEMA`) that a
   worker or a CLI user must materialise into an attempt workspace;
2. :class:`PythonRoutingSolver`, the adapter that turns that input into the
   numeric call on :mod:`app.services.hydraulic.solvers.python_hydraulics` and
   streams :class:`~app.services.hydraulic.solvers.base.SimulationFrame` values
   into a :class:`~app.services.hydraulic.solvers.base.FrameSink`;
3. the **frame / quality / metrics documents** every ``py_*`` solver emits, so
   the CLI, the API, and the benchmark harness observe one schema.

Frozen-contract constraints honoured here
------------------------------------------
* ``IMPLEMENTATION_CONTRACT.md`` §5.5 — ``py_mw`` is node continuity plus a
  pipe Manning relation with a capacity-limited surcharge approximation; it is
  **not** a dynamic-wave solver and never resolves water hammer.
* §5.2 — the solver is probed before submission and its identity is frozen for
  the run.  A run-time failure raises; it never substitutes another solver.
* §6.2 — progress is monotonic and stays inside the published phase bands.
* §1.2/§3.6 — the four independent content hashes (network revision, snapshot,
  initial state, target manifest) plus the rainfall hash are bound through
  :class:`~app.services.hydraulic.solvers.base.SolverInputManifest`, and none
  of them substitutes for another.
* §15.3 — this file must not import the database, the HTTP layer, or the
  process-wide configuration.  It performs no I/O of its own; the CLI owns the
  filesystem.

``py_dw`` reuses everything in this module and only overrides the frozen
identity and the regime guard; see :mod:`app.services.hydraulic.solvers.python_dw`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.hydraulic.solvers.base import (
    FrameSink,
    ProgressTracker,
    RunControl,
    SimulationFrame,
    SolverCancelledError,
    SolverCapabilities,
    SolverContractError,
    SolverErrorCode,
    SolverExecutionError,
    SolverInputError,
    SolverInputManifest,
    SolverPhase,
    SolverProbe,
    SolverResultError,
    SolverTimeoutError,
    SolverValidationError,
    SolveSummary,
    ValidationIssue,
    ValidationReport,
    canonical_json,
    hash_json,
)
from app.services.hydraulic.solvers.python_hydraulics import (
    Catchment,
    FrameResult,
    HydraulicNetwork,
    HydraulicNode,
    HydraulicPipe,
    MassBalanceError,
    NetworkValidationError,
    NumericalStabilityError,
    QualityStatus,
    RainfallForcing,
    RoutingMode,
    SimulationResult,
    SolverConfig,
    UnsupportedRegimeError,
    solve_network,
)

UTC = timezone.utc

PYTHON_SOLVER_INPUT_SCHEMA = "python-solver-input-v1"
PYTHON_SOLVER_NETWORK_SCHEMA = "python-solver-network-v1"
PYTHON_SOLVER_RAINFALL_SCHEMA = "python-solver-rainfall-v1"
PYTHON_SOLVER_INITIAL_STATE_SCHEMA = "python-solver-initial-state-v1"
PYTHON_FRAME_SCHEMA = "hydraulic-frame-v1"
PYTHON_QUALITY_SCHEMA = "solver-quality-v1"
PYTHON_METRICS_SCHEMA = "solver-metrics-v1"

#: Schema version carried by every frame payload a ``py_*`` solver publishes.
PYTHON_FRAME_SCHEMA_VERSION = 1

#: Progress bands from IMPLEMENTATION_CONTRACT.md §6.2.
_SOLVING_PROGRESS_START = 5
_SOLVING_PROGRESS_END = 79
_QUALITY_PROGRESS_END = 94
_PERSISTING_PROGRESS = 99
_DONE_PROGRESS = 100

#: Untrusted-input budget: IMPLEMENTATION_CONTRACT.md §6.5, 20 MiB.
MAX_SOLVER_INPUT_BYTES = 20 * 1024 * 1024

_QUALITY_ORDER = {
    QualityStatus.VERIFIED: 0,
    QualityStatus.WARNING: 1,
    QualityStatus.FAILED: 2,
}


# ---------------------------------------------------------------------------
# strict document readers
# ---------------------------------------------------------------------------


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SolverInputError(f"{label} must be a JSON object")
    return value


def _closed(document: Mapping[str, Any], allowed: Sequence[str], label: str) -> None:
    """Reject unknown keys: the input schema is closed, not permissive."""

    unknown = sorted(set(document) - set(allowed))
    if unknown:
        raise SolverInputError(f"{label} has unsupported keys: {unknown}")


def _text(document: Mapping[str, Any], key: str, label: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SolverInputError(f"{label}.{key} must be a non-empty string")
    return str(value).strip()


def _digest(document: Mapping[str, Any], key: str, label: str) -> str:
    value = _text(document, key, label).lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise SolverInputError(
            f"{label}.{key} must be a lowercase 64-hex SHA-256 digest"
        )
    return value


def _integer(document: Mapping[str, Any], key: str, label: str) -> int:
    value = document.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SolverInputError(f"{label}.{key} must be a positive integer")
    return int(value)


def _number(
    document: Mapping[str, Any],
    key: str,
    label: str,
    *,
    required: bool = False,
    default: float | None = None,
) -> float:
    value = document.get(key, default)
    if value is None:
        if required:
            raise SolverInputError(f"{label}.{key} is required")
        raise SolverInputError(f"{label}.{key} must be a number")
    return _finite_number(value, f"{label}.{key}")


def _optional_number(document: Mapping[str, Any], key: str, label: str) -> float | None:
    if document.get(key) is None:
        return None
    return _number(document, key, label, required=True)


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SolverInputError(f"{label} must be a number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise SolverInputError(f"{label} must be finite")
    return numeric


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SolverInputError(f"{label} must be an RFC 3339 UTC timestamp")
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SolverInputError(f"{label} is not a valid RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SolverInputError(f"{label} must carry an explicit UTC offset")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# network document
# ---------------------------------------------------------------------------


_NODE_FIELDS = (
    "invert_elevation_m",
    "max_depth_m",
    "initial_depth_m",
    "surface_area_m2",
    "kind",
    "outfall_width_m",
)
_PIPE_FIELDS = (
    "upstream_node_id",
    "downstream_node_id",
    "length_m",
    "manning_n",
    "upstream_invert_m",
    "downstream_invert_m",
    "diameter_m",
    "width_m",
    "height_m",
    "shape",
    "initial_flow_m3s",
    "upstream_crown_m",
    "downstream_crown_m",
)
_CATCHMENT_FIELDS = (
    "outlet_node_id",
    "area_m2",
    "runoff_coefficient",
    "depression_storage_mm",
)
_NETWORK_FIELDS = (
    "schema",
    "nodes",
    "pipes",
    "catchments",
    "initial_state",
)
#: A sibling declaration of the features a network *needs*.  It is metadata
#: about capability, not hydraulic input, so it is read by ``validate`` and
#: ignored by the numeric kernel rather than rejected.
_NETWORK_FEATURE_FIELDS = ("required_features",)
_INITIAL_STATE_FIELDS = ("schema", "kind", "node_depth_m", "pipe_flow_m3s")


def _state_values(
    document: Mapping[str, Any], key: str, known: Sequence[str]
) -> dict[str, float]:
    """Read an initial-state field given either as a uniform scalar or a map."""

    if key not in document:
        return {}
    value = document.get(key)
    known_keys = set(known)
    if isinstance(value, bool):
        raise SolverInputError(f"initial_state.{key} must be a number or an object")
    if isinstance(value, (int, float)):
        numeric = _finite_number(value, f"initial_state.{key}")
        return {stable_key: numeric for stable_key in known_keys}
    body = _object(value, f"initial_state.{key}")
    unknown = sorted(set(body) - known_keys)
    if unknown:
        raise SolverInputError(
            f"initial_state.{key} references unknown stable keys: {unknown}"
        )
    return {
        str(stable_key): _finite_number(item, f"initial_state.{key}[{stable_key!r}]")
        for stable_key, item in body.items()
    }


def _build_nodes(
    entries: Mapping[str, Any],
    label: str,
    initial_depths: Mapping[str, float],
) -> tuple[HydraulicNode, ...]:
    nodes: list[HydraulicNode] = []
    for stable_key, value in entries.items():
        item_label = f"{label}[{stable_key!r}]"
        body = _object(value, item_label)
        _closed(body, _NODE_FIELDS, item_label)
        node_id = str(stable_key)
        initial_depth = body.get("initial_depth_m", initial_depths.get(node_id, 0.0))
        try:
            nodes.append(
                HydraulicNode(
                    node_id=node_id,
                    invert_elevation_m=_number(
                        body, "invert_elevation_m", item_label, required=True
                    ),
                    max_depth_m=_number(body, "max_depth_m", item_label, required=True),
                    initial_depth_m=_finite_number(
                        initial_depth, f"{item_label}.initial_depth_m"
                    ),
                    surface_area_m2=_number(
                        body, "surface_area_m2", item_label, default=0.1
                    ),
                    kind=str(body.get("kind", "junction")),
                    outfall_width_m=_number(
                        body, "outfall_width_m", item_label, default=1.0
                    ),
                )
            )
        except NetworkValidationError as exc:
            raise SolverInputError(f"{item_label} is invalid: {exc}") from exc
    return tuple(nodes)


def _build_pipes(
    entries: Mapping[str, Any],
    label: str,
    initial_flows: Mapping[str, float],
) -> tuple[HydraulicPipe, ...]:
    pipes: list[HydraulicPipe] = []
    for stable_key, value in entries.items():
        item_label = f"{label}[{stable_key!r}]"
        body = _object(value, item_label)
        _closed(body, _PIPE_FIELDS, item_label)
        pipe_id = str(stable_key)
        initial_flow = body.get("initial_flow_m3s", initial_flows.get(pipe_id, 0.0))
        try:
            pipes.append(
                HydraulicPipe(
                    pipe_id=pipe_id,
                    upstream_node_id=_text(body, "upstream_node_id", item_label),
                    downstream_node_id=_text(body, "downstream_node_id", item_label),
                    length_m=_number(body, "length_m", item_label, required=True),
                    manning_n=_number(body, "manning_n", item_label, required=True),
                    upstream_invert_m=_number(
                        body, "upstream_invert_m", item_label, required=True
                    ),
                    downstream_invert_m=_number(
                        body, "downstream_invert_m", item_label, required=True
                    ),
                    diameter_m=_optional_number(body, "diameter_m", item_label),
                    width_m=_optional_number(body, "width_m", item_label),
                    height_m=_optional_number(body, "height_m", item_label),
                    shape=str(body.get("shape", "circular")),
                    initial_flow_m3s=_finite_number(
                        initial_flow, f"{item_label}.initial_flow_m3s"
                    ),
                    upstream_crown_m=_optional_number(
                        body, "upstream_crown_m", item_label
                    ),
                    downstream_crown_m=_optional_number(
                        body, "downstream_crown_m", item_label
                    ),
                )
            )
        except NetworkValidationError as exc:
            raise SolverInputError(f"{item_label} is invalid: {exc}") from exc
    return tuple(pipes)


def _build_catchments(entries: Mapping[str, Any], label: str) -> tuple[Catchment, ...]:
    catchments: list[Catchment] = []
    for stable_key, value in entries.items():
        item_label = f"{label}[{stable_key!r}]"
        body = _object(value, item_label)
        _closed(body, _CATCHMENT_FIELDS, item_label)
        try:
            catchments.append(
                Catchment(
                    catchment_id=str(stable_key),
                    outlet_node_id=_text(body, "outlet_node_id", item_label),
                    area_m2=_number(body, "area_m2", item_label, required=True),
                    runoff_coefficient=_number(
                        body, "runoff_coefficient", item_label, default=1.0
                    ),
                    depression_storage_mm=_number(
                        body, "depression_storage_mm", item_label, default=0.0
                    ),
                )
            )
        except NetworkValidationError as exc:
            raise SolverInputError(f"{item_label} is invalid: {exc}") from exc
    return tuple(catchments)


@dataclass(frozen=True, slots=True)
class SolverNetworkDocument:
    """The ``network`` block: stable keys plus SI physical quantities."""

    nodes: tuple[HydraulicNode, ...]
    pipes: tuple[HydraulicPipe, ...]
    catchments: tuple[Catchment, ...]

    @classmethod
    def from_document(cls, document: Any) -> "SolverNetworkDocument":
        body = _object(document, "network")
        _closed(body, _NETWORK_FIELDS + _NETWORK_FEATURE_FIELDS, "network")
        schema = _text(body, "schema", "network")
        if schema != PYTHON_SOLVER_NETWORK_SCHEMA:
            raise SolverInputError(
                f"network.schema must be {PYTHON_SOLVER_NETWORK_SCHEMA!r}, "
                f"got {schema!r}"
            )
        node_entries = _object(body.get("nodes", {}), "network.nodes")
        pipe_entries = _object(body.get("pipes", {}), "network.pipes")
        catchment_entries = _object(body.get("catchments", {}), "network.catchments")
        if not node_entries:
            raise SolverInputError("network.nodes must not be empty")
        if not pipe_entries:
            raise SolverInputError("network.pipes must not be empty")

        state = _object(body.get("initial_state", {}), "network.initial_state")
        _closed(state, _INITIAL_STATE_FIELDS, "network.initial_state")
        if state:
            state_schema = _text(state, "schema", "network.initial_state")
            if state_schema != PYTHON_SOLVER_INITIAL_STATE_SCHEMA:
                raise SolverInputError(
                    "network.initial_state.schema must be "
                    f"{PYTHON_SOLVER_INITIAL_STATE_SCHEMA!r}, "
                    f"got {state_schema!r}"
                )
            state_kind = _text(state, "kind", "network.initial_state")
            if state_kind not in {"dry", "warm_state"}:
                raise SolverInputError(
                    "network.initial_state.kind must be dry or warm_state"
                )
        node_keys = [str(key) for key in node_entries]
        pipe_keys = [str(key) for key in pipe_entries]
        nodes = _build_nodes(
            node_entries,
            "network.nodes",
            _state_values(state, "node_depth_m", node_keys),
        )
        pipes = _build_pipes(
            pipe_entries,
            "network.pipes",
            _state_values(state, "pipe_flow_m3s", pipe_keys),
        )
        catchments = _build_catchments(catchment_entries, "network.catchments")
        try:
            network = HydraulicNetwork(nodes=nodes, pipes=pipes, catchments=catchments)
        except NetworkValidationError as exc:
            raise SolverInputError(f"network is not solvable: {exc}") from exc
        return cls(
            nodes=network.nodes,
            pipes=network.pipes,
            catchments=network.catchments,
        )

    def to_network(self) -> HydraulicNetwork:
        return HydraulicNetwork(
            nodes=self.nodes, pipes=self.pipes, catchments=self.catchments
        )


# ---------------------------------------------------------------------------
# rainfall document
# ---------------------------------------------------------------------------


def rainfall_content_sha256(
    intensities_mm_h: Sequence[float], step_seconds: float
) -> str:
    """Canonical rainfall hash: the series plus its native step, nothing else."""

    return hash_json(
        {
            "schema": "python-solver-rainfall-hash-v1",
            "step_seconds": float(step_seconds),
            "intensities_mm_h": [float(value) for value in intensities_mm_h],
        }
    )


@dataclass(frozen=True, slots=True)
class SolverRainfallDocument:
    """The ``rainfall`` block plus its self-declared content hash."""

    forcing: RainfallForcing
    content_sha256: str

    @classmethod
    def from_document(cls, document: Any) -> "SolverRainfallDocument":
        body = _object(document, "rainfall")
        _closed(
            body,
            ("schema", "step_seconds", "intensities_mm_h", "content_sha256"),
            "rainfall",
        )
        schema = _text(body, "schema", "rainfall")
        if schema != PYTHON_SOLVER_RAINFALL_SCHEMA:
            raise SolverInputError(
                f"rainfall.schema must be {PYTHON_SOLVER_RAINFALL_SCHEMA!r}, "
                f"got {schema!r}"
            )
        step_seconds = _number(body, "step_seconds", "rainfall", required=True)
        if step_seconds <= 0.0:
            raise SolverInputError("rainfall.step_seconds must be positive")
        raw = body.get("intensities_mm_h")
        if not isinstance(raw, (list, tuple)):
            raise SolverInputError("rainfall.intensities_mm_h must be an array")
        intensities: list[float] = []
        for index, item in enumerate(raw):
            label = f"rainfall.intensities_mm_h[{index}]"
            value = _finite_number(item, label)
            if value < 0.0:
                raise SolverInputError(f"{label} must be non-negative")
            intensities.append(value)
        try:
            forcing = RainfallForcing(
                intensities_mm_h=tuple(intensities), step_seconds=step_seconds
            )
        except NetworkValidationError as exc:
            raise SolverInputError(f"rainfall is invalid: {exc}") from exc
        declared = _digest(body, "content_sha256", "rainfall")
        observed = rainfall_content_sha256(
            forcing.intensities_mm_h, forcing.step_seconds
        )
        if declared != observed:
            raise SolverInputError(
                f"rainfall.content_sha256 {declared} does not match the "
                f"canonical rainfall hash {observed}"
            )
        return cls(forcing=forcing, content_sha256=observed)


# ---------------------------------------------------------------------------
# the request
# ---------------------------------------------------------------------------

_INPUT_FIELDS = (
    "schema",
    "solver_id",
    "run",
    "provenance",
    "time_grid",
    "solver_config",
    "network",
    "rainfall",
)
_RUN_FIELDS = (
    "simulation_id",
    "attempt_id",
    "attempt_no",
    "fencing_token",
    "network_id",
    "network_revision_id",
)
_PROVENANCE_TEXT_FIELDS = (
    "schema_version",
    "solver_config_schema_id",
    "solver_config_schema_version",
    "parameter_set_id",
    "parameter_schema_id",
    "parameter_schema_version",
    "transform_id",
    "solver_crs",
)
_PROVENANCE_DIGEST_FIELDS = (
    "network_revision_content_sha256",
    "snapshot_content_sha256",
    "initial_state_content_sha256",
    "target_manifest_sha256",
    "rainfall_content_sha256",
    "parameter_set_content_sha256",
    "parameter_schema_sha256",
)
_PROVENANCE_FIELDS = (
    _PROVENANCE_TEXT_FIELDS + _PROVENANCE_DIGEST_FIELDS + ("ponding_enabled",)
)
_TIME_GRID_FIELDS = (
    "start_at",
    "duration_seconds",
    "frame_interval_seconds",
    "expected_frame_count",
)
_CONFIG_FIELDS = (
    "duration_s",
    "reporting_interval_s",
    "rain_step_s",
    "max_courant",
    "max_substeps",
    "initial_time_step_s",
    "min_time_step_s",
    "depth_floor_m",
    "gravity_mps2",
    "rapid_reversal_fraction",
)


@dataclass(frozen=True, slots=True)
class PythonSolveRequest:
    """Everything one ``py_*`` run needs, already validated and hashed."""

    run: Mapping[str, Any]
    manifest: SolverInputManifest
    network: SolverNetworkDocument
    rainfall: SolverRainfallDocument
    config: SolverConfig
    routing_mode: RoutingMode

    @property
    def simulation_id(self) -> str:
        return str(self.run.get("simulation_id", ""))

    @property
    def attempt_id(self) -> str:
        return str(self.run.get("attempt_id", ""))

    @property
    def attempt_no(self) -> int:
        return int(self.run.get("attempt_no", 0))

    @property
    def fencing_token(self) -> int:
        return int(self.run.get("fencing_token", 0))

    @property
    def start_at(self) -> datetime:
        return self.manifest.start_at

    @property
    def frame_interval_seconds(self) -> int:
        return int(self.manifest.frame_interval_seconds)

    @property
    def expected_frame_count(self) -> int:
        return int(self.manifest.expected_frame_count)

    def frame_time(self, frame_index: int) -> datetime:
        return self.start_at + timedelta(
            seconds=self.frame_interval_seconds * int(frame_index)
        )

    def input_sha256(self) -> str:
        return self.manifest.input_sha256


def parse_solver_input(
    document: Any,
    *,
    solver_id: str,
    solver_version: str,
    routing_mode: RoutingMode,
) -> PythonSolveRequest:
    """Parse and cross-validate the frozen ``solver_input.json`` document.

    Every failure is an input error, never a silently defaulted run: an unknown
    key, a missing or malformed hash, a time grid that does not align, or a
    rainfall series that contradicts its own digest all raise.
    """

    body = _object(document, "solver_input")
    _closed(body, _INPUT_FIELDS, "solver_input")
    schema = _text(body, "schema", "solver_input")
    if schema != PYTHON_SOLVER_INPUT_SCHEMA:
        raise SolverInputError(
            f"solver_input.schema must be {PYTHON_SOLVER_INPUT_SCHEMA!r}, "
            f"got {schema!r}"
        )
    declared_solver_id = _text(body, "solver_id", "solver_input")
    if declared_solver_id != solver_id:
        raise SolverInputError(
            f"solver_input.solver_id is {declared_solver_id!r} but this solver "
            f"is {solver_id!r}; a frozen identity cannot change mid-run"
        )

    run = _object(body.get("run", {}), "solver_input.run")
    _closed(run, _RUN_FIELDS, "solver_input.run")
    _text(run, "simulation_id", "solver_input.run")
    _text(run, "attempt_id", "solver_input.run")
    _integer(run, "attempt_no", "solver_input.run")
    _integer(run, "fencing_token", "solver_input.run")

    provenance = _object(body.get("provenance", {}), "solver_input.provenance")
    _closed(provenance, _PROVENANCE_FIELDS, "solver_input.provenance")
    for key in _PROVENANCE_DIGEST_FIELDS:
        _digest(provenance, key, "solver_input.provenance")
    # Contract §1.2: the four material hashes are independent and MUST NOT
    # substitute for one another.  Reusing one digest for two of them would
    # make a changed network indistinguishable from a changed snapshot.
    material_keys = (
        "network_revision_content_sha256",
        "snapshot_content_sha256",
        "initial_state_content_sha256",
        "target_manifest_sha256",
    )
    material = {key: str(provenance[key]) for key in material_keys}
    if len(set(material.values())) != len(material):
        seen: set[str] = set()
        aliased = sorted(
            key
            for key, value in material.items()
            if value in seen or seen.add(value)  # type: ignore[func-returns-value]
        )
        raise SolverInputError(
            "solver_input.provenance reuses one digest for "
            f"{aliased}; the four material hashes are independent"
        )
    for key in _PROVENANCE_TEXT_FIELDS:
        _text(provenance, key, "solver_input.provenance")
    ponding_enabled = provenance.get("ponding_enabled", False)
    if not isinstance(ponding_enabled, bool):
        raise SolverInputError(
            "solver_input.provenance.ponding_enabled must be a boolean"
        )
    if ponding_enabled:
        raise SolverInputError(
            f"{solver_id} does not solve ponded surface storage; "
            "solver_input.provenance.ponding_enabled must be false"
        )

    grid = _object(body.get("time_grid", {}), "solver_input.time_grid")
    _closed(grid, _TIME_GRID_FIELDS, "solver_input.time_grid")
    start_at = _timestamp(grid.get("start_at"), "solver_input.time_grid.start_at")
    duration_seconds = _integer(grid, "duration_seconds", "solver_input.time_grid")
    frame_interval_seconds = _integer(
        grid, "frame_interval_seconds", "solver_input.time_grid"
    )
    if frame_interval_seconds > 300:
        raise SolverInputError(
            "solver_input.time_grid.frame_interval_seconds must be <= 300"
        )
    if duration_seconds % frame_interval_seconds != 0:
        raise SolverInputError(
            "solver_input.time_grid.duration_seconds must align to "
            "frame_interval_seconds"
        )
    expected_frame_count = _integer(
        grid, "expected_frame_count", "solver_input.time_grid"
    )
    if expected_frame_count != duration_seconds // frame_interval_seconds + 1:
        raise SolverInputError(
            "solver_input.time_grid.expected_frame_count must include both "
            "endpoints of the time grid"
        )

    config_document = _object(
        body.get("solver_config", {}), "solver_input.solver_config"
    )
    _closed(config_document, _CONFIG_FIELDS, "solver_input.solver_config")
    reporting_interval_s = _number(
        config_document,
        "reporting_interval_s",
        "solver_input.solver_config",
        required=True,
    )
    if not math.isclose(reporting_interval_s, float(frame_interval_seconds)):
        raise SolverInputError(
            "solver_input.solver_config.reporting_interval_s must equal "
            "time_grid.frame_interval_seconds; the frame grid is frozen"
        )
    expected_duration = (expected_frame_count - 1) * reporting_interval_s
    if not math.isclose(expected_duration, float(duration_seconds)):
        raise SolverInputError(
            "solver_input.solver_config.reporting_interval_s does not match "
            "the frozen time grid"
        )
    max_substeps = config_document.get("max_substeps", 20)
    if isinstance(max_substeps, bool) or not isinstance(max_substeps, int):
        raise SolverInputError(
            "solver_input.solver_config.max_substeps must be an integer"
        )
    initial_time_step_s = config_document.get("initial_time_step_s")
    if initial_time_step_s is not None:
        initial_time_step_s = _finite_number(
            initial_time_step_s, "solver_input.solver_config.initial_time_step_s"
        )
    try:
        config = SolverConfig(
            duration_s=float(duration_seconds),
            reporting_interval_s=reporting_interval_s,
            rain_step_s=_number(
                config_document,
                "rain_step_s",
                "solver_input.solver_config",
                required=True,
            ),
            max_courant=_number(
                config_document,
                "max_courant",
                "solver_input.solver_config",
                default=0.5,
            ),
            max_substeps=int(max_substeps),
            initial_time_step_s=initial_time_step_s,
            min_time_step_s=_number(
                config_document,
                "min_time_step_s",
                "solver_input.solver_config",
                default=1.0e-6,
            ),
            depth_floor_m=_number(
                config_document,
                "depth_floor_m",
                "solver_input.solver_config",
                default=1.0e-4,
            ),
            gravity_mps2=_number(
                config_document,
                "gravity_mps2",
                "solver_input.solver_config",
                default=9.80665,
            ),
            rapid_reversal_fraction=_number(
                config_document,
                "rapid_reversal_fraction",
                "solver_input.solver_config",
                default=0.05,
            ),
        )
    except ValueError as exc:
        raise SolverInputError(f"solver_input.solver_config is invalid: {exc}") from exc

    network = SolverNetworkDocument.from_document(body.get("network"))
    rainfall = SolverRainfallDocument.from_document(body.get("rainfall"))
    declared_rainfall = str(provenance["rainfall_content_sha256"])
    if declared_rainfall != rainfall.content_sha256:
        raise SolverInputError(
            "solver_input.provenance.rainfall_content_sha256 "
            f"{declared_rainfall} does not match the rainfall document hash "
            f"{rainfall.content_sha256}"
        )

    try:
        manifest = SolverInputManifest(
            schema_version=str(provenance["schema_version"]),
            network_revision_content_sha256=str(
                provenance["network_revision_content_sha256"]
            ),
            snapshot_content_sha256=str(provenance["snapshot_content_sha256"]),
            initial_state_content_sha256=str(
                provenance["initial_state_content_sha256"]
            ),
            target_manifest_sha256=str(provenance["target_manifest_sha256"]),
            rainfall_content_sha256=rainfall.content_sha256,
            solver_id=solver_id,
            solver_version=solver_version,
            solver_config_schema_id=str(provenance["solver_config_schema_id"]),
            solver_config_schema_version=str(
                provenance["solver_config_schema_version"]
            ),
            parameter_set_id=str(provenance["parameter_set_id"]),
            parameter_set_content_sha256=str(
                provenance["parameter_set_content_sha256"]
            ),
            parameter_schema_id=str(provenance["parameter_schema_id"]),
            parameter_schema_version=str(provenance["parameter_schema_version"]),
            parameter_schema_sha256=str(provenance["parameter_schema_sha256"]),
            solver_config=dict(config_document),
            transform_id=str(provenance["transform_id"]),
            solver_crs=str(provenance["solver_crs"]),
            start_at=start_at,
            duration_seconds=duration_seconds,
            frame_interval_seconds=frame_interval_seconds,
            expected_frame_count=expected_frame_count,
            ponding_enabled=False,
        )
    except (TypeError, ValueError) as exc:
        raise SolverInputError(
            f"solver_input.provenance does not satisfy the input manifest: {exc}"
        ) from exc

    return PythonSolveRequest(
        run=dict(run),
        manifest=manifest,
        network=network,
        rainfall=rainfall,
        config=config,
        routing_mode=routing_mode,
    )


def load_solver_input_bytes(payload: bytes) -> Any:
    """Decode ``solver_input.json`` with a hard 20 MiB untrusted-input cap."""

    if len(payload) > MAX_SOLVER_INPUT_BYTES:
        raise SolverInputError(
            f"solver input is {len(payload)} bytes; the untrusted input budget "
            f"is {MAX_SOLVER_INPUT_BYTES}"
        )
    try:
        return json.loads(payload.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SolverInputError("solver input is not UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise SolverInputError(f"solver input is not valid JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# streaming accumulator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FrameAccumulator:
    """Bounded per-run aggregates collected while frames stream past.

    The solver never retains the full frame history: it keeps one manifest
    entry per frame (index + payload hash, ~90 bytes) plus running maxima, so
    a 289-frame benchmark and a 5-node smoke test cost the same memory.
    """

    expected_frame_count: int
    frame_count: int = 0
    quality_status: QualityStatus = QualityStatus.VERIFIED
    frame_manifest: list[dict[str, Any]] = field(default_factory=list)
    max_node_depth_m: float = 0.0
    _node_depth_sum: float = 0.0
    _node_samples: int = 0
    max_pipe_fill_ratio: float = 0.0
    _pipe_fill_sum: float = 0.0
    _pipe_samples: int = 0
    peak_pipe_flow_m3s: float = 0.0

    def observe(self, frame: FrameResult) -> None:
        self.frame_count += 1
        if _QUALITY_ORDER[frame.quality_status] > _QUALITY_ORDER[self.quality_status]:
            self.quality_status = frame.quality_status
        for node in frame.nodes:
            self.max_node_depth_m = max(self.max_node_depth_m, node.depth_m)
            self._node_depth_sum += node.depth_m
            self._node_samples += 1
        for pipe in frame.pipes:
            self.max_pipe_fill_ratio = max(self.max_pipe_fill_ratio, pipe.fill_ratio)
            self._pipe_fill_sum += pipe.fill_ratio
            self._pipe_samples += 1
            self.peak_pipe_flow_m3s = max(self.peak_pipe_flow_m3s, abs(pipe.flow_m3s))

    def record(self, payload_sha256: str) -> None:
        self.frame_manifest.append(
            {"frame_index": self.frame_count - 1, "payload_sha256": payload_sha256}
        )

    @property
    def mean_node_depth_m(self) -> float:
        return self._node_depth_sum / self._node_samples if self._node_samples else 0.0

    @property
    def mean_pipe_fill_ratio(self) -> float:
        return self._pipe_fill_sum / self._pipe_samples if self._pipe_samples else 0.0

    @property
    def node_sample_count(self) -> int:
        return self._node_samples

    @property
    def pipe_sample_count(self) -> int:
        return self._pipe_samples


# ---------------------------------------------------------------------------
# frame / quality / metrics documents
# ---------------------------------------------------------------------------


def _frame_payload(
    frame: FrameResult,
    *,
    frame_index: int,
    solver_id: str,
    solver_version: str,
    valid_at: datetime,
) -> dict[str, Any]:
    depths = [node.depth_m for node in frame.nodes]
    fills = [pipe.fill_ratio for pipe in frame.pipes]
    return {
        "schema": PYTHON_FRAME_SCHEMA,
        "schema_version": PYTHON_FRAME_SCHEMA_VERSION,
        "solver_id": solver_id,
        "solver_version": solver_version,
        "frame_index": frame_index,
        "time_offset_s": round(float(frame.time_s), 6),
        "valid_at": _format_timestamp(valid_at),
        "node_count": len(frame.nodes),
        "pipe_count": len(frame.pipes),
        "max_node_depth_m": max(depths) if depths else 0.0,
        "mean_node_depth_m": (sum(depths) / len(depths)) if depths else 0.0,
        "max_pipe_fill_ratio": max(fills) if fills else 0.0,
        "mean_pipe_fill_ratio": (sum(fills) / len(fills)) if fills else 0.0,
        "surcharged_pipe_count": sum(
            1 for pipe in frame.pipes if pipe.fill_ratio >= 1.0
        ),
        "nodes": [node.to_dict() for node in frame.nodes],
        "pipes": [pipe.to_dict() for pipe in frame.pipes],
        "mass_residual_m3": float(frame.mass_residual_m3),
        "mass_residual_relative": float(frame.mass_residual_relative),
        "min_time_step_s": (
            None if frame.min_time_step_s is None else float(frame.min_time_step_s)
        ),
        "max_courant": float(frame.max_courant),
        "substep_count": int(frame.substep_count),
        "quality_status": frame.quality_status.value,
    }


def build_frame_document(
    frame: FrameResult,
    request: PythonSolveRequest,
    *,
    frame_index: int,
    solver_version: str,
) -> dict[str, Any]:
    """Return the canonical frame document, its bytes, and its SHA-256.

    ``frame_index`` is assigned by the caller *before* the frame is observed,
    so the dense 0..N-1 grid of the frozen manifest is what the payload
    carries, not whatever the accumulator happens to hold afterwards.
    """

    payload = _frame_payload(
        frame,
        frame_index=frame_index,
        solver_id=request.manifest.solver_id,
        solver_version=solver_version,
        valid_at=request.frame_time(frame_index),
    )
    text = canonical_json(payload)
    return {
        "frame_index": frame_index,
        "document": payload,
        "canonical_json": text,
        "payload_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def degraded_features(result: SimulationResult) -> tuple[str, ...]:
    """The honest list of what this reduced-fidelity solver did not model."""

    degraded: list[str] = []
    if result.stability.rapid_reversals:
        degraded.append("rapid_flow_reversal")
    if result.stability.unsupported_regime_events:
        degraded.append("pressure_or_supercritical_regime")
    return tuple(degraded)


def build_quality_document(
    result: SimulationResult,
    request: PythonSolveRequest,
    accumulator: FrameAccumulator,
    *,
    solver_version: str,
) -> dict[str, Any]:
    """The quality evidence: mass ledger, stability, and the residual verdict."""

    ledger = result.mass_balance
    return {
        "schema": PYTHON_QUALITY_SCHEMA,
        "solver_id": request.manifest.solver_id,
        "solver_version": solver_version,
        "model_fidelity": result.model_fidelity,
        "is_authoritative": False,
        "routing_mode": result.routing_mode.value,
        "result_status": accumulator.quality_status.value,
        "frame_count": accumulator.frame_count,
        "verified_relative_residual": 1.0e-4,
        "warning_relative_residual": 1.0e-3,
        "mass_balance": ledger.to_dict(),
        "stability": result.stability.to_dict(),
        "unsupported_or_degraded_features": list(degraded_features(result)),
    }


def build_metrics_document(
    result: SimulationResult,
    request: PythonSolveRequest,
    accumulator: FrameAccumulator,
    *,
    solver_version: str,
) -> dict[str, Any]:
    """Hydraulic rollups consumed by results, calibration, and the API layer."""

    ledger = result.mass_balance
    return {
        "schema": PYTHON_METRICS_SCHEMA,
        "solver_id": request.manifest.solver_id,
        "solver_version": solver_version,
        "model_fidelity": result.model_fidelity,
        "frame_count": accumulator.frame_count,
        "node_sample_count": accumulator.node_sample_count,
        "pipe_sample_count": accumulator.pipe_sample_count,
        "max_node_depth_m": round(accumulator.max_node_depth_m, 6),
        "mean_node_depth_m": round(accumulator.mean_node_depth_m, 6),
        "max_pipe_fill_ratio": round(accumulator.max_pipe_fill_ratio, 6),
        "mean_pipe_fill_ratio": round(accumulator.mean_pipe_fill_ratio, 6),
        "peak_pipe_flow_m3s": round(accumulator.peak_pipe_flow_m3s, 6),
        "total_rainfall_input_m3": round(ledger.rainfall_input_m3, 6),
        "total_infiltration_loss_m3": round(ledger.infiltration_loss_m3, 6),
        "total_outfall_discharge_m3": round(ledger.outfall_discharge_m3, 6),
        "total_overflow_m3": round(ledger.overflow_m3, 6),
        "final_storage_m3": round(
            ledger.final_storage_m3 + ledger.final_pipe_storage_m3, 6
        ),
        "max_mass_residual_relative": round(ledger.residual_relative, 12),
        "min_time_step_s": (
            None
            if result.stability.min_time_step_s is None
            else round(result.stability.min_time_step_s, 9)
        ),
        "max_courant": round(result.stability.max_courant, 9),
        "max_substeps_per_report": int(result.stability.max_substeps),
        "result_status": accumulator.quality_status.value,
    }


def _solving_percent(published: int, expected: int) -> int:
    """Map the frame cursor onto the §6.2 ``5..79`` solving band."""

    if expected <= 0:
        return _SOLVING_PROGRESS_END
    ratio = max(0.0, min(1.0, published / float(expected)))
    span = _SOLVING_PROGRESS_END - _SOLVING_PROGRESS_START
    return _SOLVING_PROGRESS_START + int(ratio * span)


# ---------------------------------------------------------------------------
# the solver adapter
# ---------------------------------------------------------------------------


class PythonRoutingSolver:
    """Shared adapter for the ``py_mw`` / ``py_dw`` reduced-fidelity solvers.

    Subclasses fix the frozen identity (:attr:`solver_id`, :attr:`version`,
    :attr:`routing_mode`) and the capability declaration.  Everything else —
    the input parse, the streaming loop, the progress bands, the error
    mapping, and the summary — is shared, which is what keeps their frame
    schema identical while their physics stay apart.
    """

    solver_id: str = "py_mw"
    version: str = "py_mw/1.0.0"
    routing_mode: RoutingMode = RoutingMode.MOTION_WAVE
    model_fidelity: str = "fast_screening"
    routing_modes: tuple[str, ...] = ("motion_wave",)
    supported_features: frozenset[str] = frozenset(
        {"gravity", "routing", "looped_networks"}
    )
    #: Features a network may demand that this identity refuses outright.  A
    #: refusal is terminal: an explicit selection must not degrade.
    rejected_features: frozenset[str] = frozenset()
    max_substeps_per_report: int = 20

    def __init__(self, *, version: str | None = None) -> None:
        if version is not None:
            self.version = str(version)

    # -- identity ---------------------------------------------------------
    def capabilities(self) -> SolverCapabilities:
        return SolverCapabilities(
            solver_id=self.solver_id,
            version=self.version,
            model_fidelity=self.model_fidelity,
            supported_features=self.supported_features,
            routing_modes=self.routing_modes,
            supports_ponding=False,
            validated_scale=None,
            max_substeps_per_report=self.max_substeps_per_report,
            authoritative=False,
        )

    async def probe(self) -> SolverProbe:
        """Report availability, which is a fact about the interpreter.

        There is no external binary, no native kernel, and no configuration
        dependency, so the probe answers without touching the filesystem.  A
        frozen ``py_*`` identity is therefore always available on any CPython
        that can import this module.
        """

        try:
            capabilities = self.capabilities()
        except ValueError as exc:  # pragma: no cover - defensive
            return SolverProbe(
                solver_id=self.solver_id,
                version=self.version,
                available=False,
                reason="invalid_capabilities",
                details={"error": str(exc)},
            )
        return SolverProbe(
            solver_id=self.solver_id,
            version=self.version,
            available=True,
            reason="pure_python",
            capabilities=capabilities,
            details={
                "engine": "app.services.hydraulic.solvers.python_hydraulics",
                "native_dependencies": [],
                "rejected_features": sorted(self.rejected_features),
            },
        )

    # -- validation -------------------------------------------------------
    def validate(self, snapshot: Any, request: Any = None) -> ValidationReport:
        """Validate a network document before a run is queued or accepted."""

        issues: list[ValidationIssue] = []
        # A feature this identity refuses is reported first and independently
        # of whether the rest of the document parses: "this solver will not do
        # that" is a different answer from "this document is malformed", and a
        # caller must be able to tell them apart.
        for feature in sorted(self.rejected_features & _required_features(snapshot)):
            issues.append(
                ValidationIssue(
                    code=SolverErrorCode.UNSUPPORTED_FEATURE.value,
                    field=feature,
                    message=(
                        f"{self.solver_id} does not accept {feature}; an "
                        "explicit selection must not degrade to another solver"
                    ),
                )
            )
        network: HydraulicNetwork | None = None
        try:
            network = self._coerce_network(snapshot)
        except SolverInputError as exc:
            issues.append(
                ValidationIssue(
                    code=SolverErrorCode.SOLVER_INPUT_INVALID.value,
                    field="network",
                    message=str(exc),
                )
            )
        if network is not None:
            issues.extend(self._regime_issues(network, snapshot))
        return ValidationReport(
            valid=not issues,
            issues=tuple(issues),
            unsupported_features=tuple(
                str(issue.field)
                for issue in issues
                if issue.code == SolverErrorCode.UNSUPPORTED_FEATURE.value
                and issue.field
            ),
            details={
                "solver_id": self.solver_id,
                "solver_version": self.version,
                "model_fidelity": self.model_fidelity,
                "node_count": len(network.nodes) if network else 0,
                "pipe_count": len(network.pipes) if network else 0,
                "catchment_count": len(network.catchments) if network else 0,
            },
        )

    def _regime_issues(
        self, network: HydraulicNetwork, snapshot: Any
    ) -> list[ValidationIssue]:
        """Static regime screening; the dynamic guard runs during the solve."""

        return []

    def _coerce_network(self, snapshot: Any) -> HydraulicNetwork:
        if isinstance(snapshot, HydraulicNetwork):
            return snapshot
        if snapshot is None:
            raise SolverInputError("a network snapshot is required")
        return SolverNetworkDocument.from_document(snapshot).to_network()

    # -- execution --------------------------------------------------------
    async def run(
        self,
        request: Any,
        snapshot: Any,
        frame_sink: FrameSink,
        control: RunControl,
    ) -> SolveSummary:
        """Solve, stream every frame, then finalize the attempt-scoped sink."""

        solve_request = self._coerce_request(request, snapshot)
        control.check()
        progress = ProgressTracker(control.progress_cb)
        await progress.update(0, SolverPhase.accepted)
        await progress.update(_SOLVING_PROGRESS_START, SolverPhase.validation)

        network = solve_request.network.to_network()
        if not network.nodes or not network.pipes:
            raise SolverInputError("network has no solvable nodes or pipes")
        report = self.validate(network)
        if not report.valid:
            blocking = [issue for issue in report.errors]
            if blocking:
                raise SolverValidationError(
                    f"{self.solver_id} cannot accept this network: "
                    f"{blocking[0].message}",
                    solver_id=self.solver_id,
                    details={"validation": report.to_dict()},
                )
        await progress.update(_SOLVING_PROGRESS_START + 1, SolverPhase.solving)

        accumulator = FrameAccumulator(
            expected_frame_count=solve_request.expected_frame_count
        )
        loop = asyncio.get_running_loop()

        async def publish(frame: FrameResult) -> None:
            control.check()
            if accumulator.frame_count >= accumulator.expected_frame_count:
                raise SolverResultError(
                    f"{self.solver_id} produced frame {accumulator.frame_count} "
                    "beyond the frozen grid of "
                    f"{accumulator.expected_frame_count} frames"
                )
            frame_index = accumulator.frame_count
            accumulator.observe(frame)
            document = build_frame_document(
                frame,
                solve_request,
                frame_index=frame_index,
                solver_version=self.version,
            )
            accumulator.record(document["payload_sha256"])
            await frame_sink.write(
                SimulationFrame(
                    attempt_id=solve_request.attempt_id,
                    simulation_id=solve_request.simulation_id,
                    frame_index=document["frame_index"],
                    valid_at=solve_request.frame_time(document["frame_index"]),
                    payload=document["document"],
                    solver_id=self.solver_id,
                    solver_version=self.version,
                    payload_sha256=document["payload_sha256"],
                    target_manifest_sha256=(
                        solve_request.manifest.target_manifest_sha256
                    ),
                    quality_status=frame.quality_status.value,
                )
            )
            await progress.update(
                _solving_percent(
                    accumulator.frame_count, solve_request.expected_frame_count
                ),
                SolverPhase.solving,
            )

        def on_frame(frame: FrameResult) -> None:
            # ``solve_network`` runs on a worker thread; hand each frame to the
            # event loop and block until the sink has accepted it, so the
            # attempt staging order and the progress order stay identical.
            asyncio.run_coroutine_threadsafe(publish(frame), loop).result()

        result = await self._run_kernel(network, solve_request, on_frame)

        if accumulator.frame_count != solve_request.expected_frame_count:
            raise SolverResultError(
                f"{self.solver_id} produced {accumulator.frame_count} frames but "
                f"the frozen grid has {solve_request.expected_frame_count}"
            )

        quality = build_quality_document(
            result, solve_request, accumulator, solver_version=self.version
        )
        metrics = build_metrics_document(
            result, solve_request, accumulator, solver_version=self.version
        )
        if accumulator.quality_status is QualityStatus.FAILED:
            raise SolverResultError(
                "mass balance residual exceeded the py_* failure gate "
                f"({ledger_relative(result)} > 1e-3)",
                solver_id=self.solver_id,
                details={"quality": quality},
            )
        await progress.update(_QUALITY_PROGRESS_END, SolverPhase.parsing)
        await progress.update(_PERSISTING_PROGRESS, SolverPhase.persisting)

        summary = self._build_summary(
            result,
            solve_request,
            accumulator,
            quality=quality,
            metrics=metrics,
        )
        await frame_sink.finalize(summary)
        await progress.update(_DONE_PROGRESS, SolverPhase.completed)
        return summary

    def _coerce_request(self, request: Any, snapshot: Any) -> PythonSolveRequest:
        if isinstance(request, PythonSolveRequest):
            return request
        document: Any = request
        if isinstance(document, (bytes, bytearray)):
            document = load_solver_input_bytes(bytes(document))
        if isinstance(document, Mapping) and snapshot is not None:
            if "network" not in document and not isinstance(snapshot, HydraulicNetwork):
                merged = dict(document)
                merged["network"] = snapshot
                document = merged
        return parse_solver_input(
            document,
            solver_id=self.solver_id,
            solver_version=self.version,
            routing_mode=self.routing_mode,
        )

    async def _run_kernel(
        self,
        network: HydraulicNetwork,
        request: PythonSolveRequest,
        on_frame: Any,
    ) -> SimulationResult:
        try:
            return await asyncio.to_thread(
                solve_network,
                network,
                request.rainfall.forcing,
                routing_mode=self.routing_mode,
                config=request.config,
                frame_callback=on_frame,
                retain_frames=False,
            )
        except (SolverCancelledError, SolverTimeoutError, SolverContractError):
            raise
        except NetworkValidationError as exc:
            raise SolverValidationError(
                f"network cannot be solved by {self.solver_id}: {exc}",
                solver_id=self.solver_id,
            ) from exc
        except UnsupportedRegimeError as exc:
            raise SolverExecutionError(
                f"{self.solver_id} left its supported regime: {exc}",
                code=SolverErrorCode.UNSUPPORTED_FEATURE.value,
                solver_id=self.solver_id,
                retryable=False,
            ) from exc
        except NumericalStabilityError as exc:
            raise SolverExecutionError(
                f"{self.solver_id} numerical stability limit reached: {exc}",
                code=SolverErrorCode.SOLVER_EXECUTION_FAILED.value,
                solver_id=self.solver_id,
                retryable=True,
                details={"reason": "numerical_stability_limit"},
            ) from exc
        except MassBalanceError as exc:
            raise SolverResultError(
                f"{self.solver_id} mass balance failed: {exc}",
                solver_id=self.solver_id,
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive mapping
            raise SolverExecutionError(
                f"{self.solver_id} failed: {type(exc).__name__}",
                code=SolverErrorCode.SOLVER_EXECUTION_FAILED.value,
                solver_id=self.solver_id,
                retryable=True,
            ) from exc

    def _build_summary(
        self,
        result: SimulationResult,
        request: PythonSolveRequest,
        accumulator: FrameAccumulator,
        *,
        quality: Mapping[str, Any],
        metrics: Mapping[str, Any],
    ) -> SolveSummary:
        ledger = result.mass_balance
        return SolveSummary(
            solver_id=self.solver_id,
            solver_version=self.version,
            model_fidelity=result.model_fidelity,
            input_sha256=request.input_sha256(),
            network_content_hash=request.manifest.network_revision_content_sha256,
            snapshot_content_hash=request.manifest.snapshot_content_sha256,
            initial_state_hash=request.manifest.initial_state_content_sha256,
            target_manifest_hash=request.manifest.target_manifest_sha256,
            rainfall_content_hash=request.manifest.rainfall_content_sha256,
            solver_config_hash=request.manifest.solver_config_hash,
            frame_count=accumulator.frame_count,
            first_at=request.frame_time(0),
            last_at=request.frame_time(max(accumulator.frame_count - 1, 0)),
            total_mass_residual_m3=ledger.residual_m3,
            max_mass_residual_relative=ledger.residual_relative,
            min_time_step_s=result.stability.min_time_step_s,
            max_courant=result.stability.max_courant,
            total_input_m3=ledger.rainfall_input_m3,
            final_storage_m3=(ledger.final_storage_m3 + ledger.final_pipe_storage_m3),
            outfall_m3=ledger.outfall_discharge_m3,
            infiltration_m3=ledger.infiltration_loss_m3,
            overflow_m3=ledger.overflow_m3,
            unsupported_or_degraded_features=degraded_features(result),
            result_status=accumulator.quality_status.value,
            diagnostics={
                "routing_mode": result.routing_mode.value,
                "max_substeps_per_report": int(result.stability.max_substeps),
                "quality": dict(quality),
                "metrics": dict(metrics),
                "frame_manifest": list(accumulator.frame_manifest),
            },
        )


def _required_features(snapshot: Any) -> frozenset[str]:
    if isinstance(snapshot, Mapping):
        raw = snapshot.get("required_features", ())
        if isinstance(raw, (list, tuple)):
            return frozenset(str(item) for item in raw)
    return frozenset()


def ledger_relative(result: SimulationResult) -> float:
    return result.mass_balance.residual_relative


class PythonMotionWaveSolver(PythonRoutingSolver):
    """``py_mw``: node continuity + Manning pipe relation with surcharging.

    Reduced fidelity by construction.  It supports tree and looped pipe sets,
    a capacity-limited pressure-flow approximation, and node/pipe storage, and
    it explicitly does **not** resolve water hammer.
    """

    solver_id = "py_mw"
    version = "py_mw/1.0.0"
    routing_mode = RoutingMode.MOTION_WAVE
    model_fidelity = "fast_screening"
    routing_modes = ("motion_wave",)
    supported_features = frozenset(
        {
            "gravity",
            "routing",
            "looped_networks",
            "surcharge_approximation",
            "capacity_limited_pressure_flow",
        }
    )


def make_motion_wave_solver(version: str | None = None) -> PythonMotionWaveSolver:
    """Build a fresh ``py_mw`` instance; the registry owns long-lived copies."""

    return PythonMotionWaveSolver(version=version)


__all__ = [
    "MAX_SOLVER_INPUT_BYTES",
    "PYTHON_FRAME_SCHEMA",
    "PYTHON_FRAME_SCHEMA_VERSION",
    "PYTHON_METRICS_SCHEMA",
    "PYTHON_QUALITY_SCHEMA",
    "PYTHON_SOLVER_INITIAL_STATE_SCHEMA",
    "PYTHON_SOLVER_INPUT_SCHEMA",
    "PYTHON_SOLVER_NETWORK_SCHEMA",
    "PYTHON_SOLVER_RAINFALL_SCHEMA",
    "FrameAccumulator",
    "PythonMotionWaveSolver",
    "PythonRoutingSolver",
    "PythonSolveRequest",
    "SolverNetworkDocument",
    "SolverRainfallDocument",
    "build_frame_document",
    "build_metrics_document",
    "build_quality_document",
    "degraded_features",
    "load_solver_input_bytes",
    "make_motion_wave_solver",
    "parse_solver_input",
    "rainfall_content_sha256",
]
