"""Reduced-fidelity surface ponding for the hydraulic twin.

This module deliberately models one storage balance per mapped catchment/pond
cell.  It is a screening model, not a two-dimensional shallow-water solver.
The public inputs are immutable, database-free records so the same code can be
used by a worker, a calibration harness, or a unit test.

The model has four important properties:

* input magnitudes and geometry are validated before any balance is run;
* node overflow and overloaded pipes are transferred only to an explicitly
  resolved catchment (or a documented point-in-polygon fallback);
* the balance is volume based, so depths and areas can never be negative; and
* with no rainfall and no source inflow, depth, flooded area, and volume are
  monotonically non-increasing and eventually reach the dry state.

The output GeoJSON is an explicitly labelled ``simplified_extent``.  It is
not a claim of hydrodynamic truth or building/road-level inundation.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, is_dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, cast

from app.services.hydraulic.geometry import (
    canonical_geojson,
    parse_geojson,
    parse_wkt,
    validate_geometry,
)

UTC = timezone.utc
MODEL_VERSION = "surface-flood-simplified-v1"
FRAME_SCHEMA_VERSION = "surface-flood-frame-v1"
GEOJSON_SCHEMA_VERSION = "surface-flood-geojson-v1"
_EPSILON = 1e-12


class _FrozenMapping(dict[Any, Any]):
    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("surface-flood mappings are immutable")

    __setitem__ = _immutable  # type: ignore[assignment]
    __delitem__ = _immutable  # type: ignore[assignment]
    clear = _immutable  # type: ignore[assignment]
    pop = _immutable  # type: ignore[assignment]
    popitem = _immutable  # type: ignore[assignment]
    setdefault = _immutable  # type: ignore[assignment]
    update = _immutable  # type: ignore[assignment]


class _FrozenList(list[Any]):
    def _immutable(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("surface-flood sequences are immutable")

    __setitem__ = _immutable  # type: ignore[assignment]
    __delitem__ = _immutable  # type: ignore[assignment]
    append = _immutable  # type: ignore[assignment]
    clear = _immutable  # type: ignore[assignment]
    extend = _immutable  # type: ignore[assignment]
    insert = _immutable  # type: ignore[assignment]
    pop = _immutable  # type: ignore[assignment]
    remove = _immutable  # type: ignore[assignment]
    reverse = _immutable  # type: ignore[assignment]
    sort = _immutable  # type: ignore[assignment]


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenMapping(
            {str(key): _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return _FrozenList(_freeze_value(item) for item in value)
    return value


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_value(item) for item in value]
    return value


class SurfaceFloodError(ValueError):
    """Base error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        self.code = code
        self.details = dict(details)
        super().__init__(message)


class SurfaceFloodValidationError(SurfaceFloodError):
    """Raised when an input record or a generated frame is invalid."""


class FloodStatus(str, Enum):
    """Public state of one simplified ponding cell."""

    dry = "dry"
    surface_flow = "surface_flow"
    accumulating = "accumulating"
    flooded = "flooded"
    receding = "receding"
    steady = "steady"


# ``SurfaceFloodState`` is a useful spelling for callers that model the value
# as a state enum.  Keep ``FloodStatus`` as the canonical exported name.
SurfaceFloodState = FloodStatus


@dataclass(frozen=True, slots=True, init=False)
class SurfaceFloodConfig:
    """Numerical and reporting policy for the reduced ponding balance.

    ``recession_rate_m_per_s`` is a depth recession rate.  It is converted to
    a volume loss with the cell's full plan area for each reporting interval.
    ``max_depth_m`` is only the reference depth used to turn a water volume
    into a simplified flooded-area fraction; it is not a cap on water depth.

    The alias keyword arguments are intentionally accepted because older
    callers used ``*_mps``/``min_depth_m``/``pipe_overload_threshold`` names.
    The canonical dataclass fields remain the names used in serialized output.
    """

    recession_rate_m_per_s: float
    flood_depth_threshold_m: float
    reporting_depth_threshold_m: float
    max_depth_m: float
    node_overflow_threshold_m: float
    pipe_overload_threshold_ratio: float
    overflow_depth_volume_fraction: float
    default_pipe_capacity_m3s: float
    include_dry_features: bool
    strict_mapping: bool

    def __init__(
        self,
        recession_rate_m_per_s: float = 1.0e-5,
        flood_depth_threshold_m: float = 0.15,
        reporting_depth_threshold_m: float = 0.0,
        max_depth_m: float = 1.0,
        node_overflow_threshold_m: float = 0.0,
        pipe_overload_threshold_ratio: float = 1.0,
        overflow_depth_volume_fraction: float = 0.1,
        default_pipe_capacity_m3s: float = 1.0,
        include_dry_features: bool = False,
        strict_mapping: bool = False,
        *,
        recession_rate_mps: float | None = None,
        flood_threshold_m: float | None = None,
        min_depth_m: float | None = None,
        pipe_overload_threshold: float | None = None,
        max_flood_depth_m: float | None = None,
    ) -> None:
        if recession_rate_mps is not None:
            recession_rate_m_per_s = recession_rate_mps
        if flood_threshold_m is not None:
            flood_depth_threshold_m = flood_threshold_m
        if min_depth_m is not None:
            reporting_depth_threshold_m = min_depth_m
        if pipe_overload_threshold is not None:
            pipe_overload_threshold_ratio = pipe_overload_threshold
        if max_flood_depth_m is not None:
            max_depth_m = max_flood_depth_m

        values = {
            "recession_rate_m_per_s": recession_rate_m_per_s,
            "flood_depth_threshold_m": flood_depth_threshold_m,
            "reporting_depth_threshold_m": reporting_depth_threshold_m,
            "max_depth_m": max_depth_m,
            "node_overflow_threshold_m": node_overflow_threshold_m,
            "pipe_overload_threshold_ratio": pipe_overload_threshold_ratio,
            "overflow_depth_volume_fraction": overflow_depth_volume_fraction,
            "default_pipe_capacity_m3s": default_pipe_capacity_m3s,
        }
        checked = {name: _number(value, name) for name, value in values.items()}
        if checked["recession_rate_m_per_s"] < 0:
            raise SurfaceFloodValidationError(
                "INVALID_RECESSION_RATE", "recession_rate_m_per_s must be non-negative"
            )
        for name in (
            "flood_depth_threshold_m",
            "reporting_depth_threshold_m",
            "node_overflow_threshold_m",
        ):
            if checked[name] < 0:
                raise SurfaceFloodValidationError(
                    "INVALID_THRESHOLD", f"{name} must be non-negative", field=name
                )
        if checked["max_depth_m"] <= 0:
            raise SurfaceFloodValidationError(
                "INVALID_MAX_DEPTH", "max_depth_m must be greater than zero"
            )
        if checked["pipe_overload_threshold_ratio"] < 0:
            raise SurfaceFloodValidationError(
                "INVALID_THRESHOLD",
                "pipe_overload_threshold_ratio must be non-negative",
            )
        if checked["overflow_depth_volume_fraction"] < 0:
            raise SurfaceFloodValidationError(
                "INVALID_FRACTION",
                "overflow_depth_volume_fraction must be non-negative",
            )
        if checked["default_pipe_capacity_m3s"] < 0:
            raise SurfaceFloodValidationError(
                "INVALID_CAPACITY", "default_pipe_capacity_m3s must be non-negative"
            )
        if not isinstance(include_dry_features, bool) or not isinstance(
            strict_mapping, bool
        ):
            raise TypeError("include_dry_features and strict_mapping must be bool")
        for name, value in checked.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "include_dry_features", include_dry_features)
        object.__setattr__(self, "strict_mapping", strict_mapping)

    @property
    def recession_rate_mps(self) -> float:
        return self.recession_rate_m_per_s

    @property
    def min_depth_m(self) -> float:
        return self.reporting_depth_threshold_m

    @property
    def pipe_overload_threshold(self) -> float:
        return self.pipe_overload_threshold_ratio

    @property
    def max_flood_depth_m(self) -> float:
        return self.max_depth_m

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_version": MODEL_VERSION,
            "recession_rate_m_per_s": self.recession_rate_m_per_s,
            "flood_depth_threshold_m": self.flood_depth_threshold_m,
            "reporting_depth_threshold_m": self.reporting_depth_threshold_m,
            "max_depth_m": self.max_depth_m,
            "node_overflow_threshold_m": self.node_overflow_threshold_m,
            "pipe_overload_threshold_ratio": self.pipe_overload_threshold_ratio,
            "overflow_depth_volume_fraction": self.overflow_depth_volume_fraction,
            "default_pipe_capacity_m3s": self.default_pipe_capacity_m3s,
            "include_dry_features": self.include_dry_features,
            "strict_mapping": self.strict_mapping,
        }


@dataclass(frozen=True, slots=True)
class FloodStageAreaPoint:
    stage_m: float
    area_m2: float


@dataclass(frozen=True, slots=True)
class FloodCellLink:
    stable_key: str
    from_catchment_id: str
    to_catchment_id: str
    crest_elevation_m: float = 0.0
    width_m: float = 1.0
    roughness_manning_n: float = 0.05
    length_m: float = 100.0
    max_capacity_m3s: float | None = None
    direction: str = "forward"


@dataclass(frozen=True, slots=True)
class FloodSewerExchange:
    stable_key: str
    node_key: str
    direction: str = "inflow"
    capacity_m3s: float | None = None


@dataclass(frozen=True, slots=True)
class SurfaceFloodTransfer:
    source: str
    target: str
    kind: str
    volume_m3: float


@dataclass(frozen=True, slots=True)
class FloodCatchment:
    """Normalized immutable catchment/pond-cell input."""

    stable_key: str
    area_m2: float
    geometry: Any
    outlet_node_key: str | None = None
    elevation_m: float | None = None
    device_elevation_m: float | None = None
    device_ids: tuple[str, ...] = ()
    stage_area_curve: tuple[tuple[float, float], ...] = ()
    roughness_manning_n: float = 0.05
    depression_storage_m3: float = 0.0
    cell_links: tuple[FloodCellLink, ...] = ()
    sewer_exchanges: tuple[FloodSewerExchange, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "geometry", _freeze_value(self.geometry))
        for name in (
            "device_ids",
            "stage_area_curve",
            "cell_links",
            "sewer_exchanges",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True, slots=True)
class FloodNode:
    """Normalized node input and current overflow indicators."""

    stable_key: str
    longitude: float | None = None
    latitude: float | None = None
    invert_elevation_m: float | None = None
    rim_elevation_m: float | None = None
    overflow_m3s: float = 0.0
    overflow_depth_m: float | None = None
    surcharged: bool = False
    catchment_id: str | None = None


@dataclass(frozen=True, slots=True)
class FloodPipe:
    """Normalized pipe input and current overload indicators."""

    stable_key: str
    upstream_node_key: str
    downstream_node_key: str
    fill_ratio: float = 0.0
    overload_ratio: float | None = None
    flow_m3s: float = 0.0
    overflow_m3s: float = 0.0
    capacity_m3s: float | None = None
    surcharged: bool = False
    catchment_id: str | None = None


@dataclass(frozen=True, slots=True)
class FloodDevice:
    """A device used for explicit or geometric catchment membership mapping."""

    stable_key: str
    longitude: float | None = None
    latitude: float | None = None
    elevation_m: float | None = None
    catchment_id: str | None = None
    node_key: str | None = None


# Explicit, descriptive aliases for downstream solver code.
SurfaceFloodCatchment = FloodCatchment
SurfaceFloodNode = FloodNode
SurfaceFloodPipe = FloodPipe
SurfaceFloodDevice = FloodDevice
CatchmentFloodInput = FloodCatchment
NodeFloodInput = FloodNode
PipeFloodInput = FloodPipe
DeviceFloodInput = FloodDevice


