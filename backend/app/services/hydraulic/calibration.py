"""Hydraulic parameter calibration against Influx ``water_level_mm`` data.

Raw Influx points remain ``external_unverified`` because the legacy source
has no stable upstream event identity.  They may be used for diagnostic
calibration, but a report can only mark AC-5 eligible when every paired
observation is ``external_verified`` and quality ``good``.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import re
import uuid
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

from app.core.config import settings
from app.services.hydraulic.results import (
    ControlledArtifactReference,
    write_controlled_json_artifact,
)

UTC = timezone.utc
MEASUREMENT_NAME = "sensor_readings"
OBSERVATION_FIELD = "water_level_mm"
MIN_EVALUATION_PAIRS = 12
MIN_EVENT_COUNT = 3
NSE_DELTA_THRESHOLD = 0.05
RMSE_RATIO_THRESHOLD = 0.95
PEAK_ERROR_RATIO_THRESHOLD = 1.10
_FORMAL_PROVENANCE_FIELDS = frozenset(
    {
        "network_revision_id",
        "rainfall_scenario_id",
        "solver_id",
        "solver_version",
        "baseline_parameter_sha256",
        "evaluation_code_sha256",
    }
)
_TRUST_LEVELS = frozenset(
    {"external_verified", "external_unverified", "synthetic", "mock"}
)
_QUALITY_VALUES = frozenset({"good", "bad", "missing", "stale"})
# A caller-supplied service that corroborates an observation's trust claim
# against the observation ingest/binding records.
TrustResolver = Callable[["ObservationPoint"], bool]
_FRAME_QUALITY_VALUES = frozenset({"verified", "warning", "failed"})
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CalibrationError(ValueError):
    """Base error for calibration preflight and evaluation failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class InsufficientObservationsError(CalibrationError):
    """The frozen event/pair/trust admission gate was not met."""

    def __init__(self, message: str) -> None:
        super().__init__("INSUFFICIENT_OBSERVATIONS", message)


class ObservationSourceUnavailableError(CalibrationError):
    """Influx is disconnected or cannot execute a bounded Flux query."""

    def __init__(self, message: str) -> None:
        super().__init__("OBSERVATION_SOURCE_UNAVAILABLE", message)


class CalibrationExecutionError(CalibrationError):
    """No evaluated candidate produced a usable metric result."""

    def __init__(self, message: str) -> None:
        super().__init__("CALIBRATION_EXECUTION_FAILED", message)


@dataclass(frozen=True, slots=True)
class ObservationPoint:
    """One immutable point read from ``sensor_readings.water_level_mm``."""

    observed_at: datetime
    target_id: str
    value_mm: float
    event_id: str
    trust_level: str = "external_unverified"
    quality: str = "good"
    measurement: str = MEASUREMENT_NAME
    field: str = OBSERVATION_FIELD

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _as_utc(self.observed_at))
        if not self.target_id.strip() or not self.event_id.strip():
            raise CalibrationError(
                "INVALID_OBSERVATION", "target_id and event_id must be non-empty"
            )
        value = float(self.value_mm)
        if not math.isfinite(value):
            raise CalibrationError(
                "NON_FINITE_OBSERVATION", "water_level_mm must be finite"
            )
        object.__setattr__(self, "value_mm", value)
        if self.trust_level not in _TRUST_LEVELS:
            raise CalibrationError(
                "INVALID_TRUST_LEVEL", f"unsupported trust level: {self.trust_level}"
            )
        if self.quality not in _QUALITY_VALUES:
            raise CalibrationError(
                "INVALID_OBSERVATION_QUALITY", f"unsupported quality: {self.quality}"
            )
        if self.measurement != MEASUREMENT_NAME or self.field != OBSERVATION_FIELD:
            raise CalibrationError(
                "INVALID_OBSERVATION_SOURCE",
                "calibration observations must use sensor_readings.water_level_mm",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at": _datetime_text(self.observed_at),
            "target_id": self.target_id,
            "value_mm": self.value_mm,
            "event_id": self.event_id,
            "trust_level": self.trust_level,
            "quality": self.quality,
            "measurement": self.measurement,
            "field": self.field,
        }


