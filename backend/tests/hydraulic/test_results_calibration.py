"""Behavioral tests for frame persistence and hydraulic calibration."""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.models.hydraulic_models import (
    SimulationFrame,
    SimulationFrameRegistration,
    SimulationResult,
)
from app.services.hydraulic.calibration import (
    CalibrationExecutionError,
    CalibrationObjective,
    CandidateEvaluation,
    InfluxWaterLevelObservationSource,
    ObservationPoint,
    ParameterSpec,
    SimulatedWaterLevel,
    compute_metrics,
    run_calibration,
    write_calibration_report,
)
from app.services.hydraulic.results import (
    MAX_FRAME_INTERVAL_SECONDS,
    ArtifactConflictError,
    FrameInput,
    FrameWriteContext,
    ResultRepository,
    ResultValidationError,
    prepare_frame,
    retention_cutoff,
    retention_state,
    serialize_frame_payload,
    write_controlled_json_artifact,
)

UTC = timezone.utc
START = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)


class _Rows:
    def __init__(self, rows: list[Any], rowcount: int = -1) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def scalars(self) -> "_Rows":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None


class _RecordingSession:
    def __init__(self, attempt: Any, simulation: Any | None = None) -> None:
        self.attempt = attempt
        self.simulation = simulation
        self.registrations: list[Any] = []
        self.frames: list[Any] = []
        self.results: list[Any] = []
        self.added: list[Any] = []
        self.statements: list[str] = []
        self.flush_count = 0

    async def get(self, model: type[Any], primary_key: Any) -> Any:
        assert model.__name__ == "SimulationAttempt"
        assert primary_key == self.attempt.id
        return self.attempt

    async def execute(self, statement: Any) -> _Rows:
        rendered = str(statement)
        self.statements.append(rendered)
        if "DELETE FROM" in rendered and "simulation_frames" in rendered:
            return _Rows([], rowcount=3)
        if "simulation_attempts" in rendered:
            return _Rows([self.attempt])
        if "simulation_frame_registrations" in rendered:
            return _Rows(self.registrations)
        if "simulation_frames" in rendered:
            return _Rows(self.frames)
        if "simulation_results" in rendered:
            return _Rows(self.results)
        if "simulations" in rendered and self.simulation is not None:
            return _Rows([self.simulation])
        return _Rows([])

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1


def _attempt() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000103"),
        simulation_id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        attempt_no=1,
        network_revision_id=uuid.UUID("00000000-0000-0000-0000-000000000102"),
        target_manifest_id=uuid.UUID("00000000-0000-0000-0000-000000000104"),
        target_manifest_sha256="a" * 64,
        solver_id="py_mw",
        solver_version="1.0.0",
        fencing_token=7,
        status="running",
        frame_count=None,
        first_frame_time=None,
        last_frame_time=None,
    )


def _simulation() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        published_attempt_id=None,
        published_attempt_no=None,
        status="running",
        phase="persisting",
        progress=95,
        lease_owner="worker-1",
        lease_token=uuid.UUID("00000000-0000-0000-0000-000000000106"),
        lease_until=START + timedelta(minutes=5),
        result_summary=None,
        finished_at=None,
    )


def _frame_context() -> FrameWriteContext:
    return FrameWriteContext(
        simulation_id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        attempt_id=uuid.UUID("00000000-0000-0000-0000-000000000103"),
        attempt_no=1,
        network_id=uuid.UUID("00000000-0000-0000-0000-000000000105"),
        network_revision_id=uuid.UUID("00000000-0000-0000-0000-000000000102"),
        target_manifest_id=uuid.UUID("00000000-0000-0000-0000-000000000104"),
        target_manifest_sha256="a" * 64,
        solver_id="py_mw",
        solver_version="1.0.0",
        fencing_token=7,
        first_frame_at=START,
        frame_interval_seconds=MAX_FRAME_INTERVAL_SECONDS,
    )


def test_frame_serialization_is_canonical_and_interval_is_capped() -> None:
    payload = {"nodes": {"depth_m": [0.1, 0.2]}, "frame_index": 0}
    encoded = serialize_frame_payload(payload)

    assert encoded == b'{"frame_index":0,"nodes":{"depth_m":[0.1,0.2]}}'
    digest = hashlib.sha256(encoded).hexdigest()
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")

    with pytest.raises(ResultValidationError, match="300"):
        replace(_frame_context(), frame_interval_seconds=301)


