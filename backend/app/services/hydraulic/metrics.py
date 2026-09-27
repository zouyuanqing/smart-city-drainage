"""Prometheus metrics and structured logging for hydraulic simulations.

The collectors in this module deliberately keep high-cardinality identifiers out
of Prometheus labels.  A ``simulation_id`` is written only to the structured
log record emitted for a simulation event; the metrics are grouped by bounded
operational dimensions such as solver, terminal status, error code, and lane.

The database remains the source of truth for queue and slot state.  Workers
publish snapshots through :meth:`HydraulicMetrics.set_queue_state` and
:meth:`HydraulicMetrics.set_active_slots`; terminal outcomes are recorded with
:meth:`HydraulicMetrics.record_simulation_terminal`.
"""

from __future__ import annotations

import logging
import math
import operator
import re
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

LOGGER = logging.getLogger(__name__)

SIMULATION_TERMINAL_STATUSES = frozenset(
    {"completed", "failed", "timed_out", "cancelled"}
)
SIMULATION_LANES = ("general", "nowcast")
SOLVER_LABELS = frozenset({"swmm_kw", "swmm_dw", "py_mw", "py_dw", "py_ponding"})
SIMULATION_ERROR_CODES = frozenset(
    {
        "SOLVER_UNAVAILABLE",
        "SOLVER_VERSION_MISMATCH",
        "UNSUPPORTED_FEATURE",
        "SOLVER_INPUT_INVALID",
        "SOLVER_RESULT_INVALID",
        "SOLVER_EXECUTION_FAILED",
        "SIMULATION_TIMEOUT",
        "SIMULATION_CANCELLED",
        "RESULT_PERSIST_FAILED",
        "NUMERICAL_STABILITY_LIMIT",
        "NUMERICAL_UNSTABLE",
        "MASS_BALANCE_FAILED",
        "FRAME_SCHEMA_INVALID",
        "FRAME_ATTEMPT_CONFLICT",
        "OUTPUT_LIMIT_EXCEEDED",
        "LEASE_EXPIRED",
        "UNSAFE_PATH",
        "PROPERTIES_SCHEMA_INVALID",
    }
)
RETRY_REASONS = frozenset(
    {
        "transient_io",
        "transient_db",
        "transient_network",
        "lease_expired",
        "numerical_instability",
    }
)
FORECAST_ALERT_TYPES = frozenset({"flood_forecast", "pipe_surcharge"})
FORECAST_ALERT_LEVELS = frozenset({"info", "warning", "critical"})
FORECAST_SUPPRESSION_REASONS = frozenset(
    {"cooldown", "active_window", "duplicate", "below_threshold"}
)
NOTIFICATION_CHANNELS = frozenset({"webhook", "email", "sms", "in_app"})
NOTIFICATION_STATUSES = frozenset({"delivered", "failed", "retry_wait", "dead_letter"})
_LOG_EVENT_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,95}$")
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(bearer\s+|password|secret|api[_-]?key|access[_-]?token)"
)
CONTRACT_METRIC_NAMES = frozenset(
    {
        "simulation_queue_depth",
        "simulation_oldest_queued_seconds",
        "simulation_duration_seconds",
        "simulation_failures_total",
        "simulation_retries_total",
        "simulation_cancelled_total",
        "simulation_timeout_total",
        "simulation_active_slots",
        "solver_fallback_total",
        "simulation_frames_total",
        "forecast_nowcast_duration_seconds",
        "forecast_nowcast_to_alert_seconds",
        "forecast_runs_total",
        "forecast_alerts_created_total",
        "forecast_alert_candidates_suppressed_total",
        "forecast_active_alerts",
        "hydraulic_event_publish_lag_seconds",
        "hydraulic_event_outbox_backlog",
        "forecast_sse_queue_overflow_total",
        "notification_delivery_total",
        "hydraulic_cleanup_pending_total",
    }
)

_DURATION_BUCKETS = (
    0.1,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    180.0,
    300.0,
    600.0,
)
_RESULT_SIZE_BUCKETS = (
    1024,
    16 * 1024,
    256 * 1024,
    4 * 1024 * 1024,
    64 * 1024 * 1024,
    512 * 1024 * 1024,
    2 * 1024 * 1024 * 1024,
    8 * 1024 * 1024 * 1024,
)
_NOWCAST_BUCKETS = (1, 5, 15, 30, 60, 120, 180, 300)
_EVENT_LAG_BUCKETS = (0.05, 0.1, 0.5, 1, 5, 30, 60, 300, 900)


