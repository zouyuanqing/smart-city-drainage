"""Deterministic pure-Python 1D sewer fallback hydraulics.

The solver is intentionally independent from FastAPI, SQLAlchemy, SWMM, and
filesystem access.  It provides a reduced-fidelity kinetic screening model:

* ``motion_wave`` uses a pipe-slope Manning target with a node-water-surface
  correction and a capacity-limited Manning surcharge approximation;
* ``diffusion_wave`` uses the actual node water-surface difference in a
  Manning resistance relation and rejects supercritical or pressure flow.

Both modes route every pipe in the same simultaneous conservation step, so
parallel and looped pipe sets are not silently reduced to a spanning tree.
Node storage, catchment losses, outfall discharge, and overflow are tracked in
one explicit mass ledger.  The result is suitable for deterministic screening
and tests, not for water-hammer analysis or engineering certification.
"""

from __future__ import annotations

import asyncio
import bisect
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

GRAVITY_MPS2 = 9.80665
DEPTH_FLOOR_M = 1.0e-4
MAX_REPORTING_INTERVAL_S = 300.0
MAX_SIMULATION_DURATION_S = 72.0 * 60.0 * 60.0
MAX_SUBSTEPS_PER_REPORT = 20
TIME_EPSILON_S = 1.0e-9
MIN_FLOOR_SLOPE = 1.0e-6
OUTFALL_DISCHARGE_COEFFICIENT = 1.6
VERIFIED_RELATIVE_RESIDUAL = 1.0e-4
WARNING_RELATIVE_RESIDUAL = 1.0e-3


class RoutingMode(str, Enum):
    MOTION_WAVE = "motion_wave"
    DIFFUSION_WAVE = "diffusion_wave"

    @property
    def solver_id(self) -> str:
        return "py_mw" if self is RoutingMode.MOTION_WAVE else "py_dw"


class FlowDirection(str, Enum):
    NONE = "none"
    DOWNSTREAM = "downstream"
    UPSTREAM = "upstream"


class PipeState(str, Enum):
    DRY = "dry"
    OPEN_CHANNEL = "open_channel"
    PRESSURIZED = "pressurized"
    SURCHARGED = "surcharged"


class NodeState(str, Enum):
    DRY = "dry"
    SURFACE_FLOW = "surface_flow"
    SURCHARGED = "surcharged"
    DISCONNECTED = "disconnected"


class QualityStatus(str, Enum):
    VERIFIED = "verified"
    WARNING = "warning"
    FAILED = "failed"


class HydraulicSolverError(RuntimeError):
    """Base class for deterministic fallback-solver failures."""


class NetworkValidationError(HydraulicSolverError, ValueError):
    """Raised before integration when a typed network is not solvable."""


class MassBalanceError(HydraulicSolverError):
    """Raised when the explicit product mass ledger exceeds its gate."""


class NumericalStabilityError(HydraulicSolverError):
    """Raised when a report interval cannot satisfy the frozen CFL/substep cap."""


class UnsupportedRegimeError(HydraulicSolverError):
    """Raised when diffusion-wave routing leaves its supported regime."""


def _finite(value: float, field_name: str) -> float:
    if not math.isfinite(value):
        raise NetworkValidationError(f"{field_name} must be finite")
    return value


def _coerce_mode(value: RoutingMode | str) -> RoutingMode:
    if isinstance(value, RoutingMode):
        return value
    aliases = {
        "mw": RoutingMode.MOTION_WAVE,
        "py_mw": RoutingMode.MOTION_WAVE,
        "motion-wave": RoutingMode.MOTION_WAVE,
        "dw": RoutingMode.DIFFUSION_WAVE,
        "py_dw": RoutingMode.DIFFUSION_WAVE,
        "diffusion-wave": RoutingMode.DIFFUSION_WAVE,
    }
    try:
        return aliases.get(value, RoutingMode(value))
    except (TypeError, ValueError) as error:
        raise NetworkValidationError(f"unsupported routing mode: {value!r}") from error


@dataclass(frozen=True, slots=True)
class HydraulicNode:
    """Minimum node state consumed by the pure-Python solver."""

    node_id: str
    invert_elevation_m: float
    max_depth_m: float
    initial_depth_m: float = 0.0
    surface_area_m2: float = 0.1
    kind: str = "junction"
    outfall_width_m: float = 1.0

    def __post_init__(self) -> None:
        if not self.node_id or not self.node_id.strip():
            raise NetworkValidationError("node_id must be non-empty")
        _finite(self.invert_elevation_m, "invert_elevation_m")
        if not math.isfinite(self.max_depth_m) or self.max_depth_m <= 0.0:
            raise NetworkValidationError("max_depth_m must be finite and positive")
        if (
            not math.isfinite(self.initial_depth_m)
            or self.initial_depth_m < 0.0
            or self.initial_depth_m > self.max_depth_m
        ):
            raise NetworkValidationError(
                "initial_depth_m must be finite and between zero and max_depth_m"
            )
        if not math.isfinite(self.surface_area_m2) or self.surface_area_m2 <= 0.0:
            raise NetworkValidationError("surface_area_m2 must be finite and positive")
        if not math.isfinite(self.outfall_width_m) or self.outfall_width_m <= 0.0:
            raise NetworkValidationError("outfall_width_m must be finite and positive")
        if not self.kind.strip():
            raise NetworkValidationError("node kind must be non-empty")

    @property
    def is_outfall(self) -> bool:
        return self.kind.strip().lower() == "outfall"


@dataclass(frozen=True, slots=True)
class HydraulicPipe:
    """Circular or rectangular pipe with directed inverts and SI units."""

    pipe_id: str
    upstream_node_id: str
    downstream_node_id: str
    length_m: float
    manning_n: float
    upstream_invert_m: float
    downstream_invert_m: float
    diameter_m: float | None = None
    width_m: float | None = None
    height_m: float | None = None
    shape: str = "circular"
    initial_flow_m3s: float = 0.0
    upstream_crown_m: float | None = None
    downstream_crown_m: float | None = None

    def __post_init__(self) -> None:
        if not self.pipe_id or not self.pipe_id.strip():
            raise NetworkValidationError("pipe_id must be non-empty")
        if self.upstream_node_id == self.downstream_node_id:
            raise NetworkValidationError("pipe endpoints must be distinct")
        for name, value in (
            ("length_m", self.length_m),
            ("manning_n", self.manning_n),
            ("upstream_invert_m", self.upstream_invert_m),
            ("downstream_invert_m", self.downstream_invert_m),
            ("initial_flow_m3s", self.initial_flow_m3s),
        ):
            _finite(value, name)
        if self.length_m <= 0.0:
            raise NetworkValidationError("length_m must be positive")
        if self.manning_n <= 0.0:
            raise NetworkValidationError("manning_n must be positive")
        if self.upstream_invert_m + 1.0e-9 < self.downstream_invert_m:
            raise NetworkValidationError("pipe reverse slope is not supported")
        shape = self.shape.strip().lower()
        if shape == "circular":
            if (
                self.diameter_m is None
                or not math.isfinite(self.diameter_m)
                or self.diameter_m <= 0.0
            ):
                raise NetworkValidationError(
                    "circular diameter_m must be finite and positive"
                )
        elif shape in {"rectangular", "box"}:
            if (
                self.width_m is None
                or self.height_m is None
                or not math.isfinite(self.width_m)
                or not math.isfinite(self.height_m)
                or self.width_m <= 0.0
                or self.height_m <= 0.0
            ):
                raise NetworkValidationError(
                    "rectangular width_m and height_m must be finite and positive"
                )
        else:
            raise NetworkValidationError(f"unsupported pipe shape: {self.shape!r}")
        for name, crown_value in (
            ("upstream_crown_m", self.upstream_crown_m),
            ("downstream_crown_m", self.downstream_crown_m),
        ):
            if crown_value is not None and not math.isfinite(crown_value):
                raise NetworkValidationError(f"{name} must be finite")

    @property
    def full_height_m(self) -> float:
        if self.shape.strip().lower() == "circular":
            assert self.diameter_m is not None
            return self.diameter_m
        assert self.height_m is not None
        return self.height_m

    @property
    def slope(self) -> float:
        drop = self.upstream_invert_m - self.downstream_invert_m
        return max(drop / self.length_m, MIN_FLOOR_SLOPE)