def test_twenty_four_hours_at_five_minutes_has_exactly_289_grid_frames() -> None:
    context = replace(_frame_context(), expected_frame_count=289)
    last = prepare_frame(
        context,
        FrameInput(
            frame_index=288,
            frame_time=START + timedelta(hours=24),
            payload={"nodes": {"depth_m": []}},
        ),
    )

    assert last.frame_index == 288
    assert last.frame_time - context.first_frame_at == timedelta(hours=24)
    assert last.payload_sha256 == hashlib.sha256(last.canonical_payload).hexdigest()

    with pytest.raises(ResultValidationError, match="expected_frame_count"):
        prepare_frame(
            context,
            FrameInput(
                frame_index=289,
                frame_time=START + timedelta(hours=24, minutes=5),
                payload={},
            ),
        )


@pytest.mark.asyncio
async def test_repository_writes_registration_and_timescale_frame_in_one_flush() -> (
    None
):
    session = _RecordingSession(_attempt())
    repository = ResultRepository(session)
    context = _frame_context()
    frame = FrameInput(
        frame_index=0,
        frame_time=START,
        payload={"nodes": {"depth_m": [0.0, 0.2]}},
    )

    result = await repository.write_frame(context, frame)

    assert result.inserted is True
    assert result.frame_index == 0
    assert result.payload_bytes == len(result.canonical_payload)
    assert result.payload_sha256 == hashlib.sha256(result.canonical_payload).hexdigest()
    assert [type(item) for item in session.added] == [
        SimulationFrameRegistration,
        SimulationFrame,
    ]
    assert session.added[1].payload == frame.payload
    assert session.added[1].payload_sha256 == result.payload_sha256
    assert session.flush_count == 1

    session.registrations.append(session.added[0])
    duplicate = await repository.write_frame(context, frame)
    assert duplicate.inserted is False
    assert len(session.added) == 2

    with pytest.raises(ResultValidationError, match="FRAME_ATTEMPT_CONFLICT"):
        await repository.write_frame(
            context,
            FrameInput(
                frame_index=0,
                frame_time=START,
                payload={"nodes": {"depth_m": [0.0, 0.3]}},
            ),
        )


@pytest.mark.asyncio
async def test_repository_rejects_frame_gaps_and_wrong_grid() -> None:
    session = _RecordingSession(_attempt())
    repository = ResultRepository(session)
    context = _frame_context()

    with pytest.raises(ResultValidationError, match="expected frame index"):
        await repository.write_frame(
            context,
            FrameInput(
                frame_index=1,
                frame_time=START + timedelta(minutes=5),
                payload={},
            ),
        )

    with pytest.raises(ResultValidationError, match="frame_time"):
        await repository.write_frame(
            context,
            FrameInput(
                frame_index=0, frame_time=START + timedelta(seconds=1), payload={}
            ),
        )


