"""Deterministic nowcast scheduler and initialization tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.hydraulic.nowcast import (
    CalibratedModel,
    InitialStateAssimilator,
    ManualClock,
    NowcastConfig,
    NowcastScheduler,
    SensorReading,
)

UTC = timezone.utc
START = datetime(2026, 9, 25, 0, 0, tzinfo=UTC)
NETWORK_ID = "network-demo"
LINEAGE = {
    "network_revision_id": "00000000-0000-4000-8000-000000000001",
    "target_manifest_id": "00000000-0000-4000-8000-000000000002",
    "target_manifest_sha256": "a" * 64,
    "rainfall_scenario_id": "00000000-0000-4000-8000-000000000003",
    "solver_id": "python-fallback",
    "solver_version": "1",
    "flood_forecast_probability_calibration_id": "00000000-0000-4000-8000-000000000004",
    "flood_forecast_probability_sha256": "a" * 64,
    "pipe_surcharge_probability_calibration_id": "00000000-0000-4000-8000-000000000005",
    "pipe_surcharge_probability_sha256": "a" * 64,
    "flood_forecast_rule_id": "00000000-0000-4000-8000-000000000006",
    "flood_forecast_rule_sha256": "a" * 64,
    "pipe_surcharge_rule_id": "00000000-0000-4000-8000-000000000007",
    "pipe_surcharge_rule_sha256": "a" * 64,
}


def _model() -> CalibratedModel:
    return CalibratedModel(
        model_id="calibrated-v3",
        model_version="3.2.1",
        calibration_id="calibration-42",
        parameter_set_id="parameter-set-1",
        parameter_set_sha256="a" * 64,
        initial_state={
            "N-001": {"depth_m": 0.01},
            "N-002": {"depth_m": 0.02},
        },
    )


def test_default_cycle_config_is_ten_minutes_zero_to_two_hours_five_minute_grid() -> (
    None
):
    config = NowcastConfig()

    assert config.interval_minutes == 10
    assert config.horizon_minutes == 120
    assert config.step_minutes == 5
    assert config.deadline_seconds == 180
    assert config.frame_count == 25


@pytest.mark.parametrize(
    "kwargs",
    [
        {"horizon_minutes": 121},
        {"step_minutes": 1},
        {"horizon_minutes": 7},
        {"deadline_seconds": 181},
        {"retry_backoff_seconds": (15, 5)},
    ],
)
def test_cycle_config_rejects_contract_breaking_values(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        NowcastConfig(**kwargs)


def test_virtual_clock_drives_aligned_horizon_and_repeated_trigger_replays_slot() -> (
    None
):
    clock = ManualClock(START)
    ids = iter(("00000000-0000-0000-0000-000000000001",))
    calls: list[str] = []

    def model_provider(*, network_id: str, as_of: datetime) -> CalibratedModel:
        calls.append(f"{network_id}:{as_of.isoformat()}")
        return _model()

    scheduler = NowcastScheduler(
        NowcastConfig(),
        clock=clock,
        model_provider=model_provider,
        lineage=LINEAGE,
        id_factory=lambda: next(ids),
    )

    assert scheduler.next_run_at(clock.now()) == START + timedelta(minutes=10)
    assert scheduler.next_run_at(START + timedelta(minutes=3)) == START + timedelta(
        minutes=10
    )

    clock.set(START + timedelta(minutes=10))
    first = scheduler.run_due(NETWORK_ID)
    assert first.created is True
    assert first.duplicate is False
    assert first.task_id != first.id or first.id == first.task_id
    assert first.scheduled_for == START + timedelta(minutes=10)
    assert first.forecast_origin_at == first.scheduled_for
    assert first.deadline_at == first.scheduled_for + timedelta(seconds=180)
    assert first.horizon[0] == START + timedelta(minutes=10)
    assert first.horizon[-1] == START + timedelta(hours=2, minutes=10)
    assert len(first.horizon) == 25
    assert all(
        right - left == timedelta(minutes=5)
        for left, right in zip(first.horizon, first.horizon[1:])
    )

    # The same due slot is a replay, not a second task or a second model call.
    duplicate = scheduler.run_due(NETWORK_ID)
    assert duplicate.created is False
    assert duplicate.duplicate is True
    assert duplicate.task_id == first.task_id
    assert calls == [f"{NETWORK_ID}:{(START + timedelta(minutes=10)).isoformat()}"]
    assert len(scheduler.list_runs(NETWORK_ID)) == 1


def test_next_network_slot_is_not_replayed_after_advancing_virtual_clock() -> None:
    clock = ManualClock(START)
    ids = iter(
        (
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        )
    )
    scheduler = NowcastScheduler(
        clock=clock,
        model=_model(),
        lineage=LINEAGE,
        id_factory=lambda: next(ids),
    )

    first = scheduler.run_due(NETWORK_ID)
    clock.advance(minutes=10)
    second = scheduler.run_due(NETWORK_ID)

    assert first.task_id != second.task_id
    assert second.scheduled_for == START + timedelta(minutes=10)
    assert len(scheduler.list_runs(NETWORK_ID)) == 2


def test_assimilation_uses_latest_verified_sensor_and_reports_missing_targets() -> None:
    clock = ManualClock(START + timedelta(minutes=10))
    assimilator = InitialStateAssimilator(clock=clock, observation_lookback_seconds=300)
    network = {"node_ids": ["N-001", "N-002"]}
    observations = (
        SensorReading(
            "N-001",
            START + timedelta(minutes=5),
            0.12,
            source_event_id="event-n001-05",
            trust_level="external_verified",
            auth_verified=True,
            binding_verified=True,
        ),
        SensorReading(
            "N-001",
            START + timedelta(minutes=9),
            0.18,
            source_event_id="event-n001-09",
            trust_level="external_verified",
            auth_verified=True,
            binding_verified=True,
        ),
        # This is outside the lookback window and must not be assimilated.
        SensorReading(
            "N-002",
            START,
            0.9,
            source_event_id="event-n002-stale",
            trust_level="external_verified",
            auth_verified=True,
            binding_verified=True,
        ),
        # Future data must never leak into the current initial state.
        SensorReading(
            "N-001",
            START + timedelta(minutes=11),
            99.0,
            source_event_id="event-n001-future",
            trust_level="external_verified",
            auth_verified=True,
            binding_verified=True,
        ),
        # An unverified point is diagnostic only, not formal assimilation.
        SensorReading(
            "N-002",
            START + timedelta(minutes=9),
            0.8,
            trust_level="external_unverified",
            auth_verified=False,
            binding_verified=False,
        ),
    )

    state = assimilator.assimilate(
        network,
        observations,
        as_of=clock.now(),
        model=_model(),
    )

    assert state.kind == "warm_state"
    assert state.observed_as_of == START + timedelta(minutes=9)
    assert state.node_states["N-001"]["depth_m"] == pytest.approx(0.18)
    assert state.node_states["N-001"]["source"] == "sensor"
    assert state.node_states["N-002"]["depth_m"] == pytest.approx(0.02)
    assert state.node_states["N-002"]["source"] == "model"
    assert state.missing_targets == ("N-002",)
    assert state.is_complete is False
    assert "future:N-001" in state.rejected_observations
    assert "stale:N-002" in state.rejected_observations
    assert "unverified:N-002" in state.rejected_observations
    assert len(state.initial_state_content_sha256) == 64


def test_scheduler_passes_observations_and_model_into_frozen_request() -> None:
    clock = ManualClock(START)
    model = _model()
    scheduler = NowcastScheduler(
        clock=clock,
        model=model,
        lineage=LINEAGE,
        observation_provider=lambda _network_id, _as_of: (
            SensorReading(
                "N-001",
                START,
                0.25,
                source_event_id="event-n001-initial",
                trust_level="external_verified",
                auth_verified=True,
                binding_verified=True,
            ),
        ),
    )

    outcome = scheduler.run_due(NETWORK_ID, now=clock.now())

    assert outcome.created is True
    assert outcome.model is model
    assert outcome.initial_state.node_states["N-001"]["depth_m"] == pytest.approx(0.25)
    assert outcome.initial_state.source_event_ids == ("event-n001-initial",)
    assert outcome.request_fingerprint


def test_model_state_supplies_targets_when_sensor_stream_is_empty() -> None:
    scheduler = NowcastScheduler(
        clock=ManualClock(START),
        model=_model(),
        lineage=LINEAGE,
    )

    outcome = scheduler.run_due(NETWORK_ID, now=START)

    assert outcome.initial_state.kind == "warm_state"
    assert set(outcome.initial_state.node_states) == {"N-001", "N-002"}
    assert outcome.initial_state.missing_targets == ("N-001", "N-002")
    assert outcome.initial_state.node_states["N-001"]["depth_m"] == pytest.approx(0.01)


def test_missing_sensor_coverage_can_be_required_explicitly() -> None:
    assimilator = InitialStateAssimilator(
        clock=ManualClock(START),
        require_complete=True,
    )

    with pytest.raises(Exception) as error:
        assimilator.assimilate(
            {"node_ids": ["N-001", "N-002"]},
            (),
            as_of=START,
            model=_model(),
        )

    assert getattr(error.value, "code", "") == "SENSOR_DATA_UNAVAILABLE"
    assert getattr(error.value, "missing_targets", ()) == ("N-001", "N-002")
