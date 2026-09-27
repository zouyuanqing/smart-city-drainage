from __future__ import annotations

import asyncio
import hashlib
import io
import struct
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.services.hydraulic.solvers.base import (
    RunControl,
    SimulationFrame,
    SolverCapabilities,
    SolverExecutionError,
    SolverInputManifest,
    SolverProbe,
    SolverRegistry,
    SolverSelection,
    SolverTimeoutError,
    SolverUnavailableError,
    SolveSummary,
    compute_solver_input_hash,
)
from app.services.hydraulic.solvers.swmm_adapter import (
    Swmm5OutputParser,
    SwmmAdapter,
    SwmmParseContext,
    SwmmProcessRequest,
    SwmmSolveRequest,
)

UTC = timezone.utc
ENGINE_BYTES = b"fake epa swmm engine"
ENGINE_SHA256 = hashlib.sha256(ENGINE_BYTES).hexdigest()


class FakeProcess:
    def __init__(self, return_code: int = 0) -> None:
        self.pid = 4242
        self.return_code: int | None = None
        self._return_code = return_code
        self.killed = False

    async def wait(self) -> int:
        self.return_code = self._return_code
        return self._return_code

    async def kill_process_tree(self) -> None:
        self.killed = True
        self.return_code = -1


class HangingProcess(FakeProcess):
    def __init__(self) -> None:
        super().__init__()
        self._stopped = asyncio.Event()

    async def wait(self) -> int:
        await self._stopped.wait()
        return -1

    async def kill_process_tree(self) -> None:
        await super().kill_process_tree()
        self._stopped.set()


class FakeRunner:
    def __init__(self, process: FakeProcess | None = None) -> None:
        self.process = process or FakeProcess()
        self.requests: list[SwmmProcessRequest] = []

    async def start(self, request: SwmmProcessRequest) -> FakeProcess:
        self.requests.append(request)
        request.argv[0]
        Path(request.cwd, "simulation.rpt").write_bytes(b"report")
        Path(request.cwd, "simulation.out").write_bytes(b"binary output")
        return self.process


class ExplodingRunner:
    async def start(self, request: SwmmProcessRequest) -> FakeProcess:
        del request
        raise RuntimeError("fake engine exploded")


class FakeParser:
    parser_version = "fake-swmm-parser-v1"
    engine_version = "5.2.4"

    def parse(self, context: SwmmParseContext) -> AsyncIterator[SimulationFrame]:
        async def frames() -> AsyncIterator[SimulationFrame]:
            for index in range(context.expected_frame_count):
                yield SimulationFrame(
                    attempt_id=context.attempt_id,
                    simulation_id=context.simulation_id,
                    frame_index=index,
                    valid_at=context.first_frame_at
                    + timedelta(seconds=index * context.frame_interval_seconds),
                    payload={"depth_m": [0.0, 0.1]},
                    solver_id=context.solver_id,
                    solver_version=context.solver_version,
                    target_manifest_sha256=context.target_manifest_sha256,
                )

        return frames()


class FakePythonSolver:
    solver_id = "py_mw"
    version = "py-mw-v1"

    def __init__(self) -> None:
        self.run_count = 0

    async def probe(self) -> SolverProbe:
        return SolverProbe(
            solver_id=self.solver_id,
            version=self.version,
            available=True,
            reason="verified",
            capabilities=SolverCapabilities(
                solver_id=self.solver_id,
                version=self.version,
                model_fidelity="fast_screening",
                authoritative=False,
            ),
        )

    def capabilities(self) -> SolverCapabilities:
        return SolverCapabilities(
            solver_id=self.solver_id,
            version=self.version,
            model_fidelity="fast_screening",
            authoritative=False,
        )

    async def run(self, *args: Any, **kwargs: Any) -> SolveSummary:
        del args, kwargs
        self.run_count += 1
        return SolveSummary(solver_id=self.solver_id, solver_version=self.version)