@pytest.mark.asyncio
async def test_finalize_publishes_summary_quality_and_artifact_index(
    tmp_path: Path,
) -> None:
    context = replace(_frame_context(), expected_frame_count=2)
    attempt = _attempt()
    simulation = _simulation()
    session = _RecordingSession(attempt, simulation)
    for index in range(2):
        prepared = prepare_frame(
            context,
            FrameInput(
                frame_index=index,
                frame_time=START + timedelta(minutes=5 * index),
                payload={"frame_index": index},
            ),
        )
        session.registrations.append(
            SimulationFrameRegistration(
                attempt_id=context.attempt_id,
                frame_index=index,
                frame_time=prepared.frame_time,
                payload_bytes=prepared.payload_bytes,
                payload_sha256=prepared.payload_sha256,
                quality_status="verified",
            )
        )
        session.frames.append(
            SimulationFrame(
                frame_time=prepared.frame_time,
                attempt_id=context.attempt_id,
                simulation_id=context.simulation_id,
                attempt_no=context.attempt_no,
                frame_index=index,
                network_id=context.network_id,
                network_revision_id=context.network_revision_id,
                target_manifest_id=context.target_manifest_id,
                target_manifest_sha256=context.target_manifest_sha256,
                solver_id=context.solver_id,
                solver_version=context.solver_version,
                schema_version=prepared.schema_version,
                payload=prepared.payload,
                payload_bytes=prepared.payload_bytes,
                payload_sha256=prepared.payload_sha256,
                quality_status="verified",
            )
        )
    repository = ResultRepository(session, artifact_root=tmp_path)

    index = await repository.finalize(
        context,
        summary={"frame_count": 2},
        quality_report={
            "status": "verified",
            "max_mass_residual_relative": 1e-5,
            "max_courant": 0.4,
        },
        completed_at=START + timedelta(minutes=10),
    )

    result = next(item for item in session.added if isinstance(item, SimulationResult))
    assert index.frame_count == 2
    assert index.artifact.path.is_relative_to(tmp_path.resolve())
    assert index.artifact.path.is_file()
    assert result.summary["artifact_index"]["frame_count"] == 2
    assert attempt.summary_id == result.id
    assert attempt.status == "published"
    assert simulation.published_attempt_id == context.attempt_id
    assert simulation.published_attempt_no == context.attempt_no
    assert simulation.status == "completed"
    assert simulation.progress == 100
    assert simulation.lease_owner is None
    assert simulation.lease_token is None
    assert simulation.lease_until is None


@pytest.mark.asyncio
async def test_frame_retention_keeps_metadata_and_uses_ninety_day_boundary() -> None:
    now = START + timedelta(days=100)
    exact_boundary = now - timedelta(days=90)
    older = exact_boundary - timedelta(microseconds=1)
    session = _RecordingSession(_attempt())
    repository = ResultRepository(session)

    assert retention_cutoff(now, retention_days=90) == exact_boundary
    assert retention_state(exact_boundary, now=now, retention_days=90) == "retained"
    assert retention_state(older, now=now, retention_days=90) == "expired"
    assert repository.retention_days == 90

    swept = await repository.expire_frames(now)

    assert swept.cutoff == exact_boundary
    assert swept.deleted_frame_count == 3
    assert "DELETE FROM" in session.statements[-1]
    assert "simulation_frames" in session.statements[-1]
    assert "simulation_results" not in session.statements[-1]
    assert "simulation_attempts" not in session.statements[-1]


def test_controlled_artifact_is_atomic_hashed_and_immutable(tmp_path: Path) -> None:
    artifact_id = uuid.UUID("00000000-0000-0000-0000-000000000201")
    payload = {"schema": "simulation-artifact-index-v1", "frame_count": 289}

    reference = write_controlled_json_artifact(
        tmp_path, "simulation-results", artifact_id, payload
    )
    repeated = write_controlled_json_artifact(
        tmp_path, "simulation-results", artifact_id, payload
    )

    assert repeated == reference
    assert reference.path.is_relative_to(tmp_path.resolve())
    assert reference.sha256 == hashlib.sha256(reference.path.read_bytes()).hexdigest()

    with pytest.raises(ArtifactConflictError):
        write_controlled_json_artifact(
            tmp_path,
            "simulation-results",
            artifact_id,
            {"schema": "simulation-artifact-index-v1", "frame_count": 1},
        )


def _synthetic_dataset() -> tuple[list[ObservationPoint], list[SimulatedWaterLevel]]:
    observations: list[ObservationPoint] = []
    baseline: list[SimulatedWaterLevel] = []
    for event_index in range(3):
        for sample_index in range(12):
            timestamp = START + timedelta(minutes=5 * (event_index * 12 + sample_index))
            value_mm = 50.0 + 10.0 * (sample_index % 6)
            observations.append(
                ObservationPoint(
                    observed_at=timestamp,
                    target_id="N-001",
                    value_mm=value_mm,
                    event_id=f"event-{event_index}",
                    trust_level="synthetic",
                )
            )
            baseline.append(
                SimulatedWaterLevel(
                    timestamp=timestamp,
                    target_id="N-001",
                    value_mm=value_mm + 10.0,
                )
            )
    return observations, baseline


