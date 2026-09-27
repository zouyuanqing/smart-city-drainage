"""Round-one regression checks for forecast fact and audit boundaries."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services.hydraulic.forecast_alerts import (
    ForecastAlertError,
    ForecastAlertService,
    ForecastCandidate,
    ForecastRuleRef,
    ForecastTransitionError,
    InMemoryForecastAlertStore,
    ProbabilityCalibrationRef,
)
from app.services.hydraulic.nowcast import (
    CalibratedModel,
    ForecastPolicyUnavailable,
    InitialState,
    InitialStateAssimilator,
    ManualClock,
    ModelNotCalibrated,
    NowcastScheduler,
    SensorReading,
)
from app.services.hydraulic.risk import calculate_risk, rank_top_risk
from app.services.notification_service import NotificationService
from app.services.sse_manager import SSEManager

UTC = timezone.utc
START = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
HASH = "a" * 64
OTHER_HASH = "b" * 64
NETWORK_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
RULE_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
CALIBRATION_ID = uuid.UUID("00000000-0000-4000-8000-000000000003")
SIMULATION_ID = uuid.UUID("00000000-0000-4000-8000-000000000004")
REVISION_ID = uuid.UUID("00000000-0000-4000-8000-000000000005")
MANIFEST_ID = uuid.UUID("00000000-0000-4000-8000-000000000006")
TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000007")
LINEAGE = {
    "network_revision_id": str(REVISION_ID),
    "target_manifest_id": str(MANIFEST_ID),
    "target_manifest_sha256": HASH,
    "rainfall_scenario_id": str(uuid.UUID("00000000-0000-4000-8000-000000000008")),
    "solver_id": "python-fallback",
    "solver_version": "1",
    "flood_forecast_probability_calibration_id": str(
        uuid.UUID("00000000-0000-4000-8000-000000000009")
    ),
    "flood_forecast_probability_sha256": HASH,
    "pipe_surcharge_probability_calibration_id": str(
        uuid.UUID("00000000-0000-4000-8000-000000000010")
    ),
    "pipe_surcharge_probability_sha256": HASH,
    "flood_forecast_rule_id": str(RULE_ID),
    "flood_forecast_rule_sha256": HASH,
    "pipe_surcharge_rule_id": str(uuid.UUID("00000000-0000-4000-8000-000000000011")),
    "pipe_surcharge_rule_sha256": HASH,
}


class _Artifacts:
    def verify(self, *, artifact_sha256: str, **_kwargs: object) -> dict[str, object]:
        return {"sha256": artifact_sha256, "schema_version": 1}


def _model() -> CalibratedModel:
    return CalibratedModel(
        model_id="model",
        model_version="1",
        calibration_id="calibration",
        parameter_set_id="parameters",
        parameter_set_sha256=HASH,
        initial_state={"N-001": {"depth_m": 0.01}},
    )


def _rule() -> ForecastRuleRef:
    return ForecastRuleRef(
        id=RULE_ID,
        artifact_sha256=HASH,
        version="rules-v1",
        alert_type="flood_forecast",
        metric="water_depth_m",
        operator=">=",
        threshold_value=0.30,
        threshold_unit="m",
        risk_scale=0.30,
        valid_from=START - timedelta(days=1),
        valid_until=START + timedelta(days=1),
    )


def _calibration() -> ProbabilityCalibrationRef:
    return ProbabilityCalibrationRef(
        id=CALIBRATION_ID,
        artifact_sha256=OTHER_HASH,
        method="ensemble_exceedance_frequency",
        metric="water_depth_m",
        lead_min_minutes=0,
        lead_max_minutes=120,
        magnitude_min=0.0,
        magnitude_max=2.0,
        valid_from=START - timedelta(days=1),
        valid_until=START + timedelta(days=1),
    )


def _candidate(
    *,
    nowcast_number: int = 1,
    issued_at: datetime = START,
    confidence: float = 0.86,
    depth: float = 0.42,
) -> ForecastCandidate:
    target = TARGET_ID
    stable_key = f"node-{target.int:04d}"
    return ForecastCandidate(
        network_id=NETWORK_ID,
        nowcast_run_id=uuid.UUID(int=nowcast_number),
        issued_at=issued_at,
        simulation_id=SIMULATION_ID,
        network_revision_id=REVISION_ID,
        target_manifest_id=MANIFEST_ID,
        target_manifest_sha256=HASH,
        alert_type="flood_forecast",
        target_type="node",
        target_id=target,
        target_stable_key=stable_key,
        expected_at=START + timedelta(minutes=35),
        depth_m=depth,
        confidence=confidence,
        level="warning",
        target_manifest={"target_ids": {"node": {stable_key: str(target)}}},
    )


def _service() -> ForecastAlertService:
    return ForecastAlertService(
        InMemoryForecastAlertStore(), artifact_resolver=_Artifacts()
    )


def test_nowcast_rejects_untrusted_and_requires_lineage() -> None:
    assimilator = InitialStateAssimilator(clock=ManualClock(START))
    state = assimilator.assimilate(
        {"node_ids": ["N-001"]},
        [{"source": "mock", "target_id": "N-001", "observed_at": START, "value": 0.2}],
        as_of=START,
        model=_model(),
    )
    assert state.missing_targets == ("N-001",)
    assert (
        "unverified:N-001" in state.rejected_observations
        or "invalid:N-001" in state.rejected_observations
    )
    with pytest.raises((ModelNotCalibrated, ForecastPolicyUnavailable)):
        NowcastScheduler(
            clock=ManualClock(START), network={"node_ids": ["N-001"]}, model=_model()
        ).run_due("network", now=START)
    run = NowcastScheduler(
        clock=ManualClock(START),
        network={"node_ids": ["N-001"]},
        model=_model(),
        lineage=LINEAGE,
    ).run_due("network", now=START)
    assert run.lineage_complete


def test_initial_state_is_deeply_frozen_and_hash_checked() -> None:
    state = InitialState(kind="warm_state", node_states={"N": {"depth_m": 0.1}})
    with pytest.raises(TypeError):
        state.node_states["N"]["depth_m"] = 9.0  # type: ignore[index]
    with pytest.raises(ValueError):
        InitialState(
            kind="warm_state",
            node_states={"N": {"depth_m": 0.1}},
            content_sha256=OTHER_HASH,
        )


@pytest.mark.asyncio
async def test_async_duplicate_does_not_call_providers_twice() -> None:
    calls = {"model": 0, "observations": 0}

    async def model_provider(**_kwargs: object) -> CalibratedModel:
        calls["model"] += 1
        return _model()

    async def observation_provider(**_kwargs: object) -> tuple[SensorReading, ...]:
        calls["observations"] += 1
        return ()

    scheduler = NowcastScheduler(
        clock=ManualClock(START),
        network={"node_ids": ["N-001"]},
        model_provider=model_provider,
        observation_provider=observation_provider,
        lineage=LINEAGE,
    )
    first = await scheduler.run_once_async("network", now=START)
    second = await scheduler.run_once_async("network", now=START)
    assert first.created and not second.created
    assert calls == {"model": 1, "observations": 1}


def test_alert_event_lineage_expiry_and_idempotency() -> None:
    service = _service()
    created = service.publish(_candidate(), _rule(), _calibration())
    assert created.event is not None
    payload = created.event.payload()["alert"]
    assert payload["level"] == "warning"
    assert payload["risk_score"] == created.alert.risk_score
    with pytest.raises(ForecastTransitionError):
        service.mark_terminal(
            alert_id=created.alert.id,
            expected_version=created.alert.version,
            terminal_status="expired",
            occurred_at=START + timedelta(minutes=5),
            reason="too early",
        )
    service.mark_terminal(
        alert_id=created.alert.id,
        expected_version=created.alert.version,
        terminal_status="resolved",
        occurred_at=START + timedelta(minutes=35),
        reason="clear",
    )
    replay = service.publish(_candidate(), _rule(), _calibration())
    assert replay.action == "unchanged"
    assert replay.window.suppressed_count == 0


def test_alert_rejects_forged_level_and_policy_lineage_change() -> None:
    service = _service()
    with pytest.raises(ForecastAlertError, match="level"):
        service.publish(
            replace(_candidate(depth=0.31), level="critical"), _rule(), _calibration()
        )
    service.publish(_candidate(), _rule(), _calibration())
    with pytest.raises(ForecastAlertError, match="immutable"):
        service.publish(
            _candidate(nowcast_number=2, issued_at=START + timedelta(seconds=1)),
            replace(_rule(), artifact_sha256=OTHER_HASH),
            _calibration(),
        )


def test_risk_normalizes_network_and_rejects_unknown_policy() -> None:
    alert = _service().publish(_candidate(), _rule(), _calibration()).alert
    assert rank_top_risk([alert], network_id=str(NETWORK_ID), as_of=START)
    with pytest.raises(ValueError):
        calculate_risk(
            confidence=0.8,
            level="warning",
            phase="predicted",
            lead_time_minutes=10,
            metric_value=0.4,
            threshold_value=0.3,
            risk_scale=0.3,
            policy_version="unregistered-v99",
        )


@pytest.mark.asyncio
async def test_sse_rejects_unknown_schema_and_reports_overflow_range() -> None:
    manager = SSEManager(forecast_queue_size=1)
    network_id = str(NETWORK_ID)
    client_id, queue = await manager.connect_forecast("0", network_id=network_id)
    with pytest.raises(ValueError):
        await manager.broadcast_forecast_event(
            {
                "schema_version": 999,
                "event_id": "bad",
                "sequence": 1,
                "event_type": "alert.created",
            }
        )
    event = {
        "schema_version": 1,
        "event_id": "one",
        "sequence": 1,
        "event_type": "alert.created",
        "occurred_at": "2026-09-25T08:00:00Z",
        "network_id": network_id,
        "nowcast_run_id": "run",
        "simulation_id": "simulation",
        "alert": {
            "id": "alert",
            "type": "flood_forecast",
            "status": "open",
            "phase": "predicted",
            "level": "warning",
            "target": {"type": "node", "id": "N"},
            "expected_at": "2026-09-25T08:35:00Z",
            "confidence": 0.8,
            "risk_score": 70,
            "version": 1,
        },
    }
    await manager.broadcast_forecast_event(event)
    await manager.broadcast_forecast_event({**event, "event_id": "two", "sequence": 2})
    stream = manager.forecast_event_generator(client_id, queue, "0")
    assert (await anext(stream))["retry"] == 3000
    resync = json.loads((await anext(stream))["data"])
    assert resync["from_sequence"] == 1
    assert resync["to_sequence"] == 2
    await stream.aclose()


def test_webhook_dns_rebinding_is_rejected() -> None:
    assert NotificationService._is_https_webhook("https://127.0.0.1.nip.io") is False