@dataclass(frozen=True, slots=True)
class SimulatedWaterLevel:
    """A simulated water level already converted to the sensor unit (mm)."""

    timestamp: datetime
    target_id: str
    value_mm: float
    quality_status: str = "verified"
    quality_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _as_utc(self.timestamp))
        if not self.target_id.strip():
            raise CalibrationError(
                "INVALID_SIMULATION_VALUE", "target_id must be non-empty"
            )
        value = float(self.value_mm)
        if not math.isfinite(value):
            raise CalibrationError(
                "NON_FINITE_SIMULATION", "simulated water level must be finite"
            )
        object.__setattr__(self, "value_mm", value)
        if self.quality_status not in _FRAME_QUALITY_VALUES:
            raise CalibrationError(
                "INVALID_SIMULATION_QUALITY",
                f"unsupported frame quality status: {self.quality_status}",
            )
        object.__setattr__(self, "quality_errors", tuple(self.quality_errors))


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Predictions and solver-quality verdict returned for one candidate."""

    predictions: tuple[SimulatedWaterLevel, ...]
    quality_ok: bool = True
    quality_errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.quality_ok, bool):
            raise CalibrationError(
                "INVALID_CANDIDATE_QUALITY", "quality_ok must be a boolean"
            )
        predictions = tuple(self.predictions)
        quality_errors = tuple(self.quality_errors)
        if any(not isinstance(item, SimulatedWaterLevel) for item in predictions):
            raise CalibrationError(
                "INVALID_CANDIDATE_EVALUATION",
                "predictions must contain SimulatedWaterLevel values",
            )
        if any(
            not isinstance(item, str) or not item.strip() for item in quality_errors
        ):
            raise CalibrationError(
                "INVALID_CANDIDATE_QUALITY",
                "quality_errors must contain non-empty strings",
            )
        object.__setattr__(self, "predictions", predictions)
        object.__setattr__(self, "quality_errors", quality_errors)


@dataclass(frozen=True, slots=True)
class HydraulicMetrics:
    """RMSE/NSE and supporting metrics in the original ``mm`` unit."""

    sample_count: int
    observation_count: int
    missing_count: int
    event_count: int
    rmse_mm: float
    nse: float | None
    nse_code: Literal["NSE_OK", "NSE_UNDEFINED"]
    bias_mm: float
    peak_abs_error_mm: float
    missing_rate: float
    quality_ok: bool
    quality_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "sample_count": self.sample_count,
            "observation_count": self.observation_count,
            "missing_count": self.missing_count,
            "event_count": self.event_count,
            "rmse_mm": self.rmse_mm,
            "nse": self.nse,
            "nse_code": self.nse_code,
            "bias_mm": self.bias_mm,
            "peak_abs_error_mm": self.peak_abs_error_mm,
            "missing_rate": self.missing_rate,
            "quality_ok": self.quality_ok,
            "quality_errors": list(self.quality_errors),
        }


@dataclass(frozen=True, slots=True)
class CalibrationObjective:
    """Minimized weighted error objective used by grid and random search."""

    rmse_weight: float = 1.0
    nse_weight: float = 0.25
    peak_error_weight: float = 0.10

    def __post_init__(self) -> None:
        for field_name in (
            "rmse_weight",
            "nse_weight",
            "peak_error_weight",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value < 0:
                raise CalibrationError(
                    "INVALID_OBJECTIVE_WEIGHT", f"{field_name} must be finite and >= 0"
                )
            object.__setattr__(self, field_name, value)
        if self.rmse_weight + self.nse_weight + self.peak_error_weight <= 0:
            raise CalibrationError(
                "INVALID_OBJECTIVE_WEIGHT", "at least one objective weight must be > 0"
            )

    def score(self, metrics: HydraulicMetrics) -> float:
        nse_penalty = 1.0 if metrics.nse is None else 1.0 - metrics.nse
        return (
            self.rmse_weight * metrics.rmse_mm
            + self.nse_weight * nse_penalty
            + self.peak_error_weight * metrics.peak_abs_error_mm
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": "weighted-water-level-error-v1",
            "direction": "minimize",
            "rmse_weight": self.rmse_weight,
            "nse_weight": self.nse_weight,
            "peak_error_weight": self.peak_error_weight,
            "nse_undefined_penalty": 1.0,
        }


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """Bounded parameter range; ``step`` is required for deterministic grid."""

    name: str
    minimum: float
    maximum: float
    step: float | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise CalibrationError("INVALID_PARAMETER", "parameter name is required")
        minimum = float(self.minimum)
        maximum = float(self.maximum)
        if (
            not math.isfinite(minimum)
            or not math.isfinite(maximum)
            or maximum < minimum
        ):
            raise CalibrationError(
                "INVALID_PARAMETER_RANGE",
                f"{self.name} must have a finite minimum <= maximum",
            )
        object.__setattr__(self, "minimum", minimum)
        object.__setattr__(self, "maximum", maximum)
        if self.step is not None:
            step = float(self.step)
            if not math.isfinite(step) or step <= 0:
                raise CalibrationError(
                    "INVALID_PARAMETER_STEP", f"{self.name} step must be finite and > 0"
                )
            span = maximum - minimum
            quotient = span / step
            if not math.isclose(
                quotient, round(quotient), rel_tol=1e-12, abs_tol=1e-12
            ):
                raise CalibrationError(
                    "INVALID_PARAMETER_STEP",
                    f"{self.name} range must be an integer multiple of step",
                )
            object.__setattr__(self, "step", step)

    def grid_values(self) -> tuple[float, ...]:
        if self.step is None:
            raise CalibrationError(
                "GRID_STEP_REQUIRED", f"grid search requires a step for {self.name}"
            )
        count = int(round((self.maximum - self.minimum) / self.step)) + 1
        return tuple(
            round(self.minimum + index * self.step, 12) for index in range(count)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
        }


@dataclass(frozen=True, slots=True)
class CalibrationTrial:
    parameters: dict[str, float]
    metrics: HydraulicMetrics
    objective_value: float | None
    quality_ok: bool
    quality_errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameters": dict(sorted(self.parameters.items())),
            "metrics": self.metrics.to_dict(),
            "objective_value": self.objective_value,
            "quality_ok": self.quality_ok,
            "quality_errors": list(self.quality_errors),
        }


@dataclass(frozen=True, slots=True)
class ImprovementReport:
    rmse_reduction_pct: float | None
    nse_delta: float | None
    peak_error_change_pct: float | None
    rmse_threshold_met: bool
    nse_threshold_met: bool
    peak_threshold_met: bool
    quality_threshold_met: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "rmse_reduction_pct": self.rmse_reduction_pct,
            "nse_delta": self.nse_delta,
            "peak_error_change_pct": self.peak_error_change_pct,
            "thresholds": {
                "rmse_calibrated_lte_0_95_baseline": self.rmse_threshold_met,
                "nse_delta_gte_0_05": self.nse_threshold_met,
                "peak_error_lte_1_10_baseline": self.peak_threshold_met,
                "no_quality_or_stability_failure": self.quality_threshold_met,
            },
        }


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Canonical before/after report and selected parameter set."""

    method: Literal["grid", "random"]
    seed: int
    objective: CalibrationObjective
    parameter_space: tuple[ParameterSpec, ...]
    observation_dataset_sha256: str
    split_manifest: dict[str, Any]
    paired_cohort_sha256: str
    provenance: dict[str, Any]
    baseline_metrics: HydraulicMetrics
    calibrated_metrics: HydraulicMetrics
    best_parameters: dict[str, float]
    best_parameter_set_sha256: str
    objective_before: float
    objective_after: float
    improvement: ImprovementReport
    trials: tuple[CalibrationTrial, ...]
    ac5_eligible: bool
    improved: bool
    diagnostic_numeric_improved: bool
    report_code: Literal["PASS", "FAILED_THRESHOLDS", "INELIGIBLE_OBSERVATIONS"]

    @property
    def trial_count(self) -> int:
        return len(self.trials)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "hydraulic-calibration-report-v1",
            "method": self.method,
            "seed": self.seed,
            "objective": self.objective.to_dict(),
            "parameter_space": [item.to_dict() for item in self.parameter_space],
            "observation_dataset_sha256": self.observation_dataset_sha256,
            "split_manifest": dict(sorted(self.split_manifest.items())),
            "paired_cohort_sha256": self.paired_cohort_sha256,
            "provenance": dict(sorted(self.provenance.items())),
            "baseline_metrics": self.baseline_metrics.to_dict(),
            "calibrated_metrics": self.calibrated_metrics.to_dict(),
            "best_parameters": dict(sorted(self.best_parameters.items())),
            "best_parameter_set_sha256": self.best_parameter_set_sha256,
            "objective_before": self.objective_before,
            "objective_after": self.objective_after,
            "improvement": self.improvement.to_dict(),
            "trial_count": self.trial_count,
            "trials": [item.to_dict() for item in self.trials],
            "ac5_eligible": self.ac5_eligible,
            "improved": self.improved,
            "diagnostic_numeric_improved": self.diagnostic_numeric_improved,
            "report_code": self.report_code,
        }

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


