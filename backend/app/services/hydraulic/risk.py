"""Explainable forecast risk scoring and deterministic Top-N ranking.

The risk score is a response-priority score, not a probability of flooding or
of harm.  Its formula and weights are fixed by the v1 forecast-risk policy and
are evaluated only from finite, versioned rule inputs.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Generic, Protocol, Sequence, TypeVar

UTC = timezone.utc
RISK_POLICY_VERSION = "risk-v1"
FORECAST_FRESH_MAX_AGE_SECONDS = 900
MAX_RISK_LEAD_MINUTES = 120

_SEVERITY = {
    "info": 0.25,
    "warning": 0.60,
    "critical": 1.00,
}
_ACTIVE_STATUSES = frozenset({"open", "acknowledged"})
_VALID_PHASES = frozenset({"predicted", "observed"})


class RiskInputError(ValueError):
    """A risk input cannot be scored under the frozen v1 policy."""


@dataclass(frozen=True, slots=True)
class RiskComponents:
    """Normalized terms used by the explainable v1 risk formula."""

    confidence: float
    severity: float
    urgency: float
    impact: float


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """A risk score together with the values that produced it."""

    score: float
    components: RiskComponents
    policy_version: str


class RankableRiskAlert(Protocol):
    """Fields required from a persisted forecast alert for ranking."""

    id: uuid.UUID | str
    network_id: uuid.UUID | str
    nowcast_issued_at: datetime
    status: str
    phase: str
    level: str
    confidence: float
    lead_time_minutes: int
    metric_value: float
    threshold_value: float
    risk_scale: float
    expected_at: datetime
    risk_policy_version: str


RiskAlertT = TypeVar("RiskAlertT", bound=RankableRiskAlert)


@dataclass(frozen=True, slots=True)
class RiskRankingItem(Generic[RiskAlertT]):
    """One ranked alert with its explainable score components."""

    alert: RiskAlertT
    assessment: RiskAssessment

    @property
    def risk_score(self) -> float:
        return self.assessment.score

    @property
    def score_components(self) -> RiskComponents:
        return self.assessment.components

    @property
    def policy_version(self) -> str:
        return self.assessment.policy_version

    def to_dict(self) -> dict[str, Any]:
        """Return the frontend/API-compatible ranked item shape."""

        if not hasattr(self.alert, "to_dict"):
            raise TypeError("ranked alert must expose to_dict()")
        payload = dict(self.alert.to_dict())
        payload["risk_score"] = self.assessment.score
        payload["risk_policy_version"] = self.assessment.policy_version
        payload["score_components"] = {
            "confidence": self.assessment.components.confidence,
            "severity": self.assessment.components.severity,
            "urgency": self.assessment.components.urgency,
            "impact": self.assessment.components.impact,
        }
        payload["policy_version"] = self.assessment.policy_version
        return payload


def _finite_number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise RiskInputError(f"{field_name} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RiskInputError(f"{field_name} must be a finite number") from exc
    if not math.isfinite(result):
        raise RiskInputError(f"{field_name} must be a finite number")
    return result


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RiskInputError(f"{field_name} must be an aware datetime")
    if value.utcoffset() is None:
        raise RiskInputError(f"{field_name} must be an aware datetime")
    return value.astimezone(UTC)


def calculate_risk(
    *,
    confidence: float,
    level: str,
    phase: str,
    lead_time_minutes: int,
    metric_value: float,
    threshold_value: float,
    risk_scale: float,
    policy_version: str = RISK_POLICY_VERSION,
) -> RiskAssessment:
    """Calculate the frozen explainable v1 risk score.

    ``risk_scale`` must come from the run-bound forecast rule.  This function
    intentionally rejects non-finite and out-of-range values instead of
    silently sanitizing an unpublishable forecast.
    """

    confidence_value = _finite_number(confidence, "confidence")
    try:
        confidence_value = float(
            Decimal(str(confidence_value)).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        )
    except (InvalidOperation, ValueError) as exc:
        raise RiskInputError("confidence must be a finite decimal") from exc
    if not 0.0 <= confidence_value <= 1.0:
        raise RiskInputError("confidence must be between 0 and 1")

    normalized_level = str(level)
    if normalized_level not in _SEVERITY:
        raise RiskInputError("level must be info, warning, or critical")
    normalized_phase = str(phase)
    if normalized_phase not in _VALID_PHASES:
        raise RiskInputError("phase must be predicted or observed")

    if isinstance(lead_time_minutes, bool) or not isinstance(lead_time_minutes, int):
        raise RiskInputError("lead_time_minutes must be an integer")
    if not 0 <= lead_time_minutes <= MAX_RISK_LEAD_MINUTES:
        raise RiskInputError("lead_time_minutes must be between 0 and 120")

    metric = _finite_number(metric_value, "metric_value")
    threshold = _finite_number(threshold_value, "threshold_value")
    scale = _finite_number(risk_scale, "risk_scale")
    if scale <= 0.0:
        raise RiskInputError("risk_scale must be greater than zero")

    normalized_policy = str(policy_version).strip()
    if normalized_policy != RISK_POLICY_VERSION:
        raise RiskInputError(f"unsupported risk policy version: {normalized_policy}")

    severity = _SEVERITY[normalized_level]
    urgency = (
        1.0
        if normalized_phase == "observed"
        else max(0.0, 1.0 - lead_time_minutes / MAX_RISK_LEAD_MINUTES)
    )
    impact = min(1.0, max(0.0, (metric - threshold) / scale))
    score = round(
        100.0
        * (0.35 * confidence_value + 0.30 * severity + 0.20 * urgency + 0.15 * impact),
        2,
    )
    return RiskAssessment(
        score=score,
        components=RiskComponents(
            confidence=confidence_value,
            severity=severity,
            urgency=urgency,
            impact=impact,
        ),
        policy_version=normalized_policy,
    )


def _normalize_network_id(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, bool):
        raise RiskInputError("network_id must be a UUID")
    try:
        result = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise RiskInputError("network_id must be a UUID") from exc
    if result.int == 0:
        raise RiskInputError("network_id must not be nil")
    return result


def rank_top_risk(
    alerts: Sequence[RiskAlertT],
    *,
    top_n: int = 20,
    network_id: uuid.UUID | str | None = None,
    as_of: datetime | None = None,
    max_age_seconds: int = FORECAST_FRESH_MAX_AGE_SECONDS,
) -> list[RiskRankingItem[RiskAlertT]]:
    """Return fresh active alerts in canonical Top-N risk order.

    Freshness is evaluated from the source run's immutable ``nowcast_issued_at``
    value.  The scheduler/finalizer remains responsible for detecting a missing
    due slot; this function can only reject alerts whose source timestamp is in
    the future or older than the configured freshness interval.
    """

    if isinstance(top_n, bool) or not isinstance(top_n, int):
        raise RiskInputError("top_n must be an integer")
    if not 1 <= top_n <= 100:
        raise RiskInputError("top_n must be between 1 and 100")
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, int)
        or max_age_seconds < 0
    ):
        raise RiskInputError("max_age_seconds must be a non-negative integer")

    reference_time = _aware_utc(as_of or datetime.now(UTC), "as_of")
    ranked: list[RiskRankingItem[RiskAlertT]] = []
    normalized_network = (
        _normalize_network_id(network_id) if network_id is not None else None
    )
    for alert in alerts:
        alert_network = _normalize_network_id(alert.network_id)
        if normalized_network is not None and alert_network != normalized_network:
            continue
        if alert.status not in _ACTIVE_STATUSES:
            continue
        issued_at = _aware_utc(alert.nowcast_issued_at, "nowcast_issued_at")
        age_seconds = (reference_time - issued_at).total_seconds()
        if age_seconds < 0.0 or age_seconds > max_age_seconds:
            continue
        _aware_utc(alert.expected_at, "expected_at")
        assessment = calculate_risk(
            confidence=alert.confidence,
            level=alert.level,
            phase=alert.phase,
            lead_time_minutes=alert.lead_time_minutes,
            metric_value=alert.metric_value,
            threshold_value=alert.threshold_value,
            risk_scale=alert.risk_scale,
            policy_version=alert.risk_policy_version,
        )
        ranked.append(RiskRankingItem(alert=alert, assessment=assessment))

    ranked.sort(
        key=lambda item: (
            -item.risk_score,
            _aware_utc(item.alert.expected_at, "expected_at"),
            str(item.alert.id),
        )
    )
    return ranked[:top_n]


__all__ = [
    "FORECAST_FRESH_MAX_AGE_SECONDS",
    "MAX_RISK_LEAD_MINUTES",
    "RISK_POLICY_VERSION",
    "RankableRiskAlert",
    "RiskAssessment",
    "RiskComponents",
    "RiskInputError",
    "RiskRankingItem",
    "calculate_risk",
    "rank_top_risk",
]
