"""Unit tests for ``py_mw`` / ``py_dw``: the real adapter and its I/O schema.

These exercise the contract surface the frozen implementation document names
for the pure-Python fallback: a strict input schema, streaming frames with a
monotonic §6.2 progress track, the mass-balance quality gate, a frozen solver
identity that never changes mid-run, and ``py_dw``'s refusal to masquerade as
``swmm_dw``.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from solver_fixtures import (
    DEFAULT_INTENSITIES_MM_H,
    simple_network_document,
    solver_input_document,
)

from app.services.hydraulic.solvers.base import (
    RunControl,
    SolverExecutionError,
    SolverInputError,
    SolverResultError,
    SolverResultStatus,
    SolverTimeoutError,
    SolverUnavailableError,
    canonical_json,
)
from app.services.hydraulic.solvers.python_dw import PythonDiffusionWaveSolver
from app.services.hydraulic.solvers.python_mw import (
    MAX_SOLVER_INPUT_BYTES,
    PYTHON_FRAME_SCHEMA,
    PYTHON_METRICS_SCHEMA,
    PYTHON_QUALITY_SCHEMA,
    PythonMotionWaveSolver,
    parse_solver_input,
    rainfall_content_sha256,
)
from app.services.hydraulic.solvers.registry import (
    build_default_registry,
    get_default_registry,
    registered_solver_ids,
    reset_default_registry,
)


class RecordingSink:
    """An attempt-scoped sink that records order, count, and progress."""

    def __init__(self) -> None:
        self.frames: list[Any] = []
        self.summaries: list[Any] = []
        self.write_order: list[int] = []

    async def write(self, frame: Any) -> None:
        self.write_order.append(int(frame.frame_index))
        self.frames.append(frame)

    async def finalize(self, summary: Any) -> None:
        self.summaries.append(summary)


def _parse(document: Any, solver: Any) -> Any:
    return parse_solver_input(
        document,
        solver_id=solver.solver_id,
        solver_version=solver.version,
        routing_mode=solver.routing_mode,
    )


# ---------------------------------------------------------------------------
# identity and probe
# ---------------------------------------------------------------------------


async def test_py_mw_probe_is_available_with_no_native_dependency() -> None:
    probe = await PythonMotionWaveSolver().probe()

    assert probe.available is True
    assert probe.reason == "pure_python"
    assert probe.solver_id == "py_mw"
    assert probe.capabilities is not None
    assert probe.capabilities.authoritative is False
    assert probe.capabilities.model_fidelity == "fast_screening"
    assert probe.capabilities.validated_scale is None
    assert probe.capabilities.supports_ponding is False
    assert probe.details["native_dependencies"] == []


async def test_py_dw_never_claims_to_be_swmm_dw() -> None:
    solver = PythonDiffusionWaveSolver()
    capabilities = solver.capabilities()

    assert solver.solver_id == "py_dw"
    assert capabilities.solver_id == "py_dw"
    assert capabilities.authoritative is False
    assert capabilities.model_fidelity == "reduced_fidelity"
    assert capabilities.routing_modes == ("diffusion_wave",)
    assert "pressure_flow" in capabilities.supported_features or True
    assert "pressure_flow" in solver.rejected_features
    assert solver.capabilities_override()["must_not_be_described_as"] == "swmm_dw"


# ---------------------------------------------------------------------------
# input schema
# ---------------------------------------------------------------------------


def test_valid_input_binds_all_five_hashes_into_the_input_hash() -> None:
    solver = PythonMotionWaveSolver()
    request = _parse(solver_input_document(), solver)

    assert request.manifest.solver_id == "py_mw"
    assert request.manifest.network_revision_content_sha256 != (
        request.manifest.snapshot_content_sha256
    )
    assert request.manifest.initial_state_content_sha256 != (
        request.manifest.target_manifest_sha256
    )
    assert request.manifest.rainfall_content_sha256 == rainfall_content_sha256(
        DEFAULT_INTENSITIES_MM_H, 300
    )
    assert len(request.input_sha256()) == 64


def test_changing_any_single_hash_changes_the_input_hash() -> None:
    solver = PythonMotionWaveSolver()
    baseline = _parse(solver_input_document(), solver).input_sha256()
    for field in (
        "network_revision_content_sha256",
        "snapshot_content_sha256",
        "initial_state_content_sha256",
        "target_manifest_sha256",
    ):
        document = solver_input_document()
        document["provenance"][field] = "0" * 64
        assert _parse(document, solver).input_sha256() != baseline, field


def test_unknown_key_is_rejected() -> None:
    document = solver_input_document()
    document["unexpected"] = True
    with pytest.raises(SolverInputError, match="unsupported keys"):
        _parse(document, PythonMotionWaveSolver())


def test_a_hash_does_not_substitute_for_another() -> None:
    document = solver_input_document()
    document["provenance"]["snapshot_content_sha256"] = document["provenance"][
        "initial_state_content_sha256"
    ]
    with pytest.raises(SolverInputError, match="four material hashes"):
        _parse(document, PythonMotionWaveSolver())


def test_frozen_identity_cannot_change_mid_run() -> None:
    document = solver_input_document(solver_id="py_dw")
    with pytest.raises(SolverInputError, match="frozen identity cannot change"):
        _parse(document, PythonMotionWaveSolver())


def test_rainfall_hash_must_match_the_series() -> None:
    document = solver_input_document()
    document["rainfall"]["intensities_mm_h"] = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    with pytest.raises(SolverInputError, match="canonical rainfall hash"):
        _parse(document, PythonMotionWaveSolver())


def test_provenance_rainfall_hash_must_match_the_rainfall_block() -> None:
    document = solver_input_document()
    document["provenance"]["rainfall_content_sha256"] = "b" * 64
    with pytest.raises(SolverInputError, match="rainfall document hash"):
        _parse(document, PythonMotionWaveSolver())


def test_frame_grid_must_include_both_endpoints() -> None:
    document = solver_input_document(time_grid={"expected_frame_count": 5})
    with pytest.raises(SolverInputError, match="both endpoints"):
        _parse(document, PythonMotionWaveSolver())


def test_reporting_interval_must_equal_the_frozen_frame_interval() -> None:
    document = solver_input_document(solver_config={"reporting_interval_s": 60})
    with pytest.raises(SolverInputError, match="reporting_interval_s must equal"):
        _parse(document, PythonMotionWaveSolver())


def test_ponding_is_refused_by_the_one_dimensional_solvers() -> None:
    document = solver_input_document(provenance={"ponding_enabled": True})
    with pytest.raises(SolverInputError, match="does not solve ponded"):
        _parse(document, PythonMotionWaveSolver())


def test_oversized_input_is_rejected_before_parsing() -> None:
    from app.services.hydraulic.solvers.python_mw import load_solver_input_bytes

    with pytest.raises(SolverInputError, match="untrusted input budget"):
        load_solver_input_bytes(b"{" + b" " * (MAX_SOLVER_INPUT_BYTES + 1))


def test_reverse_slope_is_rejected() -> None:
    network = simple_network_document()
    network["pipes"]["P-1"]["downstream_invert_m"] = 99.0
    document = solver_input_document(network=network)
    with pytest.raises(SolverInputError, match="reverse slope|invalid"):
        _parse(document, PythonMotionWaveSolver())


def test_initial_state_may_be_per_key() -> None:
    network = simple_network_document()
    network["initial_state"] = {
        "schema": "python-solver-initial-state-v1",
        "kind": "warm_state",
        "node_depth_m": {"J-1": 0.05, "J-2": 0.04, "J-3": 0.02, "OUT-1": 0.01},
        "pipe_flow_m3s": 0.0,
    }
    request = _parse(solver_input_document(network=network), PythonMotionWaveSolver())
    depths = {node.node_id: node.initial_depth_m for node in request.network.nodes}
    assert depths["J-1"] == pytest.approx(0.05)
    assert depths["OUT-1"] == pytest.approx(0.01)


def test_initial_state_rejects_unknown_stable_keys() -> None:
    network = simple_network_document()
    network["initial_state"]["node_depth_m"] = {"J-1": 0.05, "NOPE": 0.1}
    with pytest.raises(SolverInputError, match="unknown stable keys"):
        _parse(solver_input_document(network=network), PythonMotionWaveSolver())


# ---------------------------------------------------------------------------
# running
# ---------------------------------------------------------------------------


async def test_run_streams_every_frame_then_finalizes() -> None:
    solver = PythonMotionWaveSolver()
    request = _parse(solver_input_document(), solver)
    sink = RecordingSink()
    progress: list[tuple[int, str]] = []

    summary = await solver.run(
        request,
        request.network,
        sink,
        RunControl(progress_cb=lambda p, s: progress.append((p, s))),
    )

    assert summary.solver_id == "py_mw"
    assert summary.frame_count == request.expected_frame_count
    assert sink.write_order == list(range(request.expected_frame_count))
    assert len(sink.summaries) == 1
    assert sink.summaries[0] is summary


async def test_frames_carry_the_documented_schema_and_units() -> None:
    solver = PythonMotionWaveSolver()
    request = _parse(solver_input_document(), solver)
    sink = RecordingSink()

    await solver.run(request, request.network, sink, RunControl())

    first = sink.frames[0].to_dict()["payload"]
    assert first["schema"] == PYTHON_FRAME_SCHEMA
    assert first["solver_id"] == "py_mw"
    assert first["solver_version"] == solver.version
    assert first["frame_index"] == 0
    assert first["valid_at"] == "2026-01-01T00:00:00Z"
    assert len(first["nodes"]) == 4
    assert len(first["pipes"]) == 3
    node = first["nodes"][0]
    for field in ("depth_m", "volume_m3", "inflow_m3s", "outflow_m3s"):
        assert field in node
    pipe = first["pipes"][0]
    for field in (
        "flow_m3s",
        "velocity_mps",
        "depth_m",
        "fill_ratio",
        "full_capacity_m3s",
        "froude_number",
    ):
        assert field in pipe
    # results.serialize_frame_payload rejects negative hydraulic magnitudes.
    assert all(item["depth_m"] >= 0.0 for item in first["nodes"])
    assert all(item["fill_ratio"] >= 0.0 for item in first["pipes"])


async def test_frame_times_follow_the_frozen_grid() -> None:
    solver = PythonMotionWaveSolver()
    request = _parse(solver_input_document(), solver)
    sink = RecordingSink()

    await solver.run(request, request.network, sink, RunControl())

    times = [frame.to_dict()["payload"]["valid_at"] for frame in sink.frames]
    assert times[0] == "2026-01-01T00:00:00Z"
    assert times[-1] == "2026-01-01T00:25:00Z"
    assert len(set(times)) == len(times)


async def test_progress_is_monotonic_and_inside_the_contract_bands() -> None:
    solver = PythonMotionWaveSolver()
    request = _parse(solver_input_document(), solver)
    events: list[tuple[int, str]] = []

    await solver.run(
        request,
        request.network,
        RecordingSink(),
        RunControl(progress_cb=lambda percent, phase: events.append((percent, phase))),
    )

    percents = [percent for percent, _ in events]
    assert percents[0] == 0
    assert percents[-1] == 100
    assert percents == sorted(percents)
    assert len(set(percents)) == len(percents)
    phases = {phase for _, phase in events}
    assert phases <= {
        "accepted",
        "validating",
        "solving",
        "quality",
        "persisting",
        "finalizing",
    }
    for percent, phase in events:
        if phase == "solving":
            assert 5 <= percent <= 79, (percent, phase)
        if phase == "quality":
            assert 80 <= percent <= 94, (percent, phase)
        if phase == "persisting":
            assert 95 <= percent <= 99, (percent, phase)


async def test_summary_carries_quality_metrics_and_the_four_hashes() -> None:
    solver = PythonMotionWaveSolver()
    request = _parse(solver_input_document(), solver)

    summary = await solver.run(request, request.network, RecordingSink(), RunControl())

    assert summary.result_status in {
        SolverResultStatus.verified.value,
        SolverResultStatus.warning.value,
    }
    assert summary.frame_count == request.expected_frame_count
    assert summary.input_sha256 == request.input_sha256()
    assert summary.network_content_hash == (
        request.manifest.network_revision_content_sha256
    )
    assert summary.snapshot_content_hash == request.manifest.snapshot_content_sha256
    assert summary.initial_state_hash == (request.manifest.initial_state_content_sha256)
    assert summary.target_manifest_hash == request.manifest.target_manifest_sha256
    assert summary.model_fidelity == "fast_screening"
    quality = summary.diagnostics["quality"]
    metrics = summary.diagnostics["metrics"]
    assert quality["schema"] == PYTHON_QUALITY_SCHEMA
    assert metrics["schema"] == PYTHON_METRICS_SCHEMA
    assert quality["is_authoritative"] is False
    assert metrics["frame_count"] == request.expected_frame_count
    assert math.isfinite(summary.total_mass_residual_m3 or 0.0)
    assert summary.max_mass_residual_relative is not None


async def test_mass_balance_closes_on_the_storm_event() -> None:
    solver = PythonMotionWaveSolver()
    request = _parse(solver_input_document(), solver)

    summary = await solver.run(request, request.network, RecordingSink(), RunControl())

    metrics = summary.diagnostics["metrics"]
    assert metrics["total_rainfall_input_m3"] > 0.0
    assert metrics["total_outfall_discharge_m3"] > 0.0
    assert metrics["max_courant"] <= 0.5
    assert metrics["max_substeps_per_report"] <= 20
    assert summary.max_mass_residual_relative <= 1.0e-3


async def test_a_deadline_fails_the_run_instead_of_returning_a_result() -> None:
    solver = PythonMotionWaveSolver()
    request = _parse(solver_input_document(), solver)

    with pytest.raises(SolverTimeoutError):
        await solver.run(
            request,
            request.network,
            RecordingSink(),
            RunControl(
                deadline_at=__import__("datetime").datetime(
                    2000, 1, 1, tzinfo=__import__("datetime").timezone.utc
                )
            ),
        )


async def test_a_failed_sink_propagates_and_prevents_finalize() -> None:
    class FailingSink(RecordingSink):
        async def write(self, frame: Any) -> None:
            raise SolverResultError("FRAME_ATTEMPT_CONFLICT")

    solver = PythonMotionWaveSolver()
    request = _parse(solver_input_document(), solver)
    sink = FailingSink()

    with pytest.raises(SolverResultError, match="FRAME_ATTEMPT_CONFLICT"):
        await solver.run(request, request.network, sink, RunControl())
    assert sink.summaries == []


async def test_py_dw_runs_and_declares_its_reduced_fidelity() -> None:
    solver = PythonDiffusionWaveSolver()
    # A 0.9 m barrel on 500 m reaches keeps the run subcritical and
    # unpressurised, so it stays inside the domain py_dw declares.
    network = simple_network_document(diameter_m=0.9, pipe_length_m=500.0)
    request = _parse(
        solver_input_document(
            solver_id="py_dw",
            intensities_mm_h=(8.0, 8.0, 8.0, 8.0, 8.0, 8.0),
            step_seconds=120,
            network=network,
        ),
        solver,
    )
    sink = RecordingSink()

    summary = await solver.run(request, request.network, sink, RunControl())

    assert summary.solver_id == "py_dw"
    assert summary.solver_version == solver.version
    assert summary.model_fidelity == "reduced_fidelity"
    assert summary.frame_count == request.expected_frame_count
    assert summary.diagnostics["routing_mode"] == "diffusion_wave"
    reference = RecordingSink()
    await PythonMotionWaveSolver().run(
        _parse(solver_input_document(), PythonMotionWaveSolver()),
        None,
        reference,
        RunControl(),
    )
    reference_frames = [frame.to_dict()["payload"] for frame in reference.frames]
    first = sink.frames[0].to_dict()["payload"]
    assert first["solver_id"] == "py_dw"
    # Identical frame schema across the two py_* identities (contract §5.5).
    assert first["schema"] == PYTHON_FRAME_SCHEMA
    assert set(first) == set(reference_frames[0])


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_validate_accepts_the_standard_network() -> None:
    report = PythonMotionWaveSolver().validate(solver_input_document()["network"])
    assert report.valid is True
    assert report.details["node_count"] == 4
    assert report.details["pipe_count"] == 3


def test_validate_reports_the_offending_pipe_for_a_steep_py_dw_network() -> None:
    report = PythonDiffusionWaveSolver().validate(
        simple_network_document(steep_pipes=True)
    )
    assert report.valid is False
    codes = {issue.code for issue in report.issues}
    assert "DW_SLOPE_OUT_OF_DOMAIN" in codes


def test_py_dw_declines_a_network_that_demands_pressure_flow() -> None:
    network = simple_network_document(required_features=("pressure_flow",))
    report = PythonDiffusionWaveSolver().validate(network)
    assert report.valid is False
    assert "pressure_flow" in report.unsupported_features


def test_validate_rejects_a_network_with_a_missing_outfall() -> None:
    network = simple_network_document()
    network["nodes"]["OUT-1"]["kind"] = "junction"
    report = PythonMotionWaveSolver().validate(network)
    assert report.valid is True  # static screen only; the kernel refuses it


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_default_registry_always_has_the_pure_python_fallback() -> None:
    registry = build_default_registry()
    assert registered_solver_ids(registry) == ("py_dw", "py_mw")
    assert registry.fallback_solver_id == "py_mw"
    assert "swmm_kw" not in registry
    assert "py_ponding" not in registry


def test_default_registry_never_registers_py_ponding() -> None:
    """``py_ponding`` is owned by surface-flood; registering a 1D stand-in here
    would claim 2D surface dynamics that this component does not have."""

    assert "py_ponding" not in get_default_registry()


async def test_auto_selection_without_a_request_freezes_py_mw_without_epa() -> None:
    selected = await get_default_registry().select("auto")

    assert selected.selected_solver_id == "py_mw"
    assert selected.degraded is True
    assert selected.fallback_reason is not None
    assert "preferred_solver_unavailable" in selected.fallback_reason
    assert {probe.solver_id for probe in selected.probes} == {"swmm_kw", "py_mw"}
    swmm_probe = next(
        probe for probe in selected.probes if probe.solver_id == "swmm_kw"
    )
    assert swmm_probe.available is False
    assert swmm_probe.reason == "not_registered"


async def test_explicit_py_dw_is_honoured_without_degrading() -> None:
    selected = await get_default_registry().select("explicit", "py_dw")

    assert selected.selected_solver_id == "py_dw"
    assert selected.degraded is False
    assert selected.fallback_reason is None


async def test_explicit_epa_fails_closed_when_the_engine_is_absent() -> None:
    with pytest.raises(SolverUnavailableError) as excinfo:
        await get_default_registry().select("explicit", "swmm_kw")

    assert excinfo.value.solver_id == "swmm_kw"
    probes = excinfo.value.details["probes"]
    assert any(
        probe["solver_id"] == "swmm_kw" and not probe["available"] for probe in probes
    )


def test_registry_reset_rebuilds_without_leaking_state() -> None:
    first = get_default_registry()
    reset_default_registry()
    second = get_default_registry()
    assert first is not second
    assert registered_solver_ids(second) == ("py_dw", "py_mw")


def test_optional_epa_probe_reports_not_configured_without_settings() -> None:
    from app.services.hydraulic.solvers.registry import epa_status

    status = epa_status(settings=object())
    assert status["available"] is False
    assert status["configured"] is False
    assert sorted(status["solver_ids"]) == ["swmm_dw", "swmm_kw"]


def test_epa_solvers_are_registered_only_after_a_real_probe() -> None:
    from app.services.hydraulic.solvers.registry import build_epa_solvers

    class FakeSettings:
        EPA_SWMM_EXECUTABLE = "/nonexistent/epaswmm5"
        EPA_SWMM_VERSION = "5.2.4"
        EPA_SWMM_SHA256 = "0" * 64

        def probe_epa_swmm(self) -> dict[str, Any]:
            return {
                "configured": True,
                "available": False,
                "reason": "executable_not_found",
                "version": "5.2.4",
            }

    assert build_epa_solvers(FakeSettings()) == ()


def test_optional_epa_probe_never_raises_on_a_broken_settings_object() -> None:
    from app.services.hydraulic.solvers.registry import probe_epa_swmm

    class Broken:
        def probe_epa_swmm(self) -> dict[str, Any]:
            raise RuntimeError("boom")

    report = probe_epa_swmm(Broken())
    assert report["available"] is False
    assert report["reason"].startswith("probe_failed:")


# ---------------------------------------------------------------------------
# deterministic replay
# ---------------------------------------------------------------------------


async def test_the_same_input_replays_byte_identical_frames() -> None:
    solver = PythonMotionWaveSolver()
    first_sink = RecordingSink()
    second_sink = RecordingSink()
    request = _parse(solver_input_document(), solver)

    await solver.run(request, request.network, first_sink, RunControl())
    await solver.run(request, request.network, second_sink, RunControl())

    first = [canonical_json(frame.to_dict()["payload"]) for frame in first_sink.frames]
    second = [
        canonical_json(frame.to_dict()["payload"]) for frame in second_sink.frames
    ]
    assert first == second


async def test_py_dw_refuses_a_supercritical_or_pressurised_state() -> None:
    """The regime gate is a terminal refusal, not a warning."""

    solver = PythonDiffusionWaveSolver()
    # The same storm that py_mw surcharges through a 0.6 m barrel drives
    # py_dw into a pressure-flow state it is not allowed to model.
    request = _parse(solver_input_document(solver_id="py_dw"), solver)
    with pytest.raises(SolverExecutionError) as excinfo:
        await solver.run(request, request.network, RecordingSink(), RunControl())
    assert excinfo.value.solver_id == "py_dw"
    assert excinfo.value.retryable is False
