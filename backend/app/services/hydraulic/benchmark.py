"""Frozen-fixture executor for the 5k/10k/24h hydraulic acceptance benchmark.

The benchmark harness (``scripts/benchmark_hydraulic.py``) owns timing,
resource limits and evidence.  This module owns the *work*: it rebuilds the
frozen network, runs the production pure-Python motion-wave solver, and streams
every reporting frame to the attempt-scoped sink.  Nothing here measures
anything or decides whether the run passed, so a slow or inaccurate run cannot
be reported as a good one.

The executor re-verifies every digest in the fixture manifest before it runs,
so a fixture that was edited after the manifest was frozen stops the run instead
of silently benchmarking different inputs.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services.hydraulic.solvers.python_hydraulics import (
    Catchment,
    HydraulicNetwork,
    HydraulicNode,
    HydraulicPipe,
    RainfallForcing,
    RoutingMode,
    SolverConfig,
    solve_network_to_sink,
)

SOLVER_ID = "py_mw"
MANIFEST_SCHEMA = "hydraulic-solver-benchmark-v1"
FIXTURE_INPUTS = (
    "network",
    "rainfall",
    "target_manifest",
    "initial_state",
    "solver_config",
)


class BenchmarkFixtureError(RuntimeError):
    """The frozen fixture is missing, altered, or internally inconsistent."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkFixtureError(
            f"{label} is not readable JSON: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise BenchmarkFixtureError(f"{label} must be a JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BenchmarkFixtureError(message)


def _positive(value: Any, label: str) -> float:
    number = float(value)
    _require(
        math.isfinite(number) and number > 0.0, f"{label} must be finite and positive"
    )
    return number


def _non_negative(value: Any, label: str) -> float:
    number = float(value)
    _require(
        math.isfinite(number) and number >= 0.0,
        f"{label} must be finite and non-negative",
    )
    return number


def load_verified_fixture(manifest_path: Path) -> dict[str, Any]:
    """Load the manifest and every input it pins, rejecting any drift."""

    manifest_path = manifest_path.resolve()
    manifest = _load_json(manifest_path, "fixture manifest")
    _require(manifest.get("schema") == MANIFEST_SCHEMA, "unexpected fixture schema")
    fixture_root = manifest_path.parent
    documents: dict[str, dict[str, Any]] = {}
    for name in FIXTURE_INPUTS:
        reference = manifest.get(name)
        # An explicit isinstance/raise (same exception _require would raise)
        # so the type is narrowed for the reads below.
        if not isinstance(reference, dict):
            raise BenchmarkFixtureError(f"fixture reference {name} must be an object")
        relative = Path(str(reference.get("path", "")))
        _require(not relative.is_absolute(), f"{name}.path must be relative")
        _require(".." not in relative.parts, f"{name}.path must not contain '..'")
        resolved = (fixture_root / relative).resolve()
        _require(resolved.is_file(), f"{name} file is missing: {relative}")
        _require(
            _sha256_file(resolved) == reference.get("sha256"),
            f"{name} digest does not match the frozen manifest",
        )
        documents[name] = _load_json(resolved, name)

    solver = manifest.get("solver")
    if not isinstance(solver, dict):
        raise BenchmarkFixtureError("fixture solver must be an object")
    _require(solver.get("id") == SOLVER_ID, f"fixture solver id must be {SOLVER_ID}")
    executor = manifest.get("executor")
    if not isinstance(executor, dict):
        raise BenchmarkFixtureError("fixture executor must be an object")
    _require(
        executor.get("module") == __name__
        and executor.get("qualname") == "run_frozen_benchmark_attempt",
        "fixture executor does not point at this module",
    )
    _require(executor.get("async") is True, "this executor must be declared async")
    return {"manifest": manifest, "documents": documents, "fixture_root": fixture_root}


