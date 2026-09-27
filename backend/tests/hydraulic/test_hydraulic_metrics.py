"""Focused tests for hydraulic Prometheus metrics and structured logs."""

from __future__ import annotations

import json
import logging
import uuid

import pytest
from prometheus_client import CollectorRegistry, generate_latest
from pythonjsonlogger.jsonlogger import JsonFormatter

from app.services.hydraulic.metrics import (
    CONTRACT_METRIC_NAMES,
    HydraulicMetrics,
    hydraulic_metrics,
    log_simulation_event,
)

SIMULATION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OTHER_SIMULATION_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry(auto_describe=True)


@pytest.fixture
def metrics(registry: CollectorRegistry) -> HydraulicMetrics:
    return HydraulicMetrics(registry)


def _samples(registry: CollectorRegistry) -> list:
    return [sample for family in registry.collect() for sample in family.samples]


def _sample_value(
    registry: CollectorRegistry,
    name: str,
    **labels: str,
) -> float:
    matches = [
        sample
        for sample in _samples(registry)
        if sample.name == name
        and all(sample.labels.get(key) == str(value) for key, value in labels.items())
    ]
    assert len(matches) == 1, (name, labels, matches)
    return float(matches[0].value)


def test_terminal_recording_updates_duration_failure_frame_and_result_metrics(
    metrics: HydraulicMetrics,
    registry: CollectorRegistry,
) -> None:
    metrics.record_simulation_started(
        simulation_id=SIMULATION_ID,
        solver_id="py_mw",
        attempt=1,
    )
    metrics.record_simulation_terminal(
        simulation_id=SIMULATION_ID,
        solver_id="py_mw",
        status="completed",
        duration_seconds=12.5,
        result_frames=289,
        result_bytes=4_194_304,
        attempt=1,
    )
    metrics.record_simulation_terminal(
        simulation_id=OTHER_SIMULATION_ID,
        solver_id="py_dw",
        status="failed",
        duration_seconds=2.25,
        error_code="MASS_BALANCE_FAILED",
        result_frames=0,
        result_bytes=0,
        attempt=3,
    )

    assert (
        _sample_value(
            registry,
            "simulation_duration_seconds_count",
            solver="py_mw",
            status="completed",
        )
        == 1
    )
    assert _sample_value(
        registry,
        "simulation_duration_seconds_sum",
        solver="py_mw",
        status="completed",
    ) == pytest.approx(12.5)
    assert (
        _sample_value(
            registry,
            "simulation_duration_seconds_count",
            solver="py_dw",
            status="failed",
        )
        == 1
    )
    assert _sample_value(
        registry,
        "simulation_duration_seconds_sum",
        solver="py_dw",
        status="failed",
    ) == pytest.approx(2.25)
    assert (
        _sample_value(
            registry,
            "simulation_failures_total",
            error_code="MASS_BALANCE_FAILED",
        )
        == 1
    )
    assert _sample_value(registry, "simulation_frames_total") == 289
    assert (
        _sample_value(
            registry,
            "simulation_result_size_bytes_count",
            solver="py_mw",
            status="completed",
        )
        == 1
    )
    assert _sample_value(
        registry,
        "simulation_result_size_bytes_sum",
        solver="py_mw",
        status="completed",
    ) == pytest.approx(4_194_304)


def test_cancel_timeout_retry_and_fallback_counters_are_independent(
    metrics: HydraulicMetrics,
    registry: CollectorRegistry,
) -> None:
    metrics.record_simulation_terminal(
        simulation_id=SIMULATION_ID,
        solver_id="swmm_kw",
        status="cancelled",
        duration_seconds=1,
    )
    metrics.record_simulation_terminal(
        simulation_id=OTHER_SIMULATION_ID,
        solver_id="swmm_kw",
        status="timed_out",
        duration_seconds=600,
    )
    metrics.record_simulation_retry(
        simulation_id=SIMULATION_ID,
        reason="lease_expired",
    )
    metrics.record_solver_fallback(
        simulation_id=SIMULATION_ID,
        from_solver="swmm_kw",
        to_solver="py_mw",
    )

    assert _sample_value(registry, "simulation_cancelled_total") == 1
    assert _sample_value(registry, "simulation_timeout_total") == 1
    assert (
        _sample_value(
            registry,
            "simulation_retries_total",
            reason="lease_expired",
        )
        == 1
    )
    assert (
        _sample_value(
            registry,
            "solver_fallback_total",
            **{"from": "swmm_kw", "to": "py_mw"},
        )
        == 1
    )