class RecordingSink:
    def __init__(self) -> None:
        self.frames: list[SimulationFrame] = []
        self.summaries: list[SolveSummary] = []

    async def write(self, frame: SimulationFrame) -> None:
        self.frames.append(frame)

    async def finalize(self, summary: SolveSummary) -> None:
        self.summaries.append(summary)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _input_manifest(*, solver_id: str = "swmm_kw") -> SolverInputManifest:
    return SolverInputManifest(
        schema_version="network-snapshot-v1",
        network_revision_content_sha256=_digest("network-revision"),
        snapshot_content_sha256=_digest("snapshot"),
        initial_state_content_sha256=_digest("initial-state"),
        target_manifest_sha256=_digest("target-manifest"),
        rainfall_content_sha256=_digest("rainfall"),
        solver_id=solver_id,
        solver_version="5.2.4",
        solver_config_schema_id="swmm-config-v1",
        solver_config_schema_version="1",
        parameter_set_id="00000000-0000-0000-0000-0000000000a1",
        parameter_set_content_sha256=_digest("parameter-set"),
        parameter_schema_id="hydraulic-parameter-schema-v1",
        parameter_schema_version="1",
        parameter_schema_sha256=_digest("parameter-schema"),
        solver_config={"routing": "kinematic_wave"},
        transform_id="epsg4326",
        solver_crs="EPSG:4326",
        start_at=datetime(2026, 9, 25, tzinfo=UTC),
        duration_seconds=300,
        frame_interval_seconds=300,
        expected_frame_count=2,
        ponding_enabled=False,
    )


def _request() -> SwmmSolveRequest:
    manifest = _input_manifest()
    return SwmmSolveRequest(
        simulation_id="simulation-1",
        attempt_id="attempt-1",
        inp_bytes=b"[TITLE]\nfixture\n[END]\n",
        input_manifest=manifest,
    )


def _write_minimal_swmm_output(path: Path) -> None:
    buffer = io.BytesIO()
    buffer.write(
        struct.pack(
            "<7i",
            516_114_522,
            50_204,
            3,
            1,
            1,
            1,
            0,
        )
    )
    names_position = buffer.tell()
    for name in ("C1", "N1", "P1"):
        encoded = name.encode("utf-8")
        buffer.write(struct.pack("<i", len(encoded)))
        buffer.write(encoded)
    properties_position = buffer.tell()
    buffer.write(struct.pack("<2i", 1, 1))
    buffer.write(struct.pack("<f", 100.0))
    buffer.write(struct.pack("<5i", 4, 3, 0, 2, 3))
    for value in (0.0, 1.0, 2.0, 0.0):
        buffer.write(struct.pack("<f", value))
    buffer.write(struct.pack("<7i", 6, 5, 0, 4, 4, 3, 5))
    for value in (0.0, 0.0, 0.0, 1.0, 20.0, 0.01):
        buffer.write(struct.pack("<f", value))
    groups = (range(6), range(6), range(5), range(14))
    for codes in groups:
        codes = tuple(codes)
        buffer.write(struct.pack("<i", len(codes)))
        for code in codes:
            buffer.write(struct.pack("<i", code))
    start = datetime(2026, 9, 25, tzinfo=UTC)
    excel_start = (
        start - datetime(1899, 12, 30, tzinfo=UTC)
    ).total_seconds() / 86_400.0
    buffer.write(struct.pack("<di", excel_start, 300))
    results_position = buffer.tell()
    values = (
        (10.0, 0.0, 0.0, 0.1, 0.0, 0.0)
        + (0.2, 1.0, 0.3, 0.1, 0.2, 0.0)
        + (0.1, 0.2, 0.5, 0.1, 0.2)
        + (0.0,) * 14
    )
    for index in range(2):
        buffer.write(struct.pack("<d", excel_start + index * 300.0 / 86_400.0))
        buffer.write(struct.pack("<31f", *values))
    buffer.write(
        struct.pack(
            "<6i",
            names_position,
            properties_position,
            results_position,
            2,
            0,
            516_114_522,
        )
    )
    path.write_bytes(buffer.getvalue())