def _build_network(document: dict[str, Any]) -> HydraulicNetwork:
    nodes = tuple(
        HydraulicNode(
            node_id=str(item["node_id"]),
            invert_elevation_m=float(item["invert_elevation_m"]),
            max_depth_m=_positive(item["max_depth_m"], "node max_depth_m"),
            initial_depth_m=_non_negative(
                item["initial_depth_m"], "node initial_depth_m"
            ),
            surface_area_m2=_positive(item["surface_area_m2"], "node surface_area_m2"),
            kind=str(item.get("kind", "junction")),
            outfall_width_m=_positive(
                item.get("outfall_width_m", 1.0), "node outfall_width_m"
            ),
        )
        for item in document["nodes"]
    )
    pipes = tuple(
        HydraulicPipe(
            pipe_id=str(item["pipe_id"]),
            upstream_node_id=str(item["upstream_node_id"]),
            downstream_node_id=str(item["downstream_node_id"]),
            length_m=_positive(item["length_m"], "pipe length_m"),
            manning_n=_positive(item["manning_n"], "pipe manning_n"),
            upstream_invert_m=float(item["upstream_invert_m"]),
            downstream_invert_m=float(item["downstream_invert_m"]),
            diameter_m=(
                None
                if item.get("diameter_m") is None
                else _positive(item["diameter_m"], "pipe diameter_m")
            ),
            width_m=(
                None if item.get("width_m") is None else _positive(item["width_m"], "w")
            ),
            height_m=(
                None
                if item.get("height_m") is None
                else _positive(item["height_m"], "h")
            ),
            shape=str(item.get("shape", "circular")),
            initial_flow_m3s=float(item.get("initial_flow_m3s", 0.0)),
        )
        for item in document["pipes"]
    )
    catchments = tuple(
        Catchment(
            catchment_id=str(item["catchment_id"]),
            outlet_node_id=str(item["outlet_node_id"]),
            area_m2=_positive(item["area_m2"], "catchment area_m2"),
            runoff_coefficient=_non_negative(
                item.get("runoff_coefficient", 1.0), "runoff_coefficient"
            ),
            depression_storage_mm=_non_negative(
                item.get("depression_storage_mm", 0.0), "depression_storage_mm"
            ),
        )
        for item in document.get("catchments", ())
    )
    return HydraulicNetwork(nodes=nodes, pipes=pipes, catchments=catchments)


def _build_rainfall(document: dict[str, Any], expected_samples: int) -> RainfallForcing:
    intensities = document.get("intensities_mm_h")
    if not isinstance(intensities, list) or len(intensities) != expected_samples:
        raise BenchmarkFixtureError(
            f"frozen rainfall must declare exactly {expected_samples} samples"
        )
    values = []
    for position, raw in enumerate(intensities):
        value = float(raw)
        _require(
            math.isfinite(value) and value >= 0.0,
            f"rainfall sample {position} must be finite and non-negative",
        )
        values.append(value)
    return RainfallForcing(
        step_seconds=_positive(document["step_seconds"], "rainfall step_seconds"),
        intensities_mm_h=tuple(values),
    )


def _build_solver_config(document: dict[str, Any]) -> SolverConfig:
    _require(document.get("solver_id") == SOLVER_ID, "solver_config solver_id mismatch")
    return SolverConfig(
        duration_s=_positive(document["duration_s"], "duration_s"),
        reporting_interval_s=_positive(
            document["reporting_interval_s"], "reporting_interval_s"
        ),
        rain_step_s=_positive(document["rain_step_s"], "rain_step_s"),
        max_courant=_positive(document.get("max_courant", 0.5), "max_courant"),
        max_substeps=int(document.get("max_substeps", 20)),
        depth_floor_m=_positive(document.get("depth_floor_m", 1.0e-4), "depth_floor_m"),
        gravity_mps2=_positive(document.get("gravity_mps2", 9.80665), "gravity_mps2"),
    )