def test_queue_depth_age_and_running_slot_snapshots_are_exposed(
    metrics: HydraulicMetrics,
    registry: CollectorRegistry,
) -> None:
    metrics.set_queue_state(depth=3, oldest_queued_seconds=17.25)
    metrics.set_active_slots(general=1, nowcast=1)

    assert _sample_value(registry, "simulation_queue_depth") == 3
    assert _sample_value(registry, "simulation_oldest_queued_seconds") == pytest.approx(
        17.25
    )
    assert (
        _sample_value(
            registry,
            "simulation_active_slots",
            lane="general",
        )
        == 1
    )
    assert (
        _sample_value(
            registry,
            "simulation_active_slots",
            lane="nowcast",
        )
        == 1
    )


def test_structured_log_contains_simulation_id_and_json_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    formatter = JsonFormatter(
        "%(timestamp)s %(level)s %(name)s %(message)s",
        rename_fields={
            "timestamp": "timestamp",
            "level": "level",
            "name": "logger",
        },
    )

    with caplog.at_level(logging.INFO, logger="app.services.hydraulic.metrics"):
        log_simulation_event(
            "simulation.completed",
            simulation_id=SIMULATION_ID,
            request_id="request-123",
            network_id="network-123",
            solver_id="py_mw",
            solver_version="1.0.0",
            attempt=2,
            status="completed",
            duration_seconds=9.5,
            result_frames=289,
            result_bytes=2048,
        )

    record = caplog.records[-1]
    payload = json.loads(formatter.format(record))
    assert payload["event"] == "simulation.completed"
    assert payload["simulation_id"] == str(SIMULATION_ID)
    assert payload["request_id"] == "request-123"
    assert payload["network_id"] == "network-123"
    assert payload["solver_id"] == "py_mw"
    assert payload["solver_version"] == "1.0.0"
    assert payload["attempt"] == 2
    assert payload["result_frames"] == 289
    assert payload["result_bytes"] == 2048
    assert "token" not in payload
    assert "sandbox_path" not in payload


def test_terminal_log_failures_include_simulation_id_and_error_code(
    metrics: HydraulicMetrics,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="app.services.hydraulic.metrics"):
        metrics.record_simulation_terminal(
            simulation_id=SIMULATION_ID,
            solver_id="py_mw",
            status="failed",
            duration_seconds=3,
            error_code="NUMERICAL_STABILITY_LIMIT",
            attempt=3,
        )

    record = caplog.records[-1]
    assert record.levelno == logging.ERROR
    assert record.simulation_id == str(SIMULATION_ID)
    assert record.error_code == "NUMERICAL_STABILITY_LIMIT"
    assert record.attempt == 3


def test_all_frozen_contract_metric_families_can_be_emitted(
    metrics: HydraulicMetrics,
    registry: CollectorRegistry,
) -> None:
    metrics.set_queue_state(depth=1, oldest_queued_seconds=2)
    metrics.set_active_slots(general=1, nowcast=0)
    metrics.record_simulation_terminal(
        simulation_id=SIMULATION_ID,
        solver_id="py_mw",
        status="completed",
        duration_seconds=10,
        result_frames=1,
        result_bytes=1024,
    )
    metrics.record_simulation_retry(
        simulation_id=SIMULATION_ID,
        reason="transient_io",
    )
    metrics.record_solver_fallback(
        simulation_id=SIMULATION_ID,
        from_solver="swmm_kw",
        to_solver="py_mw",
    )
    metrics.record_nowcast_terminal(
        status="completed",
        solver_id="py_mw",
        duration_seconds=30,
        to_alert_seconds=45,
    )
    metrics.record_forecast_alert_created(alert_type="flood_forecast", level="warning")
    metrics.record_forecast_alert_suppressed(
        alert_type="flood_forecast",
        reason="cooldown",
    )
    metrics.set_active_alerts(alert_type="flood_forecast", level="warning", count=2)
    metrics.observe_event_publish_lag(lag_seconds=0.2)
    metrics.set_event_outbox_backlog(depth=4)
    metrics.record_sse_queue_overflow()
    metrics.record_notification_delivery(channel="webhook", status="failed")
    metrics.record_cleanup_pending()

    exposition = generate_latest(registry).decode("utf-8")
    missing = sorted(name for name in CONTRACT_METRIC_NAMES if name not in exposition)
    assert missing == []
    assert "simulation_result_size_bytes" in exposition

    forbidden_label_names = {"simulation_id", "alert_id", "event_id"}
    used_labels = {label for sample in _samples(registry) for label in sample.labels}
    assert used_labels.isdisjoint(forbidden_label_names)


