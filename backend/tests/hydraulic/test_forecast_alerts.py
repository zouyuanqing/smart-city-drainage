"""Focused behavior tests for forecast alerts, suppression, and risk ranking."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import TracebackType
from typing import Self

import pytest

from app.services.hydraulic.forecast_alerts import (
    ForecastAlert,
    ForecastAlertService,
    ForecastAlertTransition,
    ForecastAlertWindow,
    ForecastBusinessEvent,
    ForecastCandidate,
    ForecastRuleRef,
    ProbabilityCalibrationRef,
    calculate_lead_time_minutes,
    calculate_suppression_key,
    canonical_suppression_payload,
)
from app.services.hydraulic.risk import RiskInputError, calculate_risk, rank_top_risk

UTC = timezone.utc
START = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
NETWORK_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
RULE_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
CALIBRATION_ID = uuid.UUID("00000000-0000-4000-8000-000000000003")
SIMULATION_ID = uuid.UUID("00000000-0000-4000-8000-000000000004")
REVISION_ID = uuid.UUID("00000000-0000-4000-8000-000000000005")
MANIFEST_ID = uuid.UUID("00000000-0000-4000-8000-000000000006")
TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000007")
OTHER_TARGET_ID = uuid.UUID("00000000-0000-4000-8000-000000000008")
HASH_A = "a" * 64
HASH_B = "b" * 64


class _ArtifactResolver:
    def verify(self, *, artifact_sha256: str, **_kwargs: object) -> dict[str, object]:
        return {"sha256": artifact_sha256, "schema_version": 1}


_ARTIFACT_RESOLVER = _ArtifactResolver()


class _Transaction:
    def __init__(self, store: "_Store") -> None:
        self.store = store

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def lock_event_window(
        self, suppression_key: str, candidate_at: datetime
    ) -> ForecastAlertWindow:
        window = self.store.windows.get(suppression_key)
        if window is None:
            window = ForecastAlertWindow(
                suppression_key=suppression_key,
                active_alert_id=None,
                cooldown_until=None,
                last_candidate_at=candidate_at,
                last_qualifying_at=None,
                suppressed_count=0,
                version=0,
                created_at=candidate_at,
                updated_at=candidate_at,
            )
            self.store.windows[suppression_key] = window
        return window

    def get_alert(self, alert_id: uuid.UUID) -> ForecastAlert | None:
        return self.store.alerts.get(alert_id)

    def insert_alert(self, alert: ForecastAlert) -> None:
        assert alert.id not in self.store.alerts
        self.store.alerts[alert.id] = alert

    def update_alert(self, alert: ForecastAlert, *, expected_version: int) -> None:
        current = self.store.alerts[alert.id]
        assert current.version == expected_version
        self.store.alerts[alert.id] = alert

    def update_event_window(
        self, window: ForecastAlertWindow, *, expected_version: int
    ) -> None:
        current = self.store.windows[window.suppression_key]
        assert current.version == expected_version
        self.store.windows[window.suppression_key] = window

    def insert_transition(self, transition: ForecastAlertTransition) -> None:
        self.store.transitions.append(transition)

    def insert_outbox(self, event: ForecastBusinessEvent) -> None:
        self.store.events.append(event)


class _Store:
    def __init__(self) -> None:
        self.alerts: dict[uuid.UUID, ForecastAlert] = {}
        self.windows: dict[str, ForecastAlertWindow] = {}
        self.transitions: list[ForecastAlertTransition] = []
        self.events: list[ForecastBusinessEvent] = []

    def transaction(self) -> _Transaction:
        return _Transaction(self)


def _rule(
    *,
    threshold: float = 0.30,
    refresh_confidence_delta: float = 0.05,
    refresh_peak_delta: float = 0.05,
    refresh_risk_delta: float = 1.0,
) -> ForecastRuleRef:
    return ForecastRuleRef(
        id=RULE_ID,
        artifact_sha256=HASH_A,
        version="rules-v1",
        alert_type="flood_forecast",
        metric="water_depth_m",
        operator=">=",
        threshold_value=threshold,
        threshold_unit="m",
        risk_scale=0.30,
        refresh_expected_shift_steps=1,
        refresh_peak_delta=refresh_peak_delta,
        refresh_confidence_delta=refresh_confidence_delta,
        refresh_risk_delta=refresh_risk_delta,
        valid_from=START - timedelta(days=1),
        valid_until=START + timedelta(days=1),
    )


def _calibration() -> ProbabilityCalibrationRef:
    return ProbabilityCalibrationRef(
        id=CALIBRATION_ID,
        artifact_sha256=HASH_B,
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
    target_id: uuid.UUID = TARGET_ID,
    expected_at: datetime = START + timedelta(minutes=35),
    depth_m: float = 0.42,
    confidence: float = 0.86,
    level: str = "warning",
    simulation_id: uuid.UUID = SIMULATION_ID,
) -> ForecastCandidate:
    return ForecastCandidate(
        network_id=NETWORK_ID,
        nowcast_run_id=uuid.UUID(int=nowcast_number),
        issued_at=issued_at,
        simulation_id=simulation_id,
        network_revision_id=REVISION_ID,
        target_manifest_id=MANIFEST_ID,
        target_manifest_sha256=HASH_A,
        alert_type="flood_forecast",
        target_type="node",
        target_id=target_id,
        target_stable_key=f"node-{target_id.int:04d}",
        expected_at=expected_at,
        depth_m=depth_m,
        confidence=confidence,
        level=level,
        target_manifest={
            "target_ids": {
                "node": {f"node-{target_id.int:04d}": str(target_id)},
            }
        },
    )


def _pipe_candidate(nowcast_number: int = 1) -> ForecastCandidate:
    return ForecastCandidate(
        network_id=NETWORK_ID,
        nowcast_run_id=uuid.UUID(int=nowcast_number),
        issued_at=START,
        simulation_id=SIMULATION_ID,
        network_revision_id=REVISION_ID,
        target_manifest_id=MANIFEST_ID,
        target_manifest_sha256=HASH_A,
        alert_type="pipe_surcharge",
        target_type="pipe_segment",
        target_id=TARGET_ID,
        target_stable_key="pipe-0007",
        expected_at=START + timedelta(minutes=25),
        depth_m=0.08,
        fill_ratio=0.97,
        confidence=0.91,
        level="critical",
        target_manifest={
            "target_ids": {
                "pipe_segment": {"pipe-0007": str(TARGET_ID)},
            }
        },
    )


def _pipe_rule() -> ForecastRuleRef:
    return replace(
        _rule(),
        alert_type="pipe_surcharge",
        metric="fill_ratio",
        threshold_value=0.90,
        threshold_unit="ratio",
    )


def _pipe_calibration() -> ProbabilityCalibrationRef:
    return replace(_calibration(), metric="fill_ratio")


def test_suppression_key_uses_only_the_frozen_canonical_identity() -> None:
    payload = canonical_suppression_payload(
        network_id=NETWORK_ID,
        alert_type="flood_forecast",
        target_type="node",
        target_id=TARGET_ID,
        rule_id=RULE_ID,
    )
    expected_bytes = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    key = calculate_suppression_key(
        network_id=NETWORK_ID,
        alert_type="flood_forecast",
        target_type="node",
        target_id=TARGET_ID,
        rule_id=RULE_ID,
    )

    assert key == hashlib.sha256(expected_bytes).hexdigest()
    assert set(payload) == {
        "schema",
        "network_id",
        "alert_type",
        "target_type",
        "target_id",
        "rule_id",
    }
    assert "simulation_id" not in payload
    assert "issued_at" not in payload
    assert "confidence" not in payload


def test_lead_time_floors_seconds_and_rejects_beyond_two_hours() -> None:
    assert (
        calculate_lead_time_minutes(
            issued_at=START,
            expected_at=START + timedelta(minutes=35, seconds=59),
        )
        == 35
    )
    assert (
        calculate_lead_time_minutes(
            issued_at=START,
            expected_at=START - timedelta(minutes=1),
        )
        == 0
    )
    with pytest.raises(ValueError, match="within two hours"):
        calculate_lead_time_minutes(
            issued_at=START,
            expected_at=START + timedelta(hours=2, seconds=1),
        )


@pytest.mark.parametrize("pipe", [False, True])
def test_publish_creates_both_forecast_types_with_traceable_fields(pipe: bool) -> None:
    store = _Store()
    service = ForecastAlertService(store, artifact_resolver=_ARTIFACT_RESOLVER)
    candidate = _pipe_candidate() if pipe else _candidate()
    rule = _pipe_rule() if pipe else _rule()
    calibration = _pipe_calibration() if pipe else _calibration()

    result = service.publish(candidate, rule, calibration)

    assert result.action == "created"
    assert result.alert is not None
    alert = result.alert
    assert alert.alert_type == ("pipe_surcharge" if pipe else "flood_forecast")
    assert alert.lead_time_minutes == (25 if pipe else 35)
    assert alert.depth_m == (0.08 if pipe else 0.42)
    assert alert.measurements["fill_ratio"] == (0.97 if pipe else None)
    assert alert.confidence == (0.91 if pipe else 0.86)
    assert alert.simulation_id == SIMULATION_ID
    assert alert.phase == "predicted"
    assert alert.status == "open"
    assert result.event is not None
    assert result.event.event_type == "alert.created"


def test_active_window_updates_confidence_without_creating_another_alert() -> None:
    store = _Store()
    service = ForecastAlertService(store, artifact_resolver=_ARTIFACT_RESOLVER)
    rule = _rule()
    calibration = _calibration()
    created = service.publish(_candidate(), rule, calibration)
    assert created.alert is not None

    refreshed = service.publish(
        _candidate(
            nowcast_number=2,
            issued_at=START + timedelta(seconds=1),
            confidence=0.88,
            simulation_id=uuid.UUID(int=200),
        ),
        rule,
        calibration,
    )

    assert refreshed.action == "refreshed"
    assert refreshed.alert is not None
    assert refreshed.alert.id == created.alert.id
    assert refreshed.alert.confidence == 0.88
    assert refreshed.alert.simulation_id == uuid.UUID(int=200)
    assert refreshed.alert.lead_time_minutes == 34
    assert refreshed.alert.version == 2
    assert refreshed.event is None
    assert len(store.alerts) == 1
    assert len(store.transitions) == 2


def test_same_nowcast_replay_is_idempotent_but_changed_payload_conflicts() -> None:
    store = _Store()
    service = ForecastAlertService(store, artifact_resolver=_ARTIFACT_RESOLVER)
    rule = _rule()
    calibration = _calibration()
    first = service.publish(_candidate(), rule, calibration)
    assert first.alert is not None

    replay = service.publish(_candidate(), rule, calibration)
    assert replay.action == "unchanged"
    assert replay.alert == first.alert
    assert len(store.alerts) == 1
    assert len(store.transitions) == 1
    assert len(store.events) == 1

    with pytest.raises(ValueError, match="same nowcast run"):
        service.publish(_candidate(confidence=0.90), rule, calibration)


def test_active_refresh_never_downgrades_alert_level() -> None:
    store = _Store()
    service = ForecastAlertService(store, artifact_resolver=_ARTIFACT_RESOLVER)
    rule = _rule()
    calibration = _calibration()
    created = service.publish(_candidate(level="critical"), rule, calibration)
    assert created.alert is not None

    refreshed = service.publish(
        _candidate(
            nowcast_number=2,
            issued_at=START + timedelta(seconds=1),
            level="warning",
            simulation_id=uuid.UUID(int=201),
        ),
        rule,
        calibration,
    )

    assert refreshed.alert is not None
    assert refreshed.alert.id == created.alert.id
    assert refreshed.alert.level == "critical"
    assert refreshed.event is None


def test_terminal_cooldown_suppresses_inside_and_creates_at_exact_boundary() -> None:
    store = _Store()
    service = ForecastAlertService(store, artifact_resolver=_ARTIFACT_RESOLVER)
    rule = _rule()
    calibration = _calibration()
    first = service.publish(_candidate(), rule, calibration)
    assert first.alert is not None
    terminal_at = START + timedelta(minutes=5)
    terminal = service.mark_terminal(
        alert_id=first.alert.id,
        expected_version=first.alert.version,
        terminal_status="resolved",
        occurred_at=terminal_at,
        reason="clear telemetry debounce satisfied",
    )
    assert terminal.action == "terminal"
    assert terminal.window.cooldown_until == terminal_at + timedelta(minutes=30)

    inside = service.publish(
        _candidate(
            nowcast_number=2,
            issued_at=terminal_at + timedelta(minutes=29, seconds=59),
        ),
        rule,
        calibration,
    )
    assert inside.action == "suppressed"
    assert inside.alert is None
    assert inside.window.suppressed_count == 1
    assert len(store.alerts) == 1

    boundary = service.publish(
        _candidate(
            nowcast_number=3,
            issued_at=terminal_at + timedelta(minutes=30),
            simulation_id=uuid.UUID(int=300),
        ),
        rule,
        calibration,
    )
    assert boundary.action == "created"
    assert boundary.alert is not None
    assert boundary.alert.id != first.alert.id
    assert len(store.alerts) == 2


def test_top_n_ranks_active_devices_by_risk_and_filters_stale_sources() -> None:
    store = _Store()
    service = ForecastAlertService(store, artifact_resolver=_ARTIFACT_RESOLVER)
    rule = _rule()
    calibration = _calibration()
    low = service.publish(
        _candidate(target_id=TARGET_ID, depth_m=0.31, confidence=0.70),
        rule,
        calibration,
    )
    high = service.publish(
        _candidate(
            target_id=OTHER_TARGET_ID,
            depth_m=0.75,
            confidence=0.95,
            level="critical",
        ),
        rule,
        calibration,
    )
    assert low.alert is not None and high.alert is not None
    assert low.alert.id != high.alert.id

    ranking = rank_top_risk(
        list(store.alerts.values()),
        top_n=2,
        network_id=NETWORK_ID,
        as_of=START + timedelta(minutes=1),
    )

    assert [item.alert.id for item in ranking] == [high.alert.id, low.alert.id]
    assert ranking[0].score_components.confidence == 0.95
    assert ranking[0].score_components.severity == 1.0
    assert ranking[0].to_dict()["policy_version"] == "risk-v1"

    assert (
        rank_top_risk(
            list(store.alerts.values()),
            top_n=2,
            network_id=NETWORK_ID,
            as_of=START + timedelta(minutes=15, seconds=1),
        )
        == []
    )


def test_risk_formula_rejects_invalid_values_instead_of_sanitizing() -> None:
    assessment = calculate_risk(
        confidence=0.86,
        level="warning",
        phase="predicted",
        lead_time_minutes=35,
        metric_value=0.42,
        threshold_value=0.30,
        risk_scale=0.30,
    )

    assert assessment.score == 68.27
    assert assessment.components.urgency == pytest.approx(1 - 35 / 120)
    assert assessment.components.impact == pytest.approx(0.40)
    with pytest.raises(RiskInputError):
        calculate_risk(
            confidence=float("nan"),
            level="warning",
            phase="predicted",
            lead_time_minutes=35,
            metric_value=0.42,
            threshold_value=0.30,
            risk_scale=0.30,
        )