def _adapter(
    tmp_path: Path,
    *,
    runner: FakeRunner | None = None,
) -> tuple[SwmmAdapter, FakeRunner]:
    executable = tmp_path / "swmm5.exe"
    executable.write_bytes(ENGINE_BYTES)
    resolved_runner = runner or FakeRunner()
    adapter = SwmmAdapter(
        solver_id="swmm_kw",
        executable=executable,
        expected_version="5.2.4",
        expected_sha256=ENGINE_SHA256,
        process_runner=resolved_runner,
        output_parser=FakeParser(),
    )
    return adapter, resolved_runner


@pytest.mark.asyncio
async def test_fake_swmm_invocation_streams_frames_and_finalizes_summary(
    tmp_path: Path,
) -> None:
    adapter, runner = _adapter(tmp_path)
    sink = RecordingSink()
    progress: list[tuple[int, str]] = []
    request = _request()

    def record_progress(percent: int, phase: str) -> None:
        progress.append((percent, phase))

    summary = await adapter.run(
        request,
        object(),
        sink,
        RunControl(workspace_root=tmp_path, progress_cb=record_progress),
    )

    assert len(runner.requests) == 1
    invocation = runner.requests[0]
    assert invocation.shell is False
    assert invocation.cwd == tmp_path.resolve()
    assert invocation.argv == (
        str((tmp_path / "swmm5.exe").resolve()),
        str((tmp_path / "simulation.inp").resolve()),
        str((tmp_path / "simulation.rpt").resolve()),
        str((tmp_path / "simulation.out").resolve()),
    )
    assert (tmp_path / "simulation.inp").read_bytes() == request.inp_bytes
    assert [frame.frame_index for frame in sink.frames] == [0, 1]
    assert len(sink.summaries) == 1
    assert sink.summaries[0] is summary
    assert summary.input_sha256 == request.input_manifest.input_sha256
    assert summary.frame_count == 2
    assert summary.result_status == "verified"
    assert progress == [
        (1, "validating"),
        (5, "solving"),
        (80, "quality"),
        (94, "quality"),
        (95, "persisting"),
        (100, "finalizing"),
    ]


def test_solver_input_hash_binds_all_independent_materials() -> None:
    manifest = _input_manifest()
    replay = _input_manifest()

    assert replay.input_sha256 == manifest.input_sha256
    assert manifest.to_dict()["input_sha256"] == manifest.input_sha256
    assert (
        compute_solver_input_hash(request={"input_manifest": manifest})
        == manifest.input_sha256
    )

    changed_components = (
        replace(manifest, network_revision_content_sha256=_digest("network-2")),
        replace(manifest, snapshot_content_sha256=_digest("snapshot-2")),
        replace(manifest, initial_state_content_sha256=_digest("initial-state-2")),
        replace(manifest, target_manifest_sha256=_digest("target-manifest-2")),
        replace(manifest, rainfall_content_sha256=_digest("rainfall-2")),
        replace(
            manifest,
            parameter_set_content_sha256=_digest("parameter-set-2"),
        ),
        replace(manifest, parameter_schema_sha256=_digest("parameter-schema-2")),
        replace(manifest, solver_config={"routing": "dynamic_wave"}),
        replace(manifest, frame_interval_seconds=150, expected_frame_count=3),
    )
    assert all(
        component.input_sha256 != manifest.input_sha256
        for component in changed_components
    )


def test_solver_input_manifest_deeply_freezes_solver_config() -> None:
    nested = {"routing": {"mode": "kinematic_wave"}}
    manifest = replace(_input_manifest(), solver_config=nested)
    original_hash = manifest.input_sha256

    nested["routing"]["mode"] = "dynamic_wave"
    with pytest.raises(TypeError):
        manifest.solver_config["routing"]["mode"] = "dynamic_wave"
    with pytest.raises(TypeError):
        manifest.solver_config["routing"] = {"mode": "dynamic_wave"}

    assert manifest.solver_config["routing"]["mode"] == "kinematic_wave"
    assert manifest.input_sha256 == original_hash