@dataclass(frozen=True, slots=True)
class CatchmentMapping:
    """Deterministic source/device-to-catchment mapping for one revision."""

    node_to_catchment: Mapping[str, str] = field(default_factory=dict)
    pipe_to_catchment: Mapping[str, str] = field(default_factory=dict)
    device_to_catchment: Mapping[str, str] = field(default_factory=dict)
    catchment_to_devices: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    unmapped_nodes: tuple[str, ...] = ()
    unmapped_pipes: tuple[str, ...] = ()
    unmapped_devices: tuple[str, ...] = ()
    ambiguous_nodes: tuple[str, ...] = ()
    ambiguous_pipes: tuple[str, ...] = ()
    ambiguous_devices: tuple[str, ...] = ()
    unmapped_sources: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "node_to_catchment",
            "pipe_to_catchment",
            "device_to_catchment",
            "catchment_to_devices",
        ):
            object.__setattr__(self, name, _freeze_value(getattr(self, name)))
        for name in (
            "unmapped_nodes",
            "unmapped_pipes",
            "unmapped_devices",
            "ambiguous_nodes",
            "ambiguous_pipes",
            "ambiguous_devices",
            "unmapped_sources",
            "warnings",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    @property
    def complete(self) -> bool:
        return not (
            self.unmapped_nodes
            or self.unmapped_pipes
            or self.unmapped_devices
            or self.ambiguous_nodes
            or self.ambiguous_pipes
            or self.ambiguous_devices
            or self.unmapped_sources
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_to_catchment": dict(sorted(self.node_to_catchment.items())),
            "pipe_to_catchment": dict(sorted(self.pipe_to_catchment.items())),
            "device_to_catchment": dict(sorted(self.device_to_catchment.items())),
            "catchment_to_devices": {
                key: list(value)
                for key, value in sorted(self.catchment_to_devices.items())
            },
            "unmapped_nodes": list(self.unmapped_nodes),
            "unmapped_pipes": list(self.unmapped_pipes),
            "unmapped_devices": list(self.unmapped_devices),
            "ambiguous_nodes": list(self.ambiguous_nodes),
            "ambiguous_pipes": list(self.ambiguous_pipes),
            "ambiguous_devices": list(self.ambiguous_devices),
            "unmapped_sources": list(self.unmapped_sources),
            "complete": self.complete,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class SurfaceFloodCellState:
    """One catchment's reduced storage/extent state for a frame."""

    catchment_id: str
    feature_index: int
    total_area_m2: float
    depth_m: float
    area_m2: float
    volume_m3: float
    reference_elevation_m: float
    device_elevation_m: float | None
    water_surface_elevation_m: float
    rainfall_m3: float
    inflow_m3: float
    outflow_m3: float
    status: FloodStatus
    threshold_exceeded: bool
    source_ids: tuple[str, ...] = ()
    source_types: tuple[str, ...] = ()
    device_ids: tuple[str, ...] = ()
    stage_area_curve: tuple[tuple[float, float], ...] = ()
    roughness_manning_n: float = 0.05
    depression_storage_m3: float = 0.0
    cell_link_ids: tuple[str, ...] = ()
    sewer_exchange_ids: tuple[str, ...] = ()
    geometry: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "geometry", _freeze_value(self.geometry))
        for name in (
            "source_ids",
            "source_types",
            "device_ids",
            "stage_area_curve",
            "cell_link_ids",
            "sewer_exchange_ids",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    @property
    def stable_key(self) -> str:
        return self.catchment_id

    @property
    def flooded_area_m2(self) -> float:
        return self.area_m2

    @property
    def flooded(self) -> bool:
        return self.depth_m > _EPSILON and self.area_m2 > _EPSILON

    @property
    def polygon(self) -> Mapping[str, Any]:
        return self.geometry

    def to_dict(self) -> dict[str, Any]:
        return {
            "catchment_id": self.catchment_id,
            "stable_key": self.catchment_id,
            "feature_index": self.feature_index,
            "total_area_m2": self.total_area_m2,
            "area_m2": self.area_m2,
            "flooded_area_m2": self.area_m2,
            "depth_m": self.depth_m,
            "volume_m3": self.volume_m3,
            "reference_elevation_m": self.reference_elevation_m,
            "device_elevation_m": self.device_elevation_m,
            "water_surface_elevation_m": self.water_surface_elevation_m,
            "rainfall_m3": self.rainfall_m3,
            "inflow_m3": self.inflow_m3,
            "outflow_m3": self.outflow_m3,
            "status": self.status.value,
            "threshold_exceeded": self.threshold_exceeded,
            "source_ids": list(self.source_ids),
            "source_types": list(self.source_types),
            "device_ids": list(self.device_ids),
            "stage_area_curve": [
                {"stage_m": stage, "area_m2": area}
                for stage, area in self.stage_area_curve
            ],
            "roughness_manning_n": self.roughness_manning_n,
            "depression_storage_m3": self.depression_storage_m3,
            "cell_link_ids": list(self.cell_link_ids),
            "sewer_exchange_ids": list(self.sewer_exchange_ids),
            "simplified_extent": True,
        }


@dataclass(frozen=True, slots=True)
class SurfaceFloodFrame:
    """A serializable reduced-fidelity ponding frame."""

    frame_index: int
    valid_at: datetime
    dt_seconds: float
    rainfall_mm: float
    cells: tuple[SurfaceFloodCellState, ...]
    mapping: CatchmentMapping
    geojson: Mapping[str, Any]
    transfer_ledger: tuple[SurfaceFloodTransfer, ...] = ()
    network_id: str = "local"
    network_revision_id: str = "local"
    model_version: str = MODEL_VERSION
    source_catchments: tuple[FloodCatchment, ...] = ()
    source_nodes: tuple[FloodNode, ...] = ()
    source_pipes: tuple[FloodPipe, ...] = ()
    source_devices: tuple[FloodDevice, ...] = ()
    config: SurfaceFloodConfig | None = None
    attempt_id: Any = None
    simulation_id: Any = None
    solver_id: str = "py_ponding"
    solver_version: str = MODEL_VERSION
    target_manifest_sha256: str | None = None
    quality_status: str = "warning"

    def __post_init__(self) -> None:
        object.__setattr__(self, "cells", tuple(self.cells))
        object.__setattr__(self, "transfer_ledger", tuple(self.transfer_ledger))
        for name in (
            "source_catchments",
            "source_nodes",
            "source_pipes",
            "source_devices",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "geojson", _freeze_value(self.geojson))
        if self.quality_status not in {"verified", "warning", "failed"}:
            raise SurfaceFloodValidationError(
                "INVALID_QUALITY_STATUS",
                "surface-flood quality_status is invalid",
            )

    @property
    def feature_index(self) -> tuple[int, ...]:
        return tuple(cell.feature_index for cell in self.cells)

    @property
    def depth_m(self) -> tuple[float, ...]:
        return tuple(cell.depth_m for cell in self.cells)

    @property
    def area_m2(self) -> tuple[float, ...]:
        return tuple(cell.area_m2 for cell in self.cells)

    @property
    def flooded_cells(self) -> tuple[SurfaceFloodCellState, ...]:
        return tuple(cell for cell in self.cells if cell.flooded)

    @property
    def ponding(self) -> dict[str, list[Any]]:
        """Frame-oriented values consumed by the existing map/cache contract."""

        return {
            "feature_index": [cell.feature_index for cell in self.cells],
            "depth_m": [cell.depth_m for cell in self.cells],
            "flow_m3s": [
                cell.inflow_m3 / self.dt_seconds if self.dt_seconds > 0 else 0.0
                for cell in self.cells
            ],
            "area_m2": [cell.area_m2 for cell in self.cells],
            "status": [cell.status.value for cell in self.cells],
        }

    @property
    def geojson_frame(self) -> dict[str, Any]:
        return _deep_copy_json(self.geojson)

    def to_geojson(self) -> dict[str, Any]:
        return self.geojson_frame

    def geojson_json(self) -> str:
        return _canonical_json(self.geojson_frame)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FRAME_SCHEMA_VERSION,
            "model_version": self.model_version,
            "model_fidelity": "reduced_fidelity",
            "is_authoritative": False,
            "attempt_id": _json_scalar(self.attempt_id),
            "simulation_id": _json_scalar(self.simulation_id),
            "solver_id": self.solver_id,
            "solver_version": self.solver_version,
            "target_manifest_sha256": self.target_manifest_sha256,
            "quality_status": self.quality_status,
            "network_id": self.network_id,
            "network_revision_id": self.network_revision_id,
            "frame_index": self.frame_index,
            "valid_at": self.valid_at.isoformat(),
            "dt_seconds": self.dt_seconds,
            "rainfall_mm": self.rainfall_mm,
            "ponding": self.ponding,
            "cells": [cell.to_dict() for cell in self.cells],
            "catchment_mapping": self.mapping.to_dict(),
            "transfer_ledger": [
                {
                    "source": item.source,
                    "target": item.target,
                    "kind": item.kind,
                    "volume_m3": item.volume_m3,
                }
                for item in self.transfer_ledger
            ],
            "geojson": self.geojson_frame,
        }

    def content_sha256(self) -> str:
        return hashlib.sha256(
            _canonical_json(self.to_dict()).encode("utf-8")
        ).hexdigest()

    def to_solver_frame(
        self,
        *,
        input_manifest: Any,
        attempt_id: Any,
        simulation_id: Any,
        solver_id: str = "py_ponding",
        solver_version: str | None = None,
    ) -> Any:
        """Bind this local frame to one immutable attempt for persistence."""

        from app.services.hydraulic.solvers.base import SimulationFrame

        if attempt_id is None or simulation_id is None:
            raise SurfaceFloodValidationError(
                "MISSING_ATTEMPT_IDENTITY",
                "attempt_id and simulation_id are required for publication",
            )
        if (
            input_manifest.solver_id != solver_id
            or (
                self.target_manifest_sha256 is not None
                and input_manifest.target_manifest_sha256 != self.target_manifest_sha256
            )
            or (
                solver_version is not None
                and input_manifest.solver_version != solver_version
            )
        ):
            raise SurfaceFloodValidationError(
                "ATTEMPT_IDENTITY_CONFLICT",
                "surface-flood frame does not match the solver input manifest",
            )
        quality = "verified" if self.mapping.complete else "warning"
        payload = self.to_dict()
        payload["quality_status"] = quality
        payload["input_manifest_sha256"] = input_manifest.input_sha256
        return SimulationFrame(
            attempt_id=attempt_id,
            simulation_id=simulation_id,
            frame_index=self.frame_index,
            valid_at=self.valid_at,
            payload=payload,
            solver_id=solver_id,
            solver_version=input_manifest.solver_version,
            target_manifest_sha256=input_manifest.target_manifest_sha256,
            quality_status=quality,
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _thaw_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _deep_copy_json(value: Any) -> dict[str, Any]:
    # JSON round-tripping also proves that a frame contains no NaN/Inf or
    # non-transportable object before it reaches a worker/API boundary.
    return cast(dict[str, Any], json.loads(_canonical_json(value)))


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise SurfaceFloodValidationError(
            "INVALID_NUMBER", f"{field_name} must be a finite number", field=field_name
        )
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SurfaceFloodValidationError(
            "INVALID_NUMBER", f"{field_name} must be a finite number", field=field_name
        ) from exc
    if not math.isfinite(number):
        raise SurfaceFloodValidationError(
            "NON_FINITE", f"{field_name} must be finite", field=field_name
        )
    return number


def _optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    return _number(value, field_name)


def _nonnegative(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if number < 0:
        raise SurfaceFloodValidationError(
            "NEGATIVE_INPUT", f"{field_name} must be non-negative", field=field_name
        )
    return number


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise SurfaceFloodValidationError(
            "INVALID_BOOLEAN",
            f"{field_name} must be a boolean",
            field=field_name,
        )
    return value


def _key(value: Any, field_name: str = "stable_key") -> str:
    if value is None:
        raise SurfaceFloodValidationError(
            "MISSING_KEY", f"{field_name} is required", field=field_name
        )
    text = str(value).strip()
    if not text:
        raise SurfaceFloodValidationError(
            "MISSING_KEY", f"{field_name} must be non-empty", field=field_name
        )
    return text


def _record(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value):
        return {
            field.name: getattr(value, field.name)
            for field in value.__dataclass_fields__.values()
        }
    result: dict[str, Any] = {}
    for name in (
        "stable_key",
        "catchment_id",
        "catchment_key",
        "node_key",
        "id",
        "key",
        "geometry",
        "catchment_geometry",
        "geometry_wkt",
        "geometry_geojson",
        "area_m2",
        "area",
        "longitude",
        "latitude",
        "lon",
        "lat",
        "x",
        "y",
        "invert_elevation_m",
        "rim_elevation_m",
        "elevation_m",
        "altitude",
        "overflow_m3s",
        "overflow_rate_m3s",
        "node_overflow_m3s",
        "overflow_flow_m3s",
        "overflow_depth_m",
        "surcharge_depth_m",
        "node_overflow_depth_m",
        "overflow_level_m",
        "surcharged",
        "upstream_node_key",
        "downstream_node_key",
        "upstream_node_id",
        "downstream_node_id",
        "fill_ratio",
        "overload_ratio",
        "surcharge_ratio",
        "pipe_overload",
        "overload",
        "flow_m3s",
        "capacity_m3s",
        "pipe_capacity_m3s",
        "node_key",
        "device_id",
        "device_key",
    ):
        if hasattr(value, name):
            result[name] = getattr(value, name)
    return result


def _get(record: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in record:
            return record[name]
    return default


def _coordinates(
    record: Mapping[str, Any], *, required: bool = False
) -> tuple[float | None, float | None]:
    geometry = _get(record, "geometry", "location")
    if isinstance(geometry, Mapping):
        if geometry.get("type") == "Point":
            coordinates = geometry.get("coordinates")
            if (
                isinstance(coordinates, Sequence)
                and not isinstance(coordinates, (str, bytes))
                and len(coordinates) >= 2
            ):
                return (
                    _number(coordinates[0], "longitude"),
                    _number(coordinates[1], "latitude"),
                )
        if "coordinates" in geometry and geometry.get("type") in {"Point", None}:
            coordinates = geometry.get("coordinates")
            if (
                isinstance(coordinates, Sequence)
                and not isinstance(coordinates, (str, bytes))
                and len(coordinates) >= 2
            ):
                return (
                    _number(coordinates[0], "longitude"),
                    _number(coordinates[1], "latitude"),
                )
    longitude = _get(record, "longitude", "lon", "x")
    latitude = _get(record, "latitude", "lat", "y")
    if longitude is None and latitude is None:
        if required:
            raise SurfaceFloodValidationError(
                "MISSING_COORDINATES",
                "device or node coordinates are required for mapping",
            )
        return None, None
    if longitude is None or latitude is None:
        raise SurfaceFloodValidationError(
            "MISSING_COORDINATES", "longitude and latitude must be supplied together"
        )
    lon = _number(longitude, "longitude")
    lat = _number(latitude, "latitude")
    if not -180 <= lon <= 180 or not -90 <= lat <= 90:
        raise SurfaceFloodValidationError(
            "INVALID_COORDINATES", "coordinates must be EPSG:4326 lon/lat"
        )
    return lon, lat


def _parse_catchment_geometry(value: Any) -> dict[str, Any]:
    if isinstance(value, FloodCatchment):
        value = value.geometry
    if isinstance(value, Mapping):
        nested = value.get("geometry") if "geometry" in value else None
        if nested is not None and "type" not in value:
            value = nested
        if "geometry_wkt" in value:
            raw: Any = value["geometry_wkt"]
        elif "geometry_geojson" in value:
            raw = value["geometry_geojson"]
        else:
            raw = value
    else:
        raw = value
    raw = _thaw_value(raw)
    try:
        if hasattr(raw, "geometry_geojson") and hasattr(raw, "geometry_type"):
            geometry = raw
        elif isinstance(raw, str) and raw.lstrip().upper().startswith(
            ("POLYGON", "MULTIPOLYGON")
        ):
            geometry = parse_wkt(raw, entity_type="catchment")
        else:
            geometry = parse_geojson(raw, entity_type="catchment")
        # Parsing already performs the strict schema/topology validation.  Do
        # not round-trip a GeometryDocument through model_dump a second time:
        # pydantic can otherwise compare differently formatted WKT/GeoJSON
        # coordinates (for example integer 0 versus float 0.0) and reject an
        # otherwise valid polygon.
        geometry.require_entity_geometry("catchment")
    except (AttributeError, TypeError, ValueError, RecursionError) as exc:
        raise SurfaceFloodValidationError(
            "INVALID_GEOJSON", f"invalid catchment geometry: {exc}"
        ) from exc
    return cast(dict[str, Any], json.loads(canonical_geojson(geometry)))


def _bbox_from_geometry(
    geometry: Mapping[str, Any]
) -> tuple[float, float, float, float]:
    points: list[tuple[float, float]] = []

    def visit(value: Any) -> None:
        if (
            isinstance(value, Sequence)
            and not isinstance(value, (str, bytes))
            and len(value) >= 2
            and all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in value[:2]
            )
        ):
            points.append((float(value[0]), float(value[1])))
            return
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for child in value:
                visit(child)

    visit(geometry.get("coordinates", ()))
    if not points:
        raise SurfaceFloodValidationError(
            "INVALID_GEOJSON", "geometry has no coordinates"
        )
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _point_on_segment(
    x: float, y: float, a: Sequence[float], b: Sequence[float]
) -> bool:
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
    if abs(cross) > 1e-12:
        return False
    return (
        min(ax, bx) - 1e-12 <= x <= max(ax, bx) + 1e-12
        and min(ay, by) - 1e-12 <= y <= max(ay, by) + 1e-12
    )


def _point_in_ring(x: float, y: float, ring: Sequence[Sequence[float]]) -> bool:
    inside = False
    for index, current in enumerate(ring):
        following = ring[(index + 1) % len(ring)]
        if _point_on_segment(x, y, current, following):
            return True
        x1, y1 = float(current[0]), float(current[1])
        x2, y2 = float(following[0]), float(following[1])
        if (y1 > y) != (y2 > y):
            crossing_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing_x:
                inside = not inside
    return inside


def _point_in_polygon(
    x: float, y: float, polygon: Sequence[Sequence[Sequence[float]]]
) -> bool:
    if not polygon or not _point_in_ring(x, y, polygon[0]):
        return False
    return not any(_point_in_ring(x, y, hole) for hole in polygon[1:])


def _point_in_geometry(x: float, y: float, geometry: Mapping[str, Any]) -> bool:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon" and isinstance(coordinates, Sequence):
        return _point_in_polygon(x, y, coordinates)
    if geometry_type == "MultiPolygon" and isinstance(coordinates, Sequence):
        return any(_point_in_polygon(x, y, polygon) for polygon in coordinates)
    return False


def _normalize_stage_area_curve(
    value: Any, total_area_m2: float
) -> tuple[tuple[float, float], ...]:
    if value is None or (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and not value
    ):
        return ()
    if isinstance(value, Mapping):
        raw_points = value.get("points", value.get("curve", ()))
    else:
        raw_points = value
    if isinstance(raw_points, (str, bytes)) or not isinstance(raw_points, Sequence):
        raise SurfaceFloodValidationError(
            "INVALID_STAGE_AREA_CURVE",
            "stage_area_curve must contain an ordered point sequence",
        )
    points: list[tuple[float, float]] = []
    for item in raw_points:
        if isinstance(item, Mapping):
            stage = _number(item.get("stage_m", item.get("stage")), "stage_m")
            area = _number(item.get("area_m2", item.get("area")), "area_m2")
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)):
            if len(item) != 2:
                raise SurfaceFloodValidationError(
                    "INVALID_STAGE_AREA_CURVE",
                    "stage-area points must contain stage_m and area_m2",
                )
            stage = _number(item[0], "stage_m")
            area = _number(item[1], "area_m2")
        else:
            raise SurfaceFloodValidationError(
                "INVALID_STAGE_AREA_CURVE",
                "invalid stage-area point",
            )
        if (
            stage < 0.0
            or area < 0.0
            or area > total_area_m2 + _EPSILON
            or (stage > 0.0 and area <= 0.0)
        ):
            raise SurfaceFloodValidationError(
                "INVALID_STAGE_AREA_CURVE",
                "stage-area values must be non-negative and within catchment area",
            )
        if points and stage <= points[-1][0]:
            raise SurfaceFloodValidationError(
                "INVALID_STAGE_AREA_CURVE",
                "stage-area stages must be strictly increasing",
            )
        points.append((stage, area))
    if len(points) < 2 or points[0] != (0.0, 0.0):
        raise SurfaceFloodValidationError(
            "INVALID_STAGE_AREA_CURVE",
            "stage-area curve must start at (0, 0) and contain at least two points",
        )
    return tuple(points)


def _normalize_cell_link(value: Any) -> FloodCellLink:
    record = _record(value)
    direction = str(_get(record, "direction", default="forward")).strip().lower()
    if direction not in {"forward", "bidirectional"}:
        raise SurfaceFloodValidationError(
            "INVALID_CELL_LINK", "cell link direction is invalid"
        )
    capacity = _optional_number(
        _get(record, "max_capacity_m3s", "capacity_m3s"), "max_capacity_m3s"
    )
    if capacity is not None and capacity <= 0.0:
        raise SurfaceFloodValidationError(
            "INVALID_CELL_LINK", "cell-link capacity must be positive"
        )
    width = _nonnegative(_get(record, "width_m", default=1.0), "width_m")
    roughness = _number(
        _get(record, "roughness_manning_n", "manning_n", default=0.05),
        "roughness_manning_n",
    )
    length = _number(_get(record, "length_m", default=100.0), "length_m")
    if width <= 0.0 or roughness <= 0.0 or length <= 0.0:
        raise SurfaceFloodValidationError(
            "INVALID_CELL_LINK",
            "cell-link width, roughness, and length must be positive",
        )
    from_id = _key(
        _get(record, "from_catchment_id", "from_catchment", "source_id"),
        "from_catchment_id",
    )
    to_id = _key(
        _get(record, "to_catchment_id", "to_catchment", "target_id"),
        "to_catchment_id",
    )
    if from_id == to_id:
        raise SurfaceFloodValidationError(
            "INVALID_CELL_LINK", "cell-link endpoints must differ"
        )
    return FloodCellLink(
        stable_key=_key(_get(record, "stable_key", "link_id", "id"), "cell_link_id"),
        from_catchment_id=from_id,
        to_catchment_id=to_id,
        crest_elevation_m=_number(
            _get(record, "crest_elevation_m", "crest_m", default=0.0),
            "crest_elevation_m",
        ),
        width_m=width,
        roughness_manning_n=roughness,
        length_m=length,
        max_capacity_m3s=capacity,
        direction=direction,
    )


def _normalize_sewer_exchange(value: Any) -> FloodSewerExchange:
    record = _record(value)
    direction = str(_get(record, "direction", default="inflow")).strip().lower()
    if direction not in {"inflow", "outflow", "bidirectional"}:
        raise SurfaceFloodValidationError(
            "INVALID_SEWER_EXCHANGE", "sewer-exchange direction is invalid"
        )
    capacity = _optional_number(
        _get(record, "capacity_m3s", "max_capacity_m3s"), "capacity_m3s"
    )
    if capacity is not None and capacity <= 0.0:
        raise SurfaceFloodValidationError(
            "INVALID_SEWER_EXCHANGE", "sewer-exchange capacity must be positive"
        )
    return FloodSewerExchange(
        stable_key=_key(
            _get(record, "stable_key", "exchange_id", "id"),
            "sewer_exchange_id",
        ),
        node_key=_key(
            _get(record, "node_key", "target_node_key", "node_id"), "node_key"
        ),
        direction=direction,
        capacity_m3s=capacity,
    )


def _normalize_catchment(value: Any) -> FloodCatchment:
    record = _record(value)
    stable_key = _key(
        _get(record, "stable_key", "catchment_id", "catchment_key", "id", "key"),
        "catchment_id",
    )
    area = _nonnegative(_get(record, "area_m2", "area", default=0), "area_m2")
    if area <= 0:
        raise SurfaceFloodValidationError(
            "INVALID_AREA",
            "catchment area_m2 must be greater than zero",
            catchment_id=stable_key,
        )
    geometry_value = _get(
        record, "geometry", "catchment_geometry", "geometry_wkt", "geometry_geojson"
    )
    if geometry_value is None:
        raise SurfaceFloodValidationError(
            "MISSING_GEOMETRY",
            "catchment geometry is required",
            catchment_id=stable_key,
        )
    geometry = _parse_catchment_geometry(geometry_value)
    outlet = _get(record, "outlet_node_key", "outlet_node_id", "outlet_node")
    outlet = None if outlet is None else _key(outlet, "outlet_node_key")
    elevation = _optional_number(
        _get(record, "elevation_m", "cell_elevation_m", "base_elevation_m"),
        "elevation_m",
    )
    device_elevation = _optional_number(
        _get(record, "device_elevation_m", "device_altitude_m"), "device_elevation_m"
    )
    raw_devices = _get(record, "device_ids", "devices", default=())
    device_ids: tuple[str, ...]
    if isinstance(raw_devices, str):
        device_ids = (raw_devices,)
    else:
        try:
            device_ids = tuple(_key(item, "device_id") for item in raw_devices)
        except TypeError as exc:
            raise SurfaceFloodValidationError(
                "INVALID_DEVICE_IDS", "catchment device_ids must be a sequence"
            ) from exc
    stage_area_curve = _normalize_stage_area_curve(
        _get(record, "stage_area_curve", "stage_area"),
        area,
    )
    roughness = _number(
        _get(record, "roughness_manning_n", "manning_n", default=0.05),
        "roughness_manning_n",
    )
    if roughness <= 0.0:
        raise SurfaceFloodValidationError(
            "INVALID_SURFACE_ROUGHNESS",
            "roughness_manning_n must be positive",
            catchment_id=stable_key,
        )
    depression = _nonnegative(
        _get(
            record,
            "depression_storage_m3",
            "depression_storage_m",
            default=0.0,
        ),
        "depression_storage_m3",
    )
    cell_links = tuple(
        _normalize_cell_link(item)
        for item in _get(record, "cell_links", "links", default=())
    )
    sewer_exchanges = tuple(
        _normalize_sewer_exchange(item)
        for item in _get(
            record,
            "sewer_exchanges",
            "sewer_exchange_links",
            default=(),
        )
    )
    return FloodCatchment(
        stable_key=stable_key,
        area_m2=area,
        geometry=geometry,
        outlet_node_key=outlet,
        elevation_m=elevation,
        device_elevation_m=device_elevation,
        device_ids=device_ids,
        stage_area_curve=stage_area_curve,
        roughness_manning_n=roughness,
        depression_storage_m3=depression,
        cell_links=cell_links,
        sewer_exchanges=sewer_exchanges,
    )


def _normalize_node(value: Any) -> FloodNode:
    record = _record(value)
    key = _key(_get(record, "stable_key", "node_key", "id", "key"), "node_key")
    longitude, latitude = _coordinates(record)
    invert = _optional_number(
        _get(record, "invert_elevation_m", "invert_elevation", "invert"),
        "invert_elevation_m",
    )
    rim = _optional_number(
        _get(record, "rim_elevation_m", "rim_elevation", "elevation_m", "altitude"),
        "rim_elevation_m",
    )
    overflow = _nonnegative(
        _get(
            record,
            "overflow_m3s",
            "overflow_rate_m3s",
            "node_overflow_m3s",
            "overflow_flow_m3s",
            default=0.0,
        ),
        "overflow_m3s",
    )
    depth = _optional_number(
        _get(
            record,
            "overflow_depth_m",
            "surcharge_depth_m",
            "node_overflow_depth_m",
            "overflow_level_m",
        ),
        "overflow_depth_m",
    )
    if depth is not None and depth < 0:
        raise SurfaceFloodValidationError(
            "NEGATIVE_INPUT", "overflow_depth_m must be non-negative", node_key=key
        )
    catchment_id = _get(record, "catchment_id", "catchment_key")
    return FloodNode(
        stable_key=key,
        longitude=longitude,
        latitude=latitude,
        invert_elevation_m=invert,
        rim_elevation_m=rim,
        overflow_m3s=overflow,
        overflow_depth_m=depth,
        surcharged=_boolean(
            _get(record, "surcharged", "overflow", default=False),
            "surcharged",
        ),
        catchment_id=(
            None if catchment_id is None else _key(catchment_id, "catchment_id")
        ),
    )


def _normalize_pipe(value: Any) -> FloodPipe:
    record = _record(value)
    key = _key(_get(record, "stable_key", "pipe_key", "id", "key"), "pipe_key")
    upstream = _key(
        _get(record, "upstream_node_key", "upstream_node_id", "from_node_key"),
        "upstream_node_key",
    )
    downstream = _key(
        _get(record, "downstream_node_key", "downstream_node_id", "to_node_key"),
        "downstream_node_key",
    )
    if upstream == downstream:
        raise SurfaceFloodValidationError(
            "SELF_LOOP", "pipe endpoints must differ", pipe_key=key
        )
    fill = _nonnegative(_get(record, "fill_ratio", default=0.0), "fill_ratio")
    raw_overload = _get(
        record, "overload_ratio", "surcharge_ratio", "pipe_overload", "overload"
    )
    overload = (
        None if raw_overload is None else _nonnegative(raw_overload, "overload_ratio")
    )
    flow = _number(_get(record, "flow_m3s", default=0.0), "flow_m3s")
    overflow = _nonnegative(
        _get(record, "overflow_m3s", "overflow_rate_m3s", default=0.0), "overflow_m3s"
    )
    capacity = _optional_number(
        _get(record, "capacity_m3s", "pipe_capacity_m3s"), "capacity_m3s"
    )
    if capacity is not None and capacity < 0:
        raise SurfaceFloodValidationError(
            "NEGATIVE_INPUT", "capacity_m3s must be non-negative", pipe_key=key
        )
    catchment_id = _get(record, "catchment_id", "catchment_key")
    return FloodPipe(
        stable_key=key,
        upstream_node_key=upstream,
        downstream_node_key=downstream,
        fill_ratio=fill,
        overload_ratio=overload,
        flow_m3s=flow,
        overflow_m3s=overflow,
        capacity_m3s=capacity,
        surcharged=_boolean(
            _get(record, "surcharged", default=False),
            "surcharged",
        ),
        catchment_id=(
            None if catchment_id is None else _key(catchment_id, "catchment_id")
        ),
    )


def _normalize_device(value: Any) -> FloodDevice:
    record = _record(value)
    key = _key(
        _get(record, "stable_key", "device_key", "device_id", "id", "key"), "device_key"
    )
    longitude, latitude = _coordinates(record)
    elevation = _optional_number(
        _get(
            record,
            "elevation_m",
            "altitude_m",
            "altitude",
            "elevation",
            "device_elevation",
        ),
        "elevation_m",
    )
    catchment_id = _get(record, "catchment_id", "catchment_key")
    node_key = _get(record, "node_key", "target_node_key")
    return FloodDevice(
        stable_key=key,
        longitude=longitude,
        latitude=latitude,
        elevation_m=elevation,
        catchment_id=(
            None if catchment_id is None else _key(catchment_id, "catchment_id")
        ),
        node_key=None if node_key is None else _key(node_key, "node_key"),
    )


def _unique_normalized(
    values: Iterable[Any], normalizer: Any, label: str
) -> tuple[Any, ...]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalizer(value)
        key = normalized.stable_key.casefold()
        if key in seen:
            raise SurfaceFloodValidationError(
                "DUPLICATE_KEY",
                f"duplicate {label} stable key",
                stable_key=normalized.stable_key,
            )
        seen.add(key)
        result.append(normalized)
    return tuple(sorted(result, key=lambda item: item.stable_key.encode("utf-8")))


def _explicit_or_none(
    value: str | None, valid_keys: set[str], *, label: str
) -> str | None:
    if value is None:
        return None
    if value not in valid_keys:
        raise SurfaceFloodValidationError(
            "UNKNOWN_CATCHMENT",
            f"{label} references an unknown catchment",
            catchment_id=value,
        )
    return value


def _point_matches(
    longitude: float | None,
    latitude: float | None,
    catchments: Sequence[FloodCatchment],
    geometries: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    if longitude is None or latitude is None:
        return ()
    return tuple(
        catchment.stable_key
        for catchment in catchments
        if _point_in_geometry(longitude, latitude, geometries[catchment.stable_key])
    )


def build_catchment_mapping(
    catchments: Sequence[Any],
    nodes: Sequence[Any] = (),
    pipes: Sequence[Any] = (),
    devices: Sequence[Any] = (),
    *,
    strict: bool = False,
    strict_mapping: bool | None = None,
) -> CatchmentMapping:
    """Resolve node, pipe, and device membership without nearest-point claims.

    Resolution order is deterministic: explicit ``catchment_id``; exact outlet
    node/device binding; then point-in-polygon for records that carry
    coordinates.  Ambiguous or unmapped records are retained in the report.
    ``strict=True`` raises instead of silently discarding an active source.
    """

    if strict_mapping is not None:
        strict = strict_mapping
    normalized_catchments = _unique_normalized(
        catchments, _normalize_catchment, "catchment"
    )
    normalized_nodes = _unique_normalized(nodes, _normalize_node, "node")
    normalized_pipes = _unique_normalized(pipes, _normalize_pipe, "pipe")
    normalized_devices = _unique_normalized(devices, _normalize_device, "device")
    valid = {item.stable_key for item in normalized_catchments}
    valid_nodes = {item.stable_key for item in normalized_nodes}
    valid_devices = {item.stable_key for item in normalized_devices}
    for catchment in normalized_catchments:
        if catchment.outlet_node_key and catchment.outlet_node_key not in valid_nodes:
            raise SurfaceFloodValidationError(
                "DANGLING_REFERENCE",
                "catchment outlet node is missing or dangling",
                catchment_id=catchment.stable_key,
                node_key=catchment.outlet_node_key,
            )
        missing_devices = sorted(set(catchment.device_ids) - valid_devices)
        if missing_devices:
            raise SurfaceFloodValidationError(
                "DANGLING_REFERENCE",
                "catchment references missing devices",
                catchment_id=catchment.stable_key,
                device_ids=missing_devices,
            )
    for pipe in normalized_pipes:
        missing_nodes = sorted(
            {pipe.upstream_node_key, pipe.downstream_node_key} - valid_nodes
        )
        if missing_nodes:
            raise SurfaceFloodValidationError(
                "DANGLING_REFERENCE",
                "pipe endpoint node does not exist",
                pipe_key=pipe.stable_key,
                node_keys=missing_nodes,
            )
    for device in normalized_devices:
        if device.node_key and device.node_key not in valid_nodes:
            raise SurfaceFloodValidationError(
                "DANGLING_REFERENCE",
                "device target node is missing or dangling",
                device_key=device.stable_key,
                node_key=device.node_key,
            )
    seen_link_ids: set[str] = set()
    seen_exchange_ids: set[str] = set()
    for catchment in normalized_catchments:
        for link in catchment.cell_links:
            if link.stable_key in seen_link_ids:
                raise SurfaceFloodValidationError(
                    "DUPLICATE_KEY", "duplicate cell-link stable key"
                )
            seen_link_ids.add(link.stable_key)
            if link.from_catchment_id not in valid or link.to_catchment_id not in valid:
                raise SurfaceFloodValidationError(
                    "DANGLING_REFERENCE",
                    "cell link references a missing catchment",
                )
        for exchange in catchment.sewer_exchanges:
            if exchange.stable_key in seen_exchange_ids:
                raise SurfaceFloodValidationError(
                    "DUPLICATE_KEY", "duplicate sewer-exchange stable key"
                )
            seen_exchange_ids.add(exchange.stable_key)
            if exchange.node_key not in valid_nodes:
                raise SurfaceFloodValidationError(
                    "DANGLING_REFERENCE",
                    "sewer exchange references a missing node",
                )
    geometries = {item.stable_key: item.geometry for item in normalized_catchments}

    device_to_catchment: dict[str, str] = {}
    unmapped_devices: list[str] = []
    ambiguous_devices: list[str] = []
    device_warnings: list[str] = []
    for device in normalized_devices:
        explicit = _explicit_or_none(
            device.catchment_id, valid, label=f"device {device.stable_key}"
        )
        device_matches: tuple[str, ...]
        if explicit:
            device_matches = (explicit,)
        else:
            declared_matches = tuple(
                catchment.stable_key
                for catchment in normalized_catchments
                if device.stable_key in catchment.device_ids
            )
            device_matches = declared_matches or _point_matches(
                device.longitude, device.latitude, normalized_catchments, geometries
            )
        if len(device_matches) == 1:
            device_to_catchment[device.stable_key] = device_matches[0]
        elif len(device_matches) > 1:
            ambiguous_devices.append(device.stable_key)
            device_warnings.append(
                f"device {device.stable_key} lies in multiple "
                f"catchments: {', '.join(device_matches)}"
            )
        else:
            unmapped_devices.append(device.stable_key)
            device_warnings.append(
                f"device {device.stable_key} has no catchment mapping"
            )
    if strict and (unmapped_devices or ambiguous_devices):
        raise SurfaceFloodValidationError(
            "AMBIGUOUS_CATCHMENT_MAPPING",
            "one or more devices could not be mapped unambiguously",
            unmapped_devices=unmapped_devices,
            ambiguous_devices=ambiguous_devices,
        )

    node_to_catchment: dict[str, str] = {}
    unmapped_nodes: list[str] = []
    ambiguous_nodes: list[str] = []
    node_warnings: list[str] = []
    outlet_candidates: dict[str, list[str]] = {}
    for catchment in normalized_catchments:
        if catchment.outlet_node_key:
            outlet_candidates.setdefault(catchment.outlet_node_key, []).append(
                catchment.stable_key
            )
    for node in normalized_nodes:
        explicit = _explicit_or_none(
            node.catchment_id, valid, label=f"node {node.stable_key}"
        )
        node_matches: tuple[str, ...]
        if explicit:
            node_matches = (explicit,)
        else:
            outlet_matches = tuple(sorted(outlet_candidates.get(node.stable_key, ())))
            if outlet_matches:
                node_matches = outlet_matches
            else:
                node_matches = _point_matches(
                    node.longitude, node.latitude, normalized_catchments, geometries
                )
        if len(node_matches) == 1:
            node_to_catchment[node.stable_key] = node_matches[0]
        elif len(node_matches) > 1:
            ambiguous_nodes.append(node.stable_key)
            node_warnings.append(
                f"node {node.stable_key} maps to multiple "
                f"catchments: {', '.join(node_matches)}"
            )
        else:
            unmapped_nodes.append(node.stable_key)
            node_warnings.append(f"node {node.stable_key} has no catchment mapping")
    if strict and (unmapped_nodes or ambiguous_nodes):
        raise SurfaceFloodValidationError(
            "AMBIGUOUS_CATCHMENT_MAPPING",
            "one or more nodes could not be mapped unambiguously",
            unmapped_nodes=unmapped_nodes,
            ambiguous_nodes=ambiguous_nodes,
        )

    # A device with an explicit node binding inherits that node's catchment.
    for device in normalized_devices:
        if device.stable_key in device_to_catchment or device.node_key is None:
            continue
        node_match = node_to_catchment.get(device.node_key)
        if node_match is not None:
            device_to_catchment[device.stable_key] = node_match

    pipe_to_catchment: dict[str, str] = {}
    unmapped_pipes: list[str] = []
    ambiguous_pipes: list[str] = []
    pipe_warnings: list[str] = []
    for pipe in normalized_pipes:
        explicit = _explicit_or_none(
            pipe.catchment_id, valid, label=f"pipe {pipe.stable_key}"
        )
        endpoint_matches = {
            node_to_catchment[endpoint]
            for endpoint in (pipe.upstream_node_key, pipe.downstream_node_key)
            if endpoint in node_to_catchment
        }
        if len(endpoint_matches) > 1:
            ambiguous_pipes.append(pipe.stable_key)
            pipe_warnings.append(
                f"pipe {pipe.stable_key} crosses catchments: "
                + ", ".join(sorted(endpoint_matches))
            )
            continue
        match = explicit or next(iter(endpoint_matches), None)
        if match is not None:
            pipe_to_catchment[pipe.stable_key] = match
        else:
            unmapped_pipes.append(pipe.stable_key)
            pipe_warnings.append(f"pipe {pipe.stable_key} has no catchment mapping")
    if strict and (unmapped_pipes or ambiguous_pipes):
        raise SurfaceFloodValidationError(
            "AMBIGUOUS_CATCHMENT_MAPPING",
            "one or more pipes are ambiguous or could not be mapped",
            unmapped_pipes=unmapped_pipes,
        )

    catchment_to_devices_mutable: dict[str, list[str]] = {
        item.stable_key: [] for item in normalized_catchments
    }
    for device_key, catchment_key in sorted(device_to_catchment.items()):
        catchment_to_devices_mutable[catchment_key].append(device_key)
    catchment_to_devices = {
        key: tuple(values)
        for key, values in sorted(catchment_to_devices_mutable.items())
    }
    return CatchmentMapping(
        node_to_catchment=dict(sorted(node_to_catchment.items())),
        pipe_to_catchment=dict(sorted(pipe_to_catchment.items())),
        device_to_catchment=dict(sorted(device_to_catchment.items())),
        catchment_to_devices=catchment_to_devices,
        unmapped_nodes=tuple(sorted(unmapped_nodes)),
        unmapped_pipes=tuple(sorted(unmapped_pipes)),
        unmapped_devices=tuple(sorted(unmapped_devices)),
        ambiguous_nodes=tuple(sorted(ambiguous_nodes)),
        ambiguous_pipes=tuple(sorted(ambiguous_pipes)),
        ambiguous_devices=tuple(sorted(ambiguous_devices)),
        warnings=tuple(
            sorted(
                device_warnings
                + node_warnings
                + pipe_warnings
                + [f"ambiguous pipe {key}" for key in sorted(ambiguous_pipes)]
            )
        ),
    )


# Common shorter spelling.
map_catchments = build_catchment_mapping


def _resolve_valid_at(
    value: datetime | str | None,
    *,
    previous: SurfaceFloodFrame | None,
    frame_index: int,
    dt_seconds: float,
) -> datetime:
    if value is None:
        if previous is not None:
            return previous.valid_at + timedelta(seconds=dt_seconds)
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
            seconds=frame_index * dt_seconds
        )
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise SurfaceFloodValidationError(
                "INVALID_TIMESTAMP", "valid_at must be an RFC3339 timestamp"
            ) from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise SurfaceFloodValidationError(
            "INVALID_TIMESTAMP", "valid_at must be timezone-aware"
        )
    return value.astimezone(UTC)


def _validate_previous_mapping(
    previous: Mapping[str, Any],
    *,
    network_id: str,
    network_revision_id: str,
    current_mapping: CatchmentMapping | None = None,
) -> tuple[int, datetime]:
    required = {
        "frame_index",
        "valid_at",
        "network_id",
        "network_revision_id",
    }
    if current_mapping is not None:
        required.add("catchment_mapping")
    missing = sorted(required - previous.keys())
    if missing:
        raise SurfaceFloodValidationError(
            "INVALID_PREVIOUS_STATE",
            "previous frame is missing attempt identity",
            fields=missing,
        )
    if (
        str(previous["network_id"]) != network_id
        or str(previous["network_revision_id"]) != network_revision_id
    ):
        raise SurfaceFloodValidationError(
            "INVALID_PREVIOUS_STATE",
            "previous frame belongs to a different network revision",
        )
    try:
        previous_index = _validate_frame_index(previous["frame_index"])
    except (TypeError, ValueError) as exc:
        raise SurfaceFloodValidationError(
            "INVALID_PREVIOUS_STATE",
            "previous frame index is invalid",
        ) from exc
    if (
        current_mapping is not None
        and previous.get("catchment_mapping") != current_mapping.to_dict()
    ):
        raise SurfaceFloodValidationError(
            "INVALID_PREVIOUS_STATE",
            "previous catchment mapping does not match the current revision",
        )
    previous_at = _resolve_valid_at(
        previous["valid_at"],
        previous=None,
        frame_index=previous_index,
        dt_seconds=1.0,
    )
    return previous_index, previous_at


def _prior_cells(
    previous: SurfaceFloodFrame | Mapping[str, Any] | None,
) -> dict[str, Mapping[str, Any]]:
    if previous is None:
        return {}
    if isinstance(previous, SurfaceFloodFrame):
        return {cell.catchment_id: cell.to_dict() for cell in previous.cells}
    if not isinstance(previous, Mapping):
        raise SurfaceFloodValidationError(
            "INVALID_PREVIOUS_STATE", "previous_frame must be a frame or mapping"
        )
    raw_cells = previous.get("cells", previous.get("catchments", previous))
    if isinstance(raw_cells, Mapping):
        result: dict[str, Mapping[str, Any]] = {}
        for key, value in raw_cells.items():
            if isinstance(value, Mapping):
                result[str(key)] = value
            else:
                result[str(key)] = {"depth_m": value}
        return result
    if isinstance(raw_cells, Sequence) and not isinstance(raw_cells, (str, bytes)):
        result = {}
        for item in raw_cells:
            if not isinstance(item, Mapping):
                continue
            key = _get(item, "catchment_id", "stable_key", "id")
            if key is not None:
                result[_key(key, "catchment_id")] = item
        return result
    return {}


def _map_number(
    mapping: Mapping[str, Any] | None,
    key: str,
    *,
    field_name: str,
    default: float = 0.0,
) -> float:
    if mapping is None:
        return default
    value: Any = default
    for candidate in (key, key.casefold()):
        if candidate in mapping:
            value = mapping[candidate]
            break
    return _nonnegative(value, field_name)


def _stage_area_at_depth(
    curve: Sequence[tuple[float, float]],
    depth_m: float,
) -> float:
    if depth_m <= 0.0:
        return 0.0
    for (stage_low, area_low), (stage_high, area_high) in zip(
        curve,
        curve[1:],
    ):
        if depth_m <= stage_high:
            if stage_high <= stage_low:
                return area_high
            fraction = (depth_m - stage_low) / (stage_high - stage_low)
            return area_low + (area_high - area_low) * fraction
    return curve[-1][1]


def _depth_from_volume(
    curve: Sequence[tuple[float, float]],
    volume_m3: float,
) -> float:
    if volume_m3 <= 0.0:
        return 0.0
    upper_volume = 0.0
    for (stage_low, area_low), (stage_high, area_high) in zip(
        curve,
        curve[1:],
    ):
        segment_volume = (area_low + area_high) * 0.5 * (stage_high - stage_low)
        next_volume = upper_volume + segment_volume
        if volume_m3 <= next_volume:
            remaining = volume_m3 - upper_volume
            target_area = max(
                _EPSILON,
                (area_low + area_high) * 0.5,
            )
            return stage_low + remaining / target_area
        upper_volume = next_volume
    return curve[-1][0]


def _catchment_depth(
    catchment: FloodCatchment,
    volume_m3: float,
    config: SurfaceFloodConfig,
) -> float:
    if not catchment.stage_area_curve:
        return volume_m3 / catchment.area_m2
    return _depth_from_volume(catchment.stage_area_curve, volume_m3)


def _catchment_area(
    catchment: FloodCatchment,
    depth_m: float,
    config: SurfaceFloodConfig,
) -> float:
    if not catchment.stage_area_curve:
        return (
            catchment.area_m2 * min(1.0, max(0.0, depth_m) / config.max_depth_m)
            if depth_m > 0.0
            else 0.0
        )
    return _stage_area_at_depth(catchment.stage_area_curve, depth_m)


def _rainfall_for_catchment(
    rainfall_mm: float,
    rainfall_by_catchment: Mapping[str, Any] | None,
    key: str,
) -> float:
    if rainfall_by_catchment is None:
        return rainfall_mm
    if key in rainfall_by_catchment:
        return _nonnegative(rainfall_by_catchment[key], f"rainfall_mm[{key}]")
    if key.casefold() in rainfall_by_catchment:
        return _nonnegative(
            rainfall_by_catchment[key.casefold()], f"rainfall_mm[{key}]"
        )
    return 0.0


def _source_volume_node(
    node: FloodNode,
    *,
    dt_seconds: float,
    config: SurfaceFloodConfig,
    area_m2: float,
    rate_override: float | None,
    volume_override: float | None,
) -> float:
    if volume_override is not None:
        return _nonnegative(volume_override, f"node_overflow_m3[{node.stable_key}]")
    rate = node.overflow_m3s if rate_override is None else rate_override
    if rate > 0:
        return rate * dt_seconds
    if node.overflow_depth_m is None:
        return 0.0
    if node.overflow_depth_m <= config.node_overflow_threshold_m:
        return 0.0
    return node.overflow_depth_m * area_m2 * config.overflow_depth_volume_fraction


def _source_volume_pipe(
    pipe: FloodPipe,
    *,
    dt_seconds: float,
    config: SurfaceFloodConfig,
    rate_override: float | None,
    volume_override: float | None,
    ratio_override: float | None,
) -> float:
    if volume_override is not None:
        return _nonnegative(volume_override, f"pipe_overflow_m3[{pipe.stable_key}]")
    rate = pipe.overflow_m3s if rate_override is None else rate_override
    if rate > 0:
        return rate * dt_seconds
    ratio = pipe.overload_ratio if ratio_override is None else ratio_override
    if ratio is None:
        ratio = pipe.fill_ratio
    if ratio is None:
        ratio = 0.0
    ratio = _nonnegative(ratio, f"pipe_overload_ratio[{pipe.stable_key}]")
    threshold = config.pipe_overload_threshold_ratio
    if pipe.surcharged and ratio <= threshold:
        # A boolean surcharge is an explicit solver signal.  Use a small,
        # deterministic excess when no numeric magnitude was supplied.
        ratio = threshold + max(0.01, threshold * 0.1)
    if ratio <= threshold:
        return 0.0
    capacity = pipe.capacity_m3s
    if capacity is None:
        capacity = config.default_pipe_capacity_m3s
    return max(0.0, ratio - threshold) * capacity * dt_seconds


def _reference_elevation(
    catchment: FloodCatchment,
    node_by_key: Mapping[str, FloodNode],
    devices: Sequence[FloodDevice],
    mapping: CatchmentMapping,
) -> tuple[float, float | None, tuple[str, ...]]:
    mapped_devices = tuple(
        device
        for device in devices
        if mapping.device_to_catchment.get(device.stable_key) == catchment.stable_key
    )
    device_elevations = [
        device.elevation_m
        for device in mapped_devices
        if device.elevation_m is not None
    ]
    if catchment.device_elevation_m is not None:
        device_elevations.append(catchment.device_elevation_m)
    device_elevation = min(device_elevations) if device_elevations else None
    base: float | None
    if catchment.elevation_m is not None:
        base = catchment.elevation_m
    elif device_elevation is not None:
        base = device_elevation
    elif (
        catchment.outlet_node_key is not None
        and catchment.outlet_node_key in node_by_key
    ):
        node = node_by_key[catchment.outlet_node_key]
        base = node.rim_elevation_m
        if base is None:
            base = node.invert_elevation_m
        if base is None:
            raise SurfaceFloodValidationError(
                "MISSING_REFERENCE_ELEVATION",
                "outlet node must define rim or invert elevation",
                catchment_id=catchment.stable_key,
                node_key=catchment.outlet_node_key,
            )
        assert base is not None
    else:
        raise SurfaceFloodValidationError(
            "MISSING_REFERENCE_ELEVATION",
            "catchment must define elevation or a mapped outlet datum",
            catchment_id=catchment.stable_key,
        )
    # A low device is the local datum when it is lower than the nominal cell
    # elevation.  This keeps device elevations observable without inventing a
    # second hydrodynamic state.
    if device_elevation is not None:
        base = min(base, device_elevation)
    device_ids = tuple(sorted(device.stable_key for device in mapped_devices))
    return base, device_elevation, device_ids


def _cell_from_prior(
    prior: Mapping[str, Any] | None,
    *,
    catchment: FloodCatchment,
    initial_depth_m: Mapping[str, Any] | None,
    initial_volume_m3: Mapping[str, Any] | None,
) -> float:
    if initial_volume_m3 is not None:
        for candidate in (catchment.stable_key, catchment.stable_key.casefold()):
            if candidate in initial_volume_m3:
                return _nonnegative(
                    initial_volume_m3[candidate],
                    f"initial_volume_m3[{catchment.stable_key}]",
                )
    if initial_depth_m is not None:
        for candidate in (catchment.stable_key, catchment.stable_key.casefold()):
            if candidate in initial_depth_m:
                depth = _nonnegative(
                    initial_depth_m[candidate],
                    f"initial_depth_m[{catchment.stable_key}]",
                )
                area = (
                    _stage_area_at_depth(catchment.stage_area_curve, depth)
                    if catchment.stage_area_curve
                    else catchment.area_m2
                )
                return depth * area
    if prior is None:
        return 0.0
    volume = _get(prior, "volume_m3")
    if volume is not None:
        return _nonnegative(volume, f"previous_volume_m3[{catchment.stable_key}]")
    depth = _get(prior, "depth_m", default=0.0)
    return (
        _nonnegative(depth, f"previous_depth_m[{catchment.stable_key}]")
        * catchment.area_m2
    )


def _status(
    *,
    depth: float,
    previous_depth: float,
    inflow: float,
    outflow: float,
    threshold_exceeded: bool,
) -> FloodStatus:
    if depth <= _EPSILON:
        return FloodStatus.dry
    if inflow > _EPSILON or depth > previous_depth + _EPSILON:
        return FloodStatus.flooded if threshold_exceeded else FloodStatus.accumulating
    if outflow > _EPSILON or depth < previous_depth - _EPSILON:
        return FloodStatus.receding
    if threshold_exceeded:
        return FloodStatus.flooded
    return FloodStatus.surface_flow


def _build_geojson(
    cells: Sequence[SurfaceFloodCellState],
    all_geometries: Mapping[str, Mapping[str, Any]],
    *,
    frame_index: int,
    valid_at: datetime,
    network_id: str,
    network_revision_id: str,
    dt_seconds: float,
    config: SurfaceFloodConfig,
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for cell in cells:
        if not config.include_dry_features and not cell.flooded:
            continue
        properties = {
            "feature_index": cell.feature_index,
            "stable_key": cell.catchment_id,
            "catchment_id": cell.catchment_id,
            "depth_m": cell.depth_m,
            "area_m2": cell.area_m2,
            "flooded_area_m2": cell.area_m2,
            "catchment_area_m2": cell.total_area_m2,
            "volume_m3": cell.volume_m3,
            "flow_m3s": cell.inflow_m3 / dt_seconds if dt_seconds > 0 else 0.0,
            "status": cell.status.value,
            "threshold_exceeded": cell.threshold_exceeded,
            "reference_elevation_m": cell.reference_elevation_m,
            "device_elevation_m": cell.device_elevation_m,
            "device_ids": list(cell.device_ids),
            "source_ids": list(cell.source_ids),
            "source_types": list(cell.source_types),
            "simplified_extent": True,
            "model_fidelity": "reduced_fidelity",
            "is_authoritative": False,
        }
        features.append(
            {
                "type": "Feature",
                "id": cell.catchment_id,
                "geometry": _deep_copy_json(all_geometries[cell.catchment_id]),
                "properties": properties,
            }
        )
    boxes = [_bbox_from_geometry(geometry) for geometry in all_geometries.values()]
    bbox: list[float]
    if boxes:
        bbox = [
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        ]
    else:
        bbox = []
    return {
        "type": "FeatureCollection",
        "schema_version": GEOJSON_SCHEMA_VERSION,
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "bbox": bbox,
        "properties": {
            "entity_type": "ponding",
            "network_id": network_id,
            "network_revision_id": network_revision_id,
            "frame_index": frame_index,
            "valid_at": valid_at.isoformat(),
            "model_version": MODEL_VERSION,
            "model_fidelity": "reduced_fidelity",
            "is_authoritative": False,
            "simplified_extent": True,
        },
        "features": features,
    }


def _validate_frame_index(frame_index: int) -> int:
    index = _number(frame_index, "frame_index")
    if index < 0 or not index.is_integer():
        raise SurfaceFloodValidationError(
            "INVALID_FRAME_INDEX", "frame_index must be a non-negative integer"
        )
    return int(index)


def _validate_dt(dt_seconds: float) -> float:
    dt = _number(dt_seconds, "dt_seconds")
    if dt <= 0:
        raise SurfaceFloodValidationError(
            "INVALID_TIME_STEP", "dt_seconds must be greater than zero"
        )
    return dt


def compute_surface_flood(
    catchments: Sequence[Any],
    nodes: Sequence[Any] = (),
    pipes: Sequence[Any] = (),
    devices: Sequence[Any] = (),
    *,
    rainfall_mm: float = 0.0,
    rainfall_m: float | None = None,
    rainfall_depth_mm: float | None = None,
    rainfall_by_catchment: Mapping[str, Any] | None = None,
    node_overflow_m3s: Mapping[str, Any] | None = None,
    node_overflow_m3: Mapping[str, Any] | None = None,
    pipe_overload_ratio: Mapping[str, Any] | None = None,
    pipe_overloads: Mapping[str, Any] | None = None,
    pipe_overflow_m3s: Mapping[str, Any] | None = None,
    pipe_overflow_m3: Mapping[str, Any] | None = None,
    dt_seconds: float = 300.0,
    frame_index: int = 0,
    valid_at: datetime | str | None = None,
    previous_frame: SurfaceFloodFrame | Mapping[str, Any] | None = None,
    initial_depth_m: Mapping[str, Any] | None = None,
    initial_volume_m3: Mapping[str, Any] | None = None,
    config: SurfaceFloodConfig | None = None,
    network_id: str = "local",
    network_revision_id: str = "local",
    target_manifest_sha256: str | None = None,
) -> SurfaceFloodFrame:
    """Compute one reduced-fidelity ponding frame.

    The four positional record sequences are intentionally small immutable
    projections, not ORM rows.  Dynamic overflow values can be supplied by
    stable key in the keyword mappings; a direct ``*_m3`` mapping is treated
    as a volume for this interval, while ``*_m3s`` is a rate.
    """

    cfg = config or SurfaceFloodConfig()
    dt = _validate_dt(dt_seconds)
    index = _validate_frame_index(frame_index)
    if rainfall_m is not None:
        rain_m = _nonnegative(rainfall_m, "rainfall_m")
        scalar_rain_mm = rain_m * 1000.0
    elif rainfall_depth_mm is not None:
        scalar_rain_mm = _nonnegative(rainfall_depth_mm, "rainfall_depth_mm")
    else:
        scalar_rain_mm = _nonnegative(rainfall_mm, "rainfall_mm")
    if rainfall_by_catchment is not None and not isinstance(
        rainfall_by_catchment, Mapping
    ):
        raise TypeError("rainfall_by_catchment must be a mapping")

    normalized_catchments = _unique_normalized(
        catchments, _normalize_catchment, "catchment"
    )
    normalized_nodes = _unique_normalized(nodes, _normalize_node, "node")
    normalized_pipes = _unique_normalized(pipes, _normalize_pipe, "pipe")
    normalized_devices = _unique_normalized(devices, _normalize_device, "device")
    mapping = build_catchment_mapping(
        normalized_catchments,
        normalized_nodes,
        normalized_pipes,
        normalized_devices,
        strict=cfg.strict_mapping,
    )
    prior = _prior_cells(previous_frame)
    previous_frame_obj = (
        previous_frame if isinstance(previous_frame, SurfaceFloodFrame) else None
    )
    resolved_at = _resolve_valid_at(
        valid_at,
        previous=previous_frame_obj,
        frame_index=index,
        dt_seconds=dt,
    )
    if isinstance(previous_frame, Mapping):
        previous_index, previous_at = _validate_previous_mapping(
            previous_frame,
            network_id=str(network_id),
            network_revision_id=str(network_revision_id),
            current_mapping=mapping,
        )
        if index <= previous_index or resolved_at <= previous_at:
            raise SurfaceFloodValidationError(
                "NON_MONOTONIC_FRAME",
                "previous mapping frame index and time must be strictly increasing",
                previous_frame_index=previous_index,
                frame_index=index,
            )
    if previous_frame_obj is not None:
        if (
            previous_frame_obj.network_id != str(network_id)
            or previous_frame_obj.network_revision_id != str(network_revision_id)
            or previous_frame_obj.model_version != MODEL_VERSION
            or previous_frame_obj.config != cfg
            or (
                previous_frame_obj.target_manifest_sha256 is not None
                and target_manifest_sha256 is not None
                and previous_frame_obj.target_manifest_sha256 != target_manifest_sha256
            )
        ):
            raise SurfaceFloodValidationError(
                "INVALID_PREVIOUS_STATE",
                "previous frame belongs to a different network revision or model",
            )
        if index <= previous_frame_obj.frame_index:
            raise SurfaceFloodValidationError(
                "NON_MONOTONIC_FRAME",
                "frame_index must increase when a previous frame is supplied",
                previous_frame_index=previous_frame_obj.frame_index,
                frame_index=index,
            )
        if resolved_at <= previous_frame_obj.valid_at:
            raise SurfaceFloodValidationError(
                "NON_MONOTONIC_FRAME",
                "valid_at must increase when a previous frame is supplied",
                previous_valid_at=previous_frame_obj.valid_at.isoformat(),
                valid_at=resolved_at.isoformat(),
            )

    node_by_key = {node.stable_key: node for node in normalized_nodes}
    area_by_catchment = {
        item.stable_key: item.area_m2 for item in normalized_catchments
    }
    exchange_by_node: dict[str, list[tuple[FloodCatchment, FloodSewerExchange]]] = (
        defaultdict(list)
    )
    for catchment in normalized_catchments:
        for exchange in catchment.sewer_exchanges:
            exchange_by_node[exchange.node_key].append((catchment, exchange))
    geometries = {item.stable_key: item.geometry for item in normalized_catchments}
    inflow_by_catchment: dict[str, float] = {
        item.stable_key: 0.0 for item in normalized_catchments
    }
    source_ids: dict[str, list[str]] = {
        item.stable_key: [] for item in normalized_catchments
    }
    source_types: dict[str, list[str]] = {
        item.stable_key: [] for item in normalized_catchments
    }
    unmapped_sources: list[str] = []

    def allocate(
        source_type: str, source_key: str, amount: float, target: str | None
    ) -> None:
        if amount <= _EPSILON:
            return
        if target is None or target not in inflow_by_catchment:
            unmapped_sources.append(f"{source_type}:{source_key}")
            if cfg.strict_mapping:
                raise SurfaceFloodValidationError(
                    "UNMAPPED_SOURCE",
                    "overflow source has no unambiguous catchment mapping",
                    source_type=source_type,
                    source_key=source_key,
                )
            return
        inflow_by_catchment[target] += amount
        source_ids[target].append(f"{source_type}:{source_key}")
        source_types[target].append(source_type)

    for node in normalized_nodes:
        rate_override = None
        volume_override = None
        if node_overflow_m3s is not None:
            rate_override = _map_number(
                node_overflow_m3s, node.stable_key, field_name="node_overflow_m3s"
            )
        if node_overflow_m3 is not None:
            volume_override = _map_number(
                node_overflow_m3, node.stable_key, field_name="node_overflow_m3"
            )
        amount = _source_volume_node(
            node,
            dt_seconds=dt,
            config=cfg,
            area_m2=area_by_catchment.get(
                mapping.node_to_catchment.get(node.stable_key), 0.0
            ),
            rate_override=rate_override,
            volume_override=volume_override,
        )
        exchanges = exchange_by_node.get(node.stable_key, ())
        if exchanges:
            accepts_inflow = any(
                exchange.direction in {"inflow", "bidirectional"}
                for _catchment, exchange in exchanges
            )
            capacities = [
                exchange.capacity_m3s * dt
                for _catchment, exchange in exchanges
                if exchange.direction in {"inflow", "bidirectional"}
                and exchange.capacity_m3s is not None
            ]
            if not accepts_inflow:
                amount = 0.0
            elif capacities:
                amount = min(amount, sum(capacities))
        allocate(
            "node",
            node.stable_key,
            amount,
            mapping.node_to_catchment.get(node.stable_key),
        )

    for pipe in normalized_pipes:
        ratio_override = None
        if pipe_overload_ratio is not None or pipe_overloads is not None:
            ratio_mapping = (
                pipe_overload_ratio
                if pipe_overload_ratio is not None
                else pipe_overloads
            )
            ratio_override = _map_number(
                ratio_mapping, pipe.stable_key, field_name="pipe_overload_ratio"
            )
        rate_override = None
        if pipe_overflow_m3s is not None:
            rate_override = _map_number(
                pipe_overflow_m3s, pipe.stable_key, field_name="pipe_overflow_m3s"
            )
        volume_override = None
        if pipe_overflow_m3 is not None:
            volume_override = _map_number(
                pipe_overflow_m3, pipe.stable_key, field_name="pipe_overflow_m3"
            )
        amount = _source_volume_pipe(
            pipe,
            dt_seconds=dt,
            config=cfg,
            rate_override=rate_override,
            volume_override=volume_override,
            ratio_override=ratio_override,
        )
        allocate(
            "pipe",
            pipe.stable_key,
            amount,
            mapping.pipe_to_catchment.get(pipe.stable_key),
        )

    if unmapped_sources:
        mapping = replace(
            mapping,
            unmapped_sources=tuple(sorted(set(unmapped_sources))),
            warnings=tuple(
                sorted(
                    set(mapping.warnings)
                    | {f"unmapped source {source}" for source in unmapped_sources}
                )
            ),
        )

    reference_by_catchment: dict[str, float] = {}
    device_elevation_by_catchment: dict[str, float | None] = {}
    device_ids_by_catchment: dict[str, tuple[str, ...]] = {}
    for catchment in normalized_catchments:
        base, device_elevation, device_ids = _reference_elevation(
            catchment,
            node_by_key,
            normalized_devices,
            mapping,
        )
        reference_by_catchment[catchment.stable_key] = base
        device_elevation_by_catchment[catchment.stable_key] = device_elevation
        device_ids_by_catchment[catchment.stable_key] = device_ids

    volumes: dict[str, float] = {}
    rainfall_volumes_by_catchment: dict[str, float] = {}
    previous_depths: dict[str, float] = {}
    for catchment in normalized_catchments:
        rain_mm = _rainfall_for_catchment(
            scalar_rain_mm,
            rainfall_by_catchment,
            catchment.stable_key,
        )
        gross_rain_m3 = rain_mm / 1000.0 * catchment.area_m2
        rain_m3 = gross_rain_m3
        if previous_frame is None:
            rain_m3 = max(0.0, gross_rain_m3 - catchment.depression_storage_m3)
        prior_volume = _cell_from_prior(
            prior.get(catchment.stable_key),
            catchment=catchment,
            initial_depth_m=initial_depth_m if previous_frame is None else None,
            initial_volume_m3=initial_volume_m3 if previous_frame is None else None,
        )
        previous_depths[catchment.stable_key] = _catchment_depth(
            catchment,
            prior_volume,
            cfg,
        )
        rainfall_volumes_by_catchment[catchment.stable_key] = rain_m3
        volumes[catchment.stable_key] = (
            prior_volume + rain_m3 + inflow_by_catchment[catchment.stable_key]
        )

    transfer_ledger: list[SurfaceFloodTransfer] = []
    link_inflow: dict[str, float] = defaultdict(float)
    link_outflow: dict[str, float] = defaultdict(float)
    for catchment in normalized_catchments:
        for link in catchment.cell_links:
            source = next(
                item
                for item in normalized_catchments
                if item.stable_key == link.from_catchment_id
            )
            destination = next(
                item
                for item in normalized_catchments
                if item.stable_key == link.to_catchment_id
            )
            forward_head = reference_by_catchment[source.stable_key] + _catchment_depth(
                source,
                volumes[source.stable_key],
                cfg,
            )
            reverse_head = reference_by_catchment[
                destination.stable_key
            ] + _catchment_depth(
                destination,
                volumes[destination.stable_key],
                cfg,
            )
            if link.direction == "forward" or forward_head >= reverse_head:
                source_id, destination_id = link.from_catchment_id, link.to_catchment_id
                source_head, destination_head = forward_head, reverse_head
            else:
                source_id, destination_id = link.to_catchment_id, link.from_catchment_id
                source, destination = destination, source
                source_head, destination_head = reverse_head, forward_head
            head_difference = source_head - destination_head
            flow_depth = max(source_head - link.crest_elevation_m, 0.0)
            if head_difference <= _EPSILON or flow_depth <= _EPSILON:
                continue
            discharge = (
                link.width_m
                * flow_depth ** (5.0 / 3.0)
                * math.sqrt(head_difference / link.length_m)
                / link.roughness_manning_n
            )
            if link.max_capacity_m3s is not None:
                discharge = min(discharge, link.max_capacity_m3s)
            moved = min(discharge * dt, max(0.0, volumes[source_id]))
            if moved <= _EPSILON:
                continue
            volumes[source_id] -= moved
            volumes[destination_id] += moved
            link_outflow[source_id] += moved
            link_inflow[destination_id] += moved
            transfer_ledger.append(
                SurfaceFloodTransfer(
                    source=source_id,
                    target=destination_id,
                    kind=f"cell_link:{link.stable_key}",
                    volume_m3=moved,
                )
            )

    sewer_outflow: dict[str, float] = defaultdict(float)
    for catchment in normalized_catchments:
        for exchange in catchment.sewer_exchanges:
            if exchange.direction != "outflow":
                continue
            node = node_by_key[exchange.node_key]
            requested = _source_volume_node(
                node,
                dt_seconds=dt,
                config=cfg,
                area_m2=catchment.area_m2,
                rate_override=None,
                volume_override=None,
            )
            if exchange.capacity_m3s is not None:
                requested = min(requested, exchange.capacity_m3s * dt)
            moved = min(max(0.0, requested), volumes[catchment.stable_key])
            if moved <= _EPSILON:
                continue
            volumes[catchment.stable_key] -= moved
            sewer_outflow[catchment.stable_key] += moved
            transfer_ledger.append(
                SurfaceFloodTransfer(
                    source=catchment.stable_key,
                    target=f"node:{exchange.node_key}",
                    kind=f"sewer_exchange:{exchange.stable_key}",
                    volume_m3=moved,
                )
            )

    cells: list[SurfaceFloodCellState] = []
    for feature_index, catchment in enumerate(normalized_catchments):
        key = catchment.stable_key
        rain_m3 = rainfall_volumes_by_catchment[key]
        previous_depth = previous_depths[key]
        external_inflow = inflow_by_catchment[key]
        available = max(0.0, volumes[key])
        recession_area = _catchment_area(catchment, previous_depth, cfg)
        recession = min(
            available,
            cfg.recession_rate_m_per_s * dt * recession_area,
        )
        volume = max(0.0, available - recession)
        depth = _catchment_depth(catchment, volume, cfg)
        area = (
            _catchment_area(catchment, depth, cfg)
            if depth > cfg.reporting_depth_threshold_m
            else 0.0
        )
        inflow = external_inflow + link_inflow[key]
        outflow = recession + link_outflow[key] + sewer_outflow[key]
        threshold_exceeded = depth >= cfg.flood_depth_threshold_m and depth > _EPSILON
        status = _status(
            depth=depth,
            previous_depth=previous_depth,
            inflow=inflow,
            outflow=outflow,
            threshold_exceeded=threshold_exceeded,
        )
        base_elevation = reference_by_catchment[key]
        cells.append(
            SurfaceFloodCellState(
                catchment_id=key,
                feature_index=feature_index,
                total_area_m2=catchment.area_m2,
                depth_m=depth,
                area_m2=area,
                volume_m3=volume,
                reference_elevation_m=base_elevation,
                device_elevation_m=device_elevation_by_catchment[key],
                water_surface_elevation_m=base_elevation + depth,
                rainfall_m3=rain_m3,
                inflow_m3=inflow,
                outflow_m3=outflow,
                status=status,
                threshold_exceeded=threshold_exceeded,
                source_ids=tuple(sorted(set(source_ids[key]))),
                source_types=tuple(sorted(set(source_types[key]))),
                device_ids=device_ids_by_catchment[key],
                stage_area_curve=catchment.stage_area_curve,
                roughness_manning_n=catchment.roughness_manning_n,
                depression_storage_m3=catchment.depression_storage_m3,
                cell_link_ids=tuple(link.stable_key for link in catchment.cell_links),
                sewer_exchange_ids=tuple(
                    exchange.stable_key for exchange in catchment.sewer_exchanges
                ),
                geometry=catchment.geometry,
            )
        )

    frame_cells = tuple(cells)
    geojson = _build_geojson(
        frame_cells,
        geometries,
        frame_index=index,
        valid_at=resolved_at,
        network_id=str(network_id),
        network_revision_id=str(network_revision_id),
        dt_seconds=dt,
        config=cfg,
    )
    frame = SurfaceFloodFrame(
        frame_index=index,
        valid_at=resolved_at,
        dt_seconds=dt,
        rainfall_mm=scalar_rain_mm,
        cells=frame_cells,
        mapping=mapping,
        geojson=geojson,
        transfer_ledger=tuple(transfer_ledger),
        network_id=str(network_id),
        network_revision_id=str(network_revision_id),
        target_manifest_sha256=target_manifest_sha256,
        source_catchments=normalized_catchments,
        source_nodes=normalized_nodes,
        source_pipes=normalized_pipes,
        source_devices=normalized_devices,
        config=cfg,
    )
    # This is a cheap internal invariant check.  It prevents a future formula
    # change from silently publishing a negative/non-finite state.
    validate_surface_flood_frame(frame)
    return frame


# Explicit spelling used by some worker integrations.
build_surface_flood_frame = compute_surface_flood
simulate_surface_flood = compute_surface_flood
generate_surface_flood_frame = compute_surface_flood


class SurfaceFloodModel:
    """Stateful facade for sequential simulation frames.

    The facade owns only immutable normalized definitions and the last frame;
    it does not access a database or retain unbounded history.  A worker can
    therefore keep the same instance across <=5-minute reporting steps.
    """

    def __init__(
        self,
        catchments: Sequence[Any],
        nodes: Sequence[Any] = (),
        pipes: Sequence[Any] = (),
        devices: Sequence[Any] = (),
        *,
        config: SurfaceFloodConfig | None = None,
        network_id: str = "local",
        network_revision_id: str = "local",
        initial_frame: SurfaceFloodFrame | None = None,
    ) -> None:
        self.catchments = _unique_normalized(
            catchments, _normalize_catchment, "catchment"
        )
        self.nodes = _unique_normalized(nodes, _normalize_node, "node")
        self.pipes = _unique_normalized(pipes, _normalize_pipe, "pipe")
        self.devices = _unique_normalized(devices, _normalize_device, "device")
        self.config = config or SurfaceFloodConfig()
        self.network_id = str(network_id)
        self.network_revision_id = str(network_revision_id)
        if initial_frame is not None:
            expected_catchments = {item.stable_key for item in self.catchments}
            actual_catchments = {cell.catchment_id for cell in initial_frame.cells}
            if (
                initial_frame.network_id != self.network_id
                or initial_frame.network_revision_id != self.network_revision_id
                or initial_frame.model_version != MODEL_VERSION
                or initial_frame.config != self.config
                or actual_catchments != expected_catchments
            ):
                raise SurfaceFloodValidationError(
                    "INVALID_INITIAL_FRAME",
                    "initial frame does not match the frozen model definition",
                )
        self._last_frame = initial_frame
        self._last_index = (
            initial_frame.frame_index if initial_frame is not None else -1
        )

    @property
    def current_frame(self) -> SurfaceFloodFrame | None:
        return self._last_frame

    @property
    def last_frame(self) -> SurfaceFloodFrame | None:
        return self._last_frame

    @property
    def state(self) -> dict[str, float]:
        if self._last_frame is None:
            return {item.stable_key: 0.0 for item in self.catchments}
        return {cell.catchment_id: cell.depth_m for cell in self._last_frame.cells}

    def reset(self) -> None:
        self._last_frame = None
        self._last_index = -1

    def step(
        self,
        *,
        rainfall_mm: float = 0.0,
        rainfall_m: float | None = None,
        rainfall_depth_mm: float | None = None,
        rainfall_by_catchment: Mapping[str, Any] | None = None,
        node_overflow_m3s: Mapping[str, Any] | None = None,
        node_overflow_m3: Mapping[str, Any] | None = None,
        pipe_overload_ratio: Mapping[str, Any] | None = None,
        pipe_overloads: Mapping[str, Any] | None = None,
        pipe_overflow_m3s: Mapping[str, Any] | None = None,
        pipe_overflow_m3: Mapping[str, Any] | None = None,
        dt_seconds: float = 300.0,
        frame_index: int | None = None,
        valid_at: datetime | str | None = None,
        initial_depth_m: Mapping[str, Any] | None = None,
        initial_volume_m3: Mapping[str, Any] | None = None,
    ) -> SurfaceFloodFrame:
        index = self._last_index + 1 if frame_index is None else frame_index
        frame = compute_surface_flood(
            self.catchments,
            self.nodes,
            self.pipes,
            self.devices,
            rainfall_mm=rainfall_mm,
            rainfall_m=rainfall_m,
            rainfall_depth_mm=rainfall_depth_mm,
            rainfall_by_catchment=rainfall_by_catchment,
            node_overflow_m3s=node_overflow_m3s,
            node_overflow_m3=node_overflow_m3,
            pipe_overload_ratio=pipe_overload_ratio,
            pipe_overloads=pipe_overloads,
            pipe_overflow_m3s=pipe_overflow_m3s,
            pipe_overflow_m3=pipe_overflow_m3,
            dt_seconds=dt_seconds,
            frame_index=index,
            valid_at=valid_at,
            previous_frame=self._last_frame,
            initial_depth_m=initial_depth_m,
            initial_volume_m3=initial_volume_m3,
            config=self.config,
            network_id=self.network_id,
            network_revision_id=self.network_revision_id,
        )
        self._last_frame = frame
        self._last_index = frame.frame_index
        return frame

    advance = step
    run_step = step


def generate_geojson_frame(
    frame: SurfaceFloodFrame | Mapping[str, Any]
) -> dict[str, Any]:
    """Return a defensive JSON copy of a ponding frame's GeoJSON collection."""

    if isinstance(frame, SurfaceFloodFrame):
        return frame.to_geojson()
    if isinstance(frame, Mapping):
        geojson = frame.get("geojson")
        if not isinstance(geojson, Mapping):
            raise SurfaceFloodValidationError(
                "INVALID_GEOJSON", "frame mapping must contain a geojson object"
            )
        return _deep_copy_json(geojson)
    raise TypeError("frame must be a SurfaceFloodFrame or mapping")


build_flood_geojson = generate_geojson_frame


def validate_surface_flood_frame(
    frame: SurfaceFloodFrame,
    *,
    previous: SurfaceFloodFrame | None = None,
    require_recession_monotonic: bool = False,
) -> None:
    """Validate finite/non-negative state and optional recession monotonicity."""

    if not isinstance(frame, SurfaceFloodFrame):
        raise TypeError("frame must be a SurfaceFloodFrame")
    seen: set[str] = set()
    previous_by_id = (
        {} if previous is None else {cell.catchment_id: cell for cell in previous.cells}
    )
    for cell in frame.cells:
        if cell.catchment_id in seen:
            raise SurfaceFloodValidationError(
                "DUPLICATE_KEY",
                "frame contains duplicate catchment state",
                catchment_id=cell.catchment_id,
            )
        seen.add(cell.catchment_id)
        for name, value in (
            ("depth_m", cell.depth_m),
            ("area_m2", cell.area_m2),
            ("total_area_m2", cell.total_area_m2),
            ("volume_m3", cell.volume_m3),
            ("rainfall_m3", cell.rainfall_m3),
            ("inflow_m3", cell.inflow_m3),
            ("outflow_m3", cell.outflow_m3),
        ):
            if not math.isfinite(value) or value < -_EPSILON:
                raise SurfaceFloodValidationError(
                    "INVALID_STATE",
                    f"{name} must be finite and non-negative",
                    field=name,
                )
        for name, value in (
            ("reference_elevation_m", cell.reference_elevation_m),
            ("water_surface_elevation_m", cell.water_surface_elevation_m),
        ):
            if not math.isfinite(value):
                raise SurfaceFloodValidationError(
                    "INVALID_STATE", f"{name} must be finite", field=name
                )
        if cell.area_m2 > cell.total_area_m2 + _EPSILON:
            raise SurfaceFloodValidationError(
                "INVALID_AREA",
                "flooded area cannot exceed catchment area",
                catchment_id=cell.catchment_id,
            )
        geometry_report = validate_geometry(
            _thaw_value(cell.geometry),
            entity_type="catchment",
        )
        if not geometry_report.valid:
            issue = geometry_report.issues[0]
            raise SurfaceFloodValidationError(
                issue.code, issue.message, catchment_id=cell.catchment_id
            )
        if require_recession_monotonic and previous is not None:
            old = previous_by_id.get(cell.catchment_id)
            if old is not None:
                if (
                    cell.depth_m > old.depth_m + _EPSILON
                    or cell.area_m2 > old.area_m2 + _EPSILON
                    or cell.volume_m3 > old.volume_m3 + _EPSILON
                ):
                    raise SurfaceFloodValidationError(
                        "NON_MONOTONIC_RECESSION",
                        "a no-inflow recession frame increased state",
                        catchment_id=cell.catchment_id,
                    )
    geojson = _thaw_value(frame.geojson)
    if not isinstance(geojson, Mapping) or geojson.get("type") != "FeatureCollection":
        raise SurfaceFloodValidationError(
            "INVALID_GEOJSON", "frame GeoJSON must be a FeatureCollection"
        )
    features = geojson.get("features")
    if not isinstance(features, list):
        raise SurfaceFloodValidationError(
            "INVALID_GEOJSON", "GeoJSON FeatureCollection features must be a list"
        )
    crs = geojson.get("crs")
    if (
        not isinstance(crs, Mapping)
        or not isinstance(crs.get("properties"), Mapping)
        or not str(crs["properties"].get("name", ""))
    ):
        raise SurfaceFloodValidationError(
            "INVALID_GEOJSON", "FeatureCollection CRS is required"
        )
    bbox = geojson.get("bbox")
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or not all(
            isinstance(value, (int, float)) and math.isfinite(value) for value in bbox
        )
    ):
        raise SurfaceFloodValidationError(
            "INVALID_GEOJSON", "FeatureCollection bbox must contain four finite numbers"
        )
    if not isinstance(geojson.get("properties"), Mapping):
        raise SurfaceFloodValidationError(
            "INVALID_GEOJSON", "FeatureCollection properties are required"
        )
    for feature in features:
        if (
            not isinstance(feature, Mapping)
            or feature.get("type") != "Feature"
            or not isinstance(feature.get("properties"), Mapping)
        ):
            raise SurfaceFloodValidationError(
                "INVALID_GEOJSON", "invalid GeoJSON feature"
            )
        geometry_value = feature.get("geometry")
        if geometry_value is None:
            raise SurfaceFloodValidationError(
                "INVALID_GEOJSON", "feature has no geometry"
            )
        report = validate_geometry(geometry_value, entity_type="catchment")
        if not report.valid:
            issue = report.issues[0]
            raise SurfaceFloodValidationError(issue.code, issue.message)


def assert_monotonic_cleanup(
    previous: SurfaceFloodFrame, current: SurfaceFloodFrame
) -> None:
    """Assert depth, area, and volume do not rise during a cleanup step."""

    validate_surface_flood_frame(
        current,
        previous=previous,
        require_recession_monotonic=True,
    )


def calculate_flood_depth(
    water_surface_elevation_m: float,
    reference_elevation_m: float,
) -> float:
    """Return validated non-negative water depth for a reference elevation."""

    surface = _number(water_surface_elevation_m, "water_surface_elevation_m")
    reference = _number(reference_elevation_m, "reference_elevation_m")
    depth = surface - reference
    if depth < 0.0:
        raise SurfaceFloodValidationError(
            "INVALID_STATE",
            "water_surface_elevation_m must not be below reference_elevation_m",
        )
    return depth


def calculate_flood_area(
    total_area_m2: float,
    depth_m: float,
    max_depth_m: float = 1.0,
) -> float:
    """Return the simplified area fraction for a positive water depth."""

    total = _nonnegative(total_area_m2, "total_area_m2")
    depth = _nonnegative(depth_m, "depth_m")
    max_depth = _number(max_depth_m, "max_depth_m")
    if total == 0 or max_depth <= 0:
        raise SurfaceFloodValidationError(
            "INVALID_AREA",
            "total_area_m2 must be positive and max_depth_m must be positive",
        )
    return min(total, total * min(1.0, depth / max_depth))


# Alternate names kept explicit for simple worker/test call sites.
FloodConfig = SurfaceFloodConfig
FloodCell = SurfaceFloodCellState
FloodFrame = SurfaceFloodFrame
SurfaceFloodResult = SurfaceFloodFrame
flood_depth_m = calculate_flood_depth
flood_area_m2 = calculate_flood_area


def recede_surface_flood(
    frame: SurfaceFloodFrame,
    *,
    dt_seconds: float = 300.0,
    config: SurfaceFloodConfig | None = None,
    network_id: str | None = None,
    network_revision_id: str | None = None,
) -> SurfaceFloodFrame:
    """Produce a no-rain/no-inflow cleanup frame from a prior frame."""

    if not isinstance(frame, SurfaceFloodFrame):
        raise TypeError("frame must be a SurfaceFloodFrame")
    effective_config = config or frame.config
    if effective_config is None:
        raise SurfaceFloodValidationError(
            "MISSING_CONFIG",
            "recession requires the original frame configuration",
        )
    fallback_catchments = cast(
        Sequence[Any],
        tuple(
            {
                "stable_key": cell.catchment_id,
                "area_m2": cell.total_area_m2,
                "elevation_m": cell.reference_elevation_m,
                "geometry": cell.geometry,
                "device_ids": cell.device_ids,
            }
            for cell in frame.cells
        ),
    )
    catchments: Sequence[Any] = frame.source_catchments or fallback_catchments
    return compute_surface_flood(
        catchments,
        frame.source_nodes,
        frame.source_pipes,
        frame.source_devices,
        frame_index=frame.frame_index + 1,
        valid_at=frame.valid_at + timedelta(seconds=dt_seconds),
        dt_seconds=dt_seconds,
        previous_frame=frame,
        config=effective_config,
        network_id=frame.network_id if network_id is None else network_id,
        network_revision_id=(
            frame.network_revision_id
            if network_revision_id is None
            else network_revision_id
        ),
        target_manifest_sha256=frame.target_manifest_sha256,
    )


__all__ = [
    "CatchmentFloodInput",
    "CatchmentMapping",
    "DeviceFloodInput",
    "FloodCatchment",
    "FloodCellLink",
    "FloodDevice",
    "FloodNode",
    "FloodPipe",
    "FloodSewerExchange",
    "FloodStageAreaPoint",
    "FloodStatus",
    "SurfaceFloodTransfer",
    "FRAME_SCHEMA_VERSION",
    "GEOJSON_SCHEMA_VERSION",
    "MODEL_VERSION",
    "NodeFloodInput",
    "PipeFloodInput",
    "SurfaceFloodCatchment",
    "SurfaceFloodCellState",
    "SurfaceFloodConfig",
    "SurfaceFloodDevice",
    "SurfaceFloodError",
    "SurfaceFloodFrame",
    "SurfaceFloodModel",
    "SurfaceFloodNode",
    "SurfaceFloodPipe",
    "SurfaceFloodState",
    "SurfaceFloodValidationError",
    "FloodConfig",
    "FloodCell",
    "FloodFrame",
    "SurfaceFloodResult",
    "assert_monotonic_cleanup",
    "build_catchment_mapping",
    "build_flood_geojson",
    "build_surface_flood_frame",
    "calculate_flood_area",
    "calculate_flood_depth",
    "compute_surface_flood",
    "flood_area_m2",
    "flood_depth_m",
    "generate_geojson_frame",
    "generate_surface_flood_frame",
    "recede_surface_flood",
    "map_catchments",
    "simulate_surface_flood",
    "validate_surface_flood_frame",
]