def _evaluate_offset(
    parameters: dict[str, float], observations: list[ObservationPoint]
) -> CandidateEvaluation:
    correction = parameters["offset_mm"]
    return CandidateEvaluation(
        predictions=[
            SimulatedWaterLevel(
                timestamp=item.observed_at,
                target_id=item.target_id,
                value_mm=item.value_mm - (correction - 10.0),
            )
            for item in observations
        ]
    )


def test_synthetic_grid_search_quantifies_improvement_but_never_claims_ac5() -> None:
    observations, baseline = _synthetic_dataset()

    report = run_calibration(
        observations=observations,
        baseline_predictions=baseline,
        parameter_space={
            "offset_mm": ParameterSpec("offset_mm", minimum=0, maximum=20, step=5)
        },
        evaluator=lambda parameters: _evaluate_offset(parameters, observations),
        method="grid",
        max_evaluations=5,
        require_verified=False,
    )

    assert report.best_parameters == {"offset_mm": 10.0}
    assert report.baseline_metrics.rmse_mm == pytest.approx(10.0)
    assert report.calibrated_metrics.rmse_mm == pytest.approx(0.0, abs=1e-12)
    assert report.calibrated_metrics.nse == pytest.approx(1.0)
    assert report.improvement.nse_delta >= 0.05
    assert report.improvement.rmse_reduction_pct == pytest.approx(100.0)
    assert report.diagnostic_numeric_improved is True
    assert report.ac5_eligible is False
    assert report.improved is False
    assert (
        report.content_sha256
        == hashlib.sha256(report.canonical_json().encode("utf-8")).hexdigest()
    )


def test_verified_observations_meet_all_frozen_improvement_thresholds() -> None:
    observations, baseline = _synthetic_dataset()
    verified = [
        ObservationPoint(
            observed_at=item.observed_at,
            target_id=item.target_id,
            value_mm=item.value_mm,
            event_id=item.event_id,
            trust_level="external_verified",
        )
        for item in observations
    ]

    report = run_calibration(
        observations=verified,
        baseline_predictions=baseline,
        parameter_space={
            "offset_mm": ParameterSpec("offset_mm", minimum=0, maximum=20, step=5)
        },
        evaluator=lambda parameters: _evaluate_offset(parameters, verified),
        method="grid",
        max_evaluations=5,
        require_verified=True,
        # The trust claim is only half the evidence; a resolver must confirm it.
        trust_resolver=lambda point: point.trust_level == "external_verified",
        split_manifest={
            "schema": "calibration-split-v1",
            "evaluation_split": "validation",
            "event_ids": ["event-0", "event-1", "event-2"],
        },
        provenance={
            "network_revision_id": "00000000-0000-0000-0000-000000000102",
            "rainfall_scenario_id": "00000000-0000-0000-0000-000000000401",
            "solver_id": "py_mw",
            "solver_version": "1.0.0",
            "baseline_parameter_sha256": "b" * 64,
            "evaluation_code_sha256": "c" * 64,
        },
    )

    assert report.ac5_eligible is True
    assert report.improved is True
    assert report.improvement.nse_delta >= 0.05
    assert report.calibrated_metrics.rmse_mm <= 0.95 * report.baseline_metrics.rmse_mm
    assert (
        report.calibrated_metrics.peak_abs_error_mm
        <= 1.10 * report.baseline_metrics.peak_abs_error_mm
    )


def test_ac5_rejects_candidate_paired_on_disjoint_events() -> None:
    observations, baseline = _synthetic_dataset()
    verified = [replace(item, trust_level="external_verified") for item in observations]
    candidate_source = [
        item for item in verified if item.event_id in {"event-0", "event-1"}
    ]

    with pytest.raises(CalibrationExecutionError, match="cohort"):
        run_calibration(
            observations=verified,
            baseline_predictions=baseline,
            parameter_space={
                "offset_mm": ParameterSpec("offset_mm", minimum=0, maximum=20, step=5)
            },
            evaluator=lambda _parameters: CandidateEvaluation(
                [
                    SimulatedWaterLevel(
                        item.observed_at,
                        item.target_id,
                        item.value_mm,
                    )
                    for item in candidate_source
                ]
            ),
            method="grid",
            max_evaluations=5,
            require_verified=True,
            split_manifest={
                "schema": "calibration-split-v1",
                "evaluation_split": "validation",
                "event_ids": ["event-0", "event-1", "event-2"],
            },
            provenance={
                "network_revision_id": "00000000-0000-0000-0000-000000000102",
                "rainfall_scenario_id": "00000000-0000-0000-0000-000000000401",
                "solver_id": "py_mw",
                "solver_version": "1.0.0",
                "baseline_parameter_sha256": "b" * 64,
                "evaluation_code_sha256": "c" * 64,
            },
        )