@pytest.mark.asyncio
async def test_registry_never_freezes_a_solver_without_a_probe_method() -> None:
    class UnprobedSolver:
        solver_id = "py_mw"
        version = "unprobed-v1"

    registry = SolverRegistry((UnprobedSolver(),))

    probe = await registry.probe("py_mw")
    assert probe.available is False
    assert probe.reason == "probe_required"
    with pytest.raises(SolverUnavailableError, match="probe_required"):
        await registry.select(SolverSelection.explicit, "py_mw")


@pytest.mark.asyncio
async def test_fake_swmm_timeout_kills_process_without_publishing_frames(
    tmp_path: Path,
) -> None:
    process = HangingProcess()
    runner = FakeRunner(process)
    adapter, _ = _adapter(tmp_path, runner=runner)
    sink = RecordingSink()

    with pytest.raises(SolverTimeoutError) as captured:
        await adapter.run(
            _request(),
            object(),
            sink,
            RunControl(workspace_root=tmp_path, timeout_seconds=0.1),
        )

    assert captured.value.code == "SIMULATION_TIMEOUT"
    assert process.killed is True
    assert sink.frames == []
    assert sink.summaries == []


@pytest.mark.asyncio
async def test_swmm_timeout_reports_process_tree_cleanup_failure(
    tmp_path: Path,
) -> None:
    class FailingKillProcess(HangingProcess):
        async def kill_process_tree(self) -> None:
            raise RuntimeError("job object close failed")

    process = FailingKillProcess()
    adapter, _ = _adapter(tmp_path, runner=FakeRunner(process))

    with pytest.raises(SolverTimeoutError) as captured:
        await adapter.run(
            _request(),
            object(),
            RecordingSink(),
            RunControl(workspace_root=tmp_path, timeout_seconds=0.1),
        )

    assert captured.value.details["cleanup_error"] == "RuntimeError"


