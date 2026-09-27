"""Forecast alert materialization, event-window suppression, and updates.

The service is deliberately storage-agnostic.  ``ForecastAlertStore`` is the
transactional port used by the nowcast finalizer: a production implementation
must lock ``forecast_alert_windows`` in PostgreSQL and persist the alert,
transition, and outbox rows in that same transaction.  No process-local map is
used as a completion or suppression source of truth.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import uuid
from collections.abc import Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, ContextManager, Mapping, Protocol

from app.services.hydraulic.risk import (
    RISK_POLICY_VERSION,
    RiskAssessment,
    RiskInputError,
    calculate_risk,
)

UTC = timezone.utc
FORECAST_HORIZON = timedelta(hours=2)
FORECAST_STEP_MINUTES = 5
SUPPRESSION_WINDOW = timedelta(minutes=30)

_ALERT_TYPES = frozenset({"flood_forecast", "pipe_surcharge"})
_CONFIDENCE_METHODS = frozenset(
    {
        "ensemble_exceedance_frequency",
        "calibrated_residual",
        "conformal",
        "calibrated_regression",
    }
)
_TARGET_TYPES = frozenset({"node", "pipe_segment"})
_LEVELS = frozenset({"info", "warning", "critical"})
_ACTIVE_STATUSES = frozenset({"open", "acknowledged"})
_TERMINAL_STATUSES = frozenset({"resolved", "expired"})
_OPERATORS = {
    ">": lambda value, threshold: value > threshold,
    ">=": lambda value, threshold: value >= threshold,
    "<": lambda value, threshold: value < threshold,
    "<=": lambda value, threshold: value <= threshold,
}
_LEVEL_ORDER = {"info": 0, "warning": 1, "critical": 2}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONFIDENCE_QUANTUM = Decimal("0.0001")


class ForecastArtifactResolver(Protocol):
    """Controlled registry used to prove policy artifact bytes and schema."""

    def verify(
        self,
        *,
        artifact_id: uuid.UUID,
        artifact_sha256: str,
        artifact_kind: str,
    ) -> Mapping[str, Any]: ...


class ForecastTargetResolver(Protocol):
    """Resolve a target key against a revision-scoped manifest."""

    def resolve(
        self,
        *,
        network_id: uuid.UUID,
        network_revision_id: uuid.UUID,
        target_manifest_id: uuid.UUID,
        target_manifest_sha256: str,
        target_type: str,
        target_id: uuid.UUID,
        target_stable_key: str,
    ) -> bool: ...


def _confidence_4(value: Any, field_name: str = "confidence") -> float:
    try:
        decimal = Decimal(str(value)).quantize(
            _CONFIDENCE_QUANTUM, rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ForecastAlertError(
            "INVALID_FORECAST_CONFIDENCE", f"{field_name} must be a finite decimal"
        ) from exc
    if not decimal.is_finite() or decimal < 0 or decimal > 1:
        raise ForecastAlertError(
            "INVALID_FORECAST_CONFIDENCE", f"{field_name} must be between 0 and 1"
        )
    return float(decimal)


class ForecastAlertError(ValueError):
    """Stable forecast-domain error with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ForecastVersionConflict(ForecastAlertError):
    """The caller's expected aggregate version is stale."""

    def __init__(self, current_version: int) -> None:
        super().__init__(
            "FORECAST_VERSION_CONFLICT",
            f"expected alert version does not match current version {current_version}",
        )
        self.current_version = current_version


class ForecastTransitionError(ForecastAlertError):
    """The requested state transition is not legal for the alert."""

    def __init__(self, message: str) -> None:
        super().__init__("INVALID_FORECAST_TRANSITION", message)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ForecastAlertError(
            "INVALID_FORECAST_TIME", f"{field_name} must be an aware datetime"
        )
    if value.utcoffset() is None:
        raise ForecastAlertError(
            "INVALID_FORECAST_TIME", f"{field_name} must be an aware datetime"
        )
    return value.astimezone(UTC)


def _uuid(value: object, field_name: str) -> uuid.UUID:
    if isinstance(value, bool):
        raise ForecastAlertError("INVALID_FORECAST_ID", f"{field_name} must be a UUID")
    try:
        result = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ForecastAlertError(
            "INVALID_FORECAST_ID", f"{field_name} must be a UUID"
        ) from exc
    if result.int == 0:
        raise ForecastAlertError(
            "INVALID_FORECAST_ID", f"{field_name} must not be the nil UUID"
        )
    return result