CandidateEvaluator = Callable[[Mapping[str, float]], CandidateEvaluation]


def compute_metrics(
    observations: Sequence[ObservationPoint],
    predictions: Sequence[SimulatedWaterLevel],
    *,
    pairing_tolerance_seconds: int = 300,
    require_verified: bool = False,
) -> HydraulicMetrics:
    """Pair same-target values within tolerance and compute frozen RMSE/NSE."""

    if pairing_tolerance_seconds <= 0:
        raise CalibrationError(
            "INVALID_PAIRING_TOLERANCE", "pairing_tolerance_seconds must be > 0"
        )
    eligible = _eligible_observations(observations, require_verified=require_verified)
    if not eligible:
        raise InsufficientObservationsError("no eligible water-level observations")
    paired = _pair_observations(
        eligible,
        predictions,
        tolerance_seconds=pairing_tolerance_seconds,
    )
    if not paired:
        raise InsufficientObservationsError(
            "no observed/simulated water levels fall within the pairing tolerance"
        )
    observed = [item.observation.value_mm for item in paired]
    residuals = [item.predicted.value_mm - item.observation.value_mm for item in paired]
    squared_errors = [residual * residual for residual in residuals]
    rmse = math.sqrt(sum(squared_errors) / len(squared_errors))
    bias = sum(residuals) / len(residuals)
    peak = max(abs(residual) for residual in residuals)
    mean_observed = sum(observed) / len(observed)
    denominator = sum((value - mean_observed) ** 2 for value in observed)
    nse = (
        None
        if math.isclose(denominator, 0.0, rel_tol=0.0, abs_tol=1e-12)
        else 1.0 - sum(squared_errors) / denominator
    )
    quality_errors = tuple(
        sorted(
            {
                message
                for prediction in predictions
                for message in prediction.quality_errors
            }
            | {
                "FRAME_QUALITY_FAILED"
                for prediction in predictions
                if prediction.quality_status == "failed"
            }
        )
    )
    missing_count = len(eligible) - len(paired)
    return HydraulicMetrics(
        sample_count=len(paired),
        observation_count=len(eligible),
        missing_count=missing_count,
        event_count=len({item.event_id for item in eligible}),
        rmse_mm=rmse,
        nse=nse,
        nse_code="NSE_OK" if nse is not None else "NSE_UNDEFINED",
        bias_mm=bias,
        peak_abs_error_mm=peak,
        missing_rate=missing_count / len(eligible),
        quality_ok=not quality_errors,
        quality_errors=quality_errors,
    )