def _frame_rollup(frame: Any) -> dict[str, Any]:
    """Aggregate one frame into the published time-series shape.

    The benchmark harness serialises every byte the sink receives, so a frame
    must stay small enough to stream 289 times for 5000 nodes and 10000 pipes.
    """

    depths = [node.depth_m for node in frame.nodes]
    fills = [pipe.fill_ratio for pipe in frame.pipes]
    surcharged = sum(
        1
        for pipe in frame.pipes
        if pipe.fill_ratio >= 1.0 or pipe.surcharge_ratio > 1.0
    )
    return {
        "max_node_depth_m": max(depths) if depths else 0.0,
        "mean_node_depth_m": (sum(depths) / len(depths)) if depths else 0.0,
        "max_pipe_fill_ratio": max(fills) if fills else 0.0,
        "mean_pipe_fill_ratio": (sum(fills) / len(fills)) if fills else 0.0,
        "surcharged_pipe_count": surcharged,
        "mass_residual_m3": float(frame.mass_residual_m3),
        "mass_residual_relative": float(frame.mass_residual_relative),
        "max_courant": float(frame.max_courant),
        "substep_count": int(frame.substep_count),
        "quality_status": frame.quality_status.value,
    }


class _SolverFrameBridge:
    """Adapts the production solver's ``frame_sink`` protocol to the harness.

    ``solve_network_to_sink`` calls ``frame_sink.write(frame)`` once per
    reporting frame and awaits each call, so the object has to expose a
    coroutine ``write`` rather than a plain function.
    """

    def __init__(self, publish: Any) -> None:
        self._publish = publish

    async def write(self, frame: Any) -> None:
        await self._publish(frame)


def _sink_persistence_evidence(
    sink: Any, expected_frames: int, received: int
) -> dict[str, Any]:
    """Ask the sink what it actually persisted.

    These four flags used to be literal ``True`` values, which made the
    harness check (``persistence.<name> is True``) vacuous: nothing ever
    verified that a frame reached TimescaleDB.  A sink now has to say so via
    ``persistence_evidence()``; a sink that cannot is reported as having
    committed nothing, so the gate fails closed instead of asserting a
    database write that never happened.
    """

    evidence_fn = getattr(sink, "persistence_evidence", None)
    if evidence_fn is None:
        return {
            "database_committed": False,
            "all_frames_committed": received == expected_frames,
            "quality_report_committed": False,
            "published": False,
            "persistence_target": None,
            "detail": "sink exposes no persistence_evidence(); nothing is claimed",
        }
    evidence = evidence_fn()
    if not isinstance(evidence, dict):
        raise BenchmarkFixtureError("sink persistence_evidence() must return a dict")
    return {
        "database_committed": bool(evidence.get("database_committed")),
        "all_frames_committed": bool(evidence.get("all_frames_committed"))
        and received == expected_frames,
        "quality_report_committed": bool(evidence.get("quality_report_committed")),
        "published": bool(evidence.get("published")),
        "persistence_target": evidence.get("persistence_target"),
        "detail": evidence.get("detail"),
    }


