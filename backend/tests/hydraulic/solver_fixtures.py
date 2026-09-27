"""Fixture builders for the pure-Python solver, CLI, and worker-start tests.

The input document is the frozen ``python-solver-input-v1`` schema described in
``backend/app/services/hydraulic/solvers/python_mw.py``.  Every hash in it is
derived, never a literal, so a fixture that drifts from the schema fails
immediately instead of silently testing a stale shape.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.hydraulic.solvers.base import canonical_json
from app.services.hydraulic.solvers.python_mw import (
    PYTHON_SOLVER_INITIAL_STATE_SCHEMA,
    PYTHON_SOLVER_INPUT_SCHEMA,
    PYTHON_SOLVER_NETWORK_SCHEMA,
    PYTHON_SOLVER_RAINFALL_SCHEMA,
    rainfall_content_sha256,
)

DEFAULT_INTENSITIES_MM_H: tuple[float, ...] = (12.0, 36.0, 60.0, 24.0, 0.0, 18.0)
DEFAULT_STEP_SECONDS = 300

SIMULATION_ID = "11111111-1111-4111-8111-111111111111"
ATTEMPT_ID = "22222222-2222-4222-8222-222222222222"
NETWORK_ID = "33333333-3333-4333-8333-333333333333"
NETWORK_REVISION_ID = "44444444-4444-4444-8444-444444444444"
PARAMETER_SET_ID = "55555555-5555-4555-8555-555555555555"
TRANSFORM_ID = "66666666-6666-4666-8666-666666666666"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def simple_network_document(
    *,
    outfall_invert_m: float = 0.8,
    steep_pipes: bool = False,
    diameter_m: float = 0.6,
    pipe_length_m: float = 120.0,
    required_features: tuple[str, ...] = (),
) -> dict[str, Any]:
    """A four-node, three-pipe trunk with one outfall and three catchments.

    ``steep_pipes=True`` shortens the reach so the invert gradient exceeds the
    mild-slope domain ``py_dw`` declares.  ``diameter_m`` widens the barrel:
    the 0.6 m default carries the design storm, but the diffusion-wave identity
    refuses a pressurised state, so a run inside its declared subcritical
    open-channel domain needs the larger section.
    """

    nodes: dict[str, Any] = {
        "J-1": {
            "invert_elevation_m": 2.0,
            "max_depth_m": 3.0,
            "surface_area_m2": 12.0,
            "kind": "junction",
        },
        "J-2": {
            "invert_elevation_m": 1.6,
            "max_depth_m": 3.0,
            "surface_area_m2": 12.0,
            "kind": "junction",
        },
        "J-3": {
            "invert_elevation_m": 1.2,
            "max_depth_m": 3.0,
            "surface_area_m2": 12.0,
            "kind": "junction",
        },
        "OUT-1": {
            "invert_elevation_m": outfall_invert_m,
            "max_depth_m": 3.0,
            "surface_area_m2": 20.0,
            "kind": "outfall",
            "outfall_width_m": 2.0,
        },
    }
    pipes: dict[str, Any] = {}
    reaches = (("J-1", "J-2"), ("J-2", "J-3"), ("J-3", "OUT-1"))
    for index, (upstream, downstream) in enumerate(reaches, start=1):
        upstream_invert = float(nodes[upstream]["invert_elevation_m"])
        downstream_invert = (
            outfall_invert_m
            if downstream == "OUT-1"
            else float(nodes[downstream]["invert_elevation_m"])
        )
        length = 20.0 if steep_pipes else pipe_length_m
        assert (upstream_invert - downstream_invert) / length > 0.0
        pipes[f"P-{index}"] = {
            "upstream_node_id": upstream,
            "downstream_node_id": downstream,
            "length_m": length,
            "manning_n": 0.013,
            "upstream_invert_m": upstream_invert,
            "downstream_invert_m": downstream_invert,
            "diameter_m": diameter_m,
            "shape": "circular",
        }
    document: dict[str, Any] = {
        "schema": PYTHON_SOLVER_NETWORK_SCHEMA,
        "nodes": nodes,
        "pipes": pipes,
        "catchments": {
            "C-1": {
                "outlet_node_id": "J-1",
                "area_m2": 2000.0,
                "runoff_coefficient": 0.65,
            },
            "C-2": {
                "outlet_node_id": "J-2",
                "area_m2": 1500.0,
                "runoff_coefficient": 0.70,
            },
            "C-3": {
                "outlet_node_id": "J-3",
                "area_m2": 1800.0,
                "runoff_coefficient": 0.60,
            },
        },
        "initial_state": {
            "schema": PYTHON_SOLVER_INITIAL_STATE_SCHEMA,
            "kind": "dry",
            "node_depth_m": 0.0,
            "pipe_flow_m3s": 0.0,
        },
    }
    if required_features:
        document["required_features"] = list(required_features)
    return document


def rainfall_document(
    intensities_mm_h: tuple[float, ...] = DEFAULT_INTENSITIES_MM_H,
    step_seconds: int = DEFAULT_STEP_SECONDS,
) -> dict[str, Any]:
    return {
        "schema": PYTHON_SOLVER_RAINFALL_SCHEMA,
        "step_seconds": step_seconds,
        "intensities_mm_h": list(intensities_mm_h),
        "content_sha256": rainfall_content_sha256(intensities_mm_h, step_seconds),
    }


def solver_input_document(
    *,
    solver_id: str = "py_mw",
    intensities_mm_h: tuple[float, ...] = DEFAULT_INTENSITIES_MM_H,
    step_seconds: int = DEFAULT_STEP_SECONDS,
    network: dict[str, Any] | None = None,
    rainfall: dict[str, Any] | None = None,
    start_at: str = "2026-01-01T00:00:00Z",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a valid frozen input document, then apply explicit overrides."""

    intensities = tuple(intensities_mm_h)
    rainfall_body = (
        rainfall
        if rainfall is not None
        else rainfall_document(intensities, step_seconds)
    )
    duration_seconds = step_seconds * (len(intensities) - 1)
    document: dict[str, Any] = {
        "schema": PYTHON_SOLVER_INPUT_SCHEMA,
        "solver_id": solver_id,
        "run": {
            "simulation_id": SIMULATION_ID,
            "attempt_id": ATTEMPT_ID,
            "attempt_no": 1,
            "fencing_token": 1,
            "network_id": NETWORK_ID,
            "network_revision_id": NETWORK_REVISION_ID,
        },
        "provenance": {
            "schema_version": "solver-input-manifest-v1",
            "network_revision_content_sha256": _digest("network-revision"),
            "snapshot_content_sha256": _digest("snapshot"),
            "initial_state_content_sha256": _digest("initial-state"),
            "target_manifest_sha256": _digest("target-manifest"),
            "rainfall_content_sha256": rainfall_body["content_sha256"],
            "solver_config_schema_id": "python-solver-config-v1",
            "solver_config_schema_version": "1",
            "parameter_set_id": PARAMETER_SET_ID,
            "parameter_set_content_sha256": _digest("parameter-set"),
            "parameter_schema_id": "hydraulic-parameter-set-v1",
            "parameter_schema_version": "1",
            "parameter_schema_sha256": _digest("parameter-schema"),
            "transform_id": TRANSFORM_ID,
            "solver_crs": "EPSG:32650",
            "ponding_enabled": False,
        },
        "time_grid": {
            "start_at": start_at,
            "duration_seconds": duration_seconds,
            "frame_interval_seconds": step_seconds,
            "expected_frame_count": len(intensities),
        },
        "solver_config": {
            "reporting_interval_s": step_seconds,
            "rain_step_s": step_seconds,
            "max_courant": 0.5,
            "max_substeps": 20,
            "min_time_step_s": 1.0e-6,
            "depth_floor_m": 1.0e-4,
            "gravity_mps2": 9.80665,
            "rapid_reversal_fraction": 0.05,
        },
        "network": network if network is not None else simple_network_document(),
        "rainfall": rainfall_body,
    }
    for key, value in overrides.items():
        if key in {"provenance", "time_grid", "run", "solver_config"}:
            merged = dict(document[key])
            merged.update(value)
            document[key] = merged
        else:
            document[key] = value
    return document


def write_input(
    workspace: Path, document: Any, *, name: str = "solver_input.json"
) -> Path:
    """Write the input document exactly as a worker would materialise it."""

    payload = document if isinstance(document, str) else canonical_json(document)
    path = workspace / name
    path.write_text(payload, encoding="utf-8")
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_progress(workspace: Path) -> list[dict[str, Any]]:
    """Parse ``solver_progress.jsonl`` into a list of events."""

    path = workspace / "solver_progress.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