def calibration_objective(
    metrics: HydraulicMetrics,
    *,
    rmse_weight: float = 1.0,
    nse_weight: float = 0.25,
    peak_error_weight: float = 0.10,
) -> float:
    """Functional facade for callers that do not need to retain the object."""

    return CalibrationObjective(
        rmse_weight=rmse_weight,
        nse_weight=nse_weight,
        peak_error_weight=peak_error_weight,
    ).score(metrics)


def run_calibration(
    *,
    observations: Sequence[ObservationPoint],
    baseline_predictions: Sequence[SimulatedWaterLevel],
    parameter_space: Mapping[str, ParameterSpec],
    evaluator: CandidateEvaluator,
    method: Literal["grid", "random"] = "grid",
    max_evaluations: int = 100,
    seed: int = 0,
    objective: CalibrationObjective | None = None,
    pairing_tolerance_seconds: int = 300,
    require_verified: bool = False,
    provenance: Mapping[str, Any] | None = None,
    split_manifest: Mapping[str, Any] | None = None,
    min_evaluation_pairs: int = MIN_EVALUATION_PAIRS,
    min_event_count: int = MIN_EVENT_COUNT,
    trust_resolver: TrustResolver | None = None,
) -> CalibrationReport:
    """Search a parameter space and emit a before/after improvement report.

    ``trust_level`` on an :class:`ObservationPoint` is a *claim* made by
    whoever built the point, so it cannot by itself make a report AC-5
    eligible.  When ``require_verified`` is set, an observation also has to be
    corroborated by ``trust_resolver`` -- a service that looked the event up in
    the observation ingest/binding records.  With no resolver the report is
    INELIGIBLE, never PASS.
    """

    if method not in {"grid", "random"}:
        raise CalibrationError(
            "INVALID_SEARCH_METHOD", "method must be 'grid' or 'random'"
        )
    if max_evaluations < 1:
        raise CalibrationError(
            "INVALID_SEARCH_ITERATIONS", "max_evaluations must be positive"
        )
    if min_evaluation_pairs < 1 or min_event_count < 1:
        raise CalibrationError(
            "INVALID_OBSERVATION_GATE",
            "observation count and event count gates must be positive",
        )
    specs = _normalise_parameter_space(parameter_space)
    objective = objective or CalibrationObjective()
    provenance_payload = dict(provenance or {})
    _canonical_json(provenance_payload)
    provenance_complete = _formal_provenance_complete(provenance_payload)
    eligible = _eligible_observations(observations, require_verified=require_verified)
    _evaluation_events, split_payload = _evaluation_split_events(
        eligible,
        split_manifest,
    )
    evaluation_observations = (
        tuple(item for item in eligible if item.event_id in _evaluation_events)
        if _evaluation_events
        else eligible
    )
    _validate_observation_admission(
        evaluation_observations,
        min_evaluation_pairs=min_evaluation_pairs,
        min_event_count=min_event_count,
    )
    baseline_cohort = _paired_cohort(
        evaluation_observations,
        baseline_predictions,
        tolerance_seconds=pairing_tolerance_seconds,
    )
    baseline_cohort_sha256 = _paired_cohort_sha256(baseline_cohort)
    baseline_metrics = compute_metrics(
        evaluation_observations,
        baseline_predictions,
        pairing_tolerance_seconds=pairing_tolerance_seconds,
        require_verified=False,
    )
    if baseline_metrics.sample_count < min_evaluation_pairs:
        raise InsufficientObservationsError(
            f"baseline has {baseline_metrics.sample_count} paired observations; "
            f"need at least {min_evaluation_pairs}"
        )
    candidates = tuple(
        _candidate_parameters(
            specs, method=method, max_evaluations=max_evaluations, seed=seed
        )
    )
    trials: list[CalibrationTrial] = []
    for parameters in candidates:
        evaluation = evaluator(dict(parameters))
        if not isinstance(evaluation, CandidateEvaluation):
            raise CalibrationError(
                "INVALID_CANDIDATE_EVALUATION",
                "evaluator must return CandidateEvaluation",
            )
        metrics = compute_metrics(
            evaluation_observations,
            evaluation.predictions,
            pairing_tolerance_seconds=pairing_tolerance_seconds,
            require_verified=False,
        )
        candidate_cohort = _paired_cohort(
            evaluation_observations,
            evaluation.predictions,
            tolerance_seconds=pairing_tolerance_seconds,
        )
        quality_errors = set(evaluation.quality_errors) | set(metrics.quality_errors)
        if candidate_cohort != baseline_cohort:
            quality_errors.add("PAIRED_COHORT_MISMATCH")
        if metrics.sample_count < min_evaluation_pairs:
            quality_errors.add("INSUFFICIENT_OBSERVATIONS")
        quality_ok = evaluation.quality_ok and not quality_errors
        quality_errors_tuple = tuple(sorted(quality_errors))
        objective_value = objective.score(metrics) if quality_ok else None
        if objective_value is not None:
            trials.append(
                CalibrationTrial(
                    parameters=dict(parameters),
                    metrics=metrics,
                    objective_value=objective_value,
                    quality_ok=True,
                    quality_errors=(),
                )
            )
        else:
            trials.append(
                CalibrationTrial(
                    parameters=dict(parameters),
                    metrics=metrics,
                    objective_value=None,
                    quality_ok=False,
                    quality_errors=quality_errors_tuple or ("QUALITY_FAILED",),
                )
            )
    usable = [trial for trial in trials if trial.quality_ok]
    if not usable:
        if all("PAIRED_COHORT_MISMATCH" in trial.quality_errors for trial in trials):
            raise CalibrationExecutionError(
                "candidate paired cohort does not match the baseline evaluation split"
            )
        raise CalibrationExecutionError("all parameter candidates failed quality gates")
    best = min(
        usable,
        key=lambda trial: (
            trial.objective_value if trial.objective_value is not None else math.inf,
            _canonical_json(trial.parameters),
        ),
    )
    best_objective = best.objective_value
    if best_objective is None:
        raise CalibrationExecutionError("best candidate has no objective value")
    objective_before = objective.score(baseline_metrics)
    improvement = _improvement_report(
        baseline_metrics,
        best.metrics,
        require_verified=require_verified,
    )
    all_passed = (
        improvement.rmse_threshold_met
        and improvement.nse_threshold_met
        and improvement.peak_threshold_met
        and improvement.quality_threshold_met
    )
    diagnostic_numeric_improved = best.metrics.rmse_mm < baseline_metrics.rmse_mm and (
        baseline_metrics.nse is None
        or best.metrics.nse is None
        or best.metrics.nse > baseline_metrics.nse
    )
    ac5_eligible = (
        require_verified
        and trust_resolver is not None
        and bool(_evaluation_events)
        and bool(split_payload.get("split_sha256"))
        and provenance_complete
        and all(
            item.trust_level == "external_verified"
            and trust_resolver is not None
            and trust_resolver(item)
            for item in evaluation_observations
        )
    )
    report_code: Literal["PASS", "FAILED_THRESHOLDS", "INELIGIBLE_OBSERVATIONS"]
    if not ac5_eligible:
        report_code = "INELIGIBLE_OBSERVATIONS"
    elif all_passed:
        report_code = "PASS"
    else:
        report_code = "FAILED_THRESHOLDS"
    return CalibrationReport(
        method=method,
        seed=seed,
        objective=objective,
        parameter_space=specs,
        observation_dataset_sha256=_observation_dataset_sha256(eligible),
        split_manifest=split_payload,
        paired_cohort_sha256=baseline_cohort_sha256,
        provenance=provenance_payload,
        baseline_metrics=baseline_metrics,
        calibrated_metrics=best.metrics,
        best_parameters=dict(best.parameters),
        best_parameter_set_sha256=hashlib.sha256(
            _canonical_json(best.parameters).encode("utf-8")
        ).hexdigest(),
        objective_before=objective_before,
        objective_after=best_objective,
        improvement=improvement,
        trials=tuple(trials),
        ac5_eligible=ac5_eligible,
        improved=ac5_eligible and all_passed,
        diagnostic_numeric_improved=diagnostic_numeric_improved,
        report_code=report_code,
    )


