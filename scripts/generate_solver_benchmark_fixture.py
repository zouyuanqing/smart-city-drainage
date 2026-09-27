#!/usr/bin/env python3
"""Generate the frozen 5k-node/10k-pipe/24h solver benchmark fixture.

The fixture consumed by ``scripts/benchmark_hydraulic.py`` is a frozen,
digest-pinned artefact.  This generator is its source of truth: it is
deterministic, so re-running it reproduces byte-identical inputs, and it fails
loudly if the generated network stops satisfying the solver's own contract.

Geometry is sized for the acceptance run to be numerically solvable:
``_network_stable_dt`` is bounded by the network-wide minimum pipe length and
the largest pipe height, so short segments would force more substeps per
reporting interval than ``SolverConfig.max_substeps`` allows.

Usage:
    python scripts/generate_solver_benchmark_fixture.py [--check]

``--check`` regenerates in memory and fails if the committed files differ.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.services.hydraulic.solvers.python_hydraulics import (  # noqa: E402
    Catchment,
    HydraulicNetwork,
    HydraulicNode,
    HydraulicPipe,
)

FIXTURE_DIR = BACKEND / "tests" / "fixtures" / "performance" / "solver" / (
    "5k_10k_24h_v1"
)
SCHEMA = "hydraulic-solver-benchmark-v1"
FIXTURE_ID = "5k_10k_24h_v1"
NODE_COUNT = 5000
PIPE_COUNT = 10000
CATCHMENT_COUNT = 1000
FRAME_COUNT = 289
FRAME_INTERVAL_MINUTES = 5
FRAME_DURATION_MINUTES = 1440
FRAME_START = "2026-01-01T00:00:00Z"
SOLVER_ID = "py_mw"
SOLVER_VERSION = "py_mw/1.0.0"

OUTFALL_INVERT_M = 0.5
LEVEL_STEP_M = 0.05
NODE_MAX_DEPTH_M = 4.0
TREE_LENGTH_MIN_M = 250.0
TREE_LENGTH_SPAN_M = 150.0
LOOP_LENGTH_OFFSET_M = 5.0
LOOP_LENGTH_SPAN_M = 145.0
DIAMETER_MIN_M = 0.3
DIAMETER_STEP_M = 0.1
DIAMETER_STEPS = 4
MANNING_N = 0.013
# Peak design intensity of the 24h storm, mm/h.  The 50-year acceptance storm in
# this repository peaks well above 100 mm/h; the frozen benchmark storm is a
# reproducible mid-frequency event so the run stays inside the wall-clock
# budget without changing the solver's stability regime.
PEAK_INTENSITY_MM_H = 40.0
CATCHMENT_AREA_BASE_M2 = 2000.0
CATCHMENT_AREA_STEP_M2 = 250.0
RUNOFF_COEFFICIENT_BASE = 0.65
RUNOFF_COEFFICIENT_STEP = 0.05
EXECUTOR_MODULE = "app.services.hydraulic.benchmark"
EXECUTOR_QUALNAME = "run_frozen_benchmark_attempt"


def node_level(index: int) -> int:
    return index.bit_length()


def node_invert(index: int) -> float:
    return OUTFALL_INVERT_M + LEVEL_STEP_M * node_level(index)


def build_network() -> HydraulicNetwork:
    nodes = [
        HydraulicNode(
            node_id="N-00000",
            invert_elevation_m=OUTFALL_INVERT_M,
            max_depth_m=NODE_MAX_DEPTH_M,
            initial_depth_m=0.0,
            surface_area_m2=1.0,
            kind="outfall",
            outfall_width_m=2.0,
        )
    ]
    for index in range(1, NODE_COUNT):
        nodes.append(
            HydraulicNode(
                node_id=f"N-{index:05d}",
                invert_elevation_m=node_invert(index),
                max_depth_m=NODE_MAX_DEPTH_M,
                initial_depth_m=0.0,
                surface_area_m2=0.5 + (index % 5) * 0.1,
                kind="junction",
            )
        )

    pipes: list[HydraulicPipe] = []
    for index in range(1, NODE_COUNT):
        parent = index // 2
        pipes.append(
            HydraulicPipe(
                pipe_id=f"P-T{index:05d}",
                upstream_node_id=f"N-{index:05d}",
                downstream_node_id=f"N-{parent:05d}",
                length_m=TREE_LENGTH_MIN_M + (index % int(TREE_LENGTH_SPAN_M)),
                manning_n=MANNING_N,
                upstream_invert_m=node_invert(index),
                downstream_invert_m=node_invert(parent),
                diameter_m=DIAMETER_MIN_M + (index % DIAMETER_STEPS) * DIAMETER_STEP_M,
                shape="circular",
            )
        )
    for offset in range(PIPE_COUNT - len(pipes)):
        index = 1 + (offset % (NODE_COUNT - 1))
        parent = index // 2
        pipes.append(
            HydraulicPipe(
                pipe_id=f"P-L{offset:05d}",
                upstream_node_id=f"N-{index:05d}",
                downstream_node_id=f"N-{parent:05d}",
                length_m=(
                    TREE_LENGTH_MIN_M
                    + LOOP_LENGTH_OFFSET_M
                    + (offset % int(LOOP_LENGTH_SPAN_M))
                ),
                manning_n=MANNING_N,
                # The parallel segment sits a few centimetres above the trunk so
                # the two pipes between one node pair are not numerically
                # identical; both keep a non-negative slope.
                upstream_invert_m=node_invert(index) + 0.01 + (offset % 3) * 0.002,
                downstream_invert_m=node_invert(parent) + 0.005,
                diameter_m=(
                    DIAMETER_MIN_M
                    + ((index + offset) % DIAMETER_STEPS) * DIAMETER_STEP_M
                ),
                shape="circular",
            )
        )

    catchments = [
        Catchment(
            catchment_id=f"C-{index:05d}",
            outlet_node_id=f"N-{index:05d}",
            area_m2=CATCHMENT_AREA_BASE_M2 + (index % 7) * CATCHMENT_AREA_STEP_M2,
            runoff_coefficient=(
                RUNOFF_COEFFICIENT_BASE + (index % 4) * RUNOFF_COEFFICIENT_STEP
            ),
            depression_storage_mm=1.0,
        )
        for index in range(1, CATCHMENT_COUNT + 1)
    ]
    network = HydraulicNetwork(
        nodes=tuple(nodes), pipes=tuple(pipes), catchments=tuple(catchments)
    )
    if len(network.nodes) != NODE_COUNT:
        raise SystemExit(f"expected {NODE_COUNT} nodes, built {len(network.nodes)}")
    if len(network.pipes) != PIPE_COUNT:
        raise SystemExit(f"expected {PIPE_COUNT} pipes, built {len(network.pipes)}")
    if len(network.catchments) != CATCHMENT_COUNT:
        raise SystemExit(
            f"expected {CATCHMENT_COUNT} catchments, built "
            f"{len(network.catchments)}"
        )
    return network


def storm_intensity(frame_index: int) -> float:
    """Deterministic 24h hyetograph peaking once at the storm centre."""

    if not 0 <= frame_index < FRAME_COUNT:
        raise ValueError("frame index out of range")
    phase = frame_index / (FRAME_COUNT - 1)
    base = 0.15 * PEAK_INTENSITY_MM_H
    surge = PEAK_INTENSITY_MM_H - base
    return base + surge * math.exp(-(((phase - 0.42) / 0.16) ** 2))


def build_documents() -> dict[str, Any]:
    network = build_network()

    network_document = {
        "schema": "hydraulic-solver-benchmark-network-v1",
        "generator": "scripts/generate_solver_benchmark_fixture.py",
        "layout": {
            "structure": "balanced-binary trunk with parallel loop segments",
            "outfall_node_id": "N-00000",
            "tree_pipe_count": NODE_COUNT - 1,
            "loop_pipe_count": PIPE_COUNT - (NODE_COUNT - 1),
            "level_step_m": LEVEL_STEP_M,
            "outfall_invert_m": OUTFALL_INVERT_M,
            "node_max_depth_m": NODE_MAX_DEPTH_M,
            "manning_n": MANNING_N,
        },
        "nodes": [
            {
                "node_id": node.node_id,
                "invert_elevation_m": node.invert_elevation_m,
                "max_depth_m": node.max_depth_m,
                "initial_depth_m": node.initial_depth_m,
                "surface_area_m2": node.surface_area_m2,
                "kind": node.kind,
                "outfall_width_m": node.outfall_width_m,
            }
            for node in network.nodes
        ],
        "pipes": [
            {
                "pipe_id": pipe.pipe_id,
                "upstream_node_id": pipe.upstream_node_id,
                "downstream_node_id": pipe.downstream_node_id,
                "length_m": pipe.length_m,
                "manning_n": pipe.manning_n,
                "upstream_invert_m": pipe.upstream_invert_m,
                "downstream_invert_m": pipe.downstream_invert_m,
                "diameter_m": pipe.diameter_m,
                "shape": pipe.shape,
                "initial_flow_m3s": pipe.initial_flow_m3s,
            }
            for pipe in network.pipes
        ],
        "catchments": [
            {
                "catchment_id": item.catchment_id,
                "outlet_node_id": item.outlet_node_id,
                "area_m2": item.area_m2,
                "runoff_coefficient": item.runoff_coefficient,
                "depression_storage_mm": item.depression_storage_mm,
            }
            for item in network.catchments
        ],
    }

    rainfall_document = {
        "schema": "hydraulic-solver-benchmark-rainfall-v1",
        "generator": "scripts/generate_solver_benchmark_fixture.py",
        "intensity_model": "base + (peak - base) * exp(-((phase - 0.42) / 0.16) ** 2)",
        "peak_intensity_mm_h": PEAK_INTENSITY_MM_H,
        "step_seconds": FRAME_INTERVAL_MINUTES * 60,
        "sample_count": FRAME_COUNT,
        "intensities_mm_h": [storm_intensity(index) for index in range(FRAME_COUNT)],
    }

    target_document = {
        "schema": "hydraulic-solver-benchmark-target-v1",
        "target_metrics": {
            "max_mass_balance_relative_residual": 1.0e-4,
            "max_worker_wall_time_seconds": 180.0,
        },
        "reporting": {
            "frame_count": FRAME_COUNT,
            "interval_minutes": FRAME_INTERVAL_MINUTES,
            "duration_minutes": FRAME_DURATION_MINUTES,
            "start": FRAME_START,
        },
        "acceptance_counts": {
            "nodes": NODE_COUNT,
            "pipes": PIPE_COUNT,
            "catchments": CATCHMENT_COUNT,
        },
    }

    initial_state_document = {
        "schema": "hydraulic-solver-benchmark-initial-state-v1",
        "generator": "scripts/generate_solver_benchmark_fixture.py",
        "node_depth_m": 0.0,
        "pipe_flow_m3s": 0.0,
        "outfall_node_id": "N-00000",
        "note": (
            "The frozen network starts dry; the acceptance run therefore measures "
            "the full rise-and-recession of the design storm, not a warmed state."
        ),
    }

    solver_config_document = {
        "schema": "hydraulic-solver-benchmark-solver-config-v1",
        "solver_id": SOLVER_ID,
        "routing_mode": "motion_wave",
        "duration_s": FRAME_DURATION_MINUTES * 60,
        "reporting_interval_s": FRAME_INTERVAL_MINUTES * 60,
        "rain_step_s": FRAME_INTERVAL_MINUTES * 60,
        "max_courant": 0.5,
        "max_substeps": 20,
        "depth_floor_m": 1.0e-4,
        "gravity_mps2": 9.80665,
    }

    return {
        "network": network_document,
        "rainfall": rainfall_document,
        "target_manifest": target_document,
        "initial_state": initial_state_document,
        "solver_config": solver_config_document,
    }


def canonical(document: Any) -> str:
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed fixture matches this generator",
    )
    args = parser.parse_args(argv)

    documents = build_documents()
    file_names = {name: f"{name}.json" for name in documents}
    # The harness digests the file bytes, so the pinned digest must cover the
    # trailing newline that is actually written.
    rendered = {name: canonical(value) + "\n" for name, value in documents.items()}
    engine_source = BACKEND / "app/services/hydraulic/solvers/python_hydraulics.py"
    engine_sha256 = digest(engine_source.read_text(encoding="utf-8"))
    manifest = {
        "schema": SCHEMA,
        "fixture_id": FIXTURE_ID,
        "generator": "scripts/generate_solver_benchmark_fixture.py",
        "input_counts": {
            "nodes": NODE_COUNT,
            "pipes": PIPE_COUNT,
            "catchments": CATCHMENT_COUNT,
        },
        "frames": {
            "count": FRAME_COUNT,
            "start": FRAME_START,
            "interval_minutes": FRAME_INTERVAL_MINUTES,
            "duration_minutes": FRAME_DURATION_MINUTES,
        },
        "solver": {
            "id": SOLVER_ID,
            "version": SOLVER_VERSION,
            "engine_sha256": engine_sha256,
            "engine_path": "backend/app/services/hydraulic/solvers/python_hydraulics.py",
        },
        "executor": {
            "module": EXECUTOR_MODULE,
            "qualname": EXECUTOR_QUALNAME,
            "async": True,
        },
        **{
            name: {
                "path": file_names[name],
                "sha256": digest(rendered[name]),
            }
            for name in sorted(rendered)
        },
    }
    manifest_text = canonical(manifest)

    expected: dict[str, str] = {
        file_names[name]: text for name, text in rendered.items()
    }
    expected["manifest.json"] = manifest_text

    if args.check:
        problems: list[str] = []
        for name, text in expected.items():
            path = FIXTURE_DIR / name
            if not path.is_file():
                problems.append(f"missing: {path}")
            elif path.read_bytes() != text.encode("utf-8"):
                problems.append(f"stale: {path}")
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            return 1
        print(f"fixture matches the generator: {FIXTURE_DIR}")
        return 0

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in expected.items():
        # newline="\n" keeps the bytes LF-only; text mode would rewrite every
        # "\n" as "\r\n" on Windows and break the pinned digests.
        (FIXTURE_DIR / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(expected)} fixture files to {FIXTURE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