def test_default_singleton_registers_zero_queue_and_slot_series() -> None:
    exposition = generate_latest(hydraulic_metrics.registry).decode("utf-8")

    assert "simulation_queue_depth 0.0" in exposition
    assert 'simulation_active_slots{lane="general"} 0.0' in exposition
    assert 'simulation_active_slots{lane="nowcast"} 0.0' in exposition


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda metrics: metrics.set_queue_state(depth=-1), "depth"),
        (
            lambda metrics: metrics.set_queue_state(
                depth=1,
                oldest_queued_seconds=float("nan"),
            ),
            "oldest_queued_seconds",
        ),
        (
            lambda metrics: metrics.set_active_slots(general=-1, nowcast=0),
            "general",
        ),
        (
            lambda metrics: metrics.record_simulation_terminal(
                simulation_id=SIMULATION_ID,
                solver_id="py_mw",
                status="failed",
                duration_seconds=1,
            ),
            "error_code",
        ),
        (
            lambda metrics: metrics.record_simulation_terminal(
                simulation_id=SIMULATION_ID,
                solver_id="py_mw",
                status="running",
                duration_seconds=1,
            ),
            "status",
        ),
    ],
)
def test_invalid_metric_inputs_fail_closed(
    metrics: HydraulicMetrics,
    operation,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        operation(metrics)


def test_queue_snapshot_validation_is_atomic(metrics: HydraulicMetrics) -> None:
    with pytest.raises(ValueError, match="oldest_queued_seconds"):
        metrics.set_queue_state(depth=2, oldest_queued_seconds=float("nan"))
    assert _sample_value(metrics.registry, "simulation_queue_depth") == 0
    assert (
        _sample_value(
            metrics.registry,
            "simulation_oldest_queued_seconds",
        )
        == 0
    )


def test_slot_snapshot_rejects_saturated_reserved_lane(
    metrics: HydraulicMetrics,
) -> None:
    with pytest.raises(ValueError, match="at most one"):
        metrics.set_active_slots(general=1, nowcast=2)
    assert (
        _sample_value(
            metrics.registry,
            "simulation_active_slots",
            lane="general",
        )
        == 0
    )


def test_metric_label_domains_reject_unbounded_values(
    metrics: HydraulicMetrics,
) -> None:
    with pytest.raises(ValueError, match="solver_id"):
        metrics.record_simulation_terminal(
            simulation_id=SIMULATION_ID,
            solver_id="user-entered-solver",
            status="completed",
            duration_seconds=1,
        )
    with pytest.raises(ValueError, match="reason"):
        metrics.record_simulation_retry(
            simulation_id=SIMULATION_ID,
            reason="anything",
        )


def test_structured_log_rejects_secret_tokens_and_absolute_paths() -> None:
    with pytest.raises(ValueError, match="secret|filesystem"):
        log_simulation_event(
            "simulation.completed",
            simulation_id=SIMULATION_ID,
            request_id="Bearer abc.def",
        )
    with pytest.raises(ValueError, match="secret|filesystem"):
        log_simulation_event(
            "simulation.completed",
            simulation_id=SIMULATION_ID,
            network_id=r"C:\sandbox\secret",
        )


def test_terminal_recording_is_idempotent_per_simulation(
    metrics: HydraulicMetrics,
    registry: CollectorRegistry,
) -> None:
    for _ in range(2):
        metrics.record_simulation_terminal(
            simulation_id=SIMULATION_ID,
            solver_id="py_mw",
            status="completed",
            duration_seconds=2.5,
            result_frames=10,
            result_bytes=100,
        )

    assert (
        _sample_value(
            registry,
            "simulation_duration_seconds_count",
            solver="py_mw",
            status="completed",
        )
        == 1
    )
    assert _sample_value(registry, "simulation_frames_total") == 10


def test_retry_and_fallback_logs_include_simulation_id(
    metrics: HydraulicMetrics,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="app.services.hydraulic.metrics"):
        metrics.record_simulation_retry(
            simulation_id=SIMULATION_ID,
            reason="transient_io",
        )
        metrics.record_solver_fallback(
            simulation_id=OTHER_SIMULATION_ID,
            from_solver="swmm_kw",
            to_solver="py_mw",
        )

    fallback_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "simulation.solver_fallback"
    )
    assert fallback_record.simulation_id == str(OTHER_SIMULATION_ID)


def test_duration_histogram_samples_exist_before_first_task(
    metrics: HydraulicMetrics,
    registry: CollectorRegistry,
) -> None:
    assert (
        _sample_value(
            registry,
            "simulation_duration_seconds_count",
            solver="py_mw",
            status="completed",
        )
        == 0
    )