def write_calibration_report(
    artifact_root: Path | str,
    report_id: uuid.UUID | str,
    report: CalibrationReport,
) -> ControlledArtifactReference:
    """Write the canonical report as an immutable controlled artifact."""

    return write_controlled_json_artifact(
        artifact_root,
        "calibration-reports",
        report_id,
        report.to_dict(),
    )


@dataclass(frozen=True, slots=True)
class _ObservationInput:
    observation: ObservationPoint
    predicted: SimulatedWaterLevel


class InfluxWaterLevelObservationSource:
    """Bounded reader for the existing Influx ``sensor_readings`` field."""

    def __init__(
        self,
        *,
        service: Any | None = None,
        bucket: str | None = None,
        org: str | None = None,
    ) -> None:
        if service is None:
            from app.services.influxdb_service import InfluxDBService

            service = InfluxDBService.get_instance()
        self.service = service
        self.bucket = bucket or settings.INFLUXDB_BUCKET
        self.org = org or settings.INFLUXDB_ORG

    def fetch(
        self,
        *,
        device_id: str,
        start: datetime,
        stop: datetime,
    ) -> tuple[ObservationPoint, ...]:
        """Fetch raw points without upgrading their frozen trust level."""

        if not _DEVICE_ID_RE.fullmatch(device_id):
            raise CalibrationError(
                "INVALID_DEVICE_ID", "device_id contains unsupported characters"
            )
        start_at = _as_utc(start)
        stop_at = _as_utc(stop)
        if stop_at <= start_at:
            raise CalibrationError(
                "INVALID_OBSERVATION_WINDOW", "stop must be later than start"
            )
        query_api = getattr(self.service, "_query_api", None)
        if query_api is None:
            raise ObservationSourceUnavailableError("Influx query API is not connected")
        bucket = _flux_string(self.bucket, "bucket")
        org = _flux_string(self.org, "org")
        device = _flux_string(device_id, "device_id")
        range_arguments = (
            f'start: time(v: "{_datetime_text(start_at)}"), '
            f'stop: time(v: "{_datetime_text(stop_at)}")'
        )
        query = f"""
from(bucket: "{bucket}")
  |> range({range_arguments})
  |> filter(fn: (r) => r["_measurement"] == "{MEASUREMENT_NAME}")
  |> filter(fn: (r) => r["device_id"] == "{device}")
  |> filter(fn: (r) => r["_field"] == "{OBSERVATION_FIELD}")
"""
        try:
            tables = query_api.query(query, org=org)
            records = [
                record for table in tables for record in getattr(table, "records", ())
            ]
        except Exception as exc:
            raise ObservationSourceUnavailableError(
                f"Influx water-level query failed: {type(exc).__name__}"
            ) from exc
        observations: list[ObservationPoint] = []
        for record in records:
            values = dict(getattr(record, "values", {}) or {})
            if values.get("_measurement") != MEASUREMENT_NAME:
                continue
            if values.get("_field") != OBSERVATION_FIELD:
                continue
            timestamp = record.get_time()
            if timestamp is None:
                continue
            raw_value = values.get("_value")
            if raw_value is None:
                continue
            try:
                timestamp = _as_utc(timestamp)
                value = float(raw_value)
            except (CalibrationError, TypeError, ValueError):
                continue
            if not math.isfinite(value):
                continue
            source_event = (
                values.get("source_event_id")
                or values.get("event_id")
                or values.get("rainfall_event_id")
            )
            # Legacy Influx rows do not carry a trustworthy rainfall-event
            # identity. Keep them in one explicitly unverified source bucket
            # instead of fabricating one independent event per timestamp.
            event_id = (
                str(source_event) if source_event else f"unverified-raw:{device_id}"
            )
            observations.append(
                ObservationPoint(
                    observed_at=timestamp,
                    target_id=str(
                        values.get("target_stable_key")
                        or values.get("device_id")
                        or device_id
                    ),
                    value_mm=value,
                    event_id=event_id,
                    trust_level="external_unverified",
                )
            )
        return tuple(
            sorted(observations, key=lambda item: (item.target_id, item.observed_at))
        )