async def run_frozen_benchmark_attempt(
    manifest_path: Path, sink: Any
) -> dict[str, Any]:
    """Run one full attempt of the frozen acceptance scenario.

    ``sink`` is supplied by the harness and is the only channel through which
    results leave this process.  The returned mapping carries the attempt
    identity, the durable-publication evidence the simulation pipeline records
    for a completed run, and the mass-balance quality figure the harness
    thresholds.
    """

    queued_at = time.perf_counter()
    frozen = load_verified_fixture(Path(manifest_path))
    manifest = frozen["manifest"]
    documents = frozen["documents"]

    counts = manifest["input_counts"]
    frames = manifest["frames"]
    network = _build_network(documents["network"])
    _require(
        len(network.nodes) == counts["nodes"]
        and len(network.pipes) == counts["pipes"]
        and len(network.catchments) == counts["catchments"],
        "frozen network counts do not match the manifest input_counts",
    )
    rainfall = _build_rainfall(documents["rainfall"], int(frames["count"]))
    config = _build_solver_config(documents["solver_config"])
    _require(
        int(round(config.duration_s / 60.0)) == frames["duration_minutes"],
        "frozen solver duration does not match the manifest frame schedule",
    )

    interval_minutes = int(frames["interval_minutes"])
    frame_start = _parse_iso(frames["start"])
    attempt_id = f"bench-{manifest['fixture_id']}-{time.time_ns()}"
    published: dict[str, Any] = {
        "attempt_id": attempt_id,
        "frame_count": 0,
        "max_node_depth_m": 0.0,
        "max_pipe_fill_ratio": 0.0,
        "surcharged_pipe_count": 0,
        "worst_mass_residual_relative": 0.0,
    }

    async def write_frame(frame: Any) -> None:
        rollup = _frame_rollup(frame)
        index = published["frame_count"]
        published["frame_count"] += 1
        published["max_node_depth_m"] = max(
            published["max_node_depth_m"], rollup["max_node_depth_m"]
        )
        published["max_pipe_fill_ratio"] = max(
            published["max_pipe_fill_ratio"], rollup["max_pipe_fill_ratio"]
        )
        published["surcharged_pipe_count"] = max(
            published["surcharged_pipe_count"], rollup["surcharged_pipe_count"]
        )
        published["worst_mass_residual_relative"] = max(
            published["worst_mass_residual_relative"],
            rollup["mass_residual_relative"],
        )
        await sink.write(
            {
                "attempt_id": attempt_id,
                "frame_index": index,
                "frame_time": (
                    frame_start + timedelta(minutes=interval_minutes * index)
                ).isoformat(),
                "model_time_s": float(frame.time_s),
                **rollup,
            }
        )

    started_at = time.perf_counter()
    result = await solve_network_to_sink(
        network,
        rainfall,
        _SolverFrameBridge(write_frame),
        routing_mode=RoutingMode(
            str(documents["solver_config"].get("routing_mode", "motion_wave"))
        ),
        config=config,
    )
    queue_wait_seconds = max(started_at - queued_at, 0.0)
    published["queue_wait_seconds"] = queue_wait_seconds

    await sink.finalize(
        {
            "attempt_id": attempt_id,
            "solver_id": result.solver_id,
            "solver_version": manifest["solver"]["version"],
            "routing_mode": result.routing_mode.value,
            "model_fidelity": result.model_fidelity,
            "quality_status": result.quality_status.value,
            "frame_count": published["frame_count"],
            "max_node_depth_m": published["max_node_depth_m"],
            "max_pipe_fill_ratio": published["max_pipe_fill_ratio"],
            "surcharged_pipe_count": published["surcharged_pipe_count"],
            "worst_mass_residual_relative": published["worst_mass_residual_relative"],
            "mass_balance": result.mass_balance.to_dict(),
            "stability": result.stability.to_dict(),
        }
    )

    return {
        "attempt_id": attempt_id,
        "published_attempt_id": attempt_id,
        "queue_wait_seconds": queue_wait_seconds,
        "persistence": {
            **_sink_persistence_evidence(
                sink, frames["count"], published["frame_count"]
            )
        },
        "quality": {
            "mass_balance_relative_residual": result.mass_balance.residual_relative,
            "worst_frame_mass_residual_relative": published[
                "worst_mass_residual_relative"
            ],
            "quality_status": result.quality_status.value,
        },
        "summary": {
            "solver_id": result.solver_id,
            "solver_version": manifest["solver"]["version"],
            "routing_mode": result.routing_mode.value,
            "frame_count": published["frame_count"],
            "fixture_id": manifest["fixture_id"],
        },
    }


def _parse_iso(value: Any) -> datetime:
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise BenchmarkFixtureError(
            f"frames.start must be ISO-8601: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


__all__ = [
    "BenchmarkFixtureError",
    "load_verified_fixture",
    "run_frozen_benchmark_attempt",
]
