"""FR/AC acceptance diagnostics for the hydraulic backend.

The HTTP cases below intentionally use the process-local adapter where durable
wiring is absent.  Those cases are named and mapped as diagnostics only.  The
matrix gate binds every diagnostic to the current pytest session and refuses
to treat an in-memory result as AC evidence.
"""

from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app import main as main_module
from app.api.hydraulic_routes import hydraulic_store, reset_hydraulic_api_store
from app.core.security import create_access_token
from app.services.hydraulic.calibration import (
    CalibrationError,
    CandidateEvaluation,
    ObservationPoint,
    ParameterSpec,
    SimulatedWaterLevel,
    run_calibration,
)
from app.services.hydraulic.forecast_alerts import (
    ForecastAlertService,
    ForecastCandidate,
    ForecastRuleRef,
    InMemoryForecastAlertStore,
    ProbabilityCalibrationRef,
)
from app.services.hydraulic.tasks import (
    InMemoryTaskRepository,
    StaleTaskFence,
    TaskKind,
    TaskStatus,
)
from tests.acceptance.matrix_gate import all_cases_pass, validate_matrix_structure

UTC = timezone.utc
START = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
TEST_ROOT = Path(__file__).parents[1]
HYDRAULIC_FIXTURE_ROOT = TEST_ROOT / "fixtures" / "hydraulic"
ACCEPTANCE_ROOT = HYDRAULIC_FIXTURE_ROOT / "acceptance" / "v1"
HASH_A = "a" * 64
HASH_B = "b" * 64


def _load(name: str) -> dict[str, Any]:
    return json.loads((ACCEPTANCE_ROOT / name).read_text(encoding="utf-8"))


def _headers(role: str = "operator", key: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            "00000000-0000-0000-4000-8000-000000000001",
            extra_claims={"role": role},
        )
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


@pytest.fixture(autouse=True)
def _isolated_hydraulic_api_store(monkeypatch: pytest.MonkeyPatch):
    reset_hydraulic_api_store()
    main_module._rate_buckets.clear()
    for field in (
        "RATE_LIMIT_NETWORK_JOB_SUBMIT_PER_MINUTE",
        "RATE_LIMIT_SIMULATION_SUBMIT_PER_MINUTE",
        "RATE_LIMIT_FORECAST_READ_PER_MINUTE",
        "RATE_LIMIT_FORECAST_TRANSITION_PER_MINUTE",
        "RATE_LIMIT_SSE_CONNECT_PER_MINUTE",
    ):
        monkeypatch.setattr(main_module.settings, field, 100)
    yield
    reset_hydraulic_api_store()
    main_module._rate_buckets.clear()