def _finite(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise ForecastAlertError(
            "INVALID_FORECAST_VALUE", f"{field_name} must be a finite number"
        )
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastAlertError(
            "INVALID_FORECAST_VALUE", f"{field_name} must be a finite number"
        ) from exc
    if not math.isfinite(result):
        raise ForecastAlertError(
            "INVALID_FORECAST_VALUE", f"{field_name} must be a finite number"
        )
    return result


def _nonnegative(value: object, field_name: str) -> float:
    result = _finite(value, field_name)
    if result < 0.0:
        raise ForecastAlertError(
            "INVALID_FORECAST_VALUE", f"{field_name} must be non-negative"
        )
    return result


def _sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ForecastAlertError(
            "INVALID_FORECAST_ARTIFACT",
            f"{field_name} must be 64 lowercase hexadecimal characters",
        )
    return value


def _datetime_text(value: datetime) -> str:
    return _aware_utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def _bounded_text(value: object, field_name: str, *, max_length: int) -> str:
    if not isinstance(value, str):
        raise ForecastAlertError(
            "INVALID_FORECAST_TEXT", f"{field_name} must be a string"
        )
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ForecastAlertError(
            "INVALID_FORECAST_TEXT",
            f"{field_name} must contain 1..{max_length} characters",
        )
    return normalized


def canonical_suppression_payload(
    *,
    network_id: uuid.UUID | str,
    alert_type: str,
    target_type: str,
    target_id: uuid.UUID | str,
    rule_id: uuid.UUID | str,
) -> dict[str, str]:
    """Return the exact canonical object hashed into a suppression key."""

    normalized_alert_type = str(alert_type)
    normalized_target_type = str(target_type)
    if normalized_alert_type not in _ALERT_TYPES:
        raise ForecastAlertError(
            "INVALID_ALERT_TYPE", "alert_type must be flood_forecast or pipe_surcharge"
        )
    if normalized_target_type not in _TARGET_TYPES:
        raise ForecastAlertError(
            "INVALID_TARGET_TYPE", "target_type must be node or pipe_segment"
        )
    return {
        "schema": "forecast-suppression-v1",
        "network_id": str(_uuid(network_id, "network_id")),
        "alert_type": normalized_alert_type,
        "target_type": normalized_target_type,
        "target_id": str(_uuid(target_id, "target_id")),
        "rule_id": str(_uuid(rule_id, "rule_id")),
    }


def calculate_suppression_key(
    *,
    network_id: uuid.UUID | str,
    alert_type: str,
    target_type: str,
    target_id: uuid.UUID | str,
    rule_id: uuid.UUID | str,
) -> str:
    """Calculate the canonical SHA-256 event-window key."""

    payload = canonical_suppression_payload(
        network_id=network_id,
        alert_type=alert_type,
        target_type=target_type,
        target_id=target_id,
        rule_id=rule_id,
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calculate_lead_time_minutes(*, issued_at: datetime, expected_at: datetime) -> int:
    """Return ``max(0, floor((expected_at - issued_at) / 60 seconds))``."""

    issued = _aware_utc(issued_at, "issued_at")
    expected = _aware_utc(expected_at, "expected_at")
    if expected - issued > FORECAST_HORIZON:
        raise ForecastAlertError(
            "INVALID_FORECAST_HORIZON", "expected_at must be within two hours"
        )
    return max(0, int((expected - issued).total_seconds() // 60))


@dataclass(frozen=True, slots=True)
class ProbabilityCalibrationRef:
    """Run-bound probability calibration identity and applicability domain."""

    id: uuid.UUID
    artifact_sha256: str
    method: str
    metric: str
    lead_min_minutes: int
    lead_max_minutes: int
    magnitude_min: float
    magnitude_max: float
    valid_from: datetime
    valid_until: datetime
    artifact_schema_version: int = 1
    artifact_uri: str | None = None
    artifact_format: str = "json"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "probability_calibration_id"))
        object.__setattr__(
            self, "artifact_sha256", _sha256(self.artifact_sha256, "artifact_sha256")
        )
        method = _bounded_text(self.method, "method", max_length=64)
        if method not in _CONFIDENCE_METHODS:
            raise ForecastAlertError(
                "INVALID_CONFIDENCE_METHOD",
                "method is not an accepted probability-calibration method",
            )
        object.__setattr__(self, "method", method)
        object.__setattr__(
            self, "metric", _bounded_text(self.metric, "metric", max_length=64)
        )
        if (
            isinstance(self.lead_min_minutes, bool)
            or not isinstance(self.lead_min_minutes, int)
            or isinstance(self.lead_max_minutes, bool)
            or not isinstance(self.lead_max_minutes, int)
            or self.lead_min_minutes < 0
            or self.lead_max_minutes < self.lead_min_minutes
        ):
            raise ForecastAlertError(
                "INVALID_CONFIDENCE_DOMAIN", "invalid calibration lead-time domain"
            )
        object.__setattr__(
            self, "magnitude_min", _finite(self.magnitude_min, "magnitude_min")
        )
        object.__setattr__(
            self, "magnitude_max", _finite(self.magnitude_max, "magnitude_max")
        )
        if (
            isinstance(self.artifact_schema_version, bool)
            or not isinstance(self.artifact_schema_version, int)
            or self.artifact_schema_version < 1
        ):
            raise ForecastAlertError(
                "INVALID_FORECAST_ARTIFACT", "artifact_schema_version must be positive"
            )
        if self.artifact_uri is not None and (
            not isinstance(self.artifact_uri, str) or not self.artifact_uri.strip()
        ):
            raise ForecastAlertError(
                "INVALID_FORECAST_ARTIFACT", "artifact_uri must be a controlled path"
            )
        if self.magnitude_min < 0.0 or self.magnitude_min > self.magnitude_max:
            raise ForecastAlertError(
                "INVALID_CONFIDENCE_DOMAIN", "invalid calibration magnitude domain"
            )
        valid_from = _aware_utc(self.valid_from, "valid_from")
        valid_until = _aware_utc(self.valid_until, "valid_until")
        if valid_until <= valid_from:
            raise ForecastAlertError(
                "INVALID_CONFIDENCE_DOMAIN", "calibration validity interval is empty"
            )
        object.__setattr__(self, "valid_from", valid_from)
        object.__setattr__(self, "valid_until", valid_until)


@dataclass(frozen=True, slots=True)
class ForecastRuleRef:
    """Run-bound threshold, refresh, and risk-scale policy."""

    id: uuid.UUID
    artifact_sha256: str
    version: str
    alert_type: str
    metric: str
    operator: str
    threshold_value: float
    threshold_unit: str
    risk_scale: float
    valid_from: datetime
    valid_until: datetime
    step_minutes: int = FORECAST_STEP_MINUTES
    refresh_expected_shift_steps: int = 1
    refresh_peak_delta: float = 0.0
    refresh_confidence_delta: float = 0.0
    refresh_risk_delta: float = 0.0
    risk_policy_version: str = RISK_POLICY_VERSION
    artifact_schema_version: int = 1
    artifact_uri: str | None = None
    artifact_format: str = "json"
    expiry_grace_minutes: int = 0
    clear_value: float | None = None
    no_forecast_confirmations: int = 1
    clear_debounce_minutes: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _uuid(self.id, "forecast_rule_id"))
        object.__setattr__(
            self, "artifact_sha256", _sha256(self.artifact_sha256, "artifact_sha256")
        )
        object.__setattr__(
            self, "version", _bounded_text(self.version, "rule version", max_length=64)
        )
        if self.alert_type not in _ALERT_TYPES:
            raise ForecastAlertError(
                "INVALID_ALERT_TYPE",
                "alert_type must be flood_forecast or pipe_surcharge",
            )
        object.__setattr__(
            self, "metric", _bounded_text(self.metric, "metric", max_length=64)
        )
        if self.operator not in _OPERATORS:
            raise ForecastAlertError(
                "INVALID_FORECAST_RULE", "operator must be >, >=, <, or <="
            )
        object.__setattr__(
            self, "threshold_value", _finite(self.threshold_value, "threshold_value")
        )
        object.__setattr__(
            self,
            "threshold_unit",
            _bounded_text(self.threshold_unit, "threshold_unit", max_length=32),
        )
        risk_scale = _finite(self.risk_scale, "risk_scale")
        if risk_scale <= 0.0:
            raise ForecastAlertError(
                "INVALID_FORECAST_RULE", "risk_scale must be greater than zero"
            )
        object.__setattr__(self, "risk_scale", risk_scale)
        if (
            isinstance(self.step_minutes, bool)
            or not isinstance(self.step_minutes, int)
            or not 1 <= self.step_minutes <= 300
        ):
            raise ForecastAlertError(
                "INVALID_FORECAST_RULE", "step_minutes must be between 1 and 300"
            )
        for field_name in (
            "refresh_expected_shift_steps",
            "refresh_peak_delta",
            "refresh_confidence_delta",
            "refresh_risk_delta",
        ):
            value = getattr(self, field_name)
            if field_name.endswith("steps"):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ForecastAlertError(
                        "INVALID_FORECAST_RULE",
                        f"{field_name} must be a non-negative integer",
                    )
            else:
                value = _nonnegative(value, field_name)
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self,
            "risk_policy_version",
            _bounded_text(
                self.risk_policy_version, "risk_policy_version", max_length=64
            ),
        )
        if self.risk_policy_version != RISK_POLICY_VERSION:
            raise ForecastAlertError(
                "FORECAST_POLICY_INVALID", "unsupported risk policy version"
            )
        if (
            isinstance(self.artifact_schema_version, bool)
            or not isinstance(self.artifact_schema_version, int)
            or self.artifact_schema_version < 1
        ):
            raise ForecastAlertError(
                "INVALID_FORECAST_ARTIFACT", "artifact_schema_version must be positive"
            )
        if self.expiry_grace_minutes < 0 or self.no_forecast_confirmations < 1:
            raise ForecastAlertError(
                "INVALID_FORECAST_RULE", "expiry/no-forecast policy is invalid"
            )
        if self.clear_value is not None:
            object.__setattr__(
                self, "clear_value", _finite(self.clear_value, "clear_value")
            )
        valid_from = _aware_utc(self.valid_from, "valid_from")
        valid_until = _aware_utc(self.valid_until, "valid_until")
        if valid_until <= valid_from:
            raise ForecastAlertError(
                "INVALID_FORECAST_RULE", "forecast rule validity interval is empty"
            )
        object.__setattr__(self, "valid_from", valid_from)
        object.__setattr__(self, "valid_until", valid_until)