@dataclass(frozen=True, slots=True)
class Catchment:
    """Area-weighted runoff source attached to one network node."""

    catchment_id: str
    outlet_node_id: str
    area_m2: float
    runoff_coefficient: float = 1.0
    depression_storage_mm: float = 0.0

    def __post_init__(self) -> None:
        if not self.catchment_id or not self.catchment_id.strip():
            raise NetworkValidationError("catchment_id must be non-empty")
        if not math.isfinite(self.area_m2) or self.area_m2 <= 0.0:
            raise NetworkValidationError(
                "catchment area_m2 must be finite and positive"
            )
        if (
            not math.isfinite(self.runoff_coefficient)
            or not 0.0 <= self.runoff_coefficient <= 1.0
        ):
            raise NetworkValidationError("runoff_coefficient must be between 0 and 1")
        if (
            not math.isfinite(self.depression_storage_mm)
            or self.depression_storage_mm < 0.0
        ):
            raise NetworkValidationError(
                "depression_storage_mm must be finite and non-negative"
            )


@dataclass(frozen=True, slots=True)
class HydraulicNetwork:
    """Immutable, stable-key network accepted by :func:`solve_network`."""

    nodes: tuple[HydraulicNode, ...]
    pipes: tuple[HydraulicPipe, ...]
    catchments: tuple[Catchment, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "pipes", tuple(self.pipes))
        object.__setattr__(self, "catchments", tuple(self.catchments))
        node_ids = [node.node_id for node in self.nodes]
        pipe_ids = [pipe.pipe_id for pipe in self.pipes]
        catchment_ids = [item.catchment_id for item in self.catchments]
        if len(set(node_ids)) != len(node_ids):
            raise NetworkValidationError("node_id values must be unique")
        if len(set(pipe_ids)) != len(pipe_ids):
            raise NetworkValidationError("pipe_id values must be unique")
        if len(set(catchment_ids)) != len(catchment_ids):
            raise NetworkValidationError("catchment_id values must be unique")
        node_set = set(node_ids)
        for pipe in self.pipes:
            missing = {
                pipe.upstream_node_id,
                pipe.downstream_node_id,
            }.difference(node_set)
            if missing:
                raise NetworkValidationError(
                    f"pipe {pipe.pipe_id} references missing nodes: {sorted(missing)}"
                )
        for catchment in self.catchments:
            if catchment.outlet_node_id not in node_set:
                raise NetworkValidationError(
                    f"catchment {catchment.catchment_id} references missing outlet "
                    f"{catchment.outlet_node_id}"
                )

    @property
    def node_by_id(self) -> dict[str, HydraulicNode]:
        return {node.node_id: node for node in self.nodes}


@dataclass(frozen=True, slots=True)
class RainfallForcing:
    """Interval-start rainfall intensities in mm/h.

    An empty intensity tuple is valid and represents an entirely dry run.
    Once the declared sequence is exhausted, the remaining duration is dry.
    """

    intensities_mm_h: tuple[float, ...]
    step_seconds: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "intensities_mm_h", tuple(self.intensities_mm_h))
        if not math.isfinite(self.step_seconds) or self.step_seconds <= 0.0:
            raise NetworkValidationError(
                "rain step_seconds must be finite and positive"
            )
        for value in self.intensities_mm_h:
            if not math.isfinite(value) or value < 0.0:
                raise NetworkValidationError(
                    "rainfall intensities must be finite and non-negative"
                )

    def intensity_at(self, time_s: float) -> float:
        if not self.intensities_mm_h:
            return 0.0
        index = int(math.floor((time_s + TIME_EPSILON_S) / self.step_seconds))
        if index < 0 or index >= len(self.intensities_mm_h):
            return 0.0
        return self.intensities_mm_h[index]


@dataclass(frozen=True, slots=True)
class SolverConfig:
    duration_s: float
    reporting_interval_s: float
    rain_step_s: float
    max_courant: float = 0.5
    max_substeps: int = MAX_SUBSTEPS_PER_REPORT
    initial_time_step_s: float | None = None
    min_time_step_s: float = 1.0e-6
    depth_floor_m: float = DEPTH_FLOOR_M
    gravity_mps2: float = GRAVITY_MPS2
    rapid_reversal_fraction: float = 0.05

    def __post_init__(self) -> None:
        for name, value in (
            ("duration_s", self.duration_s),
            ("reporting_interval_s", self.reporting_interval_s),
            ("rain_step_s", self.rain_step_s),
            ("max_courant", self.max_courant),
            ("min_time_step_s", self.min_time_step_s),
            ("depth_floor_m", self.depth_floor_m),
            ("gravity_mps2", self.gravity_mps2),
            ("rapid_reversal_fraction", self.rapid_reversal_fraction),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if not 0.0 < self.duration_s <= MAX_SIMULATION_DURATION_S:
            raise ValueError("duration_s must be in (0, 72 hours]")
        if not 0.0 < self.reporting_interval_s <= MAX_REPORTING_INTERVAL_S:
            raise ValueError("reporting_interval_s must be in (0, 300]")
        quotient = self.duration_s / self.reporting_interval_s
        if not math.isclose(quotient, round(quotient), rel_tol=0.0, abs_tol=1.0e-9):
            raise ValueError("duration_s must align to reporting_interval_s")
        if self.rain_step_s <= 0.0:
            raise ValueError("rain_step_s must be positive")
        if not 0.0 < self.max_courant <= 0.5:
            raise ValueError("max_courant must be in (0, 0.5]")
        if (
            isinstance(self.max_substeps, bool)
            or not isinstance(self.max_substeps, int)
            or not 1 <= self.max_substeps <= MAX_SUBSTEPS_PER_REPORT
        ):
            raise ValueError("max_substeps must be an integer in [1, 20]")
        if self.min_time_step_s <= 0.0:
            raise ValueError("min_time_step_s must be positive")
        if self.depth_floor_m <= 0.0:
            raise ValueError("depth_floor_m must be positive")
        if self.gravity_mps2 <= 0.0:
            raise ValueError("gravity_mps2 must be positive")
        if not 0.0 <= self.rapid_reversal_fraction <= 1.0:
            raise ValueError("rapid_reversal_fraction must be between 0 and 1")
        if self.initial_time_step_s is not None and (
            not math.isfinite(self.initial_time_step_s)
            or self.initial_time_step_s <= 0.0
        ):
            raise ValueError("initial_time_step_s must be finite and positive")

    @property
    def initial_dt(self) -> float:
        return min(self.rain_step_s, self.reporting_interval_s, 60.0, self.duration_s)


@dataclass(frozen=True, slots=True)
class PipeResult:
    pipe_id: str
    flow_m3s: float
    demand_m3s: float
    velocity_mps: float
    depth_m: float
    fill_ratio: float
    full_capacity_m3s: float
    surcharge_ratio: float
    direction: FlowDirection
    state: PipeState
    froude_number: float | None = None
    upstream_crown_elevation_m: float = 0.0
    downstream_crown_elevation_m: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipe_id": self.pipe_id,
            "flow_m3s": _rounded(self.flow_m3s),
            "demand_m3s": _rounded(self.demand_m3s),
            "velocity_mps": _rounded(self.velocity_mps),
            "depth_m": _rounded(self.depth_m),
            "fill_ratio": _rounded(self.fill_ratio),
            "full_capacity_m3s": _rounded(self.full_capacity_m3s),
            "surcharge_ratio": _rounded(self.surcharge_ratio),
            "direction": self.direction.value,
            "state": self.state.value,
            "froude_number": (
                None if self.froude_number is None else _rounded(self.froude_number)
            ),
            "upstream_crown_elevation_m": _rounded(self.upstream_crown_elevation_m),
            "downstream_crown_elevation_m": _rounded(self.downstream_crown_elevation_m),
        }


