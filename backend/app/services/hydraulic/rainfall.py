"""Pure, deterministic rainfall scenario and rain-source contracts.

This module is intentionally independent of the database and HTTP layers.  A
scenario is materialized into a typed series first; repositories can persist
that series later, while solvers can consume the same immutable object.  The
only built-in source is synthetic, and its output is a pure function of the
request, algorithm version, and seed.

The design profile is a small deterministic Chicago-like envelope.  It is a
screening profile, not a claim about a particular local IDF table.  Its
scaling is linear in the return period so that equal-duration design storms
have a clear, auditable monotonicity property.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, NoReturn, Protocol, TypeAlias, runtime_checkable

ALLOWED_STEP_SECONDS = (60, 300, 900)
ALLOWED_RETURN_PERIODS = (1, 2, 3, 5, 10, 20, 50, 100)
MAX_DURATION_SECONDS = 72 * 60 * 60
DEFAULT_DURATION_SECONDS = 24 * 60 * 60
DEFAULT_STEP_SECONDS = 300
DEFAULT_START_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYNTHETIC_SOURCE_ID = "synthetic"
SYNTHETIC_SOURCE_VERSION = "rainfall-synthetic-v1"
DESIGN_ALGORITHM = "synthetic-design-chicago-v1"
CUSTOM_ALGORITHM = "synthetic-custom-v1"
RANDOM_ALGORITHM = "synthetic-random-block-v1"
SCHEMA_VERSION = "rainfall-series-v1"

UTC = timezone.utc
_MISSING = object()


class RainfallScenarioKind(str, Enum):
    """Persisted scenario kinds used by the hydraulic schema."""

    design = "design"
    custom = "custom"
    synthetic = "synthetic"


class RainfallPattern(str, Enum):
    """Generation patterns; ``random`` is persisted as ``synthetic``."""

    design = "design"
    custom = "custom"
    random = "random"


_PATTERN_ALGORITHMS = {
    RainfallPattern.design: DESIGN_ALGORITHM,
    RainfallPattern.custom: CUSTOM_ALGORITHM,
    RainfallPattern.random: RANDOM_ALGORITHM,
}


class RainfallReturnPeriodYears(int, Enum):
    """The frozen v1 design-storm return-period set."""

    one = 1
    two = 2
    three = 3
    five = 5
    ten = 10
    twenty = 20
    fifty = 50
    one_hundred = 100


ReturnPeriod: TypeAlias = int | str | RainfallReturnPeriodYears
ReturnPeriodValue: TypeAlias = int | str | None


@dataclass(frozen=True, slots=True)
class RainfallValidationIssue:
    """Stable field-level validation issue."""

    code: str
    field: str
    message: str
    details: Mapping[str, Any] = dataclass_field(default_factory=dict)


class RainfallValidationError(ValueError):
    """Raised when a rainfall request or series violates the frozen contract."""

    def __init__(self, issues: Sequence[RainfallValidationIssue] | str) -> None:
        normalized: tuple[RainfallValidationIssue, ...]
        if isinstance(issues, str):
            normalized = (RainfallValidationIssue("VALIDATION_ERROR", "$", issues),)
        else:
            normalized = tuple(issues)
        self.issues = normalized
        super().__init__("; ".join(issue.message for issue in normalized))


def _raise(
    code: str,
    field_name: str,
    message: str,
    **details: Any,
) -> NoReturn:
    raise RainfallValidationError(
        (RainfallValidationIssue(code, field_name, message, details),)
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        _raise("INVALID_INTEGER", field_name, f"{field_name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError:
            pass
    _raise("INVALID_INTEGER", field_name, f"{field_name} must be an integer")


def _unit_seconds(value: Any, multiplier: int, field_name: str) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        _raise("INVALID_DURATION", field_name, f"{field_name} must be a finite number")
    if not math.isfinite(numeric):
        _raise("INVALID_DURATION", field_name, f"{field_name} must be a finite number")
    return int(round(numeric * multiplier))


def _as_uuid(value: Any, field_name: str) -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        _raise("INVALID_UUID", field_name, f"{field_name} must be a UUID")
        raise AssertionError from exc


def _as_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            _raise("INVALID_DATETIME", field_name, f"{field_name} must be ISO-8601")
            raise AssertionError from exc
    if not isinstance(value, datetime):
        _raise("INVALID_DATETIME", field_name, f"{field_name} must be a datetime")
    assert isinstance(value, datetime)
    if value.tzinfo is None or value.utcoffset() is None:
        _raise("NAIVE_DATETIME", field_name, f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _datetime_text(value: datetime) -> str:
    value = _as_datetime(value, "timestamp")
    text = value.isoformat(timespec="microseconds")
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def _validate_step(value: Any, field_name: str = "step_seconds") -> int:
    step = _as_int(value, field_name)
    if step not in ALLOWED_STEP_SECONDS:
        _raise(
            "INVALID_STEP",
            field_name,
            f"{field_name} must be one of {ALLOWED_STEP_SECONDS}",
            value=step,
        )
    return step


def _validate_duration(value: Any, field_name: str = "duration_seconds") -> int:
    duration = _as_int(value, field_name)
    if duration <= 0:
        _raise(
            "INVALID_DURATION",
            field_name,
            f"{field_name} must be greater than zero",
            value=duration,
        )
    if duration > MAX_DURATION_SECONDS:
        _raise(
            "DURATION_LIMIT",
            field_name,
            f"{field_name} must not exceed 72 hours",
            value=duration,
            maximum=MAX_DURATION_SECONDS,
        )
    return duration


def _sample_count(duration_seconds: int, step_seconds: int) -> int:
    return duration_seconds // step_seconds + 1


def _coerce_pattern(kind: Any, pattern: Any) -> RainfallPattern:
    raw = pattern if pattern is not None else kind
    if isinstance(raw, RainfallPattern):
        return raw
    if isinstance(raw, Enum):
        raw = raw.value
    if not isinstance(raw, str):
        _raise(
            "INVALID_PATTERN", "kind", "rainfall kind must be design/custom/synthetic"
        )
    normalized = raw.strip().lower()
    if normalized in {"random", "stochastic"}:
        return RainfallPattern.random
    if normalized in {"design", "custom"}:
        return RainfallPattern(normalized)
    if normalized == "synthetic":
        return RainfallPattern.random
    _raise("INVALID_PATTERN", "kind", "rainfall kind must be design/custom/synthetic")


def _scenario_kind(pattern: RainfallPattern) -> RainfallScenarioKind:
    if pattern is RainfallPattern.design:
        return RainfallScenarioKind.design
    if pattern is RainfallPattern.custom:
        return RainfallScenarioKind.custom
    return RainfallScenarioKind.synthetic


def _coerce_return_period(value: Any, *, required: bool) -> ReturnPeriodValue:
    if value is None:
        if required:
            _raise(
                "MISSING_RETURN_PERIOD",
                "return_period_years",
                "design rainfall requires return_period_years",
            )
        return None
    raw_value = value.value if isinstance(value, Enum) else value
    if isinstance(raw_value, str) and raw_value.strip().lower() == "custom":
        if required:
            _raise(
                "INVALID_RETURN_PERIOD",
                "return_period_years",
                "design rainfall requires a numeric return period",
            )
        return "custom"
    period = _as_int(raw_value, "return_period_years")
    if period not in ALLOWED_RETURN_PERIODS:
        _raise(
            "INVALID_RETURN_PERIOD",
            "return_period_years",
            f"return_period_years must be one of {(*ALLOWED_RETURN_PERIODS, 'custom')}",
            value=period,
        )
    return period


def _validate_seed(value: Any) -> int:
    seed = _as_int(value, "random_seed")
    if not -(2**63) <= seed < 2**63:
        _raise(
            "INVALID_SEED",
            "random_seed",
            "random_seed must fit in a signed 64-bit integer",
            value=seed,
        )
    return seed


def _as_intensity(value: Any, index: int) -> float:
    if isinstance(value, bool):
        _raise(
            "NON_FINITE_INTENSITY",
            f"intensities[{index}]",
            "rainfall intensities must be finite and non-negative",
        )
    try:
        result = float(value)
    except (TypeError, ValueError):
        _raise(
            "NON_FINITE_INTENSITY",
            f"intensities[{index}]",
            "rainfall intensities must be finite and non-negative",
        )
        raise AssertionError
    if not math.isfinite(result) or result < 0:
        _raise(
            "NON_FINITE_INTENSITY",
            f"intensities[{index}]",
            "rainfall intensities must be finite and non-negative",
            value=repr(value),
        )
    return 0.0 if result == 0 else result


def _normalise_timestamps(values: Any) -> tuple[datetime, ...]:
    if values is None:
        return ()
    try:
        raw_values = tuple(values)
    except TypeError:
        _raise("INVALID_TIMESTAMPS", "timestamps", "timestamps must be a sequence")
    return tuple(
        _as_datetime(value, f"timestamps[{index}]")
        for index, value in enumerate(raw_values)
    )


def _normalise_areas(values: Any) -> tuple["CatchmentAllocation", ...]:
    """Normalize area input to stable, normalized catchment weights."""

    if values is None:
        return ()
    if isinstance(values, Mapping):
        items: list[Any] = [
            {"catchment_id": key, "area_m2": value} for key, value in values.items()
        ]
    else:
        try:
            items = list(values)
        except TypeError:
            _raise(
                "INVALID_CATCHMENT_AREAS",
                "catchment_areas",
                "catchment_areas must be a mapping or sequence",
            )
            raise AssertionError
    if not items:
        return ()

    parsed: list[tuple[str, float]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if isinstance(item, CatchmentAllocation):
            key = item.catchment_id
            area = item.area_m2
        elif isinstance(item, Mapping):
            key = str(item.get("catchment_id", item.get("id", "")))
            area = item.get("area_m2", item.get("area"))
        elif (
            isinstance(item, Sequence)
            and not isinstance(item, (str, bytes))
            and len(item) == 2
        ):
            key, area = item
            key = str(key)
        else:
            _raise(
                "INVALID_CATCHMENT_AREAS",
                f"catchment_areas[{index}]",
                "each catchment allocation requires an id and area_m2",
            )
            raise AssertionError
        if not key or key in seen:
            _raise(
                "DUPLICATE_CATCHMENT",
                f"catchment_areas[{index}]",
                "catchment allocation ids must be unique and non-empty",
                catchment_id=key,
            )
        seen.add(key)
        try:
            numeric_area = float(area)
        except (TypeError, ValueError):
            _raise(
                "INVALID_CATCHMENT_AREA",
                f"catchment_areas[{index}].area_m2",
                "catchment area must be a finite positive number",
            )
            raise AssertionError
        if not math.isfinite(numeric_area) or numeric_area <= 0:
            _raise(
                "INVALID_CATCHMENT_AREA",
                f"catchment_areas[{index}].area_m2",
                "catchment area must be a finite positive number",
                value=repr(area),
            )
        parsed.append((key, numeric_area))

    total_area = sum(area for _, area in parsed)
    if not math.isfinite(total_area) or total_area <= 0:
        _raise(
            "INVALID_CATCHMENT_AREA",
            "catchment_areas",
            "total catchment area must be finite and positive",
        )
    return tuple(
        CatchmentAllocation(
            catchment_id=key,
            area_m2=area,
            weight=area / total_area,
        )
        for key, area in parsed
    )


def _validate_custom_request_values(
    *,
    start_at: datetime,
    duration_seconds: int,
    step_seconds: int,
    intensities: Sequence[float],
    timestamps: Sequence[datetime],
) -> None:
    expected = _sample_count(duration_seconds, step_seconds)
    if intensities and len(intensities) != expected:
        _raise(
            "SAMPLE_COUNT_MISMATCH",
            "intensities",
            "custom rainfall sample count must equal "
            f"floor(duration/step)+1={expected}",
            expected=expected,
            actual=len(intensities),
        )
    if not timestamps:
        return
    if len(timestamps) != expected:
        _raise(
            "SAMPLE_COUNT_MISMATCH",
            "timestamps",
            f"custom rainfall timestamp count must equal {expected}",
            expected=expected,
            actual=len(timestamps),
        )
    if timestamps[0] != start_at:
        _raise(
            "TIMESTAMP_GRID_MISMATCH",
            "timestamps[0]",
            "custom timestamps must start at start_at",
        )
    for index, (left, right) in enumerate(zip(timestamps, timestamps[1:])):
        if (right - left).total_seconds() != step_seconds:
            _raise(
                "TIMESTAMP_STEP_MISMATCH",
                f"timestamps[{index + 1}]",
                "custom timestamps must be strictly increasing by step_seconds",
            )


@dataclass(frozen=True, slots=True)
class CatchmentAllocation:
    """Area-weighted rainfall allocation for one catchment."""

    catchment_id: str
    area_m2: float
    weight: float

    def __post_init__(self) -> None:
        if not isinstance(self.catchment_id, str) or not self.catchment_id.strip():
            _raise(
                "INVALID_CATCHMENT_ID",
                "catchment_id",
                "catchment_id must be non-empty text",
            )
        try:
            area = float(self.area_m2)
            weight = float(self.weight)
        except (TypeError, ValueError):
            _raise(
                "INVALID_CATCHMENT_AREA",
                "area_m2",
                "catchment area and weight must be finite numbers",
            )
            raise AssertionError
        if not math.isfinite(area) or area <= 0:
            _raise(
                "INVALID_CATCHMENT_AREA",
                "area_m2",
                "catchment area must be finite and positive",
            )
        if not math.isfinite(weight) or weight < 0 or weight > 1:
            _raise(
                "INVALID_CATCHMENT_WEIGHT",
                "weight",
                "catchment weight must be finite and between zero and one",
            )
        object.__setattr__(self, "area_m2", area)
        object.__setattr__(self, "weight", weight)

    def to_dict(self) -> dict[str, Any]:
        return {
            "catchment_id": self.catchment_id,
            "area_m2": self.area_m2,
            "weight": self.weight,
        }


@dataclass(frozen=True, slots=True)
class RainfallRequest:
    """Immutable input accepted by :class:`RainfallSource`.

    ``duration_seconds`` need not be an exact multiple of ``step_seconds``.
    The frozen contract deliberately defines the sample count as
    ``floor(duration / step) + 1``; the requested duration is retained as the
    series end time and a final partial interval may therefore be shorter.
    """

    start_at: datetime = DEFAULT_START_AT
    duration_seconds: int = DEFAULT_DURATION_SECONDS
    step_seconds: int = DEFAULT_STEP_SECONDS
    kind: str = RainfallScenarioKind.synthetic.value
    pattern: str | None = None
    return_period_years: ReturnPeriodValue = None
    random_seed: int = 0
    algorithm: str | None = None
    intensities: tuple[float, ...] = ()
    timestamps: tuple[datetime, ...] = ()
    catchment_areas: tuple[CatchmentAllocation, ...] = ()
    internal_step_seconds: int | None = None
    source_type: str = SYNTHETIC_SOURCE_ID
    allocation_method: str = "area_weighted"

    def __post_init__(self) -> None:
        start_at = _as_datetime(self.start_at, "start_at")
        duration = _validate_duration(self.duration_seconds)
        step = _validate_step(self.step_seconds)
        pattern = _coerce_pattern(self.kind, self.pattern)
        scenario_kind = _scenario_kind(pattern)
        period = _coerce_return_period(
            self.return_period_years,
            required=pattern is RainfallPattern.design,
        )
        if pattern is not RainfallPattern.design and period not in (None, "custom"):
            _raise(
                "RETURN_PERIOD_NOT_ALLOWED",
                "return_period_years",
                "only design rainfall may set return_period_years",
            )
        seed = _validate_seed(self.random_seed)
        source_type = str(self.source_type).strip().lower()
        if source_type != SYNTHETIC_SOURCE_ID:
            _raise(
                "UNSUPPORTED_RAINFALL_SOURCE",
                "source_type",
                "only the built-in synthetic rainfall source is supported",
            )
        expected_algorithm = _PATTERN_ALGORITHMS[pattern]
        algorithm = self.algorithm or expected_algorithm
        if algorithm != expected_algorithm:
            _raise(
                "INVALID_ALGORITHM",
                "algorithm",
                f"{pattern.value} rainfall requires algorithm {expected_algorithm}",
                expected=expected_algorithm,
            )
        internal_step = (
            step
            if self.internal_step_seconds is None
            else _as_int(self.internal_step_seconds, "internal_step_seconds")
        )
        if internal_step <= 0 or internal_step > step or step % internal_step:
            _raise(
                "INVALID_INTERNAL_STEP",
                "internal_step_seconds",
                "internal_step_seconds must be a positive divisor of step_seconds",
            )
        if str(self.allocation_method) not in {"area_weighted", "uniform"}:
            _raise(
                "INVALID_ALLOCATION_METHOD",
                "allocation_method",
                "allocation_method must be area_weighted or uniform",
            )
        if self.catchment_areas and str(self.allocation_method) != "area_weighted":
            _raise(
                "INVALID_ALLOCATION_METHOD",
                "allocation_method",
                "catchment areas require area_weighted allocation",
            )
        if pattern is RainfallPattern.design and (self.intensities or self.timestamps):
            _raise(
                "DESIGN_VALUES_NOT_ALLOWED",
                "intensities",
                "design rainfall is generated and cannot provide custom values",
            )
        if pattern is not RainfallPattern.custom and (
            self.intensities or self.timestamps
        ):
            _raise(
                "CUSTOM_VALUES_NOT_ALLOWED",
                "intensities",
                "custom values are only accepted for custom rainfall",
            )

        object.__setattr__(self, "start_at", start_at)
        object.__setattr__(self, "duration_seconds", duration)
        object.__setattr__(self, "step_seconds", step)
        object.__setattr__(self, "kind", scenario_kind.value)
        object.__setattr__(self, "pattern", pattern.value)
        object.__setattr__(self, "return_period_years", period)
        object.__setattr__(self, "random_seed", seed)
        object.__setattr__(self, "algorithm", algorithm.strip())
        normalized_intensities = tuple(
            _as_intensity(value, index)
            for index, value in enumerate(self.intensities or ())
        )
        normalized_timestamps = _normalise_timestamps(self.timestamps)
        object.__setattr__(self, "intensities", normalized_intensities)
        object.__setattr__(self, "timestamps", normalized_timestamps)
        object.__setattr__(
            self, "catchment_areas", _normalise_areas(self.catchment_areas)
        )
        object.__setattr__(self, "internal_step_seconds", internal_step)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "allocation_method", str(self.allocation_method))
        if pattern is RainfallPattern.custom:
            _validate_custom_request_values(
                start_at=start_at,
                duration_seconds=duration,
                step_seconds=step,
                intensities=normalized_intensities,
                timestamps=normalized_timestamps,
            )

    @property
    def end_at(self) -> datetime:
        return self.start_at + timedelta(seconds=self.duration_seconds)

    @property
    def sample_count(self) -> int:
        return _sample_count(self.duration_seconds, self.step_seconds)

    @property
    def seed(self) -> int:
        return self.random_seed

    @property
    def step(self) -> int:
        return self.step_seconds

    @property
    def duration(self) -> int:
        return self.duration_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "rainfall-request-v1",
            "start_at": _datetime_text(self.start_at),
            "end_at": _datetime_text(self.end_at),
            "duration_seconds": self.duration_seconds,
            "step_seconds": self.step_seconds,
            "internal_step_seconds": self.internal_step_seconds,
            "kind": self.kind,
            "pattern": self.pattern,
            "return_period_years": self.return_period_years,
            "source_type": self.source_type,
            "algorithm": self.algorithm,
            "random_seed": self.random_seed,
            "intensities": list(self.intensities),
            "timestamps": [_datetime_text(value) for value in self.timestamps],
            "catchment_allocation": [
                allocation.to_dict() for allocation in self.catchment_areas
            ],
            "allocation_method": self.allocation_method,
        }


@dataclass(frozen=True, slots=True)
class RainfallSample:
    """One point on the native rainfall time grid."""

    point_index: int
    valid_at: datetime
    intensity_mm_h: float

    @property
    def time(self) -> datetime:
        return self.valid_at

    @property
    def rainfall_mm_per_hour(self) -> float:
        return self.intensity_mm_h

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_index": self.point_index,
            "valid_at": _datetime_text(self.valid_at),
            "time": _datetime_text(self.valid_at),
            "intensity_mm_h": self.intensity_mm_h,
            "rainfall_mm_per_hour": self.intensity_mm_h,
        }


@dataclass(frozen=True, slots=True)
class RainfallSeries:
    """Materialized, hashable rainfall input consumed by a solver."""

    start_at: datetime
    end_at: datetime
    native_step_seconds: int
    internal_step_seconds: int
    intensity_mm_h: tuple[float, ...]
    catchment_allocation: tuple[CatchmentAllocation, ...] = ()
    random_seed: int = 0
    algorithm: str = CUSTOM_ALGORITHM
    algorithm_version: str = SYNTHETIC_SOURCE_VERSION
    source_id: str = SYNTHETIC_SOURCE_ID
    source_version: str = SYNTHETIC_SOURCE_VERSION
    content_sha256: str = ""
    kind: str = RainfallScenarioKind.custom.value
    return_period_years: ReturnPeriodValue = None
    duration_seconds: int | None = None
    allocation_method: str = "uniform"

    def __post_init__(self) -> None:
        start_at = _as_datetime(self.start_at, "start_at")
        end_at = _as_datetime(self.end_at, "end_at")
        if end_at <= start_at:
            _raise(
                "INVALID_DURATION",
                "end_at",
                "end_at must be later than start_at",
            )
        duration = (
            int(round((end_at - start_at).total_seconds()))
            if self.duration_seconds is None
            else _validate_duration(self.duration_seconds)
        )
        if duration <= 0:
            _raise("INVALID_DURATION", "duration_seconds", "duration must be positive")
        if abs((end_at - start_at).total_seconds() - duration) > 1e-6:
            _raise(
                "DURATION_MISMATCH",
                "end_at",
                "end_at must equal start_at plus duration_seconds",
            )
        native_step = _validate_step(self.native_step_seconds, "native_step_seconds")
        internal_step = (
            native_step
            if self.internal_step_seconds is None
            else _as_int(self.internal_step_seconds, "internal_step_seconds")
        )
        if (
            internal_step <= 0
            or internal_step > native_step
            or native_step % internal_step
        ):
            _raise(
                "INVALID_INTERNAL_STEP",
                "internal_step_seconds",
                "internal_step_seconds must be a positive divisor "
                "of native_step_seconds",
            )
        try:
            raw_values = tuple(self.intensity_mm_h)
        except TypeError:
            _raise(
                "INVALID_INTENSITIES",
                "intensity_mm_h",
                "intensity_mm_h must be a sequence",
            )
            raise AssertionError
        if len(raw_values) != _sample_count(duration, native_step):
            expected = _sample_count(duration, native_step)
            _raise(
                "SAMPLE_COUNT_MISMATCH",
                "intensity_mm_h",
                f"rainfall sample count must equal floor(duration/step)+1={expected}",
                expected=expected,
                actual=len(raw_values),
            )
        values = tuple(
            _as_intensity(value, index) for index, value in enumerate(raw_values)
        )
        allocations = _normalise_areas(self.catchment_allocation)
        allocation_method = str(self.allocation_method)
        if allocations:
            allocation_method = "area_weighted"
        elif allocation_method not in {"uniform", "area_weighted"}:
            _raise(
                "INVALID_ALLOCATION_METHOD",
                "allocation_method",
                "allocation_method must be area_weighted or uniform",
            )
        pattern = _coerce_pattern(self.kind, None)
        if pattern is RainfallPattern.random:
            normalized_kind = RainfallScenarioKind.synthetic.value
        else:
            normalized_kind = pattern.value
        period = _coerce_return_period(
            self.return_period_years,
            required=pattern is RainfallPattern.design,
        )
        if pattern is not RainfallPattern.design and period not in (None, "custom"):
            _raise(
                "RETURN_PERIOD_NOT_ALLOWED",
                "return_period_years",
                "only design rainfall may set return_period_years",
            )
        expected_algorithm = _PATTERN_ALGORITHMS[pattern]
        if self.algorithm != expected_algorithm:
            _raise(
                "INVALID_ALGORITHM",
                "algorithm",
                f"{pattern.value} rainfall requires algorithm {expected_algorithm}",
                expected=expected_algorithm,
            )
        if self.algorithm_version != SYNTHETIC_SOURCE_VERSION:
            _raise(
                "INVALID_ALGORITHM_VERSION",
                "algorithm_version",
                "algorithm_version must identify the built-in synthetic source",
                expected=SYNTHETIC_SOURCE_VERSION,
            )
        if self.source_id != SYNTHETIC_SOURCE_ID:
            _raise(
                "INVALID_SOURCE",
                "source_id",
                "only the built-in synthetic rainfall source is supported",
            )
        if self.source_version != SYNTHETIC_SOURCE_VERSION:
            _raise(
                "INVALID_SOURCE",
                "source_version",
                "source_version must identify the built-in synthetic source",
                expected=SYNTHETIC_SOURCE_VERSION,
            )

        object.__setattr__(self, "start_at", start_at)
        object.__setattr__(self, "end_at", end_at)
        object.__setattr__(self, "duration_seconds", duration)
        object.__setattr__(self, "native_step_seconds", native_step)
        object.__setattr__(self, "internal_step_seconds", internal_step)
        object.__setattr__(self, "intensity_mm_h", values)
        object.__setattr__(self, "catchment_allocation", allocations)
        object.__setattr__(self, "allocation_method", allocation_method)
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "return_period_years", period)
        object.__setattr__(self, "random_seed", _validate_seed(self.random_seed))
        computed_hash = _sha256(_canonical_json(_series_payload(self)))
        if self.content_sha256 and self.content_sha256 != computed_hash:
            _raise(
                "CONTENT_HASH_MISMATCH",
                "content_sha256",
                "content_sha256 does not match the canonical rainfall series",
                expected=computed_hash,
                actual=self.content_sha256,
            )
        object.__setattr__(self, "content_sha256", computed_hash)

    @property
    def sample_count(self) -> int:
        return len(self.intensity_mm_h)

    @property
    def intensities(self) -> tuple[float, ...]:
        return self.intensity_mm_h

    @property
    def rainfall_mm_per_hour(self) -> tuple[float, ...]:
        return self.intensity_mm_h

    @property
    def step_seconds(self) -> int:
        return self.native_step_seconds

    @property
    def native_step(self) -> int:
        return self.native_step_seconds

    @property
    def internal_step(self) -> int:
        return self.internal_step_seconds

    @property
    def seed(self) -> int:
        return self.random_seed

    @property
    def duration(self) -> int:
        assert self.duration_seconds is not None
        return self.duration_seconds

    @property
    def timestamps(self) -> tuple[datetime, ...]:
        return tuple(
            self.start_at + timedelta(seconds=index * self.native_step_seconds)
            for index in range(self.sample_count)
        )

    @property
    def samples(self) -> tuple[RainfallSample, ...]:
        return tuple(
            RainfallSample(index, timestamp, intensity)
            for index, (timestamp, intensity) in enumerate(
                zip(self.timestamps, self.intensity_mm_h)
            )
        )

    @property
    def total_depth_mm(self) -> float:
        """Integrated depth, including a possible final partial interval."""

        total = 0.0
        for index, intensity in enumerate(self.intensity_mm_h):
            interval_start = index * self.native_step_seconds
            if interval_start >= self.duration:
                break
            interval = min(
                self.native_step_seconds,
                self.duration - interval_start,
            )
            total += intensity * interval / 3600.0
        return round(total, 10)

    @property
    def catchment_rainfall_mm(self) -> tuple[float, ...]:
        return tuple(
            round(self.total_depth_mm * allocation.weight, 10)
            for allocation in self.catchment_allocation
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_VERSION,
            "start_at": _datetime_text(self.start_at),
            "end_at": _datetime_text(self.end_at),
            "duration_seconds": self.duration,
            "native_step_seconds": self.native_step_seconds,
            "internal_step_seconds": self.internal_step_seconds,
            "sample_count": self.sample_count,
            "intensity_mm_h": list(self.intensity_mm_h),
            "catchment_allocation": [
                allocation.to_dict() for allocation in self.catchment_allocation
            ],
            "allocation_method": self.allocation_method,
            "random_seed": self.random_seed,
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "kind": self.kind,
            "return_period_years": self.return_period_years,
            "total_depth_mm": self.total_depth_mm,
            "content_sha256": self.content_sha256,
        }

    def canonical_json(self) -> str:
        return _canonical_json(_series_payload(self))

    def validate(self) -> "RainfallSeries":
        return self


def _series_payload(series: RainfallSeries) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "start_at": _datetime_text(series.start_at),
        "end_at": _datetime_text(series.end_at),
        "duration_seconds": series.duration,
        "native_step_seconds": series.native_step_seconds,
        "internal_step_seconds": series.internal_step_seconds,
        "sample_count": series.sample_count,
        "intensity_mm_h": list(series.intensity_mm_h),
        "catchment_allocation": [
            allocation.to_dict() for allocation in series.catchment_allocation
        ],
        "allocation_method": series.allocation_method,
        "random_seed": series.random_seed,
        "algorithm": series.algorithm,
        "algorithm_version": series.algorithm_version,
        "source_id": series.source_id,
        "source_version": series.source_version,
        "kind": series.kind,
        "return_period_years": series.return_period_years,
    }


@runtime_checkable
class RainfallSource(Protocol):
    """Stable source boundary used by scenario materializers and solvers."""

    source_id: str
    version: str

    def materialize(self, request: RainfallRequest) -> RainfallSeries:
        """Materialize one validated request into an immutable series."""
        ...


def _grid_timestamps(request: RainfallRequest) -> tuple[datetime, ...]:
    return tuple(
        request.start_at + timedelta(seconds=index * request.step_seconds)
        for index in range(request.sample_count)
    )


def _design_intensities(request: RainfallRequest) -> tuple[float, ...]:
    period = _as_int(request.return_period_years, "return_period_years")
    count = request.sample_count
    if count == 2:
        # The two grid points bracket one interval.  The interval-start sample
        # carries the peak so the single-interval total is non-zero.
        return (round(20.0 * period, 8), 0.0)
    duration = float(request.duration_seconds)
    peak = max(float(request.step_seconds), duration * 0.4)
    values: list[float] = []
    for index in range(count):
        elapsed = min(float(index * request.step_seconds), duration)
        if elapsed <= peak:
            shape = (elapsed / peak) ** 2
        else:
            shape = ((duration - elapsed) / max(duration - peak, 1.0)) ** 2
        # The profile is a screening envelope, not a local IDF claim.
        values.append(round(20.0 * period * shape, 8))
    return tuple(values)


class _SplitMix64:
    """Small fixed PRNG so random output is stable across Python versions."""

    __slots__ = ("_state",)
    _MASK = (1 << 64) - 1
    _GAMMA = 0x9E3779B97F4A7C15

    def __init__(self, seed: int) -> None:
        self._state = seed & self._MASK

    def next_unit(self) -> float:
        self._state = (self._state + self._GAMMA) & self._MASK
        value = self._state
        value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & self._MASK
        value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & self._MASK
        value ^= value >> 31
        return (value >> 11) * (1.0 / (1 << 53))


def _random_intensities(request: RainfallRequest) -> tuple[float, ...]:
    rng = _SplitMix64(request.random_seed)
    count = request.sample_count
    values: list[float] = []
    for index in range(count):
        phase = index / max(1, count - 1)
        envelope = math.exp(-(((phase - 0.36) / 0.22) ** 2))
        noise = rng.next_unit()
        # A non-zero baseline keeps short (one-interval) storms meaningful.
        values.append(round(1.0 + 18.0 * noise + 22.0 * envelope * noise, 8))
    return tuple(values)


def _request_from_value(value: RainfallRequest | Mapping[str, Any]) -> RainfallRequest:
    if isinstance(value, RainfallRequest):
        return value
    if not isinstance(value, Mapping):
        _raise(
            "INVALID_REQUEST", "request", "request must be RainfallRequest or mapping"
        )
    data = dict(value)
    if "duration_seconds" not in data:
        if "duration_hours" in data:
            data["duration_seconds"] = _unit_seconds(
                data.pop("duration_hours"), 3600, "duration_hours"
            )
        elif "duration_minutes" in data:
            data["duration_seconds"] = _unit_seconds(
                data.pop("duration_minutes"), 60, "duration_minutes"
            )
    if "step_seconds" not in data and "step_minutes" in data:
        data["step_seconds"] = _unit_seconds(
            data.pop("step_minutes"), 60, "step_minutes"
        )
    if "random_seed" not in data and "seed" in data:
        data["random_seed"] = data.pop("seed")
    if "return_period_years" not in data and "return_period" in data:
        data["return_period_years"] = data.pop("return_period")
    if "intensities" not in data and "values" in data:
        data["intensities"] = data.pop("values")
    if "start_at" not in data and "start" in data:
        data["start_at"] = data.pop("start")
    if "catchment_areas" not in data:
        if "areas" in data:
            data["catchment_areas"] = data.pop("areas")
        elif "area_allocation" in data:
            data["catchment_areas"] = data.pop("area_allocation")
    if "pattern" not in data and "rainfall_type" in data:
        data["pattern"] = data.pop("rainfall_type")
    return RainfallRequest(**data)


def _make_series(
    request: RainfallRequest, intensities: tuple[float, ...]
) -> RainfallSeries:
    return RainfallSeries(
        start_at=request.start_at,
        end_at=request.end_at,
        native_step_seconds=request.step_seconds,
        internal_step_seconds=request.internal_step_seconds or request.step_seconds,
        intensity_mm_h=intensities,
        catchment_allocation=request.catchment_areas,
        random_seed=request.random_seed,
        algorithm=request.algorithm or CUSTOM_ALGORITHM,
        algorithm_version=SYNTHETIC_SOURCE_VERSION,
        source_id=self_source_id,
        source_version=SYNTHETIC_SOURCE_VERSION,
        kind=request.kind,
        return_period_years=request.return_period_years,
        duration_seconds=request.duration_seconds,
        allocation_method=(
            request.allocation_method if request.catchment_areas else "uniform"
        ),
    )


self_source_id = SYNTHETIC_SOURCE_ID


class SyntheticRainfallSource:
    """Built-in deterministic source for design, custom, and random patterns."""

    source_id = SYNTHETIC_SOURCE_ID
    version = SYNTHETIC_SOURCE_VERSION

    def materialize(
        self, request: RainfallRequest | Mapping[str, Any]
    ) -> RainfallSeries:
        normalized = _request_from_value(request)
        if normalized.pattern == RainfallPattern.design.value:
            intensities = _design_intensities(normalized)
        elif normalized.pattern == RainfallPattern.random.value:
            intensities = _random_intensities(normalized)
        else:
            if not normalized.intensities:
                _raise(
                    "MISSING_CUSTOM_INTENSITIES",
                    "intensities",
                    "custom rainfall requires at least one intensity value",
                )
            expected = normalized.sample_count
            if len(normalized.intensities) != expected:
                _raise(
                    "SAMPLE_COUNT_MISMATCH",
                    "intensities",
                    "custom rainfall sample count must equal "
                    f"floor(duration/step)+1={expected}",
                    expected=expected,
                    actual=len(normalized.intensities),
                )
            if normalized.timestamps:
                if len(normalized.timestamps) != expected:
                    _raise(
                        "SAMPLE_COUNT_MISMATCH",
                        "timestamps",
                        f"custom rainfall timestamp count must equal {expected}",
                        expected=expected,
                        actual=len(normalized.timestamps),
                    )
                if normalized.timestamps[0] != normalized.start_at:
                    _raise(
                        "TIMESTAMP_GRID_MISMATCH",
                        "timestamps[0]",
                        "custom timestamps must start at start_at",
                    )
                for index, (left, right) in enumerate(
                    zip(normalized.timestamps, normalized.timestamps[1:])
                ):
                    if (right - left).total_seconds() != normalized.step_seconds:
                        _raise(
                            "TIMESTAMP_STEP_MISMATCH",
                            f"timestamps[{index + 1}]",
                            "custom timestamps must be strictly "
                            "increasing by step_seconds",
                        )
            intensities = tuple(
                _as_intensity(value, index)
                for index, value in enumerate(normalized.intensities)
            )
        return _make_series(normalized, intensities)


SYNTHETIC_RAINFALL_SOURCE = SyntheticRainfallSource()


def generate_design_rainfall(
    return_period_years: ReturnPeriod,
    *,
    start_at: datetime = DEFAULT_START_AT,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    step_seconds: int = DEFAULT_STEP_SECONDS,
    random_seed: int = 0,
    catchment_areas: Any = (),
    internal_step_seconds: int | None = None,
) -> RainfallSeries:
    """Generate a deterministic 1/2/3/5/10/20/50/100-year design storm."""

    return SYNTHETIC_RAINFALL_SOURCE.materialize(
        RainfallRequest(
            start_at=start_at,
            duration_seconds=duration_seconds,
            step_seconds=step_seconds,
            kind=RainfallPattern.design,
            return_period_years=return_period_years,
            random_seed=random_seed,
            catchment_areas=catchment_areas,
            internal_step_seconds=internal_step_seconds,
        )
    )


def generate_custom_rainfall(
    intensities: Sequence[float],
    *,
    start_at: datetime = DEFAULT_START_AT,
    duration_seconds: int | None = None,
    step_seconds: int = DEFAULT_STEP_SECONDS,
    timestamps: Sequence[datetime] | None = None,
    random_seed: int = 0,
    return_period_years: ReturnPeriodValue = None,
    catchment_areas: Any = (),
    internal_step_seconds: int | None = None,
) -> RainfallSeries:
    """Validate and materialize a user-supplied storm of at most 72 hours."""

    values = tuple(intensities)
    if duration_seconds is None:
        if len(values) < 2:
            _raise(
                "INVALID_DURATION",
                "duration_seconds",
                "duration_seconds is required when fewer than two samples are supplied",
            )
        duration_seconds = (len(values) - 1) * _validate_step(step_seconds)
    if timestamps is not None and duration_seconds is None:
        raise AssertionError("duration is inferred before timestamp validation")
    return SYNTHETIC_RAINFALL_SOURCE.materialize(
        RainfallRequest(
            start_at=start_at,
            duration_seconds=duration_seconds,
            step_seconds=step_seconds,
            kind=RainfallPattern.custom,
            return_period_years=return_period_years,
            random_seed=random_seed,
            intensities=values,
            timestamps=tuple(timestamps or ()),
            catchment_areas=catchment_areas,
            internal_step_seconds=internal_step_seconds,
        )
    )


def generate_random_rainfall(
    *,
    start_at: datetime = DEFAULT_START_AT,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    step_seconds: int = DEFAULT_STEP_SECONDS,
    random_seed: int = 0,
    seed: int | None = None,
    catchment_areas: Any = (),
    internal_step_seconds: int | None = None,
) -> RainfallSeries:
    """Generate a random storm reproducibly from ``random_seed``/``seed``."""

    effective_seed = random_seed if seed is None else seed
    return SYNTHETIC_RAINFALL_SOURCE.materialize(
        RainfallRequest(
            start_at=start_at,
            duration_seconds=duration_seconds,
            step_seconds=step_seconds,
            kind=RainfallPattern.random,
            random_seed=effective_seed,
            catchment_areas=catchment_areas,
            internal_step_seconds=internal_step_seconds,
        )
    )


def materialize_rainfall(
    request: RainfallRequest | Mapping[str, Any],
    *,
    source: RainfallSource | None = None,
) -> RainfallSeries:
    """Materialize a request through the built-in or explicitly supplied source."""

    selected = SYNTHETIC_RAINFALL_SOURCE if source is None else source
    if not isinstance(selected, SyntheticRainfallSource):
        _raise(
            "INVALID_SOURCE",
            "source",
            "only the built-in SyntheticRainfallSource is supported",
        )
    return selected.materialize(_request_from_value(request))


def validate_rainfall_request(
    request: RainfallRequest | Mapping[str, Any]
) -> RainfallRequest:
    """Validate and normalize a request without materializing a series."""

    return _request_from_value(request)


def validate_rainfall_series(series: RainfallSeries) -> RainfallSeries:
    """Re-run the series invariant checks and return the immutable object."""

    if not isinstance(series, RainfallSeries):
        _raise("INVALID_SERIES", "series", "series must be RainfallSeries")
    # Construction already validates every field and verifies the content hash.
    return series


@dataclass(frozen=True, slots=True)
class RainfallScenario:
    """Versioned scenario metadata paired with one immutable series."""

    series: RainfallSeries
    scenario_id: uuid.UUID = dataclass_field(default_factory=uuid.uuid4)
    network_id: uuid.UUID | None = None
    root_scenario_id: uuid.UUID | None = None
    version: int = 1
    name: str = "rainfall scenario"
    kind: str = RainfallScenarioKind.synthetic.value
    return_period_years: ReturnPeriodValue = None
    source_type: str = SYNTHETIC_SOURCE_ID
    source_id: str = SYNTHETIC_SOURCE_ID
    source_version: str = SYNTHETIC_SOURCE_VERSION
    algorithm: str = RANDOM_ALGORITHM
    random_seed: int = 0
    content_sha256: str = ""
    created_at: datetime = dataclass_field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not isinstance(self.series, RainfallSeries):
            _raise("INVALID_SERIES", "series", "scenario series must be RainfallSeries")
        scenario_id = _as_uuid(self.scenario_id, "scenario_id")
        network_id = (
            None if self.network_id is None else _as_uuid(self.network_id, "network_id")
        )
        root_scenario_id = (
            None
            if self.root_scenario_id is None
            else _as_uuid(self.root_scenario_id, "root_scenario_id")
        )
        if root_scenario_id == scenario_id:
            _raise(
                "INVALID_ROOT",
                "root_scenario_id",
                "root_scenario_id must not equal scenario_id",
            )
        version = _as_int(self.version, "version")
        if version < 1:
            _raise("INVALID_VERSION", "version", "version must be at least one")
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or len(self.name) > 255
        ):
            _raise(
                "INVALID_NAME",
                "name",
                "name must be non-empty and at most 255 characters",
            )
        pattern = _coerce_pattern(self.kind, None)
        normalized_kind = _scenario_kind(pattern).value
        period = _coerce_return_period(
            self.return_period_years,
            required=pattern is RainfallPattern.design,
        )
        if pattern is not RainfallPattern.design and period not in (None, "custom"):
            _raise(
                "RETURN_PERIOD_NOT_ALLOWED",
                "return_period_years",
                "only design rainfall may set a numeric return period",
            )
        if str(self.source_type).strip().lower() != SYNTHETIC_SOURCE_ID:
            _raise(
                "UNSUPPORTED_RAINFALL_SOURCE",
                "source_type",
                "only synthetic rainfall scenarios are supported",
            )
        declared = {
            "kind": normalized_kind,
            "return_period_years": period,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "algorithm": self.algorithm,
            "random_seed": self.random_seed,
            "content_sha256": self.content_sha256 or self.series.content_sha256,
        }
        expected = {
            "kind": self.series.kind,
            "return_period_years": self.series.return_period_years,
            "source_id": self.series.source_id,
            "source_version": self.series.source_version,
            "algorithm": self.series.algorithm,
            "random_seed": self.series.random_seed,
            "content_sha256": self.series.content_sha256,
        }
        mismatches = {
            key: {"declared": declared[key], "series": expected[key]}
            for key in expected
            if declared[key] != expected[key]
        }
        if mismatches:
            _raise(
                "SCENARIO_SERIES_MISMATCH",
                "series",
                "scenario metadata must match its rainfall series",
                mismatches=mismatches,
            )
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "network_id", network_id)
        object.__setattr__(self, "root_scenario_id", root_scenario_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "kind", normalized_kind)
        object.__setattr__(self, "return_period_years", period)
        object.__setattr__(self, "source_type", SYNTHETIC_SOURCE_ID)
        object.__setattr__(self, "source_id", self.series.source_id)
        object.__setattr__(self, "source_version", self.series.source_version)
        object.__setattr__(self, "algorithm", self.series.algorithm)
        object.__setattr__(self, "random_seed", self.series.random_seed)
        object.__setattr__(self, "content_sha256", self.series.content_sha256)
        object.__setattr__(
            self, "created_at", _as_datetime(self.created_at, "created_at")
        )

    @property
    def id(self) -> uuid.UUID:
        return self.scenario_id

    @property
    def step_seconds(self) -> int:
        return self.series.native_step_seconds

    @property
    def duration_seconds(self) -> int:
        return self.series.duration

    @property
    def random_seed_value(self) -> int:
        return self.random_seed

    @property
    def samples(self) -> tuple[RainfallSample, ...]:
        return self.series.samples

    @property
    def points(self) -> tuple[RainfallSample, ...]:
        return self.series.samples

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "rainfall-scenario-v1",
            "id": str(self.scenario_id),
            "network_id": None if self.network_id is None else str(self.network_id),
            "root_scenario_id": (
                None if self.root_scenario_id is None else str(self.root_scenario_id)
            ),
            "version": self.version,
            "name": self.name,
            "kind": self.kind,
            "return_period_years": self.return_period_years,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_version": self.source_version,
            "algorithm": self.algorithm,
            "random_seed": self.random_seed,
            "content_sha256": self.content_sha256,
            "series": self.series.to_dict(),
        }

    def copy(
        self,
        *,
        scenario_id: uuid.UUID | str | None = None,
        new_id: uuid.UUID | str | None = None,
        name: str | None = None,
        root_scenario_id: Any = _MISSING,
        preserve_root: bool = True,
        network_id: Any = _MISSING,
        created_at: datetime | None = None,
    ) -> "RainfallScenario":
        return copy_scenario(
            self,
            scenario_id=scenario_id,
            new_id=new_id,
            name=name,
            root_scenario_id=root_scenario_id,
            preserve_root=preserve_root,
            network_id=network_id,
            created_at=created_at,
        )


def materialize_scenario(
    request: RainfallRequest | Mapping[str, Any] | RainfallSeries,
    *,
    scenario_id: uuid.UUID | str | None = None,
    network_id: uuid.UUID | str | None = None,
    root_scenario_id: uuid.UUID | str | None = None,
    name: str | None = None,
    version: int = 1,
    source: RainfallSource | None = None,
    created_at: datetime | None = None,
) -> RainfallScenario:
    """Materialize and wrap a request or an already materialized series."""

    if isinstance(request, RainfallSeries):
        if source is not None:
            series = materialize_rainfall(
                RainfallRequest(
                    start_at=request.start_at,
                    duration_seconds=request.duration,
                    step_seconds=request.native_step_seconds,
                    kind=request.kind,
                    return_period_years=request.return_period_years,
                    random_seed=request.random_seed,
                    algorithm=request.algorithm,
                    catchment_areas=request.catchment_allocation,
                    internal_step_seconds=request.internal_step_seconds,
                ),
                source=source,
            )
        else:
            series = request
    else:
        normalized = _request_from_value(request)
        series = materialize_rainfall(normalized, source=source)
    default_name = (
        f"{series.return_period_years}-year design storm"
        if series.kind == RainfallScenarioKind.design.value
        else (
            "custom rainfall"
            if series.kind == RainfallScenarioKind.custom.value
            else "random rainfall"
        )
    )
    resolved_scenario_id = (
        uuid.uuid4() if scenario_id is None else _as_uuid(scenario_id, "scenario_id")
    )
    resolved_network_id = (
        None if network_id is None else _as_uuid(network_id, "network_id")
    )
    resolved_root_scenario_id = (
        None
        if root_scenario_id is None
        else _as_uuid(root_scenario_id, "root_scenario_id")
    )
    return RainfallScenario(
        series=series,
        scenario_id=resolved_scenario_id,
        network_id=resolved_network_id,
        root_scenario_id=resolved_root_scenario_id,
        version=version,
        name=default_name if name is None else name,
        kind=series.kind,
        return_period_years=series.return_period_years,
        source_type=SYNTHETIC_SOURCE_ID,
        source_id=series.source_id,
        source_version=series.source_version,
        algorithm=series.algorithm,
        random_seed=series.random_seed,
        content_sha256=series.content_sha256,
        created_at=created_at or datetime.now(UTC),
    )


def create_scenario(
    request: RainfallRequest | Mapping[str, Any] | RainfallSeries,
    **kwargs: Any,
) -> RainfallScenario:
    """Compatibility alias for :func:`materialize_scenario`."""

    return materialize_scenario(request, **kwargs)


def copy_scenario(
    scenario: RainfallScenario,
    *,
    scenario_id: uuid.UUID | str | None = None,
    new_id: uuid.UUID | str | None = None,
    name: str | None = None,
    root_scenario_id: Any = _MISSING,
    preserve_root: bool = True,
    network_id: Any = _MISSING,
    created_at: datetime | None = None,
) -> RainfallScenario:
    """Copy a scenario without mutating it.

    The new scenario gets a fresh ID and ``version + 1``.  The lineage root,
    random seed, source/algorithm metadata, points, and content hash are
    preserved.  Supplying ``root_scenario_id`` explicitly changes the lineage;
    otherwise the original root value is retained, including ``None``.
    """

    if not isinstance(scenario, RainfallScenario):
        _raise("INVALID_SCENARIO", "scenario", "scenario must be RainfallScenario")
    if scenario_id is not None and new_id is not None:
        _raise(
            "DUPLICATE_ID_ARGUMENT",
            "scenario_id",
            "provide only one of scenario_id/new_id",
        )
    requested_id = scenario_id if scenario_id is not None else new_id
    copied_id = (
        uuid.uuid4() if requested_id is None else _as_uuid(requested_id, "scenario_id")
    )
    if copied_id == scenario.scenario_id:
        _raise(
            "DUPLICATE_SCENARIO_ID", "scenario_id", "copied scenario must have a new id"
        )
    copied_root = (
        scenario.root_scenario_id if root_scenario_id is _MISSING else root_scenario_id
    )
    if not preserve_root and root_scenario_id is _MISSING:
        copied_root = None
    copied_network = scenario.network_id if network_id is _MISSING else network_id
    return RainfallScenario(
        series=scenario.series,
        scenario_id=copied_id,
        network_id=copied_network,
        root_scenario_id=copied_root,
        version=scenario.version + 1,
        name=name if name is not None else scenario.name,
        kind=scenario.kind,
        return_period_years=scenario.return_period_years,
        source_type=scenario.source_type,
        source_id=scenario.source_id,
        source_version=scenario.source_version,
        algorithm=scenario.algorithm,
        random_seed=scenario.random_seed,
        content_sha256=scenario.content_sha256,
        created_at=created_at or datetime.now(UTC),
    )


def clone_scenario(scenario: RainfallScenario, **kwargs: Any) -> RainfallScenario:
    """Alias for :func:`copy_scenario`."""

    return copy_scenario(scenario, **kwargs)


def get_rainfall_source(source_type: str = SYNTHETIC_SOURCE_ID) -> RainfallSource:
    """Return the only built-in source; reject unconfigured source names."""

    normalized = str(source_type).strip().lower()
    if normalized != SYNTHETIC_SOURCE_ID:
        _raise(
            "UNSUPPORTED_RAINFALL_SOURCE",
            "source_type",
            "only the built-in synthetic rainfall source is supported",
        )
    return SYNTHETIC_RAINFALL_SOURCE


# Compatibility spellings used by domain callers and older prototypes.
generate_design_storm = generate_design_rainfall
generate_random_pattern = generate_random_rainfall
generate_random_rainfall_pattern = generate_random_rainfall
copy_rainfall_scenario = copy_scenario
validate_rainfall_parameters = validate_rainfall_request


__all__ = [
    "ALLOWED_RETURN_PERIODS",
    "ALLOWED_STEP_SECONDS",
    "CUSTOM_ALGORITHM",
    "DEFAULT_DURATION_SECONDS",
    "DEFAULT_START_AT",
    "DEFAULT_STEP_SECONDS",
    "DESIGN_ALGORITHM",
    "MAX_DURATION_SECONDS",
    "RANDOM_ALGORITHM",
    "SYNTHETIC_RAINFALL_SOURCE",
    "SYNTHETIC_SOURCE_ID",
    "SYNTHETIC_SOURCE_VERSION",
    "CatchmentAllocation",
    "RainfallPattern",
    "RainfallRequest",
    "RainfallReturnPeriodYears",
    "RainfallSample",
    "RainfallScenario",
    "RainfallScenarioKind",
    "RainfallSeries",
    "RainfallSource",
    "RainfallValidationError",
    "RainfallValidationIssue",
    "SyntheticRainfallSource",
    "clone_scenario",
    "copy_rainfall_scenario",
    "copy_scenario",
    "create_scenario",
    "generate_custom_rainfall",
    "generate_design_rainfall",
    "generate_design_storm",
    "generate_random_pattern",
    "generate_random_rainfall",
    "generate_random_rainfall_pattern",
    "get_rainfall_source",
    "materialize_rainfall",
    "materialize_scenario",
    "validate_rainfall_parameters",
    "validate_rainfall_request",
    "validate_rainfall_series",
]