def _eligible_observations(
    observations: Sequence[ObservationPoint], *, require_verified: bool
) -> tuple[ObservationPoint, ...]:
    eligible = [
        item
        for item in observations
        if item.quality == "good"
        and item.measurement == MEASUREMENT_NAME
        and item.field == OBSERVATION_FIELD
        and (not require_verified or item.trust_level == "external_verified")
    ]
    return tuple(
        sorted(
            eligible, key=lambda item: (item.observed_at, item.target_id, item.event_id)
        )
    )


def _validate_observation_admission(
    observations: Sequence[ObservationPoint],
    *,
    min_evaluation_pairs: int,
    min_event_count: int,
) -> None:
    event_count = len({item.event_id for item in observations})
    if event_count < min_event_count:
        raise InsufficientObservationsError(
            f"need at least {min_event_count} independent events, got {event_count}"
        )
    if len(observations) < min_evaluation_pairs:
        raise InsufficientObservationsError(
            f"need at least {min_evaluation_pairs} valid observations, "
            f"got {len(observations)}"
        )


def _pair_observations(
    observations: Sequence[ObservationPoint],
    predictions: Sequence[SimulatedWaterLevel],
    *,
    tolerance_seconds: int,
) -> tuple[_ObservationInput, ...]:
    predictions_by_target: dict[str, list[SimulatedWaterLevel]] = defaultdict(list)
    for prediction in predictions:
        predictions_by_target[prediction.target_id].append(prediction)
    times_by_target: dict[str, list[datetime]] = {}
    for target, indexed_predictions in predictions_by_target.items():
        indexed_predictions.sort(key=lambda item: item.timestamp)
        times_by_target[target] = [item.timestamp for item in indexed_predictions]
    used_by_target: dict[str, set[int]] = defaultdict(set)
    paired: list[_ObservationInput] = []
    for observation in observations:
        target_predictions = predictions_by_target.get(observation.target_id)
        if not target_predictions:
            continue
        times = times_by_target[observation.target_id]
        insertion = bisect_left(times, observation.observed_at)
        choices = [
            index
            for index in (insertion - 1, insertion, insertion + 1)
            if 0 <= index < len(target_predictions)
            and index not in used_by_target[observation.target_id]
        ]
        if not choices:
            continue
        best_index = min(
            choices,
            key=lambda index: abs(
                (
                    target_predictions[index].timestamp - observation.observed_at
                ).total_seconds()
            ),
        )
        if (
            abs(
                (
                    target_predictions[best_index].timestamp - observation.observed_at
                ).total_seconds()
            )
            > tolerance_seconds
        ):
            continue
        used_by_target[observation.target_id].add(best_index)
        paired.append(
            _ObservationInput(
                observation=observation,
                predicted=target_predictions[best_index],
            )
        )
    return tuple(paired)