@pytest.mark.asyncio
async def test_fake_swmm_exception_is_stable_and_publishes_nothing(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "swmm5.exe"
    executable.write_bytes(ENGINE_BYTES)
    adapter = SwmmAdapter(
        solver_id="swmm_kw",
        executable=executable,
        expected_version="5.2.4",
        expected_sha256=ENGINE_SHA256,
        output_parser=FakeParser(),
        process_runner=ExplodingRunner(),
    )
    sink = RecordingSink()

    with pytest.raises(SolverExecutionError) as captured:
        await adapter.run(
            _request(),
            object(),
            sink,
            RunControl(workspace_root=tmp_path),
        )

    assert captured.value.code == "SOLVER_EXECUTION_FAILED"
    assert captured.value.retryable is False
    assert captured.value.details["error_type"] == "RuntimeError"
    assert sink.frames == []
    assert sink.summaries == []


@pytest.mark.asyncio
async def test_runtime_swmm_failure_never_switches_to_python_fallback(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "swmm5.exe"
    executable.write_bytes(ENGINE_BYTES)
    swmm = SwmmAdapter(
        solver_id="swmm_kw",
        executable=executable,
        expected_version="5.2.4",
        expected_sha256=ENGINE_SHA256,
        output_parser=FakeParser(),
        process_runner=ExplodingRunner(),
    )
    python = FakePythonSolver()
    registry = SolverRegistry((swmm, python))
    selected = await registry.select(SolverSelection.auto)

    assert selected.selected_solver_id == "swmm_kw"
    with pytest.raises(SolverExecutionError):
        await selected.solver.run(
            _request(),
            object(),
            RecordingSink(),
            RunControl(workspace_root=tmp_path),
        )
    assert python.run_count == 0


@pytest.mark.asyncio
async def test_auto_selection_reports_explicit_swmm_unavailable_and_freezes_py_mw(
    tmp_path: Path,
) -> None:
    del tmp_path
    swmm = SwmmAdapter(
        solver_id="swmm_kw",
        executable=None,
        expected_version=None,
        expected_sha256=None,
        output_parser=FakeParser(),
    )
    python = FakePythonSolver()
    registry = SolverRegistry((swmm, python))

    selected = await registry.select(SolverSelection.auto)

    assert selected.selected_solver_id == "py_mw"
    assert selected.degraded is True
    assert selected.fallback_reason
    report = await registry.capability_report(SolverSelection.auto)
    assert report.available is True
    assert report.degraded is True
    assert report.selected_solver_id == "py_mw"
    assert {probe.solver_id for probe in report.probes} == {"swmm_kw", "py_mw"}
    assert (
        next(probe for probe in report.probes if probe.solver_id == "swmm_kw").reason
        == "not_configured"
    )


@pytest.mark.asyncio
async def test_explicit_unavailable_swmm_is_rejected_without_fallback() -> None:
    swmm = SwmmAdapter(
        solver_id="swmm_kw",
        executable=None,
        expected_version=None,
        expected_sha256=None,
        output_parser=FakeParser(),
    )
    registry = SolverRegistry((swmm, FakePythonSolver()))

    with pytest.raises(SolverUnavailableError):
        await registry.select(SolverSelection.explicit, "swmm_kw")


@pytest.mark.asyncio
async def test_windows_safe_missing_engine_probe_returns_without_blocking() -> None:
    swmm = SwmmAdapter(
        solver_id="swmm_kw",
        executable=None,
        expected_version=None,
        expected_sha256=None,
        output_parser=FakeParser(),
    )

    probe = await asyncio.wait_for(swmm.probe(), timeout=0.2)

    assert probe.available is False
    assert probe.reason == "not_configured"
    assert probe.fallback_solver_id is None


@pytest.mark.asyncio
async def test_swmm_probe_requires_parser_bound_to_the_exact_engine_version(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "swmm5.exe"
    executable.write_bytes(ENGINE_BYTES)

    class UnboundParser:
        parser_version = "fake-swmm-parser-v1"

    swmm = SwmmAdapter(
        solver_id="swmm_kw",
        executable=executable,
        expected_version="5.2.4",
        expected_sha256=ENGINE_SHA256,
        output_parser=UnboundParser(),
    )

    probe = await swmm.probe()
    assert probe.available is False
    assert probe.reason == "parser_engine_version_required"


@pytest.mark.asyncio
async def test_adapter_report_never_claims_an_unprobed_python_fallback() -> None:
    swmm = SwmmAdapter(
        solver_id="swmm_kw",
        executable=None,
        expected_version=None,
        expected_sha256=None,
        output_parser=FakeParser(),
    )

    report = await swmm.capability_report()
    assert report.available is False
    assert report.fallback_solver_id is None
    assert report.fallback_reason is None


@pytest.mark.asyncio
async def test_pre_cancelled_swmm_run_writes_no_input_and_starts_no_process(
    tmp_path: Path,
) -> None:
    adapter, runner = _adapter(tmp_path)
    cancelled = asyncio.Event()
    cancelled.set()

    with pytest.raises(Exception, match="cancelled"):
        await adapter.run(
            _request(),
            object(),
            RecordingSink(),
            RunControl(workspace_root=tmp_path, cancel_event=cancelled),
        )

    assert runner.requests == []
    assert not (tmp_path / "simulation.inp").exists()


@pytest.mark.asyncio
async def test_swmm_deadline_covers_parsing_not_only_process_wait(
    tmp_path: Path,
) -> None:
    class SlowParser(FakeParser):
        async def frames(  # type: ignore[override]
            self,
        ) -> AsyncIterator[SimulationFrame]:
            del self
            yield SimulationFrame(
                attempt_id="attempt-1",
                simulation_id="simulation-1",
                frame_index=0,
                valid_at=datetime(2026, 9, 25, tzinfo=UTC),
                payload={"depth_m": [0.0]},
                solver_id="swmm_kw",
                solver_version="5.2.4",
                target_manifest_sha256=_digest("target-manifest"),
            )
            await asyncio.sleep(0.2)

        def parse(self, context: SwmmParseContext) -> AsyncIterator[SimulationFrame]:
            return self.frames()

    executable = tmp_path / "swmm5.exe"
    executable.write_bytes(ENGINE_BYTES)
    adapter = SwmmAdapter(
        solver_id="swmm_kw",
        executable=executable,
        expected_version="5.2.4",
        expected_sha256=ENGINE_SHA256,
        output_parser=SlowParser(),
        process_runner=FakeRunner(),
    )
    sink = RecordingSink()

    with pytest.raises(SolverTimeoutError):
        await adapter.run(
            _request(),
            object(),
            sink,
            RunControl(workspace_root=tmp_path, timeout_seconds=0.05),
        )

    assert len(sink.frames) == 1
    assert sink.summaries == []


@pytest.mark.asyncio
async def test_probe_reports_binary_hash_mismatch_before_auto_fallback(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "swmm5.exe"
    executable.write_bytes(ENGINE_BYTES)
    swmm = SwmmAdapter(
        solver_id="swmm_kw",
        executable=executable,
        expected_version="5.2.4",
        expected_sha256="0" * 64,
        output_parser=FakeParser(),
    )
    registry = SolverRegistry((swmm, FakePythonSolver()))

    report = await registry.capability_report(SolverSelection.auto)

    assert report.selected_solver_id == "py_mw"
    assert report.degraded is True
    swmm_probe = next(probe for probe in report.probes if probe.solver_id == "swmm_kw")
    assert swmm_probe.reason == "sha256_mismatch"
    assert report.fallback_reason and "sha256_mismatch" in report.fallback_reason


@pytest.mark.asyncio
async def test_production_swmm5_parser_streams_binary_frames(tmp_path: Path) -> None:
    request = _request()
    manifest = request.input_manifest
    report_path = tmp_path / "simulation.rpt"
    output_path = tmp_path / "simulation.out"
    report_path.write_text("  Analysis begun on: 2026-09-25\n", encoding="utf-8")
    _write_minimal_swmm_output(output_path)
    context = SwmmParseContext(
        simulation_id=request.simulation_id,
        attempt_id=request.attempt_id,
        input_path=tmp_path / "simulation.inp",
        report_path=report_path,
        output_path=output_path,
        solver_id=manifest.solver_id,
        solver_version=manifest.solver_version,
        engine_binary_sha256=ENGINE_SHA256,
        input_sha256=manifest.input_sha256,
        network_content_hash=manifest.network_revision_content_sha256,
        snapshot_content_hash=manifest.snapshot_content_sha256,
        initial_state_hash=manifest.initial_state_content_sha256,
        target_manifest_sha256=manifest.target_manifest_sha256,
        rainfall_content_hash=manifest.rainfall_content_sha256,
        solver_config_hash=manifest.solver_config_hash,
        first_frame_at=manifest.start_at,
        frame_interval_seconds=manifest.frame_interval_seconds,
        expected_frame_count=manifest.expected_frame_count,
    )

    frames = [frame async for frame in Swmm5OutputParser("5.2.4").parse(context)]

    assert [frame.frame_index for frame in frames] == [0, 1]
    assert frames[0].payload["nodes"]["N1"]["depth_m"] == pytest.approx(0.2)
    assert frames[0].payload["pipes"]["P1"]["flow_m3s"] == pytest.approx(0.1)
    assert frames[1].valid_at == manifest.start_at + timedelta(minutes=5)


def test_simulation_frame_rejects_parser_supplied_payload_hash_mismatch() -> None:
    with pytest.raises(ValueError, match="payload_sha256"):
        SimulationFrame(
            attempt_id="attempt-1",
            simulation_id="simulation-1",
            frame_index=0,
            valid_at=datetime(2026, 9, 25, tzinfo=UTC),
            payload={"depth_m": [0.1]},
            solver_id="swmm_kw",
            solver_version="5.2.4",
            payload_sha256="0" * 64,
        )