def _as_identifier(value: object, field_name: str) -> str:
    if isinstance(value, uuid.UUID):
        result = str(value)
    elif isinstance(value, str):
        result = value.strip()
    else:
        raise ValueError(f"{field_name} must be a non-empty string or UUID")
    if not result:
        raise ValueError(f"{field_name} must be a non-empty string or UUID")
    if len(result) > 128:
        raise ValueError(f"{field_name} must not exceed 128 characters")
    return result


def _as_label(value: object, field_name: str) -> str:
    return _as_identifier(value, field_name)


def _as_enum_label(
    value: object,
    allowed: frozenset[str],
    field_name: str,
) -> str:
    result = _as_label(value, field_name)
    if result not in allowed:
        raise ValueError(f"{field_name} must be one of: {', '.join(sorted(allowed))}")
    return result


def _safe_log_value(value: object, field_name: str) -> str:
    result = _as_label(value, field_name)
    if (
        _SENSITIVE_VALUE_RE.search(result)
        or "\\" in result
        or re.match(r"^[A-Za-z]:", result)
        or result.startswith(("/", "./", "../"))
        or ".." in result
    ):
        raise ValueError(f"{field_name} contains a secret or filesystem path")
    return result


def _as_nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer")
    try:
        result = operator.index(value)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if result < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return int(result)


def _as_nonnegative_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite non-negative number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return result


def _optional_identifier(
    value: object | None,
    field_name: str,
) -> str | None:
    if value is None:
        return None
    return _as_identifier(value, field_name)


def _optional_nonnegative_int(
    value: object | None,
    field_name: str,
) -> int | None:
    if value is None:
        return None
    return _as_nonnegative_int(value, field_name)


def _optional_nonnegative_float(
    value: object | None,
    field_name: str,
) -> float | None:
    if value is None:
        return None
    return _as_nonnegative_float(value, field_name)


def log_simulation_event(
    event: str,
    *,
    simulation_id: uuid.UUID | str,
    level: int = logging.INFO,
    request_id: uuid.UUID | str | None = None,
    user_id: uuid.UUID | str | None = None,
    network_id: uuid.UUID | str | None = None,
    network_revision_id: uuid.UUID | str | None = None,
    rainfall_scenario_id: uuid.UUID | str | None = None,
    attempt: int | None = None,
    solver_id: str | None = None,
    solver_version: str | None = None,
    nowcast_run_id: uuid.UUID | str | None = None,
    alert_id: uuid.UUID | str | None = None,
    event_id: uuid.UUID | str | None = None,
    aggregate_version: int | None = None,
    phase: str | None = None,
    error_code: str | None = None,
    status: str | None = None,
    duration_seconds: float | None = None,
    result_frames: int | None = None,
    result_bytes: int | None = None,
) -> None:
    """Emit one allow-listed structured simulation log event.

    ``simulation_id`` is always included.  The explicit keyword allow-list
    prevents callers from accidentally attaching secrets, raw solver input, or
    absolute sandbox paths to a log record.
    """

    if not isinstance(level, int):
        raise ValueError("level must be an integer logging level")

    event_name = _safe_log_value(event, "event")
    if _LOG_EVENT_RE.fullmatch(event_name) is None:
        raise ValueError("event must be a bounded structured event name")
    extra: dict[str, Any] = {
        "event": event_name,
        "simulation_id": _safe_log_value(simulation_id, "simulation_id"),
    }
    optional_fields: Mapping[str, object | None] = {
        "request_id": request_id,
        "user_id": user_id,
        "network_id": network_id,
        "network_revision_id": network_revision_id,
        "rainfall_scenario_id": rainfall_scenario_id,
        "solver_id": solver_id,
        "solver_version": solver_version,
        "nowcast_run_id": nowcast_run_id,
        "alert_id": alert_id,
        "event_id": event_id,
        "phase": phase,
        "error_code": error_code,
        "status": status,
    }
    for key, value in optional_fields.items():
        if value is None:
            continue
        normalized = _safe_log_value(value, key)
        if key == "solver_id":
            normalized = _as_enum_label(normalized, SOLVER_LABELS, key)
        elif key == "error_code":
            normalized = _as_enum_label(
                normalized,
                SIMULATION_ERROR_CODES,
                key,
            )
        elif key == "status":
            normalized = _as_enum_label(
                normalized,
                SIMULATION_TERMINAL_STATUSES | {"running"},
                key,
            )
        elif key == "phase":
            normalized = _as_enum_label(
                normalized,
                frozenset(
                    {
                        "accepted",
                        "validating",
                        "solving",
                        "quality",
                        "persisting",
                        "finalizing",
                        "running",
                        *SIMULATION_TERMINAL_STATUSES,
                    }
                ),
                key,
            )
        extra[key] = normalized

    for key, value in (
        ("attempt", attempt),
        ("aggregate_version", aggregate_version),
    ):
        count_value = _optional_nonnegative_int(value, key)
        if count_value is not None:
            extra[key] = count_value

    duration = _optional_nonnegative_float(duration_seconds, "duration_seconds")
    if duration is not None:
        extra["duration_seconds"] = duration
    frames = _optional_nonnegative_int(result_frames, "result_frames")
    if frames is not None:
        extra["result_frames"] = frames
    byte_count = _optional_nonnegative_int(result_bytes, "result_bytes")
    if byte_count is not None:
        extra["result_bytes"] = byte_count

    LOGGER.log(level, event_name, extra=extra)