@dataclass(frozen=True, slots=True)
class ForecastCandidate:
    """One calibrated, qualifying forecast point ready for alert materialization."""

    network_id: uuid.UUID
    nowcast_run_id: uuid.UUID
    issued_at: datetime
    simulation_id: uuid.UUID
    network_revision_id: uuid.UUID
    target_manifest_id: uuid.UUID
    target_manifest_sha256: str
    alert_type: str
    target_type: str
    target_id: uuid.UUID
    target_stable_key: str
    expected_at: datetime
    depth_m: float
    confidence: float
    level: str
    fill_ratio: float | None = None
    nowcast_status: str | None = None
    nowcast_published_at: datetime | None = None
    nowcast_deadline_at: datetime | None = None
    status: str | None = None
    deadline_at: datetime | None = None
    observation_ingest_event_id: uuid.UUID | None = None
    target_manifest: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "network_id",
            "nowcast_run_id",
            "simulation_id",
            "network_revision_id",
            "target_manifest_id",
            "target_id",
        ):
            object.__setattr__(
                self, field_name, _uuid(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "target_manifest_sha256",
            _sha256(self.target_manifest_sha256, "target_manifest_sha256"),
        )
        if self.alert_type not in _ALERT_TYPES:
            raise ForecastAlertError(
                "INVALID_ALERT_TYPE",
                "alert_type must be flood_forecast or pipe_surcharge",
            )
        if self.target_type not in _TARGET_TYPES:
            raise ForecastAlertError(
                "INVALID_TARGET_TYPE", "target_type must be node or pipe_segment"
            )
        object.__setattr__(
            self,
            "target_stable_key",
            _bounded_text(self.target_stable_key, "target_stable_key", max_length=128),
        )
        object.__setattr__(self, "issued_at", _aware_utc(self.issued_at, "issued_at"))
        object.__setattr__(
            self, "expected_at", _aware_utc(self.expected_at, "expected_at")
        )
        object.__setattr__(self, "depth_m", _nonnegative(self.depth_m, "depth_m"))
        if self.fill_ratio is not None:
            object.__setattr__(
                self, "fill_ratio", _nonnegative(self.fill_ratio, "fill_ratio")
            )
        object.__setattr__(self, "confidence", _confidence_4(self.confidence))
        if self.level not in _LEVELS:
            raise ForecastAlertError(
                "INVALID_FORECAST_LEVEL", "level must be info, warning, or critical"
            )
        if self.nowcast_status is None and self.status is not None:
            object.__setattr__(self, "nowcast_status", self.status)
        if self.nowcast_published_at is None and self.nowcast_status in {
            "completed",
            "published",
            "ready",
        }:
            # A legacy caller may omit this field; a strict finalizer should
            # still provide it, but keep the DTO self-consistent.
            object.__setattr__(self, "nowcast_published_at", self.issued_at)
        if self.nowcast_deadline_at is None and self.deadline_at is not None:
            object.__setattr__(self, "nowcast_deadline_at", self.deadline_at)
        if self.nowcast_published_at is not None:
            object.__setattr__(
                self,
                "nowcast_published_at",
                _aware_utc(self.nowcast_published_at, "nowcast_published_at"),
            )
        if self.nowcast_deadline_at is not None:
            object.__setattr__(
                self,
                "nowcast_deadline_at",
                _aware_utc(self.nowcast_deadline_at, "nowcast_deadline_at"),
            )
        if self.observation_ingest_event_id is not None:
            object.__setattr__(
                self,
                "observation_ingest_event_id",
                _uuid(self.observation_ingest_event_id, "observation_ingest_event_id"),
            )


@dataclass(frozen=True, slots=True)
class ForecastAlert:
    """Durable forecast alert aggregate returned by the storage adapter."""

    id: uuid.UUID
    network_id: uuid.UUID
    nowcast_run_id: uuid.UUID
    nowcast_issued_at: datetime
    simulation_id: uuid.UUID
    network_revision_id: uuid.UUID
    target_manifest_id: uuid.UUID
    target_manifest_sha256: str
    probability_calibration_id: uuid.UUID
    probability_calibration_sha256: str
    forecast_rule_id: uuid.UUID
    forecast_rule_sha256: str
    alert_type: str
    target_type: str
    target_id: uuid.UUID
    target_stable_key: str
    expected_at: datetime
    lead_time_minutes: int
    phase: str
    status: str
    level: str
    confidence: float
    confidence_method: str
    threshold: Mapping[str, Any]
    measurements: Mapping[str, Any]
    risk_score: float
    risk_policy_version: str
    suppression_key: str
    first_qualifying_at: datetime
    last_qualifying_at: datetime
    peak_at: datetime
    peak_value: float
    version: int
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None
    observation_ingest_event_id: uuid.UUID | None = None
    observed_at: datetime | None = None
    acknowledged_by: uuid.UUID | None = None
    acknowledged_at: datetime | None = None
    resolved_by: uuid.UUID | None = None
    resolved_at: datetime | None = None
    resolution_reason: str | None = None
    expiry_grace_minutes: int = 0

    def __post_init__(self) -> None:
        from types import MappingProxyType

        if self.phase not in {"predicted", "observed"}:
            raise ForecastAlertError(
                "INVALID_FORECAST_TRANSITION", "invalid forecast phase"
            )
        if self.status not in _ACTIVE_STATUSES | _TERMINAL_STATUSES:
            raise ForecastAlertError(
                "INVALID_FORECAST_TRANSITION", "invalid forecast status"
            )
        if self.level not in _LEVELS:
            raise ForecastAlertError("INVALID_FORECAST_LEVEL", "invalid forecast level")
        observed = (
            self.observation_ingest_event_id is not None
            and self.observed_at is not None
        )
        if (self.phase == "observed") != observed:
            raise ForecastAlertError(
                "INVALID_FORECAST_TRANSITION",
                "observed phase requires matching observation identity and time",
            )
        object.__setattr__(
            self, "threshold", MappingProxyType(deepcopy(dict(self.threshold)))
        )
        object.__setattr__(
            self, "measurements", MappingProxyType(deepcopy(dict(self.measurements)))
        )

    @property
    def depth_m(self) -> float:
        return float(self.measurements["depth_m"])

    @property
    def metric_value(self) -> float:
        if self.alert_type == "pipe_surcharge":
            return float(self.measurements["fill_ratio"])
        return self.depth_m

    @property
    def threshold_value(self) -> float:
        return float(self.threshold["value"])

    @property
    def risk_scale(self) -> float:
        return float(self.threshold["risk_scale"])

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready alert envelope with explicit measurement units."""

        return {
            "id": str(self.id),
            "type": self.alert_type,
            "status": self.status,
            "phase": self.phase,
            "target": {
                "type": self.target_type,
                "id": str(self.target_id),
                "stable_key": self.target_stable_key,
            },
            "expected_at": _datetime_text(self.expected_at),
            "lead_time_minutes": self.lead_time_minutes,
            "depth_m": self.depth_m,
            "measurements": dict(self.measurements),
            "confidence": self.confidence,
            "confidence_method": self.confidence_method,
            "level": self.level,
            "risk_score": self.risk_score,
            "risk_policy_version": self.risk_policy_version,
            "simulation_id": str(self.simulation_id),
            "nowcast_run_id": str(self.nowcast_run_id),
            "network_revision_id": str(self.network_revision_id),
            "target_manifest_id": str(self.target_manifest_id),
            "target_manifest_sha256": self.target_manifest_sha256,
            "forecast_rule_id": str(self.forecast_rule_id),
            "forecast_rule_sha256": self.forecast_rule_sha256,
            "probability_calibration_id": str(self.probability_calibration_id),
            "probability_calibration_sha256": self.probability_calibration_sha256,
            "observation_ingest_event_id": (
                str(self.observation_ingest_event_id)
                if self.observation_ingest_event_id is not None
                else None
            ),
            "observed_at": (
                _datetime_text(self.observed_at)
                if self.observed_at is not None
                else None
            ),
            "acknowledged_at": (
                _datetime_text(self.acknowledged_at)
                if self.acknowledged_at is not None
                else None
            ),
            "resolved_at": (
                _datetime_text(self.resolved_at)
                if self.resolved_at is not None
                else None
            ),
            "resolution_reason": self.resolution_reason,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ForecastAlertWindow:
    """PostgreSQL-backed event-window state for one suppression key."""

    suppression_key: str
    active_alert_id: uuid.UUID | None
    cooldown_until: datetime | None
    last_candidate_at: datetime
    last_qualifying_at: datetime | None
    suppressed_count: int
    version: int
    created_at: datetime
    updated_at: datetime
    last_alert_id: uuid.UUID | None = None
    last_nowcast_run_id: uuid.UUID | None = None
    last_simulation_id: uuid.UUID | None = None
    last_candidate_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "suppression_key": self.suppression_key,
            "active_alert_id": (
                str(self.active_alert_id) if self.active_alert_id is not None else None
            ),
            "cooldown_until": (
                _datetime_text(self.cooldown_until)
                if self.cooldown_until is not None
                else None
            ),
            "last_candidate_at": _datetime_text(self.last_candidate_at),
            "last_qualifying_at": (
                _datetime_text(self.last_qualifying_at)
                if self.last_qualifying_at is not None
                else None
            ),
            "suppressed_count": self.suppressed_count,
            "version": self.version,
            "last_alert_id": (
                str(self.last_alert_id) if self.last_alert_id is not None else None
            ),
            "last_nowcast_run_id": (
                str(self.last_nowcast_run_id)
                if self.last_nowcast_run_id is not None
                else None
            ),
            "last_simulation_id": (
                str(self.last_simulation_id)
                if self.last_simulation_id is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class ForecastAlertTransition:
    """Audit row returned for the storage adapter to persist."""

    id: uuid.UUID
    alert_id: uuid.UUID
    aggregate_version: int
    from_status: str | None
    to_status: str
    from_phase: str | None
    to_phase: str
    action: str
    reason: str
    actor_id: uuid.UUID | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.action not in {
            "create",
            "acknowledge",
            "observe",
            "resolve",
            "expire",
            "refresh",
            "escalate",
        }:
            raise ForecastAlertError(
                "INVALID_FORECAST_TRANSITION", "unknown transition action"
            )


@dataclass(frozen=True, slots=True)
class ForecastBusinessEvent:
    """Outbox payload returned for atomic persistence with the alert mutation."""

    id: uuid.UUID
    event_id: uuid.UUID
    network_id: uuid.UUID
    nowcast_run_id: uuid.UUID
    simulation_id: uuid.UUID
    event_type: str
    alert_id: uuid.UUID
    aggregate_version: int
    occurred_at: datetime
    alert: ForecastAlert
    expires_at: datetime | None = None

    def payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "aggregate_id": str(self.alert_id),
            "aggregate_version": self.aggregate_version,
            "occurred_at": _datetime_text(self.occurred_at),
            "expires_at": (
                _datetime_text(self.expires_at) if self.expires_at is not None else None
            ),
            "network_id": str(self.network_id),
            "nowcast_run_id": str(self.nowcast_run_id),
            "simulation_id": str(self.simulation_id),
            "alert": self.alert.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AlertMutation:
    """Outcome of publishing one candidate or terminating one alert."""

    action: str
    alert: ForecastAlert | None
    window: ForecastAlertWindow
    transition: ForecastAlertTransition | None = None
    event: ForecastBusinessEvent | None = None
    reason: str = ""


class ForecastAlertTransaction(Protocol):
    """Transactional operations required by :class:`ForecastAlertService`.

    ``lock_event_window`` must use insert-on-conflict followed by ``FOR UPDATE``.
    Implementations must keep the returned transaction open through alert,
    transition, event-window, and outbox writes.
    """

    def lock_event_window(
        self, suppression_key: str, candidate_at: datetime
    ) -> ForecastAlertWindow: ...

    def get_alert(self, alert_id: uuid.UUID) -> ForecastAlert | None: ...

    def insert_alert(self, alert: ForecastAlert) -> None: ...

    def update_alert(self, alert: ForecastAlert, *, expected_version: int) -> None: ...

    def update_event_window(
        self, window: ForecastAlertWindow, *, expected_version: int
    ) -> None: ...

    def insert_transition(self, transition: ForecastAlertTransition) -> None: ...

    def insert_outbox(self, event: ForecastBusinessEvent) -> None: ...


class ForecastAlertStore(Protocol):
    """Transactional persistence port for forecast alerts."""

    def transaction(self) -> ContextManager[ForecastAlertTransaction]: ...


class _MemoryForecastTransaction:
    def __init__(self, store: "InMemoryForecastAlertStore") -> None:
        self.store = store

    def __enter__(self) -> "_MemoryForecastTransaction":
        self.store._lock.acquire()
        self.store._snapshot = (
            dict(self.store.alerts),
            dict(self.store.windows),
            list(self.store.transitions),
            list(self.store.events),
        )
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        try:
            snapshot = self.store._snapshot
            if exc_type is not None and snapshot is not None:
                alerts, windows, transitions, events = snapshot
                self.store.alerts.clear()
                self.store.alerts.update(alerts)
                self.store.windows.clear()
                self.store.windows.update(windows)
                self.store.transitions[:] = transitions
                self.store.events[:] = events
        finally:
            self.store._lock.release()

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
        if alert.id in self.store.alerts:
            raise RuntimeError("forecast alert already exists")
        self.store.alerts[alert.id] = alert

    def update_alert(self, alert: ForecastAlert, *, expected_version: int) -> None:
        current = self.store.alerts[alert.id]
        if current.version != expected_version:
            raise ForecastVersionConflict(current.version)
        self.store.alerts[alert.id] = alert

    def update_event_window(
        self, window: ForecastAlertWindow, *, expected_version: int
    ) -> None:
        current = self.store.windows[window.suppression_key]
        if current.version != expected_version:
            raise RuntimeError("forecast event window version conflict")
        self.store.windows[window.suppression_key] = window

    def insert_transition(self, transition: ForecastAlertTransition) -> None:
        self.store.transitions.append(transition)

    def insert_outbox(self, event: ForecastBusinessEvent) -> None:
        self.store.events.append(event)


class InMemoryForecastAlertStore:
    """Transactional deterministic adapter for tests and single-process demos."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.RLock()
        self._snapshot: (
            tuple[dict[Any, Any], dict[Any, Any], list[Any], list[Any]] | None
        ) = None
        self.alerts: dict[uuid.UUID, ForecastAlert] = {}
        self.windows: dict[str, ForecastAlertWindow] = {}
        self.transitions: list[ForecastAlertTransition] = []
        self.events: list[ForecastBusinessEvent] = []

    def transaction(self) -> ContextManager[ForecastAlertTransaction]:
        return _MemoryForecastTransaction(self)


def _candidate_fingerprint(candidate: ForecastCandidate) -> str:
    payload = {
        "network_id": str(candidate.network_id),
        "nowcast_run_id": str(candidate.nowcast_run_id),
        "issued_at": _datetime_text(candidate.issued_at),
        "simulation_id": str(candidate.simulation_id),
        "network_revision_id": str(candidate.network_revision_id),
        "target_manifest_id": str(candidate.target_manifest_id),
        "target_manifest_sha256": candidate.target_manifest_sha256,
        "alert_type": candidate.alert_type,
        "target_type": candidate.target_type,
        "target_id": str(candidate.target_id),
        "target_stable_key": candidate.target_stable_key,
        "expected_at": _datetime_text(candidate.expected_at),
        "depth_m": candidate.depth_m,
        "fill_ratio": candidate.fill_ratio,
        "confidence": candidate.confidence,
        "level": candidate.level,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_nowcast_gate(candidate: ForecastCandidate) -> None:
    """Reject candidates that are not finalizer-visible before publication."""

    if candidate.nowcast_status is None:
        return  # legacy adapters may resolve the run in their transaction port
    if candidate.nowcast_status not in {"completed", "published", "ready"}:
        raise ForecastAlertError(
            "FORECAST_NOWCAST_NOT_READY",
            "forecast candidate is not from a completed nowcast run",
        )
    if candidate.nowcast_published_at is None:
        raise ForecastAlertError(
            "FORECAST_NOWCAST_NOT_READY",
            "nowcast run has no producer publication timestamp",
        )
    if (
        candidate.nowcast_deadline_at is not None
        and candidate.issued_at > candidate.nowcast_deadline_at
    ):
        raise ForecastAlertError(
            "FORECAST_NOWCAST_LATE",
            "forecast candidate arrived after the nowcast deadline",
        )


def _derive_level(
    candidate: ForecastCandidate, rule: ForecastRuleRef, metric_value: float
) -> str:
    """Return the maximum level permitted by the immutable rule scale."""

    excess = (metric_value - rule.threshold_value) / rule.risk_scale
    if rule.operator in {"<", "<="}:
        excess = (rule.threshold_value - metric_value) / rule.risk_scale
    if excess >= 0.2:
        maximum = "critical"
    elif excess > 0.0:
        maximum = "warning"
    else:
        maximum = "info"
    if _LEVEL_ORDER[candidate.level] > _LEVEL_ORDER[maximum]:
        raise ForecastAlertError(
            "FORECAST_LEVEL_POLICY_INVALID",
            "candidate level exceeds the level allowed by the bound rule",
        )
    return candidate.level


def _validate_artifacts(
    rule: ForecastRuleRef,
    calibration: ProbabilityCalibrationRef,
    resolver: ForecastArtifactResolver | None,
    *,
    require_resolver: bool = False,
) -> None:
    if resolver is None:
        if require_resolver:
            raise ForecastAlertError(
                "FORECAST_POLICY_UNAVAILABLE",
                "no controlled forecast artifact resolver is configured",
            )
        # Annotated so the heterogeneous pair is a union, not the object join.
        refs: tuple[ForecastRuleRef | ProbabilityCalibrationRef, ...] = (
            rule,
            calibration,
        )
        for ref in refs:
            uri = getattr(ref, "artifact_uri", None)
            if not uri:
                continue
            candidate = Path(str(uri))
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ForecastAlertError(
                    "FORECAST_POLICY_INVALID",
                    "artifact_uri must be a relative controlled path",
                )
            try:
                observed = hashlib.sha256(candidate.read_bytes()).hexdigest()
            except OSError as exc:
                raise ForecastAlertError(
                    "FORECAST_POLICY_UNAVAILABLE", "forecast artifact cannot be read"
                ) from exc
            if observed != ref.artifact_sha256:
                raise ForecastAlertError(
                    "FORECAST_POLICY_INVALID", "forecast artifact hash does not match"
                )
        return
    for kind, artifact_id, artifact_sha256 in (
        ("forecast_rule", rule.id, rule.artifact_sha256),
        ("probability_calibration", calibration.id, calibration.artifact_sha256),
    ):
        try:
            resolved = resolver.verify(
                artifact_id=artifact_id,
                artifact_sha256=artifact_sha256,
                artifact_kind=kind,
            )
        except Exception as exc:
            raise ForecastAlertError(
                "FORECAST_POLICY_UNAVAILABLE",
                f"{kind} artifact could not be verified",
            ) from exc
        if not isinstance(resolved, Mapping):
            raise ForecastAlertError(
                "FORECAST_POLICY_INVALID",
                f"{kind} artifact resolver returned no record",
            )
        if resolved.get("sha256") != artifact_sha256:
            raise ForecastAlertError(
                "FORECAST_POLICY_INVALID", f"{kind} artifact hash does not match"
            )
        if int(resolved.get("schema_version", 0)) < 1:
            raise ForecastAlertError(
                "FORECAST_POLICY_INVALID", f"{kind} artifact schema is invalid"
            )


def _validate_target_binding(
    candidate: ForecastCandidate,
    resolver: ForecastTargetResolver | None,
    *,
    required: bool = False,
) -> None:
    if resolver is None:
        if candidate.target_manifest is not None:
            manifest = candidate.target_manifest
            target_ids = (
                manifest.get("target_ids", {}) if isinstance(manifest, Mapping) else {}
            )
            collection = "nodes" if candidate.target_type == "node" else "pipes"
            entries = target_ids.get(
                candidate.target_type, target_ids.get(collection, [])
            )
            if isinstance(entries, Mapping):
                found = entries.get(candidate.target_stable_key)
                valid = str(found) == str(candidate.target_id)
            elif isinstance(entries, Sequence) and not isinstance(
                entries, (str, bytes)
            ):
                valid = any(
                    isinstance(entry, Mapping)
                    and str(entry.get("stable_key", entry.get("id", "")))
                    == candidate.target_stable_key
                    and str(entry.get("id", entry.get("target_id", "")))
                    == str(candidate.target_id)
                    for entry in entries
                )
            else:
                valid = candidate.target_stable_key in set(entries or ())
            if not valid:
                raise ForecastAlertError(
                    "FORECAST_TARGET_BINDING_INVALID",
                    "target stable key is not bound to the target id in the manifest",
                )
        elif required:
            raise ForecastAlertError(
                "FORECAST_TARGET_BINDING_UNAVAILABLE",
                "a revision-scoped target manifest resolver is required",
            )
        return
    if not resolver.resolve(
        network_id=candidate.network_id,
        network_revision_id=candidate.network_revision_id,
        target_manifest_id=candidate.target_manifest_id,
        target_manifest_sha256=candidate.target_manifest_sha256,
        target_type=candidate.target_type,
        target_id=candidate.target_id,
        target_stable_key=candidate.target_stable_key,
    ):
        raise ForecastAlertError(
            "FORECAST_TARGET_BINDING_INVALID",
            "target stable key is not bound to the target id in the manifest",
        )


def _expected_alert_shape(
    candidate: ForecastCandidate,
    rule: ForecastRuleRef,
    calibration: ProbabilityCalibrationRef,
) -> tuple[str, float]:
    expected_target_type = (
        "node" if candidate.alert_type == "flood_forecast" else "pipe_segment"
    )
    if candidate.target_type != expected_target_type:
        raise ForecastAlertError(
            "INVALID_FORECAST_TARGET",
            f"{candidate.alert_type} requires target_type={expected_target_type}",
        )
    if rule.alert_type != candidate.alert_type:
        raise ForecastAlertError(
            "FORECAST_POLICY_INVALID",
            "forecast rule alert_type does not match candidate",
        )
    expected_metric = (
        "water_depth_m" if candidate.alert_type == "flood_forecast" else "fill_ratio"
    )
    if rule.metric != expected_metric or calibration.metric != expected_metric:
        raise ForecastAlertError(
            "FORECAST_POLICY_INVALID",
            "forecast rule/calibration metric is out of domain",
        )
    if candidate.alert_type == "pipe_surcharge" and candidate.fill_ratio is None:
        raise ForecastAlertError(
            "INVALID_FORECAST_MEASUREMENT", "pipe surcharge requires fill_ratio"
        )
    issued_at = candidate.issued_at
    if candidate.expected_at < issued_at:
        raise ForecastAlertError(
            "INVALID_FORECAST_TIME", "expected_at cannot precede issued_at"
        )
    if (
        candidate.expected_at.second
        or candidate.expected_at.microsecond
        or candidate.expected_at.minute % 5
    ):
        raise ForecastAlertError(
            "INVALID_FORECAST_TIME",
            "expected_at must be on the five-minute forecast grid",
        )
    if not rule.valid_from <= issued_at < rule.valid_until:
        raise ForecastAlertError(
            "FORECAST_POLICY_INVALID", "forecast rule is not valid at issued_at"
        )
    if not calibration.valid_from <= issued_at < calibration.valid_until:
        raise ForecastAlertError(
            "FORECAST_POLICY_INVALID",
            "probability calibration is not valid at issued_at",
        )
    lead_time_minutes = calculate_lead_time_minutes(
        issued_at=issued_at, expected_at=candidate.expected_at
    )
    if candidate.alert_type == "pipe_surcharge":
        if candidate.fill_ratio is None:
            raise ForecastAlertError(
                "INVALID_FORECAST_MEASUREMENT", "pipe surcharge requires fill_ratio"
            )
        metric_value = candidate.fill_ratio
    else:
        metric_value = candidate.depth_m
    if (
        not calibration.lead_min_minutes
        <= lead_time_minutes
        <= calibration.lead_max_minutes
    ):
        raise ForecastAlertError(
            "FORECAST_POLICY_INVALID", "lead time is outside calibration applicability"
        )
    if not calibration.magnitude_min <= metric_value <= calibration.magnitude_max:
        raise ForecastAlertError(
            "FORECAST_POLICY_INVALID", "magnitude is outside calibration applicability"
        )
    if not _OPERATORS[rule.operator](metric_value, rule.threshold_value):
        raise ForecastAlertError(
            "FORECAST_BELOW_THRESHOLD", "forecast candidate does not exceed its rule"
        )
    return expected_metric, metric_value


def _measurements(candidate: ForecastCandidate) -> dict[str, float | None]:
    return {
        "depth_m": candidate.depth_m,
        "fill_ratio": candidate.fill_ratio,
    }


def _threshold(rule: ForecastRuleRef) -> dict[str, Any]:
    return {
        "metric": rule.metric,
        "operator": rule.operator,
        "value": rule.threshold_value,
        "unit": rule.threshold_unit,
        "rule_id": str(rule.id),
        "rule_version": rule.version,
        "rule_artifact_sha256": rule.artifact_sha256,
        "rule_schema_version": rule.artifact_schema_version,
        "expiry_grace_minutes": rule.expiry_grace_minutes,
        "risk_scale": rule.risk_scale,
    }


def _risk(
    candidate: ForecastCandidate,
    rule: ForecastRuleRef,
    metric_value: float,
) -> RiskAssessment:
    lead_time_minutes = calculate_lead_time_minutes(
        issued_at=candidate.issued_at, expected_at=candidate.expected_at
    )
    try:
        return calculate_risk(
            confidence=_confidence_4(candidate.confidence),
            level=candidate.level,
            phase="predicted",
            lead_time_minutes=lead_time_minutes,
            metric_value=metric_value,
            threshold_value=rule.threshold_value,
            risk_scale=rule.risk_scale,
            policy_version=rule.risk_policy_version,
        )
    except RiskInputError as exc:
        raise ForecastAlertError("FORECAST_POLICY_INVALID", str(exc)) from exc


def _transition(
    *,
    alert: ForecastAlert,
    action: str,
    reason: str,
    occurred_at: datetime,
    from_status: str | None = None,
    to_status: str | None = None,
    from_phase: str | None = None,
    to_phase: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> ForecastAlertTransition:
    return ForecastAlertTransition(
        id=uuid.uuid4(),
        alert_id=alert.id,
        aggregate_version=alert.version,
        from_status=from_status,
        to_status=to_status or alert.status,
        from_phase=from_phase,
        to_phase=to_phase or alert.phase,
        action=action,
        reason=reason,
        actor_id=actor_id,
        occurred_at=occurred_at,
    )


def _event(
    *, alert: ForecastAlert, event_type: str, occurred_at: datetime
) -> ForecastBusinessEvent:
    return ForecastBusinessEvent(
        id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        network_id=alert.network_id,
        nowcast_run_id=alert.nowcast_run_id,
        simulation_id=alert.simulation_id,
        event_type=event_type,
        alert_id=alert.id,
        aggregate_version=alert.version,
        occurred_at=occurred_at,
        alert=alert,
        expires_at=occurred_at + timedelta(hours=168),
    )


class ForecastAlertService:
    """Create, refresh, suppress, and terminate forecast alert episodes."""

    def __init__(
        self,
        store: ForecastAlertStore,
        *,
        artifact_resolver: ForecastArtifactResolver | None = None,
        target_resolver: ForecastTargetResolver | None = None,
        artifact_registry: ForecastArtifactResolver | None = None,
        policy_registry: ForecastArtifactResolver | None = None,
        target_manifest_resolver: ForecastTargetResolver | None = None,
        require_target_binding: bool = True,
        require_verified_artifacts: bool = True,
        nowcast_gate: Any | None = None,
    ) -> None:
        self._store = store
        self._artifact_resolver = (
            artifact_resolver or artifact_registry or policy_registry
        )
        self._target_resolver = target_resolver or target_manifest_resolver
        self._require_target_binding = bool(require_target_binding)
        self._require_verified_artifacts = bool(require_verified_artifacts)
        self._nowcast_gate = nowcast_gate

    @contextmanager
    def _transaction(self):
        """Use the durable port and provide rollback for simple test stores."""

        snapshot: dict[str, Any] = {}
        for name in ("alerts", "windows", "transitions", "events"):
            value = getattr(self._store, name, None)
            if isinstance(value, dict):
                snapshot[name] = dict(value)
            elif isinstance(value, list):
                snapshot[name] = list(value)
        try:
            with self._store.transaction() as transaction:
                yield transaction
        except Exception:
            for name, value in snapshot.items():
                current = getattr(self._store, name, None)
                if isinstance(current, dict):
                    current.clear()
                    current.update(value)
                elif isinstance(current, list):
                    current[:] = value
            raise

    def publish(
        self,
        candidate: ForecastCandidate,
        rule: ForecastRuleRef,
        calibration: ProbabilityCalibrationRef,
    ) -> AlertMutation:
        """Materialize one qualifying candidate with event-window idempotency."""

        _validate_nowcast_gate(candidate)
        if self._nowcast_gate is not None:
            gate_result = self._nowcast_gate(candidate)
            if inspect.isawaitable(gate_result):
                # Publishing is intentionally synchronous; async gates belong
                # in the durable finalizer before this method is called.
                raise ForecastAlertError(
                    "FORECAST_POLICY_UNAVAILABLE", "nowcast gate must be synchronous"
                )
            if gate_result is False:
                raise ForecastAlertError(
                    "FORECAST_NOWCAST_NOT_READY", "nowcast gate rejected candidate"
                )
        _validate_artifacts(
            rule,
            calibration,
            self._artifact_resolver,
            require_resolver=self._require_verified_artifacts,
        )
        _validate_target_binding(
            candidate,
            self._target_resolver,
            required=self._require_target_binding,
        )
        _, metric_value = _expected_alert_shape(candidate, rule, calibration)
        effective_level = _derive_level(candidate, rule, metric_value)
        risk = _risk(replace(candidate, level=effective_level), rule, metric_value)
        lead_time_minutes = calculate_lead_time_minutes(
            issued_at=candidate.issued_at, expected_at=candidate.expected_at
        )
        suppression_key = calculate_suppression_key(
            network_id=candidate.network_id,
            alert_type=candidate.alert_type,
            target_type=candidate.target_type,
            target_id=candidate.target_id,
            rule_id=rule.id,
        )
        candidate_fingerprint = _candidate_fingerprint(candidate)

        with self._transaction() as transaction:
            window = transaction.lock_event_window(suppression_key, candidate.issued_at)
            if window.suppression_key != suppression_key:
                raise ForecastAlertError(
                    "FORECAST_WINDOW_CONFLICT",
                    "locked event window has a different suppression key",
                )
            if (
                window.last_nowcast_run_id == candidate.nowcast_run_id
                and window.last_candidate_fingerprint is not None
            ):
                if window.last_candidate_fingerprint != candidate_fingerprint:
                    raise ForecastAlertError(
                        "FORECAST_CANDIDATE_CONFLICT",
                        "the same nowcast run cannot change an existing candidate",
                    )
                original = (
                    transaction.get_alert(window.last_alert_id)
                    if window.last_alert_id is not None
                    else None
                )
                if original is not None:
                    return AlertMutation(
                        action="unchanged",
                        alert=original,
                        window=window,
                        reason="idempotent_replay",
                    )
            if candidate.issued_at < window.last_candidate_at:
                raise ForecastAlertError(
                    "FORECAST_OUT_OF_ORDER",
                    "candidate issued_at is older than the event window",
                )

            if window.active_alert_id is not None:
                return self._refresh_active(
                    transaction=transaction,
                    window=window,
                    candidate=candidate,
                    rule=rule,
                    calibration=calibration,
                    metric_value=metric_value,
                    lead_time_minutes=lead_time_minutes,
                    risk=risk,
                )

            if (
                window.cooldown_until is not None
                and candidate.issued_at < window.cooldown_until
            ):
                updated_window = replace(
                    window,
                    last_candidate_at=candidate.issued_at,
                    last_qualifying_at=candidate.issued_at,
                    suppressed_count=window.suppressed_count + 1,
                    version=window.version + 1,
                    updated_at=candidate.issued_at,
                )
                transaction.update_event_window(
                    updated_window, expected_version=window.version
                )
                return AlertMutation(
                    action="suppressed",
                    alert=None,
                    window=updated_window,
                    reason="cooldown",
                )

            alert = self._create_alert(
                candidate=replace(candidate, level=effective_level),
                rule=rule,
                calibration=calibration,
                metric_value=metric_value,
                lead_time_minutes=lead_time_minutes,
                risk=risk,
                suppression_key=suppression_key,
            )
            updated_window = replace(
                window,
                active_alert_id=alert.id,
                cooldown_until=None,
                last_candidate_at=candidate.issued_at,
                last_qualifying_at=candidate.issued_at,
                last_alert_id=alert.id,
                last_nowcast_run_id=candidate.nowcast_run_id,
                last_simulation_id=candidate.simulation_id,
                last_candidate_fingerprint=candidate_fingerprint,
                version=window.version + 1,
                updated_at=candidate.issued_at,
            )
            transition = _transition(
                alert=alert,
                action="create",
                reason="forecast_threshold_exceeded",
                occurred_at=candidate.issued_at,
                from_status=None,
                from_phase=None,
            )
            event = _event(
                alert=alert, event_type="alert.created", occurred_at=candidate.issued_at
            )
            transaction.insert_alert(alert)
            transaction.update_event_window(
                updated_window, expected_version=window.version
            )
            transaction.insert_transition(transition)
            transaction.insert_outbox(event)
            return AlertMutation(
                action="created",
                alert=alert,
                window=updated_window,
                transition=transition,
                event=event,
            )

    def acknowledge(
        self,
        *,
        alert_id: uuid.UUID,
        expected_version: int,
        occurred_at: datetime,
        reason: str,
        actor_id: uuid.UUID | None = None,
    ) -> AlertMutation:
        """Apply the public open→acknowledged transition."""

        if not reason or not reason.strip():
            raise ForecastTransitionError("acknowledgement reason must not be empty")
        normalized_alert_id = _uuid(alert_id, "alert_id")
        occurred = _aware_utc(occurred_at, "occurred_at")
        actor = _uuid(actor_id, "actor_id") if actor_id is not None else None
        with self._transaction() as transaction:
            alert = transaction.get_alert(normalized_alert_id)
            if alert is None:
                raise ForecastAlertError(
                    "FORECAST_ALERT_NOT_FOUND", "forecast alert does not exist"
                )
            if alert.version != expected_version:
                raise ForecastVersionConflict(alert.version)
            if alert.status != "open":
                raise ForecastTransitionError("only open alerts can be acknowledged")
            updated = replace(
                alert,
                status="acknowledged",
                acknowledged_at=occurred,
                acknowledged_by=actor,
                version=alert.version + 1,
                updated_at=occurred,
            )
            window = transaction.lock_event_window(alert.suppression_key, occurred)
            if occurred < window.last_candidate_at:
                raise ForecastTransitionError(
                    "acknowledgement is older than the latest candidate"
                )
            if window.active_alert_id != alert.id:
                raise ForecastTransitionError(
                    "forecast alert is not the active episode"
                )
            updated_window = replace(
                window,
                last_candidate_at=occurred,
                version=window.version + 1,
                updated_at=occurred,
            )
            transition = _transition(
                alert=updated,
                action="acknowledge",
                reason=reason.strip(),
                occurred_at=occurred,
                from_status=alert.status,
                from_phase=alert.phase,
                actor_id=actor,
            )
            event = _event(
                alert=updated, event_type="alert.updated", occurred_at=occurred
            )
            transaction.update_alert(updated, expected_version=alert.version)
            transaction.update_event_window(
                updated_window, expected_version=window.version
            )
            transaction.insert_transition(transition)
            transaction.insert_outbox(event)
            return AlertMutation(
                action="acknowledged",
                alert=updated,
                window=updated_window,
                transition=transition,
                event=event,
            )

    def observe(
        self,
        *,
        alert_id: uuid.UUID,
        expected_version: int,
        observation_ingest_event_id: uuid.UUID,
        observed_at: datetime,
        reason: str,
        metric_value: float | None = None,
        actor_id: uuid.UUID | None = None,
        trust_level: str = "external_verified",
        auth_verified: bool = True,
        binding_verified: bool = True,
        quality: str = "good",
    ) -> AlertMutation:
        """Bind a verified observation and move predicted→observed once."""

        if not reason or not reason.strip():
            raise ForecastTransitionError("observation reason must not be empty")
        if (
            trust_level != "external_verified"
            or auth_verified is not True
            or binding_verified is not True
            or quality != "good"
        ):
            raise ForecastTransitionError("observation is not a verified good sample")
        normalized_alert_id = _uuid(alert_id, "alert_id")
        event_id = _uuid(observation_ingest_event_id, "observation_ingest_event_id")
        observed = _aware_utc(observed_at, "observed_at")
        actor = _uuid(actor_id, "actor_id") if actor_id is not None else None
        with self._transaction() as transaction:
            alert = transaction.get_alert(normalized_alert_id)
            if alert is None:
                raise ForecastAlertError(
                    "FORECAST_ALERT_NOT_FOUND", "forecast alert does not exist"
                )
            if alert.version != expected_version:
                raise ForecastVersionConflict(alert.version)
            if alert.status not in _ACTIVE_STATUSES or alert.phase != "predicted":
                raise ForecastTransitionError(
                    "only predicted active alerts can be observed"
                )
            if observed < alert.nowcast_issued_at:
                raise ForecastTransitionError("observation predates nowcast issue")
            if observed > alert.expected_at + timedelta(
                minutes=alert.expiry_grace_minutes
            ):
                raise ForecastTransitionError("observation is outside the expiry grace")
            value = (
                alert.metric_value
                if metric_value is None
                else _finite(metric_value, "metric_value")
            )
            operator = str(alert.threshold.get("operator", ">="))
            threshold = float(alert.threshold.get("value", 0.0))
            if not _OPERATORS[operator](value, threshold):
                raise ForecastTransitionError(
                    "observation does not satisfy the bound rule"
                )
            updated = replace(
                alert,
                phase="observed",
                observation_ingest_event_id=event_id,
                observed_at=observed,
                version=alert.version + 1,
                updated_at=observed,
            )
            window = transaction.lock_event_window(alert.suppression_key, observed)
            if observed < window.last_candidate_at:
                raise ForecastTransitionError(
                    "observation is older than the latest candidate"
                )
            updated_window = replace(
                window,
                last_candidate_at=observed,
                last_qualifying_at=observed,
                version=window.version + 1,
                updated_at=observed,
            )
            transition = _transition(
                alert=updated,
                action="observe",
                reason=reason.strip(),
                occurred_at=observed,
                from_status=alert.status,
                from_phase=alert.phase,
                actor_id=actor,
            )
            event = _event(
                alert=updated, event_type="alert.observed", occurred_at=observed
            )
            transaction.update_alert(updated, expected_version=alert.version)
            transaction.update_event_window(
                updated_window, expected_version=window.version
            )
            transaction.insert_transition(transition)
            transaction.insert_outbox(event)
            return AlertMutation(
                action="observed",
                alert=updated,
                window=updated_window,
                transition=transition,
                event=event,
            )

    def refresh(
        self,
        candidate: ForecastCandidate,
        rule: ForecastRuleRef,
        calibration: ProbabilityCalibrationRef,
    ) -> AlertMutation:
        """Explicit internal alias for a candidate refresh/escalation."""

        return self.publish(candidate, rule, calibration)

    mark_observed = observe
    acknowledge_alert = acknowledge

    def mark_terminal(
        self,
        *,
        alert_id: uuid.UUID,
        expected_version: int,
        terminal_status: str,
        occurred_at: datetime,
        reason: str,
        actor_id: uuid.UUID | None = None,
    ) -> AlertMutation:
        """Resolve/expire an active alert and start its exact 30-minute cooldown."""

        if terminal_status not in _TERMINAL_STATUSES:
            raise ForecastTransitionError("terminal_status must be resolved or expired")
        if not reason or not reason.strip():
            raise ForecastTransitionError("terminal reason must not be empty")
        normalized_alert_id = _uuid(alert_id, "alert_id")
        terminal_at = _aware_utc(occurred_at, "occurred_at")
        if actor_id is not None:
            actor_id = _uuid(actor_id, "actor_id")

        with self._transaction() as transaction:
            alert = transaction.get_alert(normalized_alert_id)
            if alert is None:
                raise ForecastAlertError(
                    "FORECAST_ALERT_NOT_FOUND", "forecast alert does not exist"
                )
            if alert.version != expected_version:
                raise ForecastVersionConflict(alert.version)
            if alert.status not in _ACTIVE_STATUSES:
                raise ForecastTransitionError("forecast alert is already terminal")
            if terminal_status == "expired":
                if alert.phase != "predicted":
                    raise ForecastTransitionError("observed alerts cannot expire")
                earliest_expiry = alert.expected_at + timedelta(
                    minutes=max(0, int(alert.expiry_grace_minutes))
                )
                if terminal_at <= earliest_expiry:
                    raise ForecastTransitionError(
                        "forecast alert cannot expire before expected_at plus grace"
                    )

            window = transaction.lock_event_window(alert.suppression_key, terminal_at)
            if window.active_alert_id != alert.id:
                raise ForecastTransitionError(
                    "forecast alert is not the active event-window episode"
                )
            if terminal_at < window.last_candidate_at:
                raise ForecastTransitionError(
                    "terminal transition cannot precede the latest candidate"
                )
            updated_alert = replace(
                alert,
                status=terminal_status,
                terminal_at=terminal_at,
                resolved_at=(
                    terminal_at if terminal_status == "resolved" else alert.resolved_at
                ),
                resolution_reason=(
                    reason.strip()
                    if terminal_status == "resolved"
                    else alert.resolution_reason
                ),
                resolved_by=(
                    actor_id if terminal_status == "resolved" else alert.resolved_by
                ),
                version=alert.version + 1,
                updated_at=terminal_at,
            )
            updated_window = replace(
                window,
                active_alert_id=None,
                cooldown_until=terminal_at + SUPPRESSION_WINDOW,
                last_candidate_at=terminal_at,
                last_alert_id=alert.id,
                last_nowcast_run_id=alert.nowcast_run_id,
                last_simulation_id=alert.simulation_id,
                version=window.version + 1,
                updated_at=terminal_at,
            )
            transition = _transition(
                alert=updated_alert,
                action="resolve" if terminal_status == "resolved" else "expire",
                reason=reason.strip(),
                occurred_at=terminal_at,
                from_status=alert.status,
                from_phase=alert.phase,
                actor_id=actor_id,
            )
            event = _event(
                alert=updated_alert,
                event_type=f"alert.{terminal_status}",
                occurred_at=terminal_at,
            )
            transaction.update_alert(updated_alert, expected_version=alert.version)
            transaction.update_event_window(
                updated_window, expected_version=window.version
            )
            transaction.insert_transition(transition)
            transaction.insert_outbox(event)
            return AlertMutation(
                action="terminal",
                alert=updated_alert,
                window=updated_window,
                transition=transition,
                event=event,
            )

    @staticmethod
    def _create_alert(
        *,
        candidate: ForecastCandidate,
        rule: ForecastRuleRef,
        calibration: ProbabilityCalibrationRef,
        metric_value: float,
        lead_time_minutes: int,
        risk: RiskAssessment,
        suppression_key: str,
    ) -> ForecastAlert:
        alert_id = uuid.uuid4()
        return ForecastAlert(
            id=alert_id,
            network_id=candidate.network_id,
            nowcast_run_id=candidate.nowcast_run_id,
            nowcast_issued_at=candidate.issued_at,
            simulation_id=candidate.simulation_id,
            network_revision_id=candidate.network_revision_id,
            target_manifest_id=candidate.target_manifest_id,
            target_manifest_sha256=candidate.target_manifest_sha256,
            probability_calibration_id=calibration.id,
            probability_calibration_sha256=calibration.artifact_sha256,
            forecast_rule_id=rule.id,
            forecast_rule_sha256=rule.artifact_sha256,
            alert_type=candidate.alert_type,
            target_type=candidate.target_type,
            target_id=candidate.target_id,
            target_stable_key=candidate.target_stable_key,
            expected_at=candidate.expected_at,
            lead_time_minutes=lead_time_minutes,
            phase="predicted",
            status="open",
            level=candidate.level,
            confidence=candidate.confidence,
            confidence_method=calibration.method,
            threshold=_threshold(rule),
            measurements=_measurements(candidate),
            risk_score=risk.score,
            risk_policy_version=risk.policy_version,
            suppression_key=suppression_key,
            first_qualifying_at=candidate.issued_at,
            last_qualifying_at=candidate.issued_at,
            peak_at=candidate.issued_at,
            peak_value=metric_value,
            version=1,
            created_at=candidate.issued_at,
            updated_at=candidate.issued_at,
            expiry_grace_minutes=rule.expiry_grace_minutes,
        )

    def _refresh_active(
        self,
        *,
        transaction: ForecastAlertTransaction,
        window: ForecastAlertWindow,
        candidate: ForecastCandidate,
        rule: ForecastRuleRef,
        calibration: ProbabilityCalibrationRef,
        metric_value: float,
        lead_time_minutes: int,
        risk: RiskAssessment,
    ) -> AlertMutation:
        assert window.active_alert_id is not None
        current = transaction.get_alert(window.active_alert_id)
        if current is None:
            raise ForecastAlertError(
                "FORECAST_WINDOW_CONFLICT",
                "event window points to a missing active alert",
            )
        if current.status not in _ACTIVE_STATUSES:
            raise ForecastTransitionError(
                "active event window contains a terminal alert"
            )
        if current.network_id != candidate.network_id:
            raise ForecastAlertError(
                "FORECAST_WINDOW_CONFLICT",
                "event window network does not match candidate",
            )
        if (
            current.alert_type != candidate.alert_type
            or current.target_type != candidate.target_type
        ):
            raise ForecastAlertError(
                "FORECAST_WINDOW_CONFLICT",
                "event window target does not match candidate",
            )
        if current.target_id != candidate.target_id:
            raise ForecastAlertError(
                "FORECAST_WINDOW_CONFLICT",
                "event window target does not match candidate",
            )
        if (
            current.target_stable_key != candidate.target_stable_key
            or current.network_revision_id != candidate.network_revision_id
            or current.target_manifest_id != candidate.target_manifest_id
            or current.target_manifest_sha256 != candidate.target_manifest_sha256
        ):
            raise ForecastAlertError(
                "FORECAST_TARGET_BINDING_INVALID",
                "active episode cannot cross target, revision, or manifest identity",
            )
        if (
            current.forecast_rule_id != rule.id
            or current.forecast_rule_sha256 != rule.artifact_sha256
            or current.probability_calibration_id != calibration.id
            or current.probability_calibration_sha256 != calibration.artifact_sha256
        ):
            raise ForecastAlertError(
                "FORECAST_POLICY_LINEAGE_CONFLICT",
                "active episode cannot change an immutable rule or calibration",
            )

        measurements = _measurements(candidate)
        same_run_values = (
            current.nowcast_run_id == candidate.nowcast_run_id
            and current.nowcast_issued_at == candidate.issued_at
            and current.simulation_id == candidate.simulation_id
            and current.network_revision_id == candidate.network_revision_id
            and current.target_manifest_id == candidate.target_manifest_id
            and current.target_manifest_sha256 == candidate.target_manifest_sha256
            and current.target_stable_key == candidate.target_stable_key
            and current.probability_calibration_id == calibration.id
            and current.probability_calibration_sha256 == calibration.artifact_sha256
            and current.forecast_rule_id == rule.id
            and current.forecast_rule_sha256 == rule.artifact_sha256
            and current.expected_at == candidate.expected_at
            and current.depth_m == candidate.depth_m
            and current.measurements.get("fill_ratio") == candidate.fill_ratio
            and current.confidence == candidate.confidence
            and current.level == candidate.level
        )
        if current.nowcast_run_id == candidate.nowcast_run_id:
            if same_run_values:
                return AlertMutation(
                    action="unchanged",
                    alert=current,
                    window=window,
                    reason="idempotent_replay",
                )
            raise ForecastAlertError(
                "FORECAST_CANDIDATE_CONFLICT",
                "the same nowcast run cannot change an existing candidate",
            )

        effective_level = (
            candidate.level
            if _LEVEL_ORDER[candidate.level] > _LEVEL_ORDER[current.level]
            else current.level
        )
        _validate_artifacts(
            rule,
            calibration,
            self._artifact_resolver,
            require_resolver=self._require_verified_artifacts,
        )
        risk = _risk(replace(candidate, level=effective_level), rule, metric_value)
        expected_shift_seconds = abs(
            (
                _aware_utc(candidate.expected_at, "expected_at")
                - _aware_utc(current.expected_at, "expected_at")
            ).total_seconds()
        )
        expected_delta = rule.step_minutes * 60 * rule.refresh_expected_shift_steps
        peak_value = max(current.peak_value, metric_value)
        peak_delta = abs(peak_value - current.peak_value)
        confidence_delta = abs(candidate.confidence - current.confidence)
        risk_delta = abs(risk.score - current.risk_score)
        escalated = _LEVEL_ORDER[effective_level] > _LEVEL_ORDER[current.level]
        material_refresh = (
            expected_shift_seconds >= expected_delta
            or peak_delta >= rule.refresh_peak_delta
            or confidence_delta >= rule.refresh_confidence_delta
            or risk_delta >= rule.refresh_risk_delta
        )

        peak_at = current.peak_at
        if metric_value > current.peak_value:
            peak_at = candidate.issued_at
        if (
            current.expected_at == candidate.expected_at
            and current.depth_m == candidate.depth_m
            and current.measurements.get("fill_ratio") == candidate.fill_ratio
            and current.confidence == candidate.confidence
            and effective_level == current.level
        ):
            return AlertMutation(
                action="unchanged",
                alert=current,
                window=window,
                reason="normalized_candidate_equivalent",
            )
        updated_alert = replace(
            current,
            nowcast_run_id=candidate.nowcast_run_id,
            nowcast_issued_at=candidate.issued_at,
            simulation_id=candidate.simulation_id,
            network_revision_id=candidate.network_revision_id,
            target_manifest_id=candidate.target_manifest_id,
            target_manifest_sha256=candidate.target_manifest_sha256,
            probability_calibration_id=calibration.id,
            probability_calibration_sha256=calibration.artifact_sha256,
            confidence_method=calibration.method,
            expected_at=candidate.expected_at,
            lead_time_minutes=lead_time_minutes,
            level=effective_level,
            confidence=candidate.confidence,
            threshold=_threshold(rule),
            measurements=measurements,
            risk_score=risk.score,
            risk_policy_version=risk.policy_version,
            last_qualifying_at=candidate.issued_at,
            peak_at=peak_at,
            peak_value=peak_value,
            version=current.version + 1,
            updated_at=candidate.issued_at,
        )
        updated_window = replace(
            window,
            last_candidate_at=candidate.issued_at,
            last_qualifying_at=candidate.issued_at,
            last_alert_id=updated_alert.id,
            last_nowcast_run_id=candidate.nowcast_run_id,
            last_simulation_id=candidate.simulation_id,
            last_candidate_fingerprint=_candidate_fingerprint(candidate),
            version=window.version + 1,
            updated_at=candidate.issued_at,
        )
        transition = _transition(
            alert=updated_alert,
            action="escalate" if escalated else "refresh",
            reason=(
                "forecast_candidate_escalated"
                if escalated
                else "forecast_candidate_refreshed"
            ),
            occurred_at=candidate.issued_at,
            from_status=current.status,
            from_phase=current.phase,
        )
        event: ForecastBusinessEvent | None = None
        if escalated:
            event = _event(
                alert=updated_alert,
                event_type="alert.escalated",
                occurred_at=candidate.issued_at,
            )
        elif material_refresh:
            event = _event(
                alert=updated_alert,
                event_type="alert.updated",
                occurred_at=candidate.issued_at,
            )
        transaction.update_alert(updated_alert, expected_version=current.version)
        transaction.update_event_window(updated_window, expected_version=window.version)
        transaction.insert_transition(transition)
        if event is not None:
            transaction.insert_outbox(event)
        return AlertMutation(
            action="refreshed",
            alert=updated_alert,
            window=updated_window,
            transition=transition,
            event=event,
            reason="active_window",
        )


__all__ = [
    "FORECAST_HORIZON",
    "FORECAST_STEP_MINUTES",
    "SUPPRESSION_WINDOW",
    "AlertMutation",
    "ForecastAlert",
    "ForecastAlertError",
    "ForecastAlertService",
    "ForecastArtifactResolver",
    "ForecastTargetResolver",
    "ForecastAlertStore",
    "InMemoryForecastAlertStore",
    "ForecastAlertTransaction",
    "ForecastAlertWindow",
    "ForecastBusinessEvent",
    "ForecastCandidate",
    "ForecastRuleRef",
    "ForecastTransitionError",
    "ForecastVersionConflict",
    "ProbabilityCalibrationRef",
    "canonical_suppression_payload",
    "calculate_lead_time_minutes",
    "calculate_suppression_key",
]