async def _create_network(client, code: str = "ACCEPTANCE-NET") -> dict[str, Any]:
    response = await client.post(
        "/api/networks",
        json={"code": code, "name": "Acceptance network"},
        headers=_headers("operator", f"network-{code}"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _rainfall_points(config: dict[str, Any], start: datetime) -> list[dict[str, Any]]:
    pattern = [float(value) for value in config["intensity_pattern_mm_h"]]
    return [
        {
            "point_index": index,
            "valid_at": (
                start + timedelta(seconds=config["step_seconds"] * index)
            ).isoformat(),
            "intensity_mm_h": pattern[index % len(pattern)],
        }
        for index in range(config["point_count"])
    ]


async def _create_rainfall(
    client,
    network: dict[str, Any],
    config: dict[str, Any],
    *,
    key: str,
) -> dict[str, Any]:
    start = datetime.fromisoformat(config["start_at"].replace("Z", "+00:00"))
    points = _rainfall_points(config, start)
    body = {
        "network_id": network["id"],
        "name": "50-year acceptance storm",
        "kind": config["kind"],
        "return_period_years": config["return_period_years"],
        "step_seconds": config["step_seconds"],
        "duration_seconds": config["duration_seconds"],
        "source_type": "synthetic",
        "algorithm": "synthetic-design-chicago-v1",
        "random_seed": 50,
        "points": points,
        "content_sha256": hashlib.sha256(
            json.dumps(points, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    response = await client.post(
        "/api/rainfall-scenarios", json=body, headers=_headers("operator", key)
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _submit_simulation(
    client,
    network: dict[str, Any],
    scenario: dict[str, Any],
    *,
    key: str,
    duration_seconds: int | None = None,
    frame_interval_seconds: int = 300,
    parameter_set_id: str | None = None,
) -> dict[str, Any]:
    body = {
        "network_id": network["id"],
        "network_revision_id": network["current_revision_id"],
        "rainfall_scenario_id": scenario["id"],
        "parameter_set_id": parameter_set_id or str(uuid.uuid4()),
        "initial_state_id": None,
        "solver_selection": "auto",
        "start_at": "2026-09-25T00:00:00Z",
        "duration_seconds": duration_seconds or scenario["duration_seconds"],
        "frame_interval_seconds": frame_interval_seconds,
        "ponding_enabled": False,
        "strict_stability": True,
    }
    response = await client.post(
        "/api/simulations", json=body, headers=_headers("operator", key)
    )
    assert response.status_code == 202, response.text
    return response.json()


def _inject_in_memory_result(
    simulation_id: str,
    *,
    frame_count: int,
    metrics: dict[str, float] | None = None,
    metric_units: dict[str, str] | None = None,
) -> None:
    """Inject an in-memory diagnostic result; this is never worker evidence."""

    item = hydraulic_store.simulations[simulation_id]
    item["status"] = "completed"
    item["progress"] = 100
    item["phase"] = "completed"
    item["published_attempt_id"] = str(uuid.uuid4())
    item["published_attempt_no"] = 1
    item["finished_at"] = "2026-09-26T00:00:00Z"
    item["result"] = {
        "summary": {"frame_count": frame_count},
        "quality": {"status": "verified"},
        "metrics": metrics or {"rmse": 1.0, "nse": 0.5},
        "metric_units": metric_units or {"rmse": "mm", "nse": "1"},
        "better_directions": {"rmse": "lower", "nse": "higher"},
    }
    item["published_frames"] = {
        str(index): {
            "frame_index": index,
            "nodes": {"feature_index": [], "depth_m": [], "surcharged": []},
            "pipes": {"feature_index": [], "fill_ratio": [], "flow_m3s": []},
            "ponding": {"feature_index": [], "depth_m": []},
        }
        for index in range(frame_count)
    }


def test_acceptance_contract_runtime_is_python_311():
    assert sys.version_info[:2] == (3, 11), (
        "hydraulic acceptance must run on the contract interpreter; "
        f"observed Python {sys.version_info.major}.{sys.version_info.minor}"
    )


def test_acceptance_matrix_manifest_covers_all_fr_and_ac_entries():
    matrix = _load("matrix.json")
    requirements = _load("acceptance_requirements.json")

    errors = validate_matrix_structure(
        matrix,
        requirements,
        backend_root=TEST_ROOT.parent,
        fixture_root=HYDRAULIC_FIXTURE_ROOT,
    )
    assert errors == []
    assert matrix["schema"] == "hydraulic-backend-acceptance-matrix-v2"
    assert set(matrix["requirements"]) == {f"FR-{index}" for index in range(1, 8)}
    assert {case["id"] for case in matrix["cases"]} == {
        f"AC-{index}" for index in range(1, 9)
    }


def test_acceptance_matrix_requires_every_case_to_have_pass_evidence():
    matrix = _load("matrix.json")
    requirements = _load("acceptance_requirements.json")

    assert (
        validate_matrix_structure(
            matrix,
            requirements,
            backend_root=TEST_ROOT.parent,
            fixture_root=HYDRAULIC_FIXTURE_ROOT,
        )
        == []
    )
    assert not all_cases_pass(matrix)
    for case in matrix["cases"]:
        if case["status"] == "PASS":
            assert case["pass_nodeids"]
        else:
            assert not case["pass_nodeids"]
            assert case["reason"].strip()

    probe = json.loads(json.dumps(matrix))
    probe["cases"][0]["status"] = "PASS"
    assert not all_cases_pass(probe)
    for case in probe["cases"]:
        case["status"] = "PASS"
    assert all_cases_pass(probe)


@pytest.mark.asyncio
async def test_ac1_in_memory_adapter_valid_inp_import_is_accepted_and_replayed(
    client,
):
    fixture = HYDRAULIC_FIXTURE_ROOT / "imports" / "swmm_minimal_wgs84.inp"
    payload = fixture.read_bytes()
    key = "ac1-inp-import-key"
    headers = _headers("operator", key)

    first = await client.post(
        "/api/networks/import",
        files={"file": (fixture.name, payload, "text/plain")},
        headers=headers,
    )
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["status"] == "queued"
    assert body["network_job_id"]
    assert body["import_id"]
    assert body["result"] is None  # durable job has not been finalized yet

    replay = await client.post(
        "/api/networks/import",
        files={"file": (fixture.name, payload, "text/plain")},
        headers=headers,
    )
    assert replay.status_code == 202
    assert replay.json()["network_job_id"] == body["network_job_id"]

    job = await client.get(
        f"/api/network-jobs/{body['network_job_id']}", headers=_headers("viewer")
    )
    detail = await client.get(
        f"/api/network-imports/{body['import_id']}", headers=_headers("viewer")
    )
    assert job.status_code == 200
    assert detail.status_code == 200
    assert job.json()["status"] == "queued"
    assert detail.json()["committed"] is False
    assert detail.json()["counts"]["conduits"] == 1
    assert len(hydraulic_store.networks) == 0


@pytest.mark.asyncio
async def test_ac1_in_memory_adapter_valid_geojson_import_is_accepted_and_replayed(
    client,
):
    fixture = ACCEPTANCE_ROOT / "network_valid.geojson"
    payload = fixture.read_bytes()
    key = "ac1-geojson-import-key"
    headers = _headers("operator", key)

    first = await client.post(
        "/api/networks/import",
        files={"file": (fixture.name, payload, "application/geo+json")},
        headers=headers,
    )
    assert first.status_code == 202, first.text
    assert first.json()["result"] is None
    import_id = first.json()["import_id"]
    detail = await client.get(
        f"/api/network-imports/{import_id}", headers=_headers("viewer")
    )
    assert detail.status_code == 200
    assert detail.json()["counts"]["features"] == 2

    replay = await client.post(
        "/api/networks/import",
        files={"file": (fixture.name, payload, "application/geo+json")},
        headers=headers,
    )
    assert replay.status_code == 202
    assert replay.json()["network_job_id"] == first.json()["network_job_id"]


@pytest.mark.asyncio
async def test_ac1_production_api_fails_closed_without_durable_repository(
    client, monkeypatch
):
    fixture = HYDRAULIC_FIXTURE_ROOT / "imports" / "swmm_minimal_wgs84.inp"
    headers = _headers("operator", "ac1-production-fail-closed")
    monkeypatch.setattr(main_module.settings, "DEPLOYMENT_ENVIRONMENT", "production")

    response = await client.post(
        "/api/networks/import",
        files={"file": (fixture.name, fixture.read_bytes(), "text/plain")},
        headers=headers,
    )

    assert response.status_code == 503
    assert response.json()["code"] == "DURABLE_STORE_UNAVAILABLE"
    assert response.headers["Retry-After"] == "5"
    assert len(hydraulic_store.jobs) == 0


@pytest.mark.asyncio
async def test_ac3_request_smoke_50_year_24_hour_preserves_289_frame_grid(
    client,
):
    config = _load("rainfall_50y_24h.json")
    network = await _create_network(client, "AC3-50YEAR")
    scenario = await _create_rainfall(client, network, config, key="ac3-rainfall-50y")
    parameter_set_id = str(uuid.uuid4())
    simulation = await _submit_simulation(
        client,
        network,
        scenario,
        key="ac3-submit-50y",
        parameter_set_id=parameter_set_id,
    )

    assert scenario["return_period_years"] == 50
    assert scenario["duration_seconds"] == 86_400
    assert scenario["step_seconds"] == 300
    assert len(scenario["points"]) == 289
    assert simulation["status"] == "queued"
    assert simulation["progress"] == 0
    assert simulation["request"]["duration_seconds"] == 86_400
    assert simulation["frame_count"] == 289
    assert simulation["solver_id"] in config["expected_solver_ids"]

    replay = await _submit_simulation(
        client,
        network,
        scenario,
        key="ac3-submit-50y",
        parameter_set_id=parameter_set_id,
    )
    assert replay["id"] == simulation["id"]


@pytest.mark.asyncio
async def test_ac3_in_memory_frame_injection_is_replayable(client):
    config = _load("rainfall_50y_24h.json")
    network = await _create_network(client, "AC3-REPLAY")
    scenario = await _create_rainfall(
        client, network, config, key="ac3-rainfall-replay"
    )
    simulation = await _submit_simulation(
        client, network, scenario, key="ac3-submit-replay"
    )
    _inject_in_memory_result(simulation["id"], frame_count=289)

    frames = await client.get(
        f"/api/simulations/{simulation['id']}/frames", headers=_headers("viewer")
    )
    assert frames.status_code == 200, frames.text
    payload = frames.json()
    assert payload["frame_count"] == 289
    assert [item["frame_index"] for item in payload["frames"]] == list(range(289))
    assert payload["frames"][0]["frame_time"].endswith("Z")
    assert payload["frames"][-1]["frame_time"].endswith("Z")

    first = await client.get(
        f"/api/simulations/{simulation['id']}/frames/0", headers=_headers("viewer")
    )
    last = await client.get(
        f"/api/simulations/{simulation['id']}/frames/288", headers=_headers("viewer")
    )
    assert first.status_code == 200
    assert last.status_code == 200
    assert last.json()["frame_index"] == 288


@pytest.mark.asyncio
async def test_fr3_in_memory_task_repository_crash_recovery_fences_old_attempt():
    repository = InMemoryTaskRepository(lease_seconds=1)
    submitted = await repository.submit(
        kind=TaskKind.simulation,
        user_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        idempotency_key="acceptance-recovery-key",
        request_fingerprint="a" * 64,
        now=START,
    )
    stale = await repository.claim_next("crashed-worker", now=START)
    assert stale is not None
    await repository.heartbeat(stale, now=START)

    recovered_at = START + timedelta(seconds=2)
    recovered = await repository.recover_expired(now=recovered_at)
    assert [item.status for item in recovered] == [TaskStatus.retry_wait]
    assert recovered[0].next_attempt_at == recovered_at + timedelta(seconds=10)

    replacement = await repository.claim_next(
        "replacement-worker", now=recovered_at + timedelta(seconds=10)
    )
    assert replacement is not None
    assert replacement.attempt_no == 2
    assert replacement.fencing_token > stale.fencing_token
    with pytest.raises(StaleTaskFence):
        await repository.complete(
            stale, result_committed=True, now=recovered_at + timedelta(seconds=10)
        )
    assert (await repository.get(submitted.resource.id)).status is TaskStatus.leased


def _forecast_refs(
    fixture: dict[str, Any],
) -> tuple[ForecastRuleRef, ProbabilityCalibrationRef]:
    issued = datetime.fromisoformat(fixture["issued_at"].replace("Z", "+00:00"))
    rule = ForecastRuleRef(
        id=fixture["rule_id"],
        artifact_sha256=HASH_A,
        version="acceptance-v1",
        alert_type="flood_forecast",
        metric="water_depth_m",
        operator=">=",
        threshold_value=0.30,
        threshold_unit="m",
        risk_scale=0.30,
        valid_from=issued - timedelta(days=1),
        valid_until=issued + timedelta(days=1),
    )
    calibration = ProbabilityCalibrationRef(
        id=fixture["calibration_id"],
        artifact_sha256=HASH_B,
        method="ensemble_exceedance_frequency",
        metric="water_depth_m",
        lead_min_minutes=0,
        lead_max_minutes=120,
        magnitude_min=0.0,
        magnitude_max=2.0,
        valid_from=issued - timedelta(days=1),
        valid_until=issued + timedelta(days=1),
    )
    return rule, calibration


class _AcceptanceArtifactResolver:
    def verify(self, *, artifact_sha256: str, **_kwargs: object) -> dict[str, object]:
        return {"schema_version": 1, "sha256": artifact_sha256}


def _forecast_candidate(
    fixture: dict[str, Any],
    *,
    nowcast_number: int,
    issued_at: datetime,
) -> ForecastCandidate:
    return ForecastCandidate(
        network_id=fixture["network_id"],
        nowcast_run_id=uuid.UUID(int=nowcast_number),
        issued_at=issued_at,
        simulation_id=fixture["simulation_id"],
        network_revision_id=fixture["revision_id"],
        target_manifest_id=fixture["manifest_id"],
        target_manifest_sha256=HASH_A,
        alert_type="flood_forecast",
        target_type="node",
        target_id=fixture["target_id"],
        target_stable_key="N-ACCEPTANCE-1",
        expected_at=issued_at.replace(
            second=0, microsecond=0, minute=(issued_at.minute // 5) * 5
        )
        + timedelta(minutes=35),
        depth_m=0.42,
        confidence=0.86,
        level="warning",
        target_manifest={
            "target_ids": {"node": {"N-ACCEPTANCE-1": fixture["target_id"]}}
        },
    )


def test_ac4_in_memory_forecast_suppression_uses_half_open_30_minute_window():
    fixture = _load("forecast_suppression.json")
    rule, calibration = _forecast_refs(fixture)
    service = ForecastAlertService(
        InMemoryForecastAlertStore(), artifact_resolver=_AcceptanceArtifactResolver()
    )
    issued = datetime.fromisoformat(fixture["issued_at"].replace("Z", "+00:00"))
    terminal_at = issued + timedelta(minutes=fixture["terminal_after_minutes"])
    first = service.publish(
        _forecast_candidate(fixture, nowcast_number=1, issued_at=issued),
        rule,
        calibration,
    )
    assert first.action == "created"
    assert first.alert is not None
    assert first.event is not None

    service.mark_terminal(
        alert_id=first.alert.id,
        expected_version=first.alert.version,
        terminal_status="resolved",
        occurred_at=terminal_at,
        reason="fixture clear evidence",
    )
    inside = service.publish(
        _forecast_candidate(
            fixture,
            nowcast_number=2,
            issued_at=terminal_at
            + timedelta(seconds=fixture["inside_cooldown_seconds"]),
        ),
        rule,
        calibration,
    )
    assert inside.action == "suppressed"
    assert inside.alert is None

    boundary = service.publish(
        _forecast_candidate(
            fixture,
            nowcast_number=3,
            issued_at=terminal_at
            + timedelta(minutes=fixture["boundary_offset_minutes"]),
        ),
        rule,
        calibration,
    )
    assert boundary.action == "created"
    assert boundary.alert is not None
    assert boundary.alert.id != first.alert.id


def _synthetic_calibration_inputs():
    fixture = _load("calibration_improvement.json")
    observations: list[ObservationPoint] = []
    baseline: list[SimulatedWaterLevel] = []
    for event_index in range(fixture["event_count"]):
        for sample_index in range(fixture["samples_per_event"]):
            timestamp = START + timedelta(minutes=5 * (event_index * 12 + sample_index))
            value = 50.0 + 10.0 * (sample_index % 6)
            observations.append(
                ObservationPoint(
                    observed_at=timestamp,
                    target_id=fixture["target_id"],
                    value_mm=value,
                    event_id=f"synthetic-event-{event_index}",
                    trust_level=fixture["trust_level"],
                )
            )
            baseline.append(
                SimulatedWaterLevel(
                    timestamp=timestamp,
                    target_id=fixture["target_id"],
                    value_mm=value + fixture["synthetic_baseline_bias_mm"],
                )
            )
    return fixture, observations, baseline


def test_ac5_unverified_fixture_cannot_claim_eligibility():
    fixture, observations, baseline = _synthetic_calibration_inputs()

    assert fixture["fixture_role"] == "blocked_evidence_contract"
    assert fixture["evidence_state"] == "BLOCKED"
    assert fixture["is_ac5_evidence"] is False
    assert fixture["source_evidence"]["state"] == "MISSING"
    with pytest.raises(CalibrationError) as raised:
        run_calibration(
            observations=observations,
            baseline_predictions=baseline,
            parameter_space={
                "offset_mm": ParameterSpec("offset_mm", minimum=0, maximum=20, step=5)
            },
            evaluator=lambda _parameters: CandidateEvaluation(tuple(baseline)),
            require_verified=True,
            split_manifest=None,
            provenance=fixture["provenance"],
        )
    assert raised.value.code == "INSUFFICIENT_OBSERVATIONS"


def test_ac5_synthetic_observations_cannot_claim_ac5_eligibility():
    fixture, _observations, _baseline = _synthetic_calibration_inputs()
    # The fixture is intentionally explicit about the trust boundary.  Rebuild
    # only the trust field to prove AC-5 cannot be satisfied by self-reported
    # synthetic data.
    observations = [
        ObservationPoint(
            observed_at=START + timedelta(minutes=5 * index),
            target_id=fixture["target_id"],
            value_mm=50.0 + 10.0 * (index % 6),
            event_id=f"synthetic-event-{index // 12}",
            trust_level="synthetic",
        )
        for index in range(36)
    ]
    baseline = [
        SimulatedWaterLevel(
            item.observed_at,
            item.target_id,
            item.value_mm + fixture["synthetic_baseline_bias_mm"],
        )
        for item in observations
    ]

    with pytest.raises(CalibrationError) as raised:
        run_calibration(
            observations=observations,
            baseline_predictions=baseline,
            parameter_space={
                "offset_mm": ParameterSpec("offset_mm", minimum=0, maximum=20, step=5)
            },
            evaluator=lambda _parameters: CandidateEvaluation(tuple(baseline)),
            require_verified=True,
            split_manifest=None,
            provenance=fixture["provenance"],
        )
    assert raised.value.code == "INSUFFICIENT_OBSERVATIONS"


@pytest.mark.asyncio
async def test_ac5_observed_comparison_requires_real_verified_pairs(client):
    network = await _create_network(client, "AC5-OBSERVED")
    config = _load("rainfall_50y_24h.json")
    scenario = await _create_rainfall(client, network, config, key="ac5-observed-rain")
    simulation = await _submit_simulation(
        client, network, scenario, key="ac5-observed-sim"
    )
    _inject_in_memory_result(simulation["id"], frame_count=289)

    response = await client.get(
        f"/api/simulations/{simulation['id']}/observed-comparison",
        headers=_headers("viewer"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["coverage"] == {"observation_count": 0, "paired_count": 0}
    assert body["pairs"] == []
    assert body["metrics"] == {"rmse": None, "nse": None, "nse_code": "NSE_UNDEFINED"}


@pytest.mark.asyncio
async def test_ac6_in_memory_comparison_returns_deltas_and_rejects_incompatible_grid(
    client,
):
    config = _load("rainfall_50y_24h.json")
    network = await _create_network(client, "AC6-COMPARE")
    scenario = await _create_rainfall(client, network, config, key="ac6-rain")
    baseline = await _submit_simulation(client, network, scenario, key="ac6-baseline")
    candidate = await _submit_simulation(client, network, scenario, key="ac6-candidate")
    _inject_in_memory_result(
        baseline["id"], frame_count=289, metrics={"rmse": 10.0, "nse": 0.60}
    )
    _inject_in_memory_result(
        candidate["id"], frame_count=289, metrics={"rmse": 5.0, "nse": 0.90}
    )

    response = await client.get(
        f"/api/simulations/{baseline['id']}/compare/{candidate['id']}",
        params={"metrics": "rmse,nse"},
        headers=_headers("viewer"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["compatible"] is True
    assert body["frame_grid"]["frame_count"] == 289
    metrics = {item["key"]: item for item in body["metrics"]}
    assert body["baseline"]["published_attempt_id"]
    assert body["candidate"]["published_attempt_id"]
    assert body["baseline"]["published_attempt_no"] == 1
    assert body["candidate"]["published_attempt_no"] == 1
    assert body["target_manifest_id"]
    assert body["target_manifest_sha256"]
    assert metrics["rmse"]["unit"] == "mm"
    assert metrics["rmse"]["absolute_delta"] == pytest.approx(-5.0)
    assert metrics["rmse"]["relative_delta"] == pytest.approx(-0.5)
    assert metrics["rmse"]["better_direction"] == "lower"
    assert metrics["nse"]["absolute_delta"] == pytest.approx(0.30)

    incompatible = await _submit_simulation(
        client,
        network,
        scenario,
        key="ac6-incompatible",
        duration_seconds=300,
    )
    _inject_in_memory_result(
        incompatible["id"], frame_count=2, metrics={"rmse": 1.0, "nse": 0.1}
    )
    rejected = await client.get(
        f"/api/simulations/{baseline['id']}/compare/{incompatible['id']}",
        headers=_headers("viewer"),
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "COMPARISON_INCOMPATIBLE"


@pytest.mark.asyncio
async def test_ac6_in_memory_comparison_handles_zero_baseline(client):
    config = _load("rainfall_50y_24h.json")
    network = await _create_network(client, "AC6-ZERO")
    scenario = await _create_rainfall(client, network, config, key="ac6-zero-rain")
    baseline = await _submit_simulation(
        client,
        network,
        scenario,
        key="ac6-zero-baseline",
        duration_seconds=300,
    )
    candidate = await _submit_simulation(
        client,
        network,
        scenario,
        key="ac6-zero-candidate",
        duration_seconds=300,
    )
    _inject_in_memory_result(
        baseline["id"], frame_count=2, metrics={"rmse": 0.0, "nse": 0.0}
    )
    _inject_in_memory_result(
        candidate["id"], frame_count=2, metrics={"rmse": 1.0, "nse": 1.0}
    )

    response = await client.get(
        f"/api/simulations/{baseline['id']}/compare/{candidate['id']}",
        headers=_headers("viewer"),
    )

    assert response.status_code == 200, response.text
    metrics = {item["key"]: item for item in response.json()["metrics"]}
    assert metrics["rmse"]["absolute_delta"] == pytest.approx(1.0)
    assert metrics["rmse"]["relative_delta"] is None


@pytest.mark.asyncio
async def test_ac6_comparison_rejects_metric_unit_mismatch(client):
    config = _load("rainfall_50y_24h.json")
    network = await _create_network(client, "AC6-UNITS")
    scenario = await _create_rainfall(client, network, config, key="ac6-units-rain")
    baseline = await _submit_simulation(
        client,
        network,
        scenario,
        key="ac6-units-baseline",
        duration_seconds=300,
    )
    candidate = await _submit_simulation(
        client,
        network,
        scenario,
        key="ac6-units-candidate",
        duration_seconds=300,
    )
    _inject_in_memory_result(
        baseline["id"],
        frame_count=2,
        metrics={"rmse": 1.0, "nse": 0.5},
        metric_units={"rmse": "m", "nse": "1"},
    )
    _inject_in_memory_result(
        candidate["id"],
        frame_count=2,
        metrics={"rmse": 1000.0, "nse": 0.6},
        metric_units={"rmse": "mm", "nse": "1"},
    )

    response = await client.get(
        f"/api/simulations/{baseline['id']}/compare/{candidate['id']}",
        headers=_headers("viewer"),
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "COMPARISON_INCOMPATIBLE"


def _parse_sse_events(payload: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in payload.replace("\r\n", "\n").strip().split("\n\n"):
        event_name = None
        event_id = None
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("id:"):
                event_id = line.removeprefix("id:").strip()
            elif line.startswith("data:"):
                data.append(line.removeprefix("data:").removeprefix(" "))
        if event_name is not None and data:
            events.append(
                {
                    "event": event_name,
                    "id": event_id,
                    "data": json.loads("\n".join(data)),
                }
            )
    return events


@pytest.mark.asyncio
async def test_ac7_legacy_api_and_sse_wire_contract_has_no_regression(
    client, monkeypatch
):
    from sse_starlette.sse import AppStatus

    from app.api import routes as legacy_routes

    monkeypatch.setattr(AppStatus, "should_exit", False)
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    legacy = _load("legacy_api.json")
    for entry in legacy["read_paths"]:
        response = await client.get(entry["path"])
        assert response.status_code == entry["status"], (entry["path"], response.text)
        assert set(entry["required_keys"]) <= set(response.json())

    cursors: list[str | None] = []
    expected_events = legacy["sse"]["events"]

    async def connect(last_event_id):
        cursors.append(last_event_id)
        return "legacy-client", object()

    async def events(_client_id, _queue, _last_event_id):
        yield {"retry": 3000}
        for item in expected_events:
            frame = {"event": item["name"], "data": json.dumps(item["data"])}
            if "id" in item:
                frame["id"] = item["id"]
            yield frame

    monkeypatch.setattr(legacy_routes.sse_manager, "connect", connect)
    monkeypatch.setattr(legacy_routes.sse_manager, "event_generator", events)
    sse = await client.get(
        legacy["sse"]["path"],
        headers={"Last-Event-ID": legacy["sse"]["last_event_id"]},
    )

    assert sse.status_code == 200
    assert sse.headers["content-type"].startswith(legacy["sse"]["content_type"])
    assert cursors == [legacy["sse"]["last_event_id"]]
    observed = _parse_sse_events(sse.text)
    assert [(item["event"], item["id"]) for item in observed] == [
        (item["name"], item.get("id")) for item in expected_events
    ]
    assert [item["data"] for item in observed] == [
        item["data"] for item in expected_events
    ]

    openapi = await client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    for path in legacy["required_openapi_paths"]:
        assert path in paths, path


@pytest.mark.asyncio
async def test_ac8_evidence_contract_requires_two_independent_profiles():
    requirements = _load("acceptance_requirements.json")
    matrix = _load("matrix.json")
    ac8 = next(case for case in matrix["cases"] if case["id"] == "AC-8")
    contract = requirements["cases"]["AC-8"]

    assert requirements["evidence_state"] == "NOT_RUN"
    assert requirements["statement"].endswith("cannot make an AC PASS.")
    assert contract["required_gates"] == ["g6"]
    assert contract["required_artifact_suffixes"] == [
        "g6/postgis/result.json",
        "g6/portable/result.json",
    ]
    assert "same RUN_ID and commit" in contract["required_observations"][-1]
    assert ac8["status"] == "NOT RUN"
    assert "Compose config" in ac8["reason"]