def _paired_cohort(
    observations: Sequence[ObservationPoint],
    predictions: Sequence[SimulatedWaterLevel],
    *,
    tolerance_seconds: int,
) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        (
            item.observation.event_id,
            item.observation.target_id,
            _datetime_text(item.observation.observed_at),
        )
        for item in _pair_observations(
            observations,
            predictions,
            tolerance_seconds=tolerance_seconds,
        )
    )


def _paired_cohort_sha256(cohort: Sequence[tuple[str, str, str]]) -> str:
    payload = [list(item) for item in cohort]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _evaluation_split_events(
    observations: Sequence[ObservationPoint],
    split_manifest: Mapping[str, Any] | None,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    payload = dict(split_manifest or {})
    if not payload:
        return (), payload
    if payload.get("schema") != "calibration-split-v1":
        raise CalibrationError(
            "INVALID_SPLIT_MANIFEST",
            "split_manifest schema must be calibration-split-v1",
        )
    split_name = payload.get("evaluation_split")
    if split_name not in {"validation", "test"}:
        raise CalibrationError(
            "INVALID_SPLIT_MANIFEST",
            "evaluation_split must be validation or test",
        )
    raw_event_ids = payload.get("event_ids")
    if (
        not isinstance(raw_event_ids, Sequence)
        or isinstance(raw_event_ids, (str, bytes))
        or not raw_event_ids
    ):
        raise CalibrationError(
            "INVALID_SPLIT_MANIFEST",
            "split_manifest event_ids must be a non-empty sequence",
        )
    event_ids = tuple(sorted({str(item) for item in raw_event_ids}))
    eligible_event_ids = {item.event_id for item in observations}
    if not set(event_ids).issubset(eligible_event_ids):
        raise CalibrationError(
            "INVALID_SPLIT_MANIFEST",
            "evaluation split must reference only admitted observation events",
        )
    payload["event_ids"] = list(event_ids)
    payload["split_sha256"] = hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()
    return event_ids, payload


def _normalise_parameter_space(
    parameter_space: Mapping[str, ParameterSpec],
) -> tuple[ParameterSpec, ...]:
    if not parameter_space:
        raise CalibrationError(
            "INVALID_PARAMETER_SPACE", "parameter_space must not be empty"
        )
    specs: list[ParameterSpec] = []
    for name, spec in sorted(parameter_space.items()):
        if not isinstance(spec, ParameterSpec):
            raise CalibrationError(
                "INVALID_PARAMETER_SPACE", f"{name} must be a ParameterSpec"
            )
        if spec.name != name:
            raise CalibrationError(
                "INVALID_PARAMETER_SPACE",
                f"parameter key {name} does not match spec name {spec.name}",
            )
        specs.append(spec)
    return tuple(specs)


def _candidate_parameters(
    specs: Sequence[ParameterSpec],
    *,
    method: Literal["grid", "random"],
    max_evaluations: int,
    seed: int,
) -> Iterable[dict[str, float]]:
    if method == "grid":
        axes = [spec.grid_values() for spec in specs]
        combinations = tuple(itertools.product(*axes))
        if len(combinations) > max_evaluations:
            raise CalibrationError(
                "INVALID_SEARCH_ITERATIONS",
                f"grid has {len(combinations)} candidates but max_evaluations is "
                f"{max_evaluations}",
            )
        for combination in combinations:
            yield {spec.name: float(value) for spec, value in zip(specs, combination)}
        return
    generator = random.Random(seed)
    seen: set[str] = set()
    generated = 0
    attempts = 0
    max_attempts = max(10_000, max_evaluations * 1_000)
    while generated < max_evaluations:
        attempts += 1
        if attempts > max_attempts:
            raise CalibrationError(
                "SEARCH_SPACE_EXHAUSTED",
                "random search could not produce the requested "
                "distinct finite candidates",
            )
        candidate = {
            spec.name: generator.uniform(spec.minimum, spec.maximum) for spec in specs
        }
        key = _canonical_json(candidate)
        if key in seen:
            continue
        seen.add(key)
        generated += 1
        yield candidate


def _improvement_report(
    baseline: HydraulicMetrics,
    calibrated: HydraulicMetrics,
    *,
    require_verified: bool,
) -> ImprovementReport:
    rmse_reduction = (
        (baseline.rmse_mm - calibrated.rmse_mm) / baseline.rmse_mm * 100.0
        if baseline.rmse_mm > 0
        else (0.0 if calibrated.rmse_mm == 0 else None)
    )
    nse_delta = (
        calibrated.nse - baseline.nse
        if calibrated.nse is not None and baseline.nse is not None
        else None
    )
    peak_change = (
        (calibrated.peak_abs_error_mm - baseline.peak_abs_error_mm)
        / baseline.peak_abs_error_mm
        * 100.0
        if baseline.peak_abs_error_mm > 0
        else (0.0 if calibrated.peak_abs_error_mm == 0 else None)
    )
    rmse_threshold = calibrated.rmse_mm <= RMSE_RATIO_THRESHOLD * baseline.rmse_mm
    nse_threshold = nse_delta is not None and nse_delta >= NSE_DELTA_THRESHOLD
    peak_threshold = (
        calibrated.peak_abs_error_mm
        <= PEAK_ERROR_RATIO_THRESHOLD * baseline.peak_abs_error_mm
    )
    quality_threshold = (
        require_verified and baseline.quality_ok and calibrated.quality_ok
    )
    return ImprovementReport(
        rmse_reduction_pct=rmse_reduction,
        nse_delta=nse_delta,
        peak_error_change_pct=peak_change,
        rmse_threshold_met=rmse_threshold,
        nse_threshold_met=nse_threshold,
        peak_threshold_met=peak_threshold,
        quality_threshold_met=quality_threshold,
    )


def _formal_provenance_complete(provenance: Mapping[str, Any]) -> bool:
    if not _FORMAL_PROVENANCE_FIELDS <= provenance.keys():
        return False
    hash_fields = {"baseline_parameter_sha256", "evaluation_code_sha256"}
    return all(
        isinstance(value, str)
        and bool(value.strip())
        and (_SHA256_RE.fullmatch(value) is not None if key in hash_fields else True)
        for key, value in provenance.items()
        if key in _FORMAL_PROVENANCE_FIELDS
    )


def _observation_dataset_sha256(observations: Sequence[ObservationPoint]) -> str:
    payload = [item.to_dict() for item in observations]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CalibrationError(
            "NAIVE_DATETIME", "timestamps must be timezone-aware datetimes"
        )
    return value.astimezone(UTC)


def _datetime_text(value: datetime) -> str:
    text = _as_utc(value).isoformat(timespec="microseconds")
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _flux_string(value: str, field_name: str) -> str:
    if not value or any(
        character in value for character in ("\r", "\n", "\0", '"', "\\")
    ):
        raise CalibrationError(
            "INVALID_OBSERVATION_SOURCE",
            f"{field_name} contains unsupported Flux characters",
        )
    return value


__all__ = [
    "CalibrationError",
    "CalibrationExecutionError",
    "CalibrationObjective",
    "CalibrationReport",
    "CalibrationTrial",
    "CandidateEvaluation",
    "HydraulicMetrics",
    "ImprovementReport",
    "InfluxWaterLevelObservationSource",
    "InsufficientObservationsError",
    "MEASUREMENT_NAME",
    "OBSERVATION_FIELD",
    "ObservationPoint",
    "ObservationSourceUnavailableError",
    "ParameterSpec",
    "SimulatedWaterLevel",
    "calibration_objective",
    "compute_metrics",
    "run_calibration",
    "write_calibration_report",
]