@dataclass(frozen=True, slots=True)
class NodeResult:
    node_id: str
    water_surface_elevation_m: float
    depth_m: float
    volume_m3: float
    inflow_m3s: float
    outflow_m3s: float
    surcharged: bool
    state: NodeState

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "water_surface_elevation_m": _rounded(self.water_surface_elevation_m),
            "depth_m": _rounded(self.depth_m),
            "volume_m3": _rounded(self.volume_m3),
            "inflow_m3s": _rounded(self.inflow_m3s),
            "outflow_m3s": _rounded(self.outflow_m3s),
            "surcharged": self.surcharged,
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class MassBalanceLedger:
    initial_storage_m3: float
    initial_pipe_storage_m3: float
    rainfall_input_m3: float
    external_boundary_inflow_m3: float
    infiltration_loss_m3: float
    outfall_discharge_m3: float
    overflow_m3: float
    external_boundary_outflow_m3: float
    numerical_correction_m3: float
    final_storage_m3: float
    final_pipe_storage_m3: float

    @property
    def residual_m3(self) -> float:
        return (
            self.initial_storage_m3
            + self.initial_pipe_storage_m3
            + self.rainfall_input_m3
            + self.external_boundary_inflow_m3
            - self.infiltration_loss_m3
            - self.outfall_discharge_m3
            - self.overflow_m3
            - self.external_boundary_outflow_m3
            - self.numerical_correction_m3
            - self.final_storage_m3
            - self.final_pipe_storage_m3
        )

    @property
    def residual_relative(self) -> float:
        denominator = max(
            1.0,
            self.initial_storage_m3
            + self.initial_pipe_storage_m3
            + self.rainfall_input_m3
            + self.external_boundary_inflow_m3,
        )
        return abs(self.residual_m3) / denominator

    @property
    def quality_status(self) -> QualityStatus:
        if self.residual_relative <= VERIFIED_RELATIVE_RESIDUAL:
            return QualityStatus.VERIFIED
        if self.residual_relative <= WARNING_RELATIVE_RESIDUAL:
            return QualityStatus.WARNING
        return QualityStatus.FAILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_storage_m3": _rounded(self.initial_storage_m3),
            "initial_pipe_storage_m3": _rounded(self.initial_pipe_storage_m3),
            "rainfall_input_m3": _rounded(self.rainfall_input_m3),
            "external_boundary_inflow_m3": _rounded(self.external_boundary_inflow_m3),
            "infiltration_loss_m3": _rounded(self.infiltration_loss_m3),
            "outfall_discharge_m3": _rounded(self.outfall_discharge_m3),
            "overflow_m3": _rounded(self.overflow_m3),
            "external_boundary_outflow_m3": _rounded(self.external_boundary_outflow_m3),
            "numerical_correction_m3": _rounded(self.numerical_correction_m3),
            "final_storage_m3": _rounded(self.final_storage_m3),
            "final_pipe_storage_m3": _rounded(self.final_pipe_storage_m3),
            "residual_m3": _rounded(self.residual_m3),
            "residual_relative": _rounded(self.residual_relative),
            "quality_status": self.quality_status.value,
        }


@dataclass(frozen=True, slots=True)
class StabilityDiagnostics:
    min_time_step_s: float | None
    max_courant: float
    max_substeps: int
    rapid_reversals: int = 0
    unsupported_regime_events: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_time_step_s": (
                None if self.min_time_step_s is None else _rounded(self.min_time_step_s)
            ),
            "max_courant": _rounded(self.max_courant),
            "max_substeps": self.max_substeps,
            "rapid_reversals": self.rapid_reversals,
            "unsupported_regime_events": self.unsupported_regime_events,
        }


@dataclass(frozen=True, slots=True)
class FrameResult:
    time_s: float
    nodes: tuple[NodeResult, ...]
    pipes: tuple[PipeResult, ...]
    mass_residual_m3: float
    mass_residual_relative: float
    min_time_step_s: float | None
    max_courant: float
    substep_count: int
    quality_status: QualityStatus

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_s": _rounded(self.time_s),
            "nodes": [node.to_dict() for node in self.nodes],
            "pipes": [pipe.to_dict() for pipe in self.pipes],
            "mass_residual_m3": _rounded(self.mass_residual_m3),
            "mass_residual_relative": _rounded(self.mass_residual_relative),
            "min_time_step_s": (
                None if self.min_time_step_s is None else _rounded(self.min_time_step_s)
            ),
            "max_courant": _rounded(self.max_courant),
            "substep_count": self.substep_count,
            "quality_status": self.quality_status.value,
        }