def test_random_search_is_reproducible_for_the_same_seed() -> None:
    observations, baseline = _synthetic_dataset()
    kwargs = {
        "observations": observations,
        "baseline_predictions": baseline,
        "parameter_space": {
            "offset_mm": ParameterSpec("offset_mm", minimum=0, maximum=20)
        },
        "evaluator": lambda parameters: _evaluate_offset(parameters, observations),
        "method": "random",
        "max_evaluations": 8,
        "seed": 20260925,
        "require_verified": False,
    }

    first = run_calibration(**kwargs)
    second = run_calibration(**kwargs)

    assert first.method == "random"
    assert first.trial_count == 8
    assert first.best_parameters == second.best_parameters
    assert first.content_sha256 == second.content_sha256


def test_nse_zero_denominator_is_reported_undefined_not_zero() -> None:
    observed = [
        ObservationPoint(
            observed_at=START + timedelta(minutes=5 * index),
            target_id="N-001",
            value_mm=10.0,
            event_id="event-constant",
            trust_level="synthetic",
        )
        for index in range(12)
    ]
    predicted = [
        SimulatedWaterLevel(
            timestamp=item.observed_at,
            target_id=item.target_id,
            value_mm=12.0,
        )
        for item in observed
    ]

    metrics = compute_metrics(observed, predicted, require_verified=False)

    assert metrics.rmse_mm == pytest.approx(2.0)
    assert metrics.nse is None
    assert metrics.nse_code == "NSE_UNDEFINED"
    objective = CalibrationObjective()
    assert math.isfinite(objective.score(metrics))


def test_influx_source_reads_sensor_readings_water_level_mm_and_feeds_metrics() -> None:
    class _Record:
        def __init__(self, timestamp: datetime, value: float) -> None:
            self._timestamp = timestamp

        def get_time(self) -> datetime:
            return self._timestamp

        @property
        def values(self) -> dict[str, Any]:
            return {
                "_measurement": "sensor_readings",
                "_field": "water_level_mm",
                "device_id": "device-1",
                "_value": 123.4,
            }

    class _Table:
        records = [_Record(START, 123.4)]

    class _QueryApi:
        def __init__(self) -> None:
            self.query_text = ""

        def query(self, query: str, org: str) -> list[_Table]:
            self.query_text = query
            assert org
            return [_Table()]

    query_api = _QueryApi()
    source = InfluxWaterLevelObservationSource(
        service=SimpleNamespace(_query_api=query_api),
        bucket="sensor-data",
        org="city",
    )

    observations = source.fetch(
        device_id="device-1", start=START, stop=START + timedelta(hours=1)
    )
    predicted = [
        SimulatedWaterLevel(item.observed_at, item.target_id, item.value_mm + 1.0)
        for item in observations
    ]
    metrics = compute_metrics(observations, predicted, require_verified=False)

    assert 'r["_measurement"] == "sensor_readings"' in query_api.query_text
    assert 'r["_field"] == "water_level_mm"' in query_api.query_text
    assert observations[0].value_mm == pytest.approx(123.4)
    assert observations[0].trust_level == "external_unverified"
    assert metrics.sample_count == 1
    assert metrics.rmse_mm == pytest.approx(1.0)


def test_calibration_report_writes_a_hashed_controlled_artifact(tmp_path: Path) -> None:
    observations, baseline = _synthetic_dataset()
    report = run_calibration(
        observations=observations,
        baseline_predictions=baseline,
        parameter_space={
            "offset_mm": ParameterSpec("offset_mm", minimum=0, maximum=20, step=5)
        },
        evaluator=lambda parameters: _evaluate_offset(parameters, observations),
        method="grid",
        max_evaluations=5,
        require_verified=False,
    )
    report_id = uuid.UUID("00000000-0000-0000-0000-000000000301")

    reference = write_calibration_report(tmp_path, report_id, report)

    assert reference.sha256 == report.content_sha256
    assert reference.path.is_relative_to(tmp_path.resolve())
    assert reference.path.name == f"{report_id}.json"