class HydraulicMetrics:
    """Own the hydraulic collector set and its recording operations.

    A caller may pass an isolated ``CollectorRegistry`` in tests.  Production
    callers normally use :data:`hydraulic_metrics`, which registers with
    Prometheus' default registry used by ``prometheus-fastapi-instrumentator``.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or REGISTRY
        self._terminal_lock = threading.Lock()
        self._terminal_results: OrderedDict[str, tuple[str, str]] = OrderedDict()
        self._terminal_result_limit = 100_000

        self.simulation_queue_depth = Gauge(
            "simulation_queue_depth",
            "Current number of durable simulations waiting to be claimed.",
            registry=self.registry,
        )
        self.simulation_oldest_queued_seconds = Gauge(
            "simulation_oldest_queued_seconds",
            "Age in seconds of the oldest queued simulation.",
            registry=self.registry,
        )
        self.simulation_duration_seconds = Histogram(
            "simulation_duration_seconds",
            "Terminal simulation worker duration in seconds.",
            labelnames=("solver", "status"),
            buckets=_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.simulation_failures_total = Counter(
            "simulation_failures_total",
            "Terminal failed simulations grouped by stable error code.",
            labelnames=("error_code",),
            registry=self.registry,
        )
        self.simulation_retries_total = Counter(
            "simulation_retries_total",
            "Simulation retry decisions grouped by stable reason.",
            labelnames=("reason",),
            registry=self.registry,
        )
        self.simulation_cancelled_total = Counter(
            "simulation_cancelled_total",
            "Cancelled simulations.",
            registry=self.registry,
        )
        self.simulation_timeout_total = Counter(
            "simulation_timeout_total",
            "Timed-out simulations.",
            registry=self.registry,
        )
        self.simulation_active_slots = Gauge(
            "simulation_active_slots",
            "Busy durable solver slots grouped by fixed lane.",
            labelnames=("lane",),
            registry=self.registry,
        )
        self.solver_fallback_total = Counter(
            "solver_fallback_total",
            "Solver selections resolved through a declared fallback.",
            labelnames=("from", "to"),
            registry=self.registry,
        )
        self.simulation_frames_total = Counter(
            "simulation_frames_total",
            "Simulation result frames durably recorded.",
            registry=self.registry,
        )
        self.simulation_result_size_bytes = Histogram(
            "simulation_result_size_bytes",
            "Serialized simulation result size in bytes.",
            labelnames=("solver", "status"),
            buckets=_RESULT_SIZE_BUCKETS,
            registry=self.registry,
        )

        self.forecast_nowcast_duration_seconds = Histogram(
            "forecast_nowcast_duration_seconds",
            "Terminal nowcast run duration in seconds.",
            labelnames=("status", "solver"),
            buckets=_NOWCAST_BUCKETS,
            registry=self.registry,
        )
        self.forecast_nowcast_to_alert_seconds = Histogram(
            "forecast_nowcast_to_alert_seconds",
            "Seconds from nowcast due time to durable alert publication.",
            buckets=_NOWCAST_BUCKETS,
            registry=self.registry,
        )
        self.forecast_runs_total = Counter(
            "forecast_runs_total",
            "Terminal nowcast runs grouped by terminal status.",
            labelnames=("terminal_status",),
            registry=self.registry,
        )
        self.forecast_alerts_created_total = Counter(
            "forecast_alerts_created_total",
            "Forecast alerts grouped by type and level.",
            labelnames=("type", "level"),
            registry=self.registry,
        )
        self.forecast_alert_candidates_suppressed_total = Counter(
            "forecast_alert_candidates_suppressed_total",
            "Suppressed forecast alert candidates.",
            labelnames=("type", "reason"),
            registry=self.registry,
        )
        self.forecast_active_alerts = Gauge(
            "forecast_active_alerts",
            "Current open forecast alerts grouped by type and level.",
            labelnames=("type", "level"),
            registry=self.registry,
        )
        self.hydraulic_event_publish_lag_seconds = Histogram(
            "hydraulic_event_publish_lag_seconds",
            "Hydraulic event publication lag in seconds.",
            buckets=_EVENT_LAG_BUCKETS,
            registry=self.registry,
        )
        self.hydraulic_event_outbox_backlog = Gauge(
            "hydraulic_event_outbox_backlog",
            "Current hydraulic event outbox backlog.",
            registry=self.registry,
        )
        self.forecast_sse_queue_overflow_total = Counter(
            "forecast_sse_queue_overflow_total",
            "Forecast SSE queues closed or reset because of overflow.",
            registry=self.registry,
        )
        self.notification_delivery_total = Counter(
            "notification_delivery_total",
            "Notification delivery outcomes grouped by channel and status.",
            labelnames=("channel", "status"),
            registry=self.registry,
        )
        self.hydraulic_cleanup_pending_total = Counter(
            "hydraulic_cleanup_pending_total",
            "Hydraulic cleanup items that entered a pending state.",
            registry=self.registry,
        )

        self._initialize_zero_series()

    def _initialize_zero_series(self) -> None:
        self.simulation_queue_depth.set(0)
        self.simulation_oldest_queued_seconds.set(0)
        for solver in SOLVER_LABELS:
            for status in SIMULATION_TERMINAL_STATUSES:
                self.simulation_duration_seconds.labels(
                    solver=solver,
                    status=status,
                )
        for lane in SIMULATION_LANES:
            self.simulation_active_slots.labels(lane=lane).set(0)
        self.simulation_cancelled_total.inc(0)
        self.simulation_timeout_total.inc(0)
        self.simulation_frames_total.inc(0)
        self.hydraulic_event_outbox_backlog.set(0)
        self.forecast_sse_queue_overflow_total.inc(0)
        self.hydraulic_cleanup_pending_total.inc(0)

    def set_queue_state(
        self,
        *,
        depth: int,
        oldest_queued_seconds: float = 0,
    ) -> None:
        """Publish a queue snapshot read from the durable job database."""

        normalized_depth = _as_nonnegative_int(depth, "depth")
        normalized_age = _as_nonnegative_float(
            oldest_queued_seconds,
            "oldest_queued_seconds",
        )
        if normalized_depth == 0 and normalized_age != 0.0:
            raise ValueError("oldest_queued_seconds must be zero for an empty queue")
        self.simulation_queue_depth.set(normalized_depth)
        self.simulation_oldest_queued_seconds.set(normalized_age)

    def set_active_slots(self, *, general: int, nowcast: int) -> None:
        """Publish busy slot counts for the two fixed worker lanes."""

        normalized_general = _as_nonnegative_int(general, "general")
        normalized_nowcast = _as_nonnegative_int(nowcast, "nowcast")
        if normalized_general > 1 or normalized_nowcast > 1:
            raise ValueError(
                "each fixed simulation lane permits at most one active slot"
            )
        self.simulation_active_slots.labels(lane="general").set(normalized_general)
        self.simulation_active_slots.labels(lane="nowcast").set(normalized_nowcast)

    def record_simulation_started(
        self,
        *,
        simulation_id: uuid.UUID | str,
        solver_id: str,
        attempt: int | None = None,
    ) -> None:
        """Emit a structured start event without adding an ID metric label."""

        log_simulation_event(
            "simulation.started",
            simulation_id=simulation_id,
            solver_id=solver_id,
            attempt=attempt,
            phase="running",
        )

    def _claim_terminal(
        self,
        simulation_id: uuid.UUID | str,
        *,
        solver_id: str,
        status: str,
    ) -> bool:
        identity = _safe_log_value(simulation_id, "simulation_id")
        with self._terminal_lock:
            existing = self._terminal_results.get(identity)
            if existing is not None:
                if existing != (solver_id, status):
                    raise ValueError(
                        "simulation_id already has a different terminal metric result"
                    )
                return False
            self._terminal_results[identity] = (solver_id, status)
            while len(self._terminal_results) > self._terminal_result_limit:
                self._terminal_results.popitem(last=False)
        return True

    def record_simulation_terminal(
        self,
        *,
        simulation_id: uuid.UUID | str,
        solver_id: str,
        status: str,
        duration_seconds: float,
        error_code: str | None = None,
        result_frames: int | None = None,
        result_bytes: int | None = None,
        attempt: int | None = None,
    ) -> None:
        """Record one terminal outcome, duration, failure, and result size."""

        terminal_status = _as_enum_label(
            status,
            SIMULATION_TERMINAL_STATUSES,
            "status",
        )
        solver = _as_enum_label(solver_id, SOLVER_LABELS, "solver_id")
        duration = _as_nonnegative_float(duration_seconds, "duration_seconds")
        normalized_error = (
            None
            if error_code is None
            else _as_enum_label(error_code, SIMULATION_ERROR_CODES, "error_code")
        )
        if terminal_status == "failed" and normalized_error is None:
            raise ValueError("error_code is required for failed simulations")
        if terminal_status == "timed_out":
            normalized_error = "SIMULATION_TIMEOUT"
        if terminal_status == "cancelled":
            normalized_error = "SIMULATION_CANCELLED"
        frames = _optional_nonnegative_int(result_frames, "result_frames")
        byte_count = _optional_nonnegative_int(result_bytes, "result_bytes")
        identity = _safe_log_value(simulation_id, "simulation_id")
        if not self._claim_terminal(
            identity,
            solver_id=solver,
            status=terminal_status,
        ):
            return

        self.simulation_duration_seconds.labels(
            solver=solver,
            status=terminal_status,
        ).observe(duration)

        if terminal_status == "failed":
            assert normalized_error is not None
            self.simulation_failures_total.labels(error_code=normalized_error).inc()
        elif terminal_status == "cancelled":
            self.simulation_cancelled_total.inc()
        elif terminal_status == "timed_out":
            self.simulation_timeout_total.inc()

        if frames is not None:
            self.simulation_frames_total.inc(frames)
        if byte_count is not None:
            self.simulation_result_size_bytes.labels(
                solver=solver,
                status=terminal_status,
            ).observe(byte_count)

        if terminal_status == "failed":
            level = logging.ERROR
        elif terminal_status in {"cancelled", "timed_out"}:
            level = logging.WARNING
        else:
            level = logging.INFO
        log_simulation_event(
            f"simulation.{terminal_status}",
            simulation_id=simulation_id,
            level=level,
            solver_id=solver,
            attempt=attempt,
            phase=terminal_status,
            status=terminal_status,
            error_code=normalized_error,
            duration_seconds=duration,
            result_frames=frames,
            result_bytes=byte_count,
        )

    def record_simulation_retry(
        self,
        *,
        simulation_id: uuid.UUID | str,
        reason: str,
    ) -> None:
        normalized_reason = _as_enum_label(reason, RETRY_REASONS, "reason")
        identity = _safe_log_value(simulation_id, "simulation_id")
        self.simulation_retries_total.labels(reason=normalized_reason).inc()
        log_simulation_event(
            "simulation.retry",
            simulation_id=identity,
            phase="solving",
        )

    def record_solver_fallback(
        self,
        *,
        simulation_id: uuid.UUID | str,
        from_solver: str,
        to_solver: str,
    ) -> None:
        source = _as_enum_label(from_solver, SOLVER_LABELS, "from_solver")
        destination = _as_enum_label(to_solver, SOLVER_LABELS, "to_solver")
        if source == destination:
            raise ValueError("solver fallback must change solver identity")
        identity = _safe_log_value(simulation_id, "simulation_id")
        self.solver_fallback_total.labels(**{"from": source, "to": destination}).inc()
        log_simulation_event(
            "simulation.solver_fallback",
            simulation_id=identity,
            solver_id=destination,
            phase="solving",
            status="running",
        )

    def record_nowcast_terminal(
        self,
        *,
        status: str,
        solver_id: str,
        duration_seconds: float,
        to_alert_seconds: float | None = None,
    ) -> None:
        terminal_status = _as_enum_label(
            status,
            SIMULATION_TERMINAL_STATUSES,
            "status",
        )
        solver = _as_enum_label(solver_id, SOLVER_LABELS, "solver_id")
        normalized_duration = _as_nonnegative_float(
            duration_seconds,
            "duration_seconds",
        )
        alert_latency = _optional_nonnegative_float(
            to_alert_seconds,
            "to_alert_seconds",
        )
        self.forecast_nowcast_duration_seconds.labels(
            status=terminal_status,
            solver=solver,
        ).observe(normalized_duration)
        if alert_latency is not None:
            self.forecast_nowcast_to_alert_seconds.observe(alert_latency)
        self.forecast_runs_total.labels(terminal_status=terminal_status).inc()

    def record_forecast_alert_created(self, *, alert_type: str, level: str) -> None:
        normalized_type = _as_enum_label(
            alert_type,
            FORECAST_ALERT_TYPES,
            "alert_type",
        )
        normalized_level = _as_enum_label(
            level,
            FORECAST_ALERT_LEVELS,
            "level",
        )
        self.forecast_alerts_created_total.labels(
            type=normalized_type,
            level=normalized_level,
        ).inc()

    def record_forecast_alert_suppressed(
        self,
        *,
        alert_type: str,
        reason: str,
    ) -> None:
        self.forecast_alert_candidates_suppressed_total.labels(
            type=_as_enum_label(alert_type, FORECAST_ALERT_TYPES, "alert_type"),
            reason=_as_enum_label(
                reason,
                FORECAST_SUPPRESSION_REASONS,
                "reason",
            ),
        ).inc()

    def set_active_alerts(
        self,
        *,
        alert_type: str,
        level: str,
        count: int,
    ) -> None:
        normalized_type = _as_enum_label(
            alert_type,
            FORECAST_ALERT_TYPES,
            "alert_type",
        )
        normalized_level = _as_enum_label(
            level,
            FORECAST_ALERT_LEVELS,
            "level",
        )
        normalized_count = _as_nonnegative_int(count, "count")
        self.forecast_active_alerts.labels(
            type=normalized_type,
            level=normalized_level,
        ).set(normalized_count)

    def observe_event_publish_lag(self, *, lag_seconds: float) -> None:
        self.hydraulic_event_publish_lag_seconds.observe(
            _as_nonnegative_float(lag_seconds, "lag_seconds")
        )

    def set_event_outbox_backlog(self, *, depth: int) -> None:
        self.hydraulic_event_outbox_backlog.set(_as_nonnegative_int(depth, "depth"))

    def record_sse_queue_overflow(self, *, count: int = 1) -> None:
        self.forecast_sse_queue_overflow_total.inc(_as_nonnegative_int(count, "count"))

    def record_notification_delivery(
        self,
        *,
        channel: str,
        status: str,
    ) -> None:
        self.notification_delivery_total.labels(
            channel=_as_enum_label(
                channel,
                NOTIFICATION_CHANNELS,
                "channel",
            ),
            status=_as_enum_label(
                status,
                NOTIFICATION_STATUSES,
                "status",
            ),
        ).inc()

    def record_cleanup_pending(self, *, count: int = 1) -> None:
        self.hydraulic_cleanup_pending_total.inc(_as_nonnegative_int(count, "count"))


hydraulic_metrics = HydraulicMetrics()
HYDRAULIC_METRICS = hydraulic_metrics

__all__ = [
    "CONTRACT_METRIC_NAMES",
    "HYDRAULIC_METRICS",
    "HydraulicMetrics",
    "hydraulic_metrics",
    "log_simulation_event",
]