@dataclass(frozen=True, slots=True)
class SimulationResult:
    solver_id: str
    routing_mode: RoutingMode
    frames: tuple[FrameResult, ...]
    mass_balance: MassBalanceLedger
    stability: StabilityDiagnostics
    quality_status: QualityStatus

    @property
    def model_fidelity(self) -> str:
        return (
            "fast_screening"
            if self.routing_mode is RoutingMode.MOTION_WAVE
            else ("reduced_fidelity")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_id": self.solver_id,
            "routing_mode": self.routing_mode.value,
            "model_fidelity": self.model_fidelity,
            "is_authoritative": False,
            "quality_status": self.quality_status.value,
            "mass_balance": self.mass_balance.to_dict(),
            "stability": self.stability.to_dict(),
            "frames": [frame.to_dict() for frame in self.frames],
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


@dataclass(slots=True)
class _PipeGeometry:
    full_area_m2: float
    full_hydraulic_radius_m: float
    full_capacity_m3s: float
    resistance: float


@dataclass(slots=True)
class _PipeRuntime:
    pipe: HydraulicPipe
    geometry: _PipeGeometry
    flow_m3s: float = 0.0
    demand_m3s: float = 0.0
    frame_peak_demand_m3s: float = 0.0
    direction: int = 0
    depth_m: float = 0.0
    fill_ratio: float = 0.0
    state: PipeState = PipeState.DRY
    froude_number: float | None = None
    last_nonzero_direction: int = 0
    last_nonzero_time_s: float = 0.0


def courant_number(
    velocity_mps: float,
    depth_m: float,
    control_length_m: float,
    time_step_s: float,
) -> float:
    """Return ``C = dt * (|u| + sqrt(g*h)) / L``."""

    if control_length_m <= 0.0 or time_step_s < 0.0:
        raise ValueError("control length must be positive and time step non-negative")
    if not all(math.isfinite(value) for value in (velocity_mps, depth_m, time_step_s)):
        raise ValueError("CFL inputs must be finite")
    wave_speed = abs(velocity_mps) + math.sqrt(
        GRAVITY_MPS2 * max(depth_m, DEPTH_FLOOR_M)
    )
    return time_step_s * wave_speed / control_length_m


def stable_time_step(
    *,
    velocity_mps: float,
    depth_m: float,
    control_length_m: float,
    courant_limit: float = 0.5,
) -> float:
    """Return the largest explicit step satisfying the configured Courant cap."""

    if control_length_m <= 0.0:
        raise ValueError("control_length_m must be positive")
    if not 0.0 < courant_limit <= 0.5:
        raise ValueError("courant_limit must be in (0, 0.5]")
    wave_speed = abs(velocity_mps) + math.sqrt(
        GRAVITY_MPS2 * max(depth_m, DEPTH_FLOOR_M)
    )
    if wave_speed <= 0.0:
        return math.inf
    return courant_limit * control_length_m / wave_speed


def _circular_angle_and_excess(depth_ratio: float) -> tuple[float, float]:
    ratio = min(max(depth_ratio, 0.0), 1.0)
    if ratio == 0.0:
        return 0.0, 0.0
    half_sine = math.sqrt(max(0.0, ratio * (1.0 - ratio)))
    theta = 2.0 * math.atan2(2.0 * half_sine, 1.0 - 2.0 * ratio)
    sine_theta = 4.0 * (1.0 - 2.0 * ratio) * half_sine
    return theta, theta - sine_theta


def _build_circular_table() -> tuple[tuple[float, ...], tuple[float, ...]]:
    ratios = tuple(index / 256.0 for index in range(257))
    discharges: list[float] = []
    for ratio in ratios:
        theta, excess = _circular_angle_and_excess(ratio)
        area_ratio = excess / 8.0
        hydraulic_radius_ratio = excess / (4.0 * theta) if theta > 0.0 else 0.0
        discharge_ratio = area_ratio * hydraulic_radius_ratio ** (2.0 / 3.0)
        discharges.append(discharge_ratio)
    return ratios, tuple(discharges)


_CIRCULAR_DEPTH_RATIOS, _CIRCULAR_DISCHARGE_RATIOS = _build_circular_table()
_CIRCULAR_PEAK_INDEX = max(
    range(len(_CIRCULAR_DISCHARGE_RATIOS)),
    key=_CIRCULAR_DISCHARGE_RATIOS.__getitem__,
)
_CIRCULAR_SEARCH_DEPTH_RATIOS = _CIRCULAR_DEPTH_RATIOS[: _CIRCULAR_PEAK_INDEX + 1]
_CIRCULAR_SEARCH_DISCHARGE_RATIOS = _CIRCULAR_DISCHARGE_RATIOS[
    : _CIRCULAR_PEAK_INDEX + 1
]


def _area_at_depth(pipe: HydraulicPipe, depth_m: float) -> float:
    depth = min(max(depth_m, 0.0), pipe.full_height_m)
    if pipe.shape.strip().lower() == "circular":
        assert pipe.diameter_m is not None
        if depth <= 0.0 or depth >= pipe.diameter_m:
            if depth >= pipe.diameter_m:
                try:
                    area = math.pi * pipe.diameter_m**2 / 4.0
                except OverflowError as exc:
                    raise NetworkValidationError(
                        f"pipe {pipe.pipe_id} full area exceeds finite range"
                    ) from exc
                if not math.isfinite(area):
                    raise NetworkValidationError(
                        f"pipe {pipe.pipe_id} full area must be finite"
                    )
                return area
            return 0.0
        _, excess = _circular_angle_and_excess(depth / pipe.diameter_m)
        return pipe.diameter_m**2 * excess / 8.0
    assert pipe.width_m is not None
    return pipe.width_m * depth


def _hydraulic_radius_at_depth(pipe: HydraulicPipe, depth_m: float) -> float:
    depth = min(max(depth_m, 0.0), pipe.full_height_m)
    if depth <= 0.0:
        return 0.0
    if pipe.shape.strip().lower() == "circular":
        assert pipe.diameter_m is not None
        theta, _ = _circular_angle_and_excess(depth / pipe.diameter_m)
        perimeter = pipe.diameter_m * theta / 2.0
        area = _area_at_depth(pipe, depth)
        return area / perimeter if perimeter > 0.0 else 0.0
    assert pipe.width_m is not None
    area = _area_at_depth(pipe, depth)
    perimeter = pipe.width_m + 2.0 * depth
    return area / perimeter if perimeter > 0.0 else 0.0


def _full_geometry(pipe: HydraulicPipe) -> _PipeGeometry:
    full_area = _area_at_depth(pipe, pipe.full_height_m)
    if not math.isfinite(full_area):
        raise NetworkValidationError(f"pipe {pipe.pipe_id} full area must be finite")
    full_radius = _hydraulic_radius_at_depth(pipe, pipe.full_height_m)
    if full_area <= 0.0 or full_radius <= 0.0:
        raise NetworkValidationError(f"pipe {pipe.pipe_id} has invalid cross-section")
    sqrt_slope = math.sqrt(pipe.slope)
    try:
        if pipe.shape.strip().lower() == "circular":
            assert pipe.diameter_m is not None
            capacity = (
                max(_CIRCULAR_DISCHARGE_RATIOS)
                * pipe.diameter_m ** (8.0 / 3.0)
                * sqrt_slope
                / pipe.manning_n
            )
        else:
            capacity = (
                full_area * full_radius ** (2.0 / 3.0) * sqrt_slope / pipe.manning_n
            )
    except OverflowError as exc:
        raise NetworkValidationError(
            f"pipe {pipe.pipe_id} capacity exceeds finite range"
        ) from exc
    if not math.isfinite(capacity) or capacity <= 0.0:
        raise NetworkValidationError(
            f"pipe {pipe.pipe_id} capacity must be finite and positive"
        )
    try:
        resistance = (
            pipe.manning_n**2
            * pipe.length_m
            / (full_area**2 * full_radius ** (4.0 / 3.0))
        )
    except (OverflowError, ZeroDivisionError) as exc:
        raise NetworkValidationError(
            f"pipe {pipe.pipe_id} resistance exceeds finite range"
        ) from exc
    if not math.isfinite(resistance) or resistance <= 0.0:
        raise NetworkValidationError(
            f"pipe {pipe.pipe_id} resistance must be finite and positive"
        )
    return _PipeGeometry(
        full_area_m2=full_area,
        full_hydraulic_radius_m=full_radius,
        full_capacity_m3s=capacity,
        resistance=resistance,
    )


def _normal_depth_for_flow(
    pipe: HydraulicPipe,
    geometry: _PipeGeometry,
    flow_m3s: float,
) -> float:
    magnitude = abs(flow_m3s)
    if magnitude <= 1.0e-15:
        return 0.0
    if pipe.shape.strip().lower() == "circular":
        assert pipe.diameter_m is not None
        if magnitude >= geometry.full_capacity_m3s:
            return _CIRCULAR_SEARCH_DEPTH_RATIOS[-1] * pipe.diameter_m
        target = (
            magnitude
            * pipe.manning_n
            / math.sqrt(pipe.slope)
            / pipe.diameter_m ** (8.0 / 3.0)
        )
        discharge_index = bisect.bisect_left(_CIRCULAR_SEARCH_DISCHARGE_RATIOS, target)
        discharge_index = min(
            max(discharge_index, 1),
            len(_CIRCULAR_SEARCH_DISCHARGE_RATIOS) - 1,
        )
        lower = _CIRCULAR_SEARCH_DISCHARGE_RATIOS[discharge_index - 1]
        upper = _CIRCULAR_SEARCH_DISCHARGE_RATIOS[discharge_index]
        fraction = 0.0 if upper == lower else (target - lower) / (upper - lower)
        fraction = min(max(fraction, 0.0), 1.0)
        ratio = _CIRCULAR_SEARCH_DEPTH_RATIOS[discharge_index - 1] + fraction * (
            _CIRCULAR_SEARCH_DEPTH_RATIOS[discharge_index]
            - _CIRCULAR_SEARCH_DEPTH_RATIOS[discharge_index - 1]
        )
        return float(ratio * pipe.diameter_m)
    if magnitude >= geometry.full_capacity_m3s:
        return pipe.full_height_m

    assert pipe.width_m is not None

    def discharge(depth: float) -> float:
        area = _area_at_depth(pipe, depth)
        radius = _hydraulic_radius_at_depth(pipe, depth)
        if area <= 0.0 or radius <= 0.0:
            return 0.0
        return float(
            area * radius ** (2.0 / 3.0) * math.sqrt(pipe.slope) / pipe.manning_n
        )

    lower = 0.0
    upper = pipe.full_height_m
    for _ in range(48):
        midpoint = (lower + upper) / 2.0
        if discharge(midpoint) < magnitude:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def _diffusion_wave_flow(
    pipe: HydraulicPipe,
    geometry: _PipeGeometry,
    friction_slope: float,
) -> float:
    """Invert the partial-section Manning relation for diffusion-wave routing."""

    if friction_slope <= 0.0:
        return 0.0
    slope_root = math.sqrt(friction_slope)

    def conveyance(magnitude: float) -> float:
        if magnitude <= 0.0:
            return 0.0
        depth = _normal_depth_for_flow(pipe, geometry, magnitude)
        area = _area_at_depth(pipe, depth)
        radius = _hydraulic_radius_at_depth(pipe, depth)
        if area <= 0.0 or radius <= 0.0:
            return 0.0
        return float(area * radius ** (2.0 / 3.0) * slope_root / pipe.manning_n)

    upper = geometry.full_capacity_m3s
    if conveyance(upper) < upper:
        return upper
    lower = 0.0
    for _ in range(64):
        midpoint = (lower + upper) / 2.0
        if conveyance(midpoint) < midpoint:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def _cfl_limited_request(
    pipe: HydraulicPipe,
    geometry: _PipeGeometry,
    request_m3s: float,
    time_step_s: float,
    config: SolverConfig,
) -> float:
    """Cap a newly requested flow so this step itself satisfies the CFL gate."""

    if abs(request_m3s) <= 1.0e-15:
        return 0.0

    def allowed(magnitude: float) -> float:
        depth = _normal_depth_for_flow(pipe, geometry, magnitude)
        area = _area_at_depth(pipe, depth)
        return (
            config.max_courant * area * pipe.length_m / time_step_s
            - area * math.sqrt(config.gravity_mps2 * max(depth, config.depth_floor_m))
        )

    candidate = min(abs(request_m3s), geometry.full_capacity_m3s)
    if candidate <= 0.0 or allowed(candidate) >= candidate:
        return math.copysign(candidate, request_m3s) if candidate > 0.0 else 0.0
    lower = 0.0
    upper = candidate
    if allowed(0.0) < 0.0:
        return 0.0
    for _ in range(40):
        midpoint = (lower + upper) / 2.0
        if allowed(midpoint) >= midpoint:
            lower = midpoint
        else:
            upper = midpoint
    return math.copysign(lower, request_m3s)


def _direction_for_request(request_m3s: float) -> int:
    if request_m3s > 1.0e-15:
        return 1
    if request_m3s < -1.0e-15:
        return -1
    return 0


def _requested_pipe_flow(
    runtime: _PipeRuntime,
    upstream_depth_m: float,
    downstream_depth_m: float,
    mode: RoutingMode,
    config: SolverConfig,
    time_s: float,
) -> float:
    pipe = runtime.geometry
    hydraulic_pipe = runtime.pipe
    if mode is RoutingMode.MOTION_WAVE:
        if (
            upstream_depth_m <= config.depth_floor_m
            and abs(runtime.flow_m3s) <= 1.0e-15
        ):
            return 0.0
        available_head = (
            upstream_depth_m
            + hydraulic_pipe.slope * hydraulic_pipe.length_m
            - downstream_depth_m
        )
        if available_head <= 0.0:
            return 0.0
        target = pipe.full_capacity_m3s * math.sqrt(
            available_head
            / max(hydraulic_pipe.slope * hydraulic_pipe.length_m, TIME_EPSILON_S)
        )
        return target

    if (
        upstream_depth_m <= config.depth_floor_m
        and downstream_depth_m <= config.depth_floor_m
        and abs(runtime.flow_m3s) <= 1.0e-15
    ):
        return 0.0
    head_up = hydraulic_pipe.upstream_invert_m + upstream_depth_m
    head_down = hydraulic_pipe.downstream_invert_m + downstream_depth_m
    head_difference = head_up - head_down
    if (
        abs(head_difference) <= config.depth_floor_m
        and abs(runtime.flow_m3s) <= 1.0e-15
    ):
        return 0.0
    if head_difference == 0.0:
        return 0.0
    friction_slope = abs(head_difference) / hydraulic_pipe.length_m
    target_magnitude = _diffusion_wave_flow(
        hydraulic_pipe,
        pipe,
        friction_slope,
    )
    target = math.copysign(target_magnitude, head_difference)
    if abs(target) > 1.0e-15:
        depth = _normal_depth_for_flow(hydraulic_pipe, pipe, target)
        if depth >= hydraulic_pipe.full_height_m * (1.0 - 1.0e-12):
            raise UnsupportedRegimeError(
                "diffusion-wave pressure flow is unsupported; Froude/regime gate "
                "requires open-channel flow"
            )
        velocity = abs(target) / max(_area_at_depth(hydraulic_pipe, depth), 1.0e-30)
        froude = velocity / math.sqrt(
            config.gravity_mps2 * max(depth, config.depth_floor_m)
        )
        if froude >= 0.95:
            raise UnsupportedRegimeError(
                f"diffusion-wave Froude number {froude:.6g} is not below 0.95"
            )
    previous_direction = runtime.last_nonzero_direction
    direction = _direction_for_request(target)
    if (
        direction
        and previous_direction
        and direction != previous_direction
        and abs(runtime.flow_m3s)
        >= pipe.full_capacity_m3s * config.rapid_reversal_fraction
        and time_s - runtime.last_nonzero_time_s
        <= max(config.rain_step_s, config.reporting_interval_s)
    ):
        raise UnsupportedRegimeError(
            "diffusion-wave rapid flow-direction reversal is unsupported"
        )
    return target


def _flow_state(
    runtime: _PipeRuntime,
    demand_m3s: float,
) -> PipeState:
    capacity = runtime.geometry.full_capacity_m3s
    if demand_m3s > capacity * (1.0 + 1.0e-9):
        return PipeState.SURCHARGED
    if runtime.fill_ratio >= 1.0 - 1.0e-6 or abs(runtime.flow_m3s) >= capacity * (
        1.0 - 1.0e-6
    ):
        return PipeState.PRESSURIZED
    if abs(runtime.flow_m3s) <= 1.0e-15 or runtime.depth_m <= 0.0:
        return PipeState.DRY
    return PipeState.OPEN_CHANNEL


def _initial_runtime(network: HydraulicNetwork) -> list[_PipeRuntime]:
    runtimes: list[_PipeRuntime] = []
    for pipe in network.pipes:
        geometry = _full_geometry(pipe)
        runtime = _PipeRuntime(pipe=pipe, geometry=geometry)
        runtime.flow_m3s = pipe.initial_flow_m3s
        runtime.demand_m3s = abs(pipe.initial_flow_m3s)
        runtime.frame_peak_demand_m3s = runtime.demand_m3s
        runtime.direction = _direction_for_request(pipe.initial_flow_m3s)
        runtime.depth_m = _normal_depth_for_flow(pipe, geometry, pipe.initial_flow_m3s)
        runtime.fill_ratio = runtime.depth_m / pipe.full_height_m
        runtime.state = _flow_state(runtime, runtime.demand_m3s)
        if runtime.direction:
            runtime.last_nonzero_direction = runtime.direction
        runtimes.append(runtime)
    return runtimes


def _pipe_storage(runtimes: Sequence[_PipeRuntime]) -> float:
    return sum(
        max(_area_at_depth(runtime.pipe, runtime.depth_m), 0.0)
        * max(runtime.depth_m, 0.0)
        for runtime in runtimes
    )


def _runtime_pipe_result(
    runtime: _PipeRuntime, *, use_interval_peak: bool = False
) -> PipeResult:
    flow = runtime.flow_m3s
    area = max(_area_at_depth(runtime.pipe, runtime.depth_m), 1.0e-30)
    velocity = 0.0 if abs(flow) <= 1.0e-15 else flow / area
    demand = (
        max(runtime.demand_m3s, runtime.frame_peak_demand_m3s)
        if use_interval_peak
        else abs(runtime.demand_m3s)
    )
    state = _flow_state(runtime, demand)
    capacity = runtime.geometry.full_capacity_m3s
    upstream_crown = runtime.pipe.upstream_crown_m
    downstream_crown = runtime.pipe.downstream_crown_m
    if upstream_crown is None:
        upstream_crown = runtime.pipe.upstream_invert_m + runtime.pipe.full_height_m
    if downstream_crown is None:
        downstream_crown = runtime.pipe.downstream_invert_m + runtime.pipe.full_height_m
    direction = (
        FlowDirection.DOWNSTREAM
        if runtime.direction > 0
        else FlowDirection.UPSTREAM if runtime.direction < 0 else FlowDirection.NONE
    )
    return PipeResult(
        pipe_id=runtime.pipe.pipe_id,
        flow_m3s=flow,
        demand_m3s=demand,
        velocity_mps=velocity,
        depth_m=runtime.depth_m,
        fill_ratio=runtime.fill_ratio,
        full_capacity_m3s=capacity,
        surcharge_ratio=demand / capacity,
        direction=direction,
        state=state,
        froude_number=runtime.froude_number,
        upstream_crown_elevation_m=upstream_crown,
        downstream_crown_elevation_m=downstream_crown,
    )


def _rainfall_volumes(
    network: HydraulicNetwork,
    forcing: RainfallForcing,
    time_s: float,
    time_step_s: float,
) -> tuple[dict[str, float], float, float]:
    intensity_mm_h = forcing.intensity_at(time_s)
    gross_depth_m = intensity_mm_h * time_step_s / 3_600_000.0
    direct_volumes: dict[str, float] = {node.node_id: 0.0 for node in network.nodes}
    gross_total = 0.0
    loss_total = 0.0
    for catchment in network.catchments:
        gross = gross_depth_m * catchment.area_m2
        available_depth = max(
            0.0, gross_depth_m - catchment.depression_storage_mm / 1000.0
        )
        effective = available_depth * catchment.runoff_coefficient * catchment.area_m2
        direct_volumes[catchment.outlet_node_id] += effective
        gross_total += gross
        loss_total += gross - effective
    return direct_volumes, gross_total, loss_total


def _next_forcing_boundary(time_s: float, step_s: float) -> float:
    return (math.floor((time_s + TIME_EPSILON_S) / step_s) + 1.0) * step_s


def _network_stable_dt(
    runtimes: Sequence[_PipeRuntime],
    courant_limit: float,
    gravity: float,
    depth_floor: float,
) -> float:
    if not runtimes:
        return math.inf
    min_length = min(runtime.pipe.length_m for runtime in runtimes)
    max_speed = 0.0
    max_depth = 0.0
    for runtime in runtimes:
        area = max(_area_at_depth(runtime.pipe, runtime.depth_m), 1.0e-30)
        speed = abs(runtime.flow_m3s) / area
        max_speed = max(max_speed, speed)
        max_depth = max(max_depth, runtime.depth_m, runtime.pipe.full_height_m)
    wave_speed = max_speed + math.sqrt(gravity * max(max_depth, depth_floor))
    if wave_speed <= 0.0:
        return math.inf
    return courant_limit * min_length / wave_speed


def _network_courant(
    runtimes: Sequence[_PipeRuntime],
    time_step_s: float,
    gravity: float,
    depth_floor: float,
) -> float:
    if not runtimes:
        return 0.0
    min_length = min(runtime.pipe.length_m for runtime in runtimes)
    max_speed = 0.0
    max_depth = 0.0
    for runtime in runtimes:
        area = max(_area_at_depth(runtime.pipe, runtime.depth_m), 1.0e-30)
        max_speed = max(max_speed, abs(runtime.flow_m3s) / area)
        max_depth = max(max_depth, runtime.depth_m)
    return (
        time_step_s
        * (max_speed + math.sqrt(gravity * max(max_depth, depth_floor)))
        / min_length
    )


def _ledger(
    *,
    initial_storage: float,
    initial_pipe_storage: float,
    rainfall_input: float,
    infiltration_loss: float,
    outfall_discharge: float,
    overflow: float,
    correction: float,
    storage: Mapping[str, float],
    pipe_storage: float,
) -> MassBalanceLedger:
    return MassBalanceLedger(
        initial_storage_m3=initial_storage,
        initial_pipe_storage_m3=initial_pipe_storage,
        rainfall_input_m3=rainfall_input,
        external_boundary_inflow_m3=0.0,
        infiltration_loss_m3=infiltration_loss,
        outfall_discharge_m3=outfall_discharge,
        overflow_m3=overflow,
        external_boundary_outflow_m3=0.0,
        numerical_correction_m3=correction,
        final_storage_m3=sum(storage.values()),
        final_pipe_storage_m3=pipe_storage,
    )


def _integrate_step(
    *,
    network: HydraulicNetwork,
    runtimes: list[_PipeRuntime],
    node_by_id: Mapping[str, HydraulicNode],
    storage: dict[str, float],
    forcing: RainfallForcing,
    mode: RoutingMode,
    config: SolverConfig,
    time_s: float,
    time_step_s: float,
    cumulative: dict[str, float],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    direct, gross_rain, infiltration_loss = _rainfall_volumes(
        network, forcing, time_s, time_step_s
    )
    available = {
        node.node_id: storage[node.node_id] + direct[node.node_id]
        for node in network.nodes
    }
    depths = {
        node_id: min(max(volume / node.surface_area_m2, 0.0), node.max_depth_m)
        for node_id, volume in available.items()
        for node in (node_by_id[node_id],)
    }

    requested: list[tuple[int, int, float]] = []
    requested_outflow: dict[str, float] = defaultdict(float)
    for index, runtime in enumerate(runtimes):
        pipe = runtime.pipe
        hydraulic_request = _requested_pipe_flow(
            runtime,
            depths[pipe.upstream_node_id],
            depths[pipe.downstream_node_id],
            mode,
            config,
            time_s,
        )
        demand = abs(hydraulic_request)
        runtime.demand_m3s = demand
        runtime.frame_peak_demand_m3s = max(runtime.frame_peak_demand_m3s, demand)
        request = _cfl_limited_request(
            pipe,
            runtime.geometry,
            hydraulic_request,
            time_step_s,
            config,
        )
        direction = _direction_for_request(request)
        if direction == 0:
            runtime.direction = 0
            continue
        source = pipe.upstream_node_id if direction > 0 else pipe.downstream_node_id
        requested_volume = abs(request) * time_step_s
        requested.append((index, direction, requested_volume))
        requested_outflow[source] += requested_volume

    outfall_volumes: dict[str, float] = {}
    for node in network.nodes:
        if not node.is_outfall:
            continue
        depth = depths[node.node_id]
        desired = OUTFALL_DISCHARGE_COEFFICIENT * node.outfall_width_m * depth**1.5
        volume = desired * time_step_s
        requested_outflow[node.node_id] += volume
        outfall_volumes[node.node_id] = volume

    scale: dict[str, float] = {}
    for node_id, requested_volume in requested_outflow.items():
        scale[node_id] = (
            min(1.0, available[node_id] / requested_volume)
            if requested_volume > 0.0
            else 1.0
        )

    new_storage = dict(available)
    actual_internal: dict[int, float] = {}
    actual_direction: dict[int, int] = {}
    for index, direction, volume in requested:
        pipe = runtimes[index].pipe
        source = pipe.upstream_node_id if direction > 0 else pipe.downstream_node_id
        destination = (
            pipe.downstream_node_id if direction > 0 else pipe.upstream_node_id
        )
        actual_volume = volume * scale[source]
        new_storage[source] -= actual_volume
        new_storage[destination] += actual_volume
        actual_internal[index] = actual_volume / time_step_s
        actual_direction[index] = direction

    actual_outfall = {
        node_id: volume * scale[node_id] for node_id, volume in outfall_volumes.items()
    }
    for node_id, volume in actual_outfall.items():
        new_storage[node_id] -= volume

    overflow_by_node: dict[str, float] = {}
    for node in network.nodes:
        node_id = node.node_id
        if new_storage[node_id] < 0.0:
            if new_storage[node_id] >= -1.0e-12:
                cumulative["numerical_correction_m3"] += -new_storage[node_id]
                new_storage[node_id] = 0.0
            else:
                raise MassBalanceError(
                    f"node {node_id} received negative volume "
                    f"{new_storage[node_id]:.12g} m3"
                )
        maximum = node.surface_area_m2 * node.max_depth_m
        if new_storage[node_id] > maximum:
            overflow = new_storage[node_id] - maximum
            new_storage[node_id] = maximum
            overflow_by_node[node_id] = overflow

    node_inflow_rate = {node.node_id: 0.0 for node in network.nodes}
    node_outflow_rate = {node.node_id: 0.0 for node in network.nodes}
    for node_id, volume in direct.items():
        node_inflow_rate[node_id] += volume / time_step_s
    for index, direction in actual_direction.items():
        pipe = runtimes[index].pipe
        source = pipe.upstream_node_id if direction > 0 else pipe.downstream_node_id
        destination = (
            pipe.downstream_node_id if direction > 0 else pipe.upstream_node_id
        )
        flow = actual_internal[index]
        node_outflow_rate[source] += abs(flow)
        node_inflow_rate[destination] += abs(flow)
    for node_id, volume in actual_outfall.items():
        node_outflow_rate[node_id] += volume / time_step_s
    for node_id, volume in overflow_by_node.items():
        node_outflow_rate[node_id] += volume / time_step_s

    for index, runtime in enumerate(runtimes):
        if index in actual_internal:
            flow = actual_internal[index]
            direction = actual_direction[index]
            runtime.flow_m3s = flow if direction > 0 else -flow
            runtime.direction = direction
        else:
            runtime.flow_m3s = 0.0
            runtime.direction = 0
        runtime.depth_m = _normal_depth_for_flow(
            runtime.pipe, runtime.geometry, runtime.flow_m3s
        )
        runtime.fill_ratio = runtime.depth_m / runtime.pipe.full_height_m
        area = max(_area_at_depth(runtime.pipe, runtime.depth_m), 1.0e-30)
        velocity = 0.0 if abs(runtime.flow_m3s) <= 1.0e-15 else runtime.flow_m3s / area
        runtime.froude_number = (
            abs(velocity)
            / math.sqrt(
                config.gravity_mps2 * max(runtime.depth_m, config.depth_floor_m)
            )
            if abs(runtime.flow_m3s) > 1.0e-15
            else None
        )
        runtime.state = _flow_state(runtime, runtime.demand_m3s)
        if runtime.direction:
            runtime.last_nonzero_direction = runtime.direction
            runtime.last_nonzero_time_s = time_s

    cumulative["rainfall_input_m3"] += gross_rain
    cumulative["infiltration_loss_m3"] += infiltration_loss
    cumulative["outfall_discharge_m3"] += sum(actual_outfall.values())
    cumulative["overflow_m3"] += sum(overflow_by_node.values())
    return new_storage, node_inflow_rate, node_outflow_rate


def _node_results(
    network: HydraulicNetwork,
    storage: Mapping[str, float],
    inflow_rate: Mapping[str, float],
    outflow_rate: Mapping[str, float],
    runtimes: Sequence[_PipeRuntime],
) -> tuple[NodeResult, ...]:
    overloaded_nodes = {
        runtime.pipe.upstream_node_id
        for runtime in runtimes
        if runtime.frame_peak_demand_m3s
        > runtime.geometry.full_capacity_m3s * (1.0 + 1.0e-9)
    } | {
        runtime.pipe.downstream_node_id
        for runtime in runtimes
        if runtime.frame_peak_demand_m3s
        > runtime.geometry.full_capacity_m3s * (1.0 + 1.0e-9)
    }
    incident_counts: dict[str, int] = defaultdict(int)
    for pipe in network.pipes:
        incident_counts[pipe.upstream_node_id] += 1
        incident_counts[pipe.downstream_node_id] += 1
    results: list[NodeResult] = []
    for node in network.nodes:
        volume = storage[node.node_id]
        depth = min(volume / node.surface_area_m2, node.max_depth_m)
        at_capacity = depth >= node.max_depth_m * (1.0 - 1.0e-9)
        surcharged = at_capacity or node.node_id in overloaded_nodes
        if surcharged:
            state = NodeState.SURCHARGED
        elif incident_counts[node.node_id] == 0:
            state = NodeState.DISCONNECTED
        elif depth <= 0.0:
            state = NodeState.DRY
        else:
            state = NodeState.SURFACE_FLOW
        results.append(
            NodeResult(
                node_id=node.node_id,
                water_surface_elevation_m=node.invert_elevation_m + depth,
                depth_m=depth,
                volume_m3=volume,
                inflow_m3s=inflow_rate[node.node_id],
                outflow_m3s=outflow_rate[node.node_id],
                surcharged=surcharged,
                state=state,
            )
        )
    return tuple(results)


def _validate_solver_network(network: HydraulicNetwork) -> None:
    if not network.nodes or not network.pipes:
        raise NetworkValidationError("hydraulic network must not be empty")
    outfalls = [node for node in network.nodes if node.is_outfall]
    if not outfalls:
        raise NetworkValidationError("hydraulic network requires an explicit outfall")
    adjacency: dict[str, set[str]] = {node.node_id: set() for node in network.nodes}
    for pipe in network.pipes:
        adjacency[pipe.upstream_node_id].add(pipe.downstream_node_id)
        adjacency[pipe.downstream_node_id].add(pipe.upstream_node_id)
    visited: set[str] = set()
    pending = [outfalls[0].node_id]
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        pending.extend(adjacency[node_id] - visited)
    if visited != set(adjacency):
        missing = sorted(set(adjacency) - visited)
        raise NetworkValidationError(
            f"hydraulic network contains disconnected or isolated nodes: {missing}"
        )


def solve_network(
    network: HydraulicNetwork,
    rainfall: RainfallForcing,
    *,
    routing_mode: RoutingMode | str = RoutingMode.MOTION_WAVE,
    config: SolverConfig,
    frame_callback: Callable[[FrameResult], Any] | None = None,
    retain_frames: bool = True,
) -> SimulationResult:
    """Solve a network and return deterministic reporting frames plus diagnostics.

    The solver raises instead of publishing a result when a report interval
    cannot satisfy ``max_substeps`` or a diffusion-wave state exceeds its
    documented subcritical open-channel regime.
    """

    mode = _coerce_mode(routing_mode)
    _validate_solver_network(network)
    node_by_id = network.node_by_id
    runtimes = _initial_runtime(network)
    storage = {
        node.node_id: node.initial_depth_m * node.surface_area_m2
        for node in network.nodes
    }
    initial_storage = sum(storage.values())
    initial_pipe_storage = _pipe_storage(runtimes)
    cumulative = {
        "rainfall_input_m3": 0.0,
        "infiltration_loss_m3": 0.0,
        "outfall_discharge_m3": 0.0,
        "overflow_m3": 0.0,
        "numerical_correction_m3": 0.0,
    }
    if not retain_frames and frame_callback is None:
        raise ValueError("retain_frames=False requires a frame_callback")
    frames: list[FrameResult] = []
    quality_so_far = QualityStatus.VERIFIED

    def publish(frame: FrameResult) -> None:
        nonlocal quality_so_far
        if list(QualityStatus).index(frame.quality_status) > list(QualityStatus).index(
            quality_so_far
        ):
            quality_so_far = frame.quality_status
        if retain_frames:
            frames.append(frame)
        if frame_callback is not None:
            frame_callback(frame)

    empty_rates = {node.node_id: 0.0 for node in network.nodes}
    initial_ledger = _ledger(
        initial_storage=initial_storage,
        initial_pipe_storage=initial_pipe_storage,
        rainfall_input=0.0,
        infiltration_loss=0.0,
        outfall_discharge=0.0,
        overflow=0.0,
        correction=0.0,
        storage=storage,
        pipe_storage=initial_pipe_storage,
    )
    publish(
        FrameResult(
            time_s=0.0,
            nodes=_node_results(network, storage, empty_rates, empty_rates, runtimes),
            pipes=tuple(_runtime_pipe_result(runtime) for runtime in runtimes),
            mass_residual_m3=initial_ledger.residual_m3,
            mass_residual_relative=initial_ledger.residual_relative,
            min_time_step_s=None,
            max_courant=0.0,
            substep_count=0,
            quality_status=initial_ledger.quality_status,
        )
    )

    report_count = int(round(config.duration_s / config.reporting_interval_s))
    overall_min_dt: float | None = None
    overall_max_courant = 0.0
    max_substeps = 0
    inflow_rate = dict(empty_rates)
    outflow_rate = dict(empty_rates)
    time_s = 0.0
    initial_dt = config.initial_time_step_s or config.initial_dt

    for report_index in range(1, report_count + 1):
        report_end = report_index * config.reporting_interval_s
        interval_min_dt: float | None = None
        interval_max_courant = 0.0
        substeps = 0
        while time_s < report_end - TIME_EPSILON_S:
            if substeps >= config.max_substeps:
                raise NumericalStabilityError(
                    f"reporting interval {report_index} requires more than "
                    f"{config.max_substeps} substeps"
                )
            stable_dt = _network_stable_dt(
                runtimes,
                config.max_courant,
                config.gravity_mps2,
                config.depth_floor_m,
            )
            if stable_dt < config.min_time_step_s:
                raise NumericalStabilityError(
                    "required CFL step is below min_time_step_s and cannot fit "
                    f"within {config.max_substeps} substeps"
                )
            remaining = report_end - time_s
            forcing_boundary = _next_forcing_boundary(time_s, rainfall.step_seconds)
            dt = min(initial_dt, remaining, forcing_boundary - time_s, stable_dt)
            if not math.isfinite(dt) or dt <= 0.0:
                raise NumericalStabilityError("computed internal time step is invalid")
            storage, inflow_rate, outflow_rate = _integrate_step(
                network=network,
                runtimes=runtimes,
                node_by_id=node_by_id,
                storage=storage,
                forcing=rainfall,
                mode=mode,
                config=config,
                time_s=time_s,
                time_step_s=dt,
                cumulative=cumulative,
            )
            courant = _network_courant(
                runtimes,
                dt,
                config.gravity_mps2,
                config.depth_floor_m,
            )
            if courant > config.max_courant * (1.0 + 1.0e-9):
                raise NumericalStabilityError(
                    f"Courant number {courant:.6g} exceeds {config.max_courant}"
                )
            interval_min_dt = (
                dt if interval_min_dt is None else min(interval_min_dt, dt)
            )
            interval_max_courant = max(interval_max_courant, courant)
            overall_min_dt = dt if overall_min_dt is None else min(overall_min_dt, dt)
            overall_max_courant = max(overall_max_courant, courant)
            substeps += 1
            time_s = min(report_end, time_s + dt)

        if substeps == 0:
            raise NumericalStabilityError("reporting interval contains no substep")
        if time_s < report_end - TIME_EPSILON_S:
            raise NumericalStabilityError(
                f"reporting interval {report_index} did not reach its end time"
            )
        time_s = report_end
        max_substeps = max(max_substeps, substeps)
        ledger = _ledger(
            initial_storage=initial_storage,
            initial_pipe_storage=initial_pipe_storage,
            rainfall_input=cumulative["rainfall_input_m3"],
            infiltration_loss=cumulative["infiltration_loss_m3"],
            outfall_discharge=cumulative["outfall_discharge_m3"],
            overflow=cumulative["overflow_m3"],
            correction=(
                cumulative["numerical_correction_m3"]
                + initial_pipe_storage
                - _pipe_storage(runtimes)
            ),
            storage=storage,
            pipe_storage=_pipe_storage(runtimes),
        )
        if ledger.quality_status is QualityStatus.FAILED:
            raise MassBalanceError(
                f"mass residual {ledger.residual_relative:.6g} exceeds gate"
            )
        publish(
            FrameResult(
                time_s=time_s,
                nodes=_node_results(
                    network, storage, inflow_rate, outflow_rate, runtimes
                ),
                pipes=tuple(
                    _runtime_pipe_result(runtime, use_interval_peak=True)
                    for runtime in runtimes
                ),
                mass_residual_m3=ledger.residual_m3,
                mass_residual_relative=ledger.residual_relative,
                min_time_step_s=interval_min_dt,
                max_courant=interval_max_courant,
                substep_count=substeps,
                quality_status=ledger.quality_status,
            )
        )
        for runtime in runtimes:
            runtime.frame_peak_demand_m3s = 0.0

    final_ledger = _ledger(
        initial_storage=initial_storage,
        initial_pipe_storage=initial_pipe_storage,
        rainfall_input=cumulative["rainfall_input_m3"],
        infiltration_loss=cumulative["infiltration_loss_m3"],
        outfall_discharge=cumulative["outfall_discharge_m3"],
        overflow=cumulative["overflow_m3"],
        correction=(
            cumulative["numerical_correction_m3"]
            + initial_pipe_storage
            - _pipe_storage(runtimes)
        ),
        storage=storage,
        pipe_storage=_pipe_storage(runtimes),
    )
    quality = quality_so_far
    return SimulationResult(
        solver_id=mode.solver_id,
        routing_mode=mode,
        frames=tuple(frames),
        mass_balance=final_ledger,
        stability=StabilityDiagnostics(
            min_time_step_s=overall_min_dt,
            max_courant=overall_max_courant,
            max_substeps=max_substeps,
        ),
        quality_status=quality,
    )


async def solve_network_to_sink(
    network: HydraulicNetwork,
    rainfall: RainfallForcing,
    frame_sink: Any,
    *,
    routing_mode: RoutingMode | str = RoutingMode.MOTION_WAVE,
    config: SolverConfig,
) -> SimulationResult:
    """Run the fallback solver without retaining the complete frame history.

    ``frame_sink.write`` is awaited once per reporting frame from the solver
    worker thread.  The returned result contains diagnostics and an empty
    ``frames`` tuple; callers must use the attempt-scoped sink for persistence.
    """

    loop = asyncio.get_running_loop()

    def forward(frame: FrameResult) -> None:
        future = asyncio.run_coroutine_threadsafe(frame_sink.write(frame), loop)
        future.result()

    return await asyncio.to_thread(
        solve_network,
        network,
        rainfall,
        routing_mode=routing_mode,
        config=config,
        frame_callback=forward,
        retain_frames=False,
    )


def _rounded(value: float, digits: int = 12) -> float:
    return round(float(value), digits)


__all__ = [
    "Catchment",
    "FlowDirection",
    "FrameResult",
    "HydraulicNetwork",
    "HydraulicNode",
    "HydraulicPipe",
    "HydraulicSolverError",
    "MassBalanceError",
    "MassBalanceLedger",
    "NetworkValidationError",
    "NodeResult",
    "NodeState",
    "NumericalStabilityError",
    "PipeResult",
    "PipeState",
    "QualityStatus",
    "RainfallForcing",
    "RoutingMode",
    "SimulationResult",
    "SolverConfig",
    "StabilityDiagnostics",
    "UnsupportedRegimeError",
    "courant_number",
    "solve_network",
    "solve_network_to_sink",
    "stable_time_step",
]