def test_frame_index_must_be_a_real_integer() -> None:
    with pytest.raises(ResultValidationError, match="integer"):
        prepare_frame(
            _frame_context(),
            FrameInput(
                frame_index=1.0,  # type: ignore[arg-type]
                frame_time=START,
                payload={},
            ),
        )


def test_negative_hydraulic_state_is_rejected_before_persistence() -> None:
    with pytest.raises(ResultValidationError, match="negative|INVALID_STATE"):
        prepare_frame(
            _frame_context(),
            FrameInput(
                frame_index=0,
                frame_time=START,
                payload={"nodes": [{"depth_m": -0.01, "volume_m3": 2.0}]},
            ),
        )


def test_courant_quality_gate_keeps_frozen_half_limit() -> None:
    with pytest.raises(ResultValidationError, match="Courant"):
        ResultRepository._validate_quality_report(
            _frame_context(),
            {
                "status": "verified",
                "max_mass_residual_relative": 1e-5,
                "max_courant": 0.500001,
            },
        )


def test_a_self_declared_trust_level_alone_cannot_reach_pass() -> None:
    """AC-5: ``trust_level`` is a claim, not evidence.

    Before the seal, re-labelling a synthetic dataset as ``external_verified``
    was enough to produce ``report_code == "PASS"``.  Eligibility now also
    requires a resolver to corroborate each observation, so a caller that only
    fills in the payload stays INELIGIBLE.
    """

    observations, baseline = _synthetic_dataset()
    self_declared = [
        replace(item, trust_level="external_verified") for item in observations
    ]

    report = run_calibration(
        observations=self_declared,
        baseline_predictions=baseline,
        parameter_space={
            "offset_mm": ParameterSpec("offset_mm", minimum=0, maximum=20, step=5)
        },
        evaluator=lambda parameters: _evaluate_offset(parameters, self_declared),
        method="grid",
        max_evaluations=5,
        require_verified=True,
        split_manifest={
            "schema": "calibration-split-v1",
            "evaluation_split": "validation",
            "event_ids": ["event-0", "event-1", "event-2"],
        },
        provenance={
            "network_revision_id": "00000000-0000-0000-0000-000000000102",
            "rainfall_scenario_id": "00000000-0000-0000-0000-000000000401",
            "solver_id": "py_mw",
            "solver_version": "1.0.0",
            "baseline_parameter_sha256": "b" * 64,
            "evaluation_code_sha256": "c" * 64,
        },
        # deliberately no trust_resolver
    )

    assert report.ac5_eligible is False
    assert report.improved is False
    assert report.report_code == "INELIGIBLE_OBSERVATIONS"


def test_a_resolver_that_rejects_an_observation_also_blocks_pass() -> None:
    """Corroboration is per observation, not a single on/off switch."""

    observations, baseline = _synthetic_dataset()
    self_declared = [
        replace(item, trust_level="external_verified") for item in observations
    ]

    report = run_calibration(
        observations=self_declared,
        baseline_predictions=baseline,
        parameter_space={
            "offset_mm": ParameterSpec("offset_mm", minimum=0, maximum=20, step=5)
        },
        evaluator=lambda parameters: _evaluate_offset(parameters, self_declared),
        method="grid",
        max_evaluations=5,
        require_verified=True,
        split_manifest={
            "schema": "calibration-split-v1",
            "evaluation_split": "validation",
            "event_ids": ["event-0", "event-1", "event-2"],
        },
        provenance={
            "network_revision_id": "00000000-0000-0000-0000-000000000102",
            "rainfall_scenario_id": "00000000-0000-0000-0000-000000000401",
            "solver_id": "py_mw",
            "solver_version": "1.0.0",
            "baseline_parameter_sha256": "b" * 64,
            "evaluation_code_sha256": "c" * 64,
        },
        trust_resolver=lambda point: point.event_id != "event-2",
    )

    assert report.ac5_eligible is False
    assert report.report_code == "INELIGIBLE_OBSERVATIONS"
