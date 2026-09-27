"""Deterministic nowcast scheduling and observation assimilation.

This module contains the deterministic control-plane pieces used by the
hydraulic forecast worker.  It deliberately does not publish SSE events or
run a solver.  A scheduler creates one immutable request for a
``(network_id, scheduled_for)`` slot; an assimilation turn freezes the
calibrated-model reference and the latest usable sensor state into that
request.

The production worker can supply a database-backed repository and providers;
the default in-memory repository is useful for deterministic unit tests.  All
time reads go through an injectable clock, so tests never need to sleep.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Protocol, TypeVar, cast

UTC = timezone.utc
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_HASH_RE = r"^[0-9a-f]{64}$"
_FORMAL_TRUST_LEVELS = frozenset({"external_verified"})
_ALLOWED_MEASUREMENT_METRICS = frozenset({"depth_m", "water_depth_m", "flow_m3s"})
_DEPTH_UNITS = frozenset({"m", "metre", "meter", "metres", "meters"})
_DEPTH_MM_UNITS = frozenset(
    {"mm", "millimetre", "millimeter", "millimetres", "millimeters"}
)
_FLOW_UNITS = frozenset({"m3/s", "m3s", "m^3/s", "cms"})

T = TypeVar("T")


def _deep_freeze(value: Any) -> Any:
    """Return a recursively immutable JSON-like representation."""

    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(
            sorted(
                (_deep_freeze(item) for item in value),
                key=lambda item: json.dumps(_json_value(item), sort_keys=True),
            )
        )
    return value


def nowcast_slot_key(
    network_id: str | uuid.UUID,
    scheduled_for: datetime,
) -> tuple[str, str]:
    """Return the canonical unique key for a network/time slot."""

    return str(network_id), _iso(_as_utc(scheduled_for))


class NowcastError(RuntimeError):
    """Base class for deterministic nowcast failures."""

    code = "NOWCAST_ERROR"


class InvalidNowcastSchedule(NowcastError, ValueError):
    code = "INVALID_NOWCAST_SCHEDULE"


class SensorDataUnavailable(NowcastError):
    """Required sensor coverage is not available for a warm state."""

    code = "SENSOR_DATA_UNAVAILABLE"

    def __init__(
        self,
        message: str,
        *,
        missing_targets: Sequence[str] = (),
    ) -> None:
        self.missing_targets = tuple(sorted(set(missing_targets)))
        super().__init__(message)


class ModelNotCalibrated(NowcastError, ValueError):
    code = "MODEL_NOT_CALIBRATED"


class ForecastPolicyUnavailable(NowcastError):
    code = "FORECAST_POLICY_UNAVAILABLE"


class NowcastDuplicateConflict(NowcastError):
    """A slot already exists with a different immutable request fingerprint."""

    code = "NOWCAST_SLOT_CONFLICT"


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    # The production contract requires aware timestamps.  Treating a naive
    # value as UTC mirrors the existing durable-task helpers and keeps the
    # small pure service convenient for callers migrating from old code.
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    value = _as_utc(value)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    """Convert supported DTO values to stable JSON-compatible values."""

    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_json_value(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
        return items
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical nowcast payload cannot contain NaN/Inf")
        return value
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class Clock(Protocol):
    """Minimal clock boundary used by scheduler and assimilator."""

    def now(self) -> datetime: ...


class SystemClock:
    """UTC system clock used by production callers."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now()


class ManualClock:
    """Thread-safe virtual clock for tests and deterministic simulations."""

    def __init__(
        self,
        now: datetime | None = None,
        *,
        start: datetime | None = None,
        initial: datetime | None = None,
        initial_time: datetime | None = None,
    ) -> None:
        chosen = now if now is not None else start
        if chosen is None:
            chosen = initial
        if chosen is None:
            chosen = initial_time
        if chosen is None:
            raise ValueError("a virtual clock requires an initial datetime")
        self._now = _as_utc(chosen)
        self._lock = threading.RLock()

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def __call__(self) -> datetime:
        return self.now()

    def set(self, value: datetime) -> datetime:
        with self._lock:
            self._now = _as_utc(value)
            return self._now

    set_time = set

    def advance(
        self,
        value: timedelta | float | int | None = None,
        *,
        seconds: float | None = None,
        minutes: float | None = None,
    ) -> datetime:
        if seconds is not None or minutes is not None:
            if value is not None:
                raise ValueError("specify either value or seconds/minutes, not both")
            if seconds is not None and minutes is not None:
                raise ValueError("specify only one of seconds or minutes")
            amount = float(seconds if seconds is not None else (minutes or 0) * 60)
        elif value is None:
            raise ValueError("clock increment is required")
        elif isinstance(value, timedelta):
            amount = value.total_seconds()
        else:
            amount = float(value)
        if not math.isfinite(amount):
            raise ValueError("clock increment must be finite")
        with self._lock:
            self._now = self._now + timedelta(seconds=amount)
            return self._now

    def advance_minutes(self, minutes: float) -> datetime:
        return self.advance(minutes=minutes)


# Common names used by test harnesses and downstream workers.
VirtualClock = ManualClock
FakeClock = ManualClock
FrozenClock = ManualClock


def _coerce_clock(clock: Clock | Callable[[], datetime] | None) -> Clock:
    if clock is None:
        return SystemClock()
    if hasattr(clock, "now") and callable(clock.now):
        return cast(Clock, clock)
    if callable(clock):
        return CallableClock(cast(Callable[[], datetime], clock))
    raise TypeError("clock must provide now() or be a zero-argument callable")


class CallableClock:
    """Adapt a zero-argument callable to the :class:`Clock` protocol."""

    def __init__(self, source: Callable[[], datetime]) -> None:
        if not callable(source):
            raise TypeError("clock source must be callable")
        self._source = source

    def now(self) -> datetime:
        return _as_utc(self._source())


@dataclass(frozen=True, slots=True)
class NowcastConfig:
    """Immutable scheduling and forecast-grid configuration.

    The default is the frozen product configuration: a ten-minute cadence,
    a zero-to-two-hour horizon, five-minute points, and a three-minute
    publication deadline.  ``horizon_minutes=0`` is retained as a valid empty
    horizon for callers that only need a scheduling heartbeat.
    """

    interval_minutes: int = 10
    horizon_minutes: int = 120
    step_minutes: int = 5
    deadline_seconds: int = 180
    max_retries: int = 2
    retry_backoff_seconds: tuple[int, ...] = (5, 15)
    observation_lookback_seconds: int = 900
    require_sensor_coverage: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.interval_minutes, bool) or self.interval_minutes <= 0:
            raise ValueError("interval_minutes must be a positive integer")
        if (
            isinstance(self.horizon_minutes, bool)
            or not 0 <= self.horizon_minutes <= 120
        ):
            raise ValueError("horizon_minutes must be between 0 and 120")
        if self.step_minutes != 5:
            raise ValueError("step_minutes must be the frozen five-minute grid")
        if self.horizon_minutes % self.step_minutes != 0:
            raise ValueError("horizon_minutes must be divisible by step_minutes")
        if (
            isinstance(self.deadline_seconds, bool)
            or not 0 < self.deadline_seconds <= 180
        ):
            raise ValueError("deadline_seconds must be in the range 1..180")
        if isinstance(self.max_retries, bool) or not 0 <= self.max_retries <= 2:
            raise ValueError("max_retries must be between 0 and 2")
        backoff = tuple(int(item) for item in self.retry_backoff_seconds)
        if len(backoff) != self.max_retries:
            raise ValueError("one retry backoff value is required per retry")
        if any(item <= 0 for item in backoff):
            raise ValueError("retry backoff values must be positive")
        if any(left >= right for left, right in zip(backoff, backoff[1:])):
            raise ValueError("retry backoff values must be strictly increasing")
        if self.observation_lookback_seconds <= 0:
            raise ValueError("observation_lookback_seconds must be positive")
        object.__setattr__(self, "retry_backoff_seconds", backoff)

    @classmethod
    def from_settings(cls, settings: Any) -> "NowcastConfig":
        """Build a config from the existing pydantic Settings object."""

        return cls(
            interval_minutes=int(getattr(settings, "NOWCAST_INTERVAL_MINUTES", 10)),
            horizon_minutes=int(getattr(settings, "NOWCAST_HORIZON_MINUTES", 120)),
            step_minutes=int(getattr(settings, "NOWCAST_STEP_MINUTES", 5)),
            deadline_seconds=int(getattr(settings, "NOWCAST_DEADLINE_SECONDS", 180)),
            max_retries=int(getattr(settings, "NOWCAST_MAX_RETRIES", 2)),
            retry_backoff_seconds=tuple(
                getattr(settings, "NOWCAST_RETRY_BACKOFF_SECONDS", (5, 15))
            ),
        )

    @property
    def horizon_seconds(self) -> int:
        return self.horizon_minutes * 60

    @property
    def step_seconds(self) -> int:
        return self.step_minutes * 60

    @property
    def frame_count(self) -> int:
        """Number of points including both horizon endpoints."""

        return self.horizon_minutes // self.step_minutes + 1

    @property
    def interval_seconds(self) -> int:
        return self.interval_minutes * 60

    @property
    def deadline(self) -> int:
        return self.deadline_seconds

    @property
    def horizon_step_minutes(self) -> int:
        return self.step_minutes

    @property
    def forecast_horizon_minutes(self) -> int:
        return self.horizon_minutes

    @property
    def nowcast_interval_minutes(self) -> int:
        return self.interval_minutes

    @property
    def nowcast_horizon_minutes(self) -> int:
        return self.horizon_minutes

    @property
    def nowcast_step_minutes(self) -> int:
        return self.step_minutes

    def horizon(self, origin: datetime) -> tuple[datetime, ...]:
        origin = _as_utc(origin)
        return tuple(
            origin + timedelta(minutes=5 * index) for index in range(self.frame_count)
        )

    def horizon_points(self, origin: datetime) -> tuple[datetime, ...]:
        return self.horizon(origin)


# Compatibility aliases used by worker configuration code.
ForecastConfig = NowcastConfig
NowcastSchedulerConfig = NowcastConfig


def align_to_interval(
    value: datetime,
    interval_minutes: int = 10,
) -> datetime:
    """Align an aware timestamp to the UTC nowcast interval grid."""

    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    current = _as_utc(value)
    interval_seconds = interval_minutes * 60
    elapsed = (current - _EPOCH).total_seconds()
    return _EPOCH + timedelta(
        seconds=math.floor(elapsed / interval_seconds) * interval_seconds
    )


def build_horizon(
    origin: datetime,
    *,
    horizon_minutes: int = 120,
    step_minutes: int = 5,
) -> tuple[datetime, ...]:
    """Build an inclusive 0..horizon forecast horizon."""

    return NowcastConfig(
        horizon_minutes=horizon_minutes,
        step_minutes=step_minutes,
    ).horizon(origin)


@dataclass(frozen=True, slots=True, init=False)
class SensorReading:
    """One sensor sample normalized for nowcast initialization.

    ``trust_level`` and binding/auth flags are explicit because the frozen
    contract does not allow a payload's ``source`` string to upgrade trust.
    Trust is fail-closed: callers must explicitly provide a policy-resolved
    trust level, authentication/binding evidence, and a stable source event
    identity.  A typed object is not, by itself, proof that a sample came
    from a registered sensor.
    """

    target_id: str
    observed_at: datetime
    value: float
    metric: str = "water_depth_m"
    quality: str = "good"
    trust_level: str = "external_unverified"
    source_event_id: str | None = None
    unit: str | None = None
    target_type: str | None = None
    auth_verified: bool = False
    binding_verified: bool = False

    def __init__(
        self,
        target_id: str | None = None,
        observed_at: datetime | None = None,
        value: float | None = None,
        metric: str = "water_depth_m",
        quality: str = "good",
        trust_level: str = "external_unverified",
        source_event_id: str | None = None,
        unit: str | None = None,
        target_type: str | None = None,
        auth_verified: bool = False,
        binding_verified: bool = False,
        **aliases: Any,
    ) -> None:
        if target_id is None:
            target_id = aliases.pop("sensor_id", aliases.pop("device_id", None))
        if observed_at is None:
            observed_at = aliases.pop("timestamp", aliases.pop("time", None))
        if value is None:
            value = aliases.pop("value_m", aliases.pop("value_mm", None))
        if source_event_id is None:
            source_event_id = aliases.pop("event_id", None)
        if quality == "good" and "quality_flag" in aliases:
            quality = aliases.pop("quality_flag")
        if unit is None:
            unit = aliases.pop("measurement_unit", None)
        if target_type is None:
            target_type = aliases.pop("sensor_type", None)
        if aliases:
            unknown = ", ".join(sorted(aliases))
            raise TypeError(f"unsupported sensor reading fields: {unknown}")
        if not isinstance(target_id, str) or not target_id.strip():
            raise ValueError("target_id must be non-empty text")
        if not isinstance(observed_at, datetime):
            raise ValueError("observed_at must be an aware datetime")
        if value is None or isinstance(value, bool):
            raise ValueError("sensor value must be a finite number")
        numeric_value = float(value)
        if not math.isfinite(numeric_value):
            raise ValueError("sensor value must be finite")
        object.__setattr__(self, "target_id", target_id.strip())
        object.__setattr__(self, "observed_at", _as_utc(observed_at))
        object.__setattr__(self, "value", numeric_value)
        object.__setattr__(self, "metric", str(metric or "water_depth_m"))
        object.__setattr__(self, "quality", str(quality or "missing"))
        object.__setattr__(
            self, "trust_level", str(trust_level or "external_unverified")
        )
        object.__setattr__(
            self,
            "source_event_id",
            str(source_event_id) if source_event_id is not None else None,
        )
        object.__setattr__(self, "unit", str(unit) if unit is not None else None)
        object.__setattr__(
            self, "target_type", str(target_type) if target_type is not None else None
        )
        if not isinstance(auth_verified, bool) or not isinstance(
            binding_verified, bool
        ):
            raise ValueError("auth_verified and binding_verified must be boolean")
        object.__setattr__(self, "auth_verified", auth_verified)
        object.__setattr__(self, "binding_verified", binding_verified)

    @property
    def sensor_id(self) -> str:
        return self.target_id

    @property
    def timestamp(self) -> datetime:
        return self.observed_at

    @property
    def event_id(self) -> str | None:
        return self.source_event_id

    @property
    def is_formal(self) -> bool:
        return (
            self.quality == "good"
            and self.trust_level in _FORMAL_TRUST_LEVELS
            and self.auth_verified is True
            and self.binding_verified is True
            and isinstance(self.source_event_id, str)
            and bool(self.source_event_id.strip())
            and self.observed_at is not None
        )


ObservationSample = SensorReading
SensorObservation = SensorReading
Observation = SensorReading


@dataclass(frozen=True, slots=True)
class CalibratedModel:
    """Model reference passed to the nowcast request.

    It intentionally carries a separate model calibration identity from any
    probability calibration used later by alert publication.
    """

    model_id: str = "calibrated-model"
    model_version: str = "1"
    calibration_id: str | None = None
    model_calibration_id: str | None = None
    parameter_set_id: str | None = None
    parameter_set_sha256: str | None = None
    initial_state: Mapping[str, Any] = field(default_factory=dict)
    calibrated: bool = True
    network_id: str | None = None
    network_revision_id: str | None = None
    target_manifest_id: str | None = None
    target_manifest_sha256: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    def __post_init__(self) -> None:
        if not str(self.model_id).strip():
            raise ValueError("model_id must be non-empty")
        if not str(self.model_version).strip():
            raise ValueError("model_version must be non-empty")
        if (
            self.calibration_id is not None
            and self.model_calibration_id is not None
            and str(self.calibration_id) != str(self.model_calibration_id)
        ):
            raise ValueError("calibration_id and model_calibration_id disagree")
        object.__setattr__(self, "initial_state", dict(self.initial_state or {}))
        if self.calibration_id is None:
            object.__setattr__(self, "calibration_id", self.model_calibration_id)
        elif self.calibration_id is not None:
            object.__setattr__(self, "calibration_id", str(self.calibration_id))
        if self.model_calibration_id is None:
            object.__setattr__(self, "model_calibration_id", self.calibration_id)
        else:
            object.__setattr__(
                self, "model_calibration_id", str(self.model_calibration_id)
            )
        if self.parameter_set_id is not None:
            object.__setattr__(self, "parameter_set_id", str(self.parameter_set_id))
        if self.parameter_set_sha256 is not None:
            if re.fullmatch(_HASH_RE, str(self.parameter_set_sha256)) is None:
                raise ValueError(
                    "parameter_set_sha256 must be a lowercase SHA-256 digest"
                )
            object.__setattr__(
                self, "parameter_set_sha256", str(self.parameter_set_sha256)
            )
        if (
            self.target_manifest_sha256 is not None
            and re.fullmatch(_HASH_RE, str(self.target_manifest_sha256)) is None
        ):
            raise ValueError(
                "target_manifest_sha256 must be a lowercase SHA-256 digest"
            )
        if self.valid_from is not None:
            object.__setattr__(self, "valid_from", _as_utc(self.valid_from))
        if self.valid_until is not None:
            object.__setattr__(self, "valid_until", _as_utc(self.valid_until))
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError("calibrated model validity interval is empty")

    @property
    def is_calibrated(self) -> bool:
        return bool(self.calibrated)

    def identity(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "calibration_id": self.calibration_id,
            "parameter_set_id": self.parameter_set_id,
            "parameter_set_sha256": self.parameter_set_sha256,
            "initial_state_content_sha256": _sha256(self.initial_state),
            "calibrated": self.calibrated,
        }


ModelReference = CalibratedModel


@dataclass(frozen=True, slots=True)
class InitialState:
    """Immutable state assembled from the model and recent verified sensors."""

    kind: str = "dry"
    observed_as_of: datetime | None = None
    node_states: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    pipe_states: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    pond_states: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    source_event_ids: tuple[str, ...] = ()
    missing_targets: tuple[str, ...] = ()
    rejected_observations: tuple[str, ...] = ()
    model_id: str | None = None
    model_version: str | None = None
    model_as_of: datetime | None = None
    content_sha256: str | None = None

    def __post_init__(self) -> None:
        kind = str(self.kind)
        if kind not in {"dry", "warm_state", "warm"}:
            raise ValueError("initial state kind must be dry or warm_state")
        if kind == "warm":
            kind = "warm_state"
        object.__setattr__(self, "kind", kind)
        if self.observed_as_of is not None:
            object.__setattr__(self, "observed_as_of", _as_utc(self.observed_as_of))
        if self.model_as_of is not None:
            object.__setattr__(self, "model_as_of", _as_utc(self.model_as_of))
        object.__setattr__(
            self, "node_states", _deep_freeze(dict(self.node_states or {}))
        )
        object.__setattr__(
            self, "pipe_states", _deep_freeze(dict(self.pipe_states or {}))
        )
        object.__setattr__(
            self, "pond_states", _deep_freeze(dict(self.pond_states or {}))
        )
        object.__setattr__(self, "source_event_ids", tuple(self.source_event_ids or ()))
        object.__setattr__(
            self, "missing_targets", tuple(sorted(set(self.missing_targets or ())))
        )
        object.__setattr__(
            self,
            "rejected_observations",
            tuple(sorted(set(self.rejected_observations or ()))),
        )
        if kind == "dry":
            for states in (self.node_states, self.pipe_states, self.pond_states):
                if _state_has_nonzero_model_value(states):
                    raise ValueError("dry initial state must contain only zero values")
        calculated_hash = self._calculate_hash()
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", calculated_hash)
        elif (
            not isinstance(self.content_sha256, str)
            or re.fullmatch(_HASH_RE, self.content_sha256) is None
        ):
            raise ValueError("content_sha256 must be a lowercase 64-character digest")
        elif self.content_sha256 != calculated_hash:
            raise ValueError(
                "content_sha256 does not match the immutable state payload"
            )

    def _calculate_hash(self) -> str:
        return _sha256(
            {
                "schema": "hydraulic-initial-state-v1",
                "kind": self.kind,
                "observed_as_of": self.observed_as_of,
                "node_states": self.node_states,
                "pipe_states": self.pipe_states,
                "pond_states": self.pond_states,
                "source_event_ids": self.source_event_ids,
                "missing_targets": self.missing_targets,
                "rejected_observations": self.rejected_observations,
                "model_id": self.model_id,
                "model_version": self.model_version,
                "model_as_of": self.model_as_of,
            }
        )

    @property
    def state_kind(self) -> str:
        return self.kind

    @property
    def initial_state_content_sha256(self) -> str:
        return cast(str, self.content_sha256)

    @property
    def is_complete(self) -> bool:
        return not self.missing_targets

    @property
    def complete(self) -> bool:
        return self.is_complete

    @property
    def missing_sensor_targets(self) -> tuple[str, ...]:
        return self.missing_targets

    @property
    def states(self) -> dict[str, Mapping[str, Any]]:
        result: dict[str, Mapping[str, Any]] = {}
        result.update(self.node_states)
        result.update(self.pipe_states)
        result.update(self.pond_states)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "hydraulic-initial-state-v1",
            "kind": self.kind,
            "observed_as_of": (
                _iso(self.observed_as_of) if self.observed_as_of is not None else None
            ),
            "node_states": _json_value(self.node_states),
            "pipe_states": _json_value(self.pipe_states),
            "pond_states": _json_value(self.pond_states),
            "source_event_ids": list(self.source_event_ids),
            "missing_targets": list(self.missing_targets),
            "rejected_observations": list(self.rejected_observations),
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_as_of": (
                _iso(self.model_as_of) if self.model_as_of is not None else None
            ),
            "content_sha256": self.content_sha256,
        }

    def __getitem__(self, target_id: str) -> Mapping[str, Any]:
        for states in (self.node_states, self.pipe_states, self.pond_states):
            if target_id in states:
                return states[target_id]
        raise KeyError(target_id)

    def get(
        self,
        target_id: str,
        default: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        try:
            return self[target_id]
        except KeyError:
            return default

    def items(self) -> Any:
        return self.states.items()


InitialStateSnapshot = InitialState


def _looks_like_reading_collection(value: Any) -> bool:
    if isinstance(value, (str, bytes, Mapping)) or value is None:
        return False
    try:
        values = list(value)
    except TypeError:
        return False
    return bool(values) and all(
        isinstance(item, (SensorReading, Mapping)) or hasattr(item, "observed_at")
        for item in values
    )


def _looks_like_model(value: Any) -> bool:
    if isinstance(value, CalibratedModel):
        return True
    if isinstance(value, Mapping):
        return any(
            key in value
            for key in ("model_id", "model_version", "calibration_id", "initial_state")
        )
    return any(
        hasattr(value, attribute)
        for attribute in ("model_id", "model_version", "initial_state_for")
    )


def _get(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return value[name]
        return default
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _target_id(value: Any) -> str | None:
    candidate = _get(
        value,
        "target_id",
        "target_stable_key",
        "stable_key",
        "id",
        "device_id",
    )
    return str(candidate) if candidate is not None and str(candidate).strip() else None


def _coerce_reading(value: Any) -> SensorReading:
    if isinstance(value, SensorReading):
        return value
    if isinstance(value, Mapping):
        target = _get(
            value,
            "target_id",
            "target_stable_key",
            "stable_key",
            "device_id",
        )
        observed = _get(value, "observed_at", "timestamp", "time")
        metric = str(_get(value, "metric", "field", default="water_depth_m"))
        if "metric" not in value and "field" not in value and "value_mm" in value:
            metric = "water_level_mm"
        raw = _get(value, "value", "value_m", "value_mm")
        if raw is None and metric in {"water_level_mm", "water_level"}:
            raw = _get(value, "value_mm", "water_level_mm")
        quality = _get(value, "quality", "quality_flag", default="good")
        trust = _get(value, "trust_level", default="external_unverified")
        source_label = _get(value, "source", "source_system", default=None)
        if source_label is not None and str(source_label).strip().lower() in {
            "mock",
            "synthetic",
            "fixture",
            "demo",
            "unverified",
        }:
            # Source labels are diagnostic only and can never upgrade trust.
            trust = "external_unverified"
        # ``external_unified`` is a typo guard for accidental hand-written
        # payloads; never silently turn it into verified data.
        if trust == "external_unified":
            trust = "external_unverified"
        source_event = _get(
            value,
            "source_event_id",
            "event_id",
            "ingest_event_id",
            "rainfall_event_id",
        )
        target_type = _get(value, "target_type", "sensor_type", default=None)
        unit = _get(value, "unit", "measurement_unit", default=None)
        auth = _get(value, "auth_verified", default=False)
        binding = _get(value, "binding_verified", default=False)
        return SensorReading(
            target_id=target,
            observed_at=observed,
            value=raw,
            metric=metric,
            quality=quality,
            trust_level=trust,
            source_event_id=source_event,
            unit=unit,
            target_type=target_type,
            auth_verified=auth,
            binding_verified=binding,
        )

    # calibration.ObservationPoint and SQLAlchemy samples are accepted without
    # importing their modules, which keeps this service usable in small tests.
    target = _get(value, "target_id", "target_stable_key", "stable_key", "device_id")
    observed = _get(value, "observed_at", "timestamp", "time")
    metric = str(_get(value, "metric", "field", default="water_depth_m"))
    raw = _get(value, "value", "value_m", "value_mm")
    if raw is None:
        raw = _get(value, "value_m3s", "flow_m3s")
        metric = "flow_m3s"
    if raw is None:
        raw = _get(value, "water_level_mm")
        metric = "water_level_mm"
    quality = _get(value, "quality", "quality_flag", default="good")
    trust = _get(value, "trust_level", default="external_unverified")
    auth_verified = _get(value, "auth_verified", default=None)
    binding_verified = _get(value, "binding_verified", default=None)
    if auth_verified is None:
        auth_verified = trust == "external_verified"
    if binding_verified is None:
        binding_verified = trust == "external_verified"
    source_event = _get(
        value,
        "source_event_id",
        "event_id",
        "ingest_event_id",
        "rainfall_event_id",
    )
    return SensorReading(
        target_id=target,
        observed_at=observed,
        value=raw,
        metric=metric,
        quality=quality,
        trust_level=trust,
        source_event_id=source_event,
        unit=_get(value, "unit", "measurement_unit", default=None),
        target_type=_get(value, "target_type", "sensor_type", default=None),
        auth_verified=auth_verified,
        binding_verified=binding_verified,
    )


def _canonical_metric(metric: str, unit: str | None = None) -> tuple[str, float]:
    """Validate and canonicalize a closed measurement metric/unit pair."""

    normalized = str(metric or "").lower().strip().replace("-", "_")
    unit_name = str(unit or "").lower().strip()
    depth_aliases = {"depth", "depth_m", "water_depth", "water_depth_m"}
    depth_mm_aliases = {"water_level_mm", "water_level", "water_depth_mm"}
    flow_aliases = {"flow", "flow_m3s", "discharge", "discharge_m3s"}
    if normalized in depth_aliases:
        if unit_name and unit_name not in _DEPTH_UNITS:
            raise ValueError("water depth unit must be metres")
        return "depth_m", 1.0
    if normalized in depth_mm_aliases:
        if unit_name and unit_name not in _DEPTH_MM_UNITS:
            raise ValueError("millimetre depth requires millimetre unit")
        return "depth_m", 0.001
    if normalized in flow_aliases:
        if unit_name and unit_name not in _FLOW_UNITS:
            raise ValueError("flow unit must be cubic metres per second")
        return "flow_m3s", 1.0
    raise ValueError("observation metric is outside the allowlist")


def _iter_target_values(container: Any) -> Iterable[tuple[str, str]]:
    if container is None:
        return ()
    result: list[tuple[str, str]] = []
    if isinstance(container, Mapping):
        entries: Iterable[tuple[Any, Any]] = container.items()
    else:
        try:
            entries = ((None, item) for item in container)
        except TypeError:
            return ()
    for key, value in entries:
        if key is not None:
            key_text = str(key).strip()
        elif isinstance(value, (str, int, uuid.UUID)):
            key_text = str(value).strip()
        else:
            key_text = _target_id(value) or ""
        if not key_text:
            continue
        nested_type = _get(value, "target_type", "kind", default=None)
        if nested_type in {"node", "pipe", "pipe_segment", "pond", "pond_cell"}:
            type_value = "pipe" if nested_type == "pipe_segment" else nested_type
        else:
            type_value = "node"
        result.append((key_text, type_value))
    return result


def _network_targets(network: Any) -> dict[str, str]:
    targets: dict[str, str] = {}
    if network is None:
        return targets
    if isinstance(network, str):
        return targets
    if isinstance(network, (list, tuple, set, frozenset)):
        for item in network:
            target = _target_id(item)
            if target:
                target_type = _get(item, "target_type", default="node")
                targets[target] = (
                    "pipe" if target_type == "pipe_segment" else str(target_type)
                )
        return targets

    # A mapping can contain one or more target collections.  Scalar IDs are
    # accepted as node targets, which is convenient for small test networks.
    if isinstance(network, Mapping):
        found = False
        for key, target_type in (
            ("node_ids", "node"),
            ("pipe_ids", "pipe"),
            ("pipe_segment_ids", "pipe"),
            ("pond_ids", "pond"),
            ("pond_cell_ids", "pond"),
        ):
            values = network.get(key)
            if values is not None:
                found = True
                for entry in _iter_target_values(values):
                    targets.setdefault(entry[0], target_type)
        for key, target_type in (
            ("nodes", "node"),
            ("pipes", "pipe"),
            ("pipe_segments", "pipe"),
            ("pond_cells", "pond"),
        ):
            values = network.get(key)
            if values is not None:
                found = True
                for entry in _iter_target_values(values):
                    targets.setdefault(entry[0], target_type)
        explicit = network.get("target_ids")
        if explicit is not None:
            found = True
            for entry in _iter_target_values(explicit):
                targets.setdefault(entry[0], "node")
        if not found:
            target = _target_id(network)
            if target:
                targets[target] = "node"
        return targets

    for key, target_type in (
        ("node_ids", "node"),
        ("pipe_ids", "pipe"),
        ("pipe_segment_ids", "pipe"),
        ("pond_ids", "pond"),
        ("pond_cell_ids", "pond"),
    ):
        values = _get(network, key, default=None)
        if values is not None:
            for entry in _iter_target_values(values):
                targets.setdefault(entry[0], target_type)
    for key, target_type in (
        ("nodes", "node"),
        ("pipes", "pipe"),
        ("pipe_segments", "pipe"),
        ("pond_cells", "pond"),
    ):
        values = _get(network, key, default=None)
        if values is not None:
            for entry in _iter_target_values(values):
                targets.setdefault(entry[0], target_type)
    explicit = _get(network, "target_ids", default=None)
    if explicit is not None:
        for entry in _iter_target_values(explicit):
            targets.setdefault(entry[0], "node")
    return targets


def _model_identity(model: Any) -> dict[str, Any]:
    if model is None:
        return {"model": None}
    if isinstance(model, CalibratedModel):
        return model.identity()
    if isinstance(model, Mapping):
        keys = (
            "model_id",
            "model_version",
            "calibration_id",
            "parameter_set_id",
            "parameter_set_sha256",
            "initial_state",
            "calibrated",
        )
        return {key: model.get(key) for key in keys if key in model}
    result: dict[str, Any] = {"model_type": type(model).__name__}
    for key in (
        "model_id",
        "model_version",
        "calibration_id",
        "parameter_set_id",
        "parameter_set_sha256",
    ):
        value = _get(model, key, default=None)
        if value is not None:
            result[key] = value
    return result


def _model_initial_state(
    model: Any,
    network: Any,
    target_id: str,
    as_of: datetime,
) -> Any:
    if model is None:
        return None
    if hasattr(model, "initial_state_for") and callable(model.initial_state_for):
        try:
            return model.initial_state_for(network, as_of, target_id)
        except TypeError:
            try:
                return model.initial_state_for(network, target_id, as_of)
            except TypeError:
                return model.initial_state_for(target_id)
    if isinstance(model, CalibratedModel):
        state: Any = model.initial_state
    elif isinstance(model, Mapping):
        state = model.get("initial_state", model.get("state"))
    else:
        state = _get(model, "initial_state", "baseline_state", "state", default=None)
    if callable(state):
        try:
            state = state(network, as_of, target_id)
        except TypeError:
            state = state(target_id)
    if isinstance(state, Mapping):
        for key in (target_id, str(target_id)):
            if key in state:
                return state[key]
        for key in ("node_states", "pipe_states", "pond_states", "targets"):
            nested = state.get(key)
            if isinstance(nested, Mapping) and target_id in nested:
                return nested[target_id]
        # A mapping with one value that is itself a state may be a target map;
        # do not mistake model metadata for a state.
        if not any(
            key in state
            for key in ("model_id", "model_version", "calibration_id", "parameters")
        ):
            return state
    elif isinstance(state, Sequence) and not isinstance(state, (str, bytes)):
        return state
    return None


def _state_value(reading: SensorReading) -> tuple[str, float]:
    key, scale = _canonical_metric(reading.metric, reading.unit)
    return key, reading.value * scale


def _default_state(target_type: str) -> dict[str, Any]:
    if target_type == "pipe":
        return {"flow_m3s": 0.0, "fill_ratio": 0.0, "source": "model"}
    if target_type == "pond":
        return {"depth_m": 0.0, "volume_m3": 0.0, "source": "model"}
    return {"depth_m": 0.0, "hydraulic_head_m": None, "source": "model"}


def _state_has_nonzero_model_value(states: Mapping[str, Mapping[str, Any]]) -> bool:
    for value in states.values():
        for key, item in value.items():
            if key in {
                "source",
                "observed_at",
                "source_event_id",
                "metric",
                "unit",
                "quality",
                "trust_level",
            }:
                continue
            if (
                isinstance(item, (int, float))
                and not isinstance(item, bool)
                and float(item) != 0.0
            ):
                return True
    return False


def _require_calibrated_model(
    model: Any, *, network_id: str | None = None, as_of: datetime | None = None
) -> Any:
    """Reject absent, unknown, or explicitly uncalibrated model references."""

    if model is None:
        raise ModelNotCalibrated("nowcast requires a calibrated model reference")
    # The three branches below disagree on type by design and the guard is a
    # strict ``is True`` identity check, so the flag stays Any.
    calibrated: Any
    if isinstance(model, CalibratedModel):
        calibrated = model.is_calibrated
        calibration_id = model.calibration_id or model.model_calibration_id
        parameter_id = model.parameter_set_id
        parameter_hash = model.parameter_set_sha256
    elif isinstance(model, Mapping):
        calibrated = model.get("calibrated")
        calibration_id = model.get("calibration_id", model.get("model_calibration_id"))
        parameter_id = model.get("parameter_set_id")
        parameter_hash = model.get("parameter_set_sha256")
    else:
        calibrated = _get(model, "calibrated", "is_calibrated", default=None)
        calibration_id = _get(
            model, "calibration_id", "model_calibration_id", default=None
        )
        parameter_id = _get(model, "parameter_set_id", default=None)
        parameter_hash = _get(model, "parameter_set_sha256", default=None)
    if calibrated is not True:
        raise ModelNotCalibrated("nowcast requires calibrated=true")
    model_id = (
        model.get("model_id")
        if isinstance(model, Mapping)
        else getattr(model, "model_id", None)
    )
    model_version = (
        model.get("model_version")
        if isinstance(model, Mapping)
        else getattr(model, "model_version", None)
    )
    if (
        model_id is None
        or not str(model_id).strip()
        or model_version is None
        or not str(model_version).strip()
    ):
        raise ModelNotCalibrated("calibrated model must carry model identity")
    if calibration_id is None or not str(calibration_id).strip():
        raise ModelNotCalibrated("calibrated model must carry a calibration identity")
    if parameter_id is None or not str(parameter_id).strip():
        raise ModelNotCalibrated("calibrated model must carry a parameter-set identity")
    if parameter_hash is None or re.fullmatch(_HASH_RE, str(parameter_hash)) is None:
        raise ModelNotCalibrated("calibrated model must carry a parameter-set SHA-256")
    if isinstance(model, CalibratedModel):
        if (
            model.network_id is not None
            and network_id is not None
            and str(model.network_id) != str(network_id)
        ):
            raise ModelNotCalibrated("calibrated model is bound to another network")
        if as_of is not None:
            reference = _as_utc(as_of)
            if model.valid_from is not None and reference < model.valid_from:
                raise ModelNotCalibrated("calibrated model is not yet valid")
            if model.valid_until is not None and reference >= model.valid_until:
                raise ModelNotCalibrated("calibrated model has expired")
    return model


def _validate_baseline_state(state: Any, target_type: str) -> None:
    if not isinstance(state, Mapping):
        return
    for key, value in state.items():
        if key in {
            "source",
            "observed_at",
            "source_event_id",
            "metric",
            "unit",
            "quality",
            "trust_level",
        }:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise ModelNotCalibrated(
                    f"calibrated baseline contains an invalid {key} value"
                )
        if key in {"depth_m", "flow_m3s", "volume_m3"} and value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ModelNotCalibrated(f"calibrated baseline {key} must be numeric")
            if float(value) < 0.0:
                raise ModelNotCalibrated("calibrated baseline cannot be negative")
        if key == "fill_ratio" and value is not None and not 0.0 <= float(value) <= 1.0:
            raise ModelNotCalibrated("calibrated fill_ratio must be between 0 and 1")
    if target_type in {"pipe", "pipe_segment"} and "depth_m" in state:
        raise ModelNotCalibrated("depth_m is not valid for a pipe target")


class InitialStateAssimilator:
    """Select recent verified observations and freeze a model-based warm state."""

    def __init__(
        self,
        *,
        clock: Clock | Callable[[], datetime] | None = None,
        observation_lookback_seconds: int = 900,
        require_verified: bool = True,
        require_complete: bool = False,
        model: Any | None = None,
    ) -> None:
        if observation_lookback_seconds <= 0:
            raise ValueError("observation_lookback_seconds must be positive")
        self.clock = _coerce_clock(clock)
        self.observation_lookback_seconds = int(observation_lookback_seconds)
        self.require_verified = bool(require_verified)
        self.require_complete = bool(require_complete)
        self.model = model

    def latest_observations(
        self,
        observations: Iterable[Any],
        *,
        as_of: datetime | None = None,
    ) -> tuple[SensorReading, ...]:
        current = _as_utc(as_of or self.clock.now())
        cutoff = current - timedelta(seconds=self.observation_lookback_seconds)
        selected: dict[str, SensorReading] = {}
        rejected: list[str] = []
        for raw in observations or ():
            try:
                reading = _coerce_reading(raw)
                metric_key, _scale = _canonical_metric(reading.metric, reading.unit)
                if metric_key not in _ALLOWED_MEASUREMENT_METRICS:
                    raise ValueError("metric is outside the allowlist")
                if (
                    reading.target_type in {"pipe", "pipe_segment"}
                    and metric_key != "flow_m3s"
                ):
                    raise ValueError("pipe target requires flow_m3s")
                if (
                    reading.target_type in {"node", "pond", "pond_cell"}
                    and metric_key != "depth_m"
                ):
                    raise ValueError("node/pond target requires water_depth_m")
                if reading.value < 0.0:
                    raise ValueError("sensor value must be non-negative")
            except (TypeError, ValueError):
                target = _target_id(raw) or "unknown"
                rejected.append(f"invalid:{target}")
                continue
            if reading.observed_at > current:
                rejected.append(f"future:{reading.target_id}")
                continue
            if reading.observed_at < cutoff:
                rejected.append(f"stale:{reading.target_id}")
                continue
            if reading.quality != "good":
                rejected.append(f"quality:{reading.target_id}")
                continue
            if self.require_verified and not reading.is_formal:
                rejected.append(f"unverified:{reading.target_id}")
                continue
            previous = selected.get(reading.target_id)
            if previous is None or (
                reading.observed_at,
                reading.source_event_id or "",
            ) > (previous.observed_at, previous.source_event_id or ""):
                selected[reading.target_id] = reading
        self._last_rejected = tuple(sorted(set(rejected)))
        return tuple(
            selected[key]
            for key in sorted(
                selected,
                key=lambda key: (selected[key].observed_at, key),
            )
        )

    def assimilate(
        self,
        network: Any = None,
        observations: Iterable[Any] | None = (),
        *,
        as_of: datetime | None = None,
        model: Any | None = None,
        target_ids: Iterable[str] | None = None,
    ) -> InitialState:
        """Return an immutable state frozen at ``as_of``.

        ``network`` may be a network DTO, a sequence of target IDs, or be
        omitted when the observations themselves define the target set.  For
        compatibility with older callers, a sequence of readings passed as
        the first positional argument is accepted as ``observations``.
        """

        if model is None and _looks_like_reading_collection(network):
            observations = cast(Iterable[Any], network)
            network = None
        elif model is None and _looks_like_model(observations):
            model = observations
            observations = ()
        current = _as_utc(as_of or self.clock.now())
        selected_model = model if model is not None else self.model
        _require_calibrated_model(
            selected_model,
            network_id=str(network) if isinstance(network, str) else None,
            as_of=current,
        )

        raw_observations = tuple(observations or ())
        selected = self.latest_observations(raw_observations, as_of=current)
        selected_by_target = {item.target_id: item for item in selected}

        targets = _network_targets(network)
        if target_ids is not None:
            targets = {
                str(target): targets.get(str(target), "node") for target in target_ids
            }
        elif not targets:
            targets = {
                item.target_id: (item.target_type or "node") for item in selected
            }
        if not targets and isinstance(selected_model, Mapping):
            model_targets = selected_model.get("target_ids")
            targets = {str(target): "node" for target in (model_targets or ())}
        if not targets:
            model_state = _get(
                selected_model,
                "initial_state",
                "baseline_state",
                "state",
                default=None,
            )
            if isinstance(model_state, Mapping):
                for target in model_state:
                    if target in {"node_states", "pipe_states", "pond_states"}:
                        continue
                    targets[str(target)] = "node"

        node_states: dict[str, Mapping[str, Any]] = {}
        pipe_states: dict[str, Mapping[str, Any]] = {}
        pond_states: dict[str, Mapping[str, Any]] = {}
        missing: list[str] = []
        for target_id in sorted(targets):
            target_type = targets[target_id]
            reading = selected_by_target.get(target_id)
            if reading is not None:
                metric_key, _ = _canonical_metric(reading.metric, reading.unit)
                if target_type in {"pipe", "pipe_segment"} and metric_key != "flow_m3s":
                    raise ValueError("formal pipe reading must use flow_m3s")
                if target_type != "pipe" and metric_key != "depth_m":
                    raise ValueError("formal node/pond reading must use water_depth_m")
            baseline = _model_initial_state(selected_model, network, target_id, current)
            if isinstance(baseline, Mapping):
                _validate_baseline_state(baseline, target_type)
                state: dict[str, Any] = dict(baseline)
            elif isinstance(baseline, (int, float)) and not isinstance(baseline, bool):
                state = {"value": float(baseline)}
            else:
                state = _default_state(target_type)
            if reading is None:
                missing.append(target_id)
                state.setdefault("source", "model")
            else:
                metric_key, value = _state_value(reading)
                state[metric_key] = value
                state.update(
                    {
                        "source": "sensor",
                        "observed_at": reading.observed_at,
                        "source_event_id": reading.source_event_id,
                        "metric": reading.metric,
                        "unit": reading.unit,
                        "quality": reading.quality,
                        "trust_level": reading.trust_level,
                    }
                )
            if target_type == "pipe":
                pipe_states[target_id] = state
            elif target_type in {"pond", "pond_cell"}:
                pond_states[target_id] = state
            else:
                node_states[target_id] = state

        source_ids = tuple(
            item.source_event_id
            for item in selected
            if item.source_event_id is not None
        )
        has_model_baseline = any(
            _state_has_nonzero_model_value(states)
            for states in (node_states, pipe_states, pond_states)
        )
        # ``state`` already names a per-target dict earlier in this function;
        # the assembled InitialState gets its own name.
        initial_state = InitialState(
            kind="warm_state" if selected or has_model_baseline else "dry",
            observed_as_of=max((item.observed_at for item in selected), default=None),
            node_states=node_states,
            pipe_states=pipe_states,
            pond_states=pond_states,
            source_event_ids=source_ids,
            missing_targets=tuple(missing),
            rejected_observations=getattr(self, "_last_rejected", ()),
            model_id=_get(selected_model, "model_id", default=None),
            model_version=_get(selected_model, "model_version", default=None),
            model_as_of=current,
        )
        if self.require_complete and not initial_state.is_complete:
            raise SensorDataUnavailable(
                "verified sensor coverage is incomplete",
                missing_targets=initial_state.missing_targets,
            )
        return initial_state

    def __call__(self, *args: Any, **kwargs: Any) -> InitialState:
        return self.assimilate(*args, **kwargs)


SensorAssimilator = InitialStateAssimilator
StateAssimilator = InitialStateAssimilator


@dataclass(frozen=True, slots=True)
class ScheduledNowcast:
    """Immutable scheduler output passed to a nowcast worker."""

    task_id: uuid.UUID
    network_id: str
    scheduled_for: datetime
    forecast_origin_at: datetime
    deadline_at: datetime
    config: NowcastConfig
    model: Any
    initial_state: InitialState
    request_fingerprint: str
    created_at: datetime
    issued_at: datetime | None = None
    network_revision_id: str | None = None
    target_manifest_id: str | None = None
    target_manifest_sha256: str | None = None
    rainfall_scenario_id: str | None = None
    solver_id: str | None = None
    solver_version: str | None = None
    flood_forecast_probability_calibration_id: str | None = None
    flood_forecast_probability_sha256: str | None = None
    pipe_surcharge_probability_calibration_id: str | None = None
    pipe_surcharge_probability_sha256: str | None = None
    flood_forecast_rule_id: str | None = None
    flood_forecast_rule_sha256: str | None = None
    pipe_surcharge_rule_id: str | None = None
    pipe_surcharge_rule_sha256: str | None = None
    forecast_policy_config_sha256: str | None = None
    config_hash: str | None = None

    @property
    def id(self) -> uuid.UUID:
        return self.task_id

    @property
    def run_id(self) -> uuid.UUID:
        return self.task_id

    @property
    def task(self) -> "ScheduledNowcast":
        return self

    @property
    def horizon(self) -> tuple[datetime, ...]:
        return self.config.horizon(self.forecast_origin_at)

    @property
    def horizon_points(self) -> tuple[datetime, ...]:
        return self.horizon

    @property
    def forecast_horizon(self) -> tuple[datetime, ...]:
        return self.horizon

    @property
    def initial_state_content_sha256(self) -> str:
        return self.initial_state.initial_state_content_sha256

    @property
    def model_calibration_id(self) -> str | None:
        return cast(
            "str | None",
            _get(self.model, "calibration_id", "model_calibration_id", default=None),
        )

    def lead_time_minutes(
        self, expected_at: datetime, *, issued_at: datetime | None = None
    ) -> int:
        """Compute lead time from the immutable final issue instant.

        A pre-issue scheduler request has no business ``issued_at`` yet.  The
        optional argument is therefore explicit; falling back to
        ``created_at`` is retained only for legacy callers and is never used
        by the finalizer.
        """

        issue = _as_utc(issued_at or self.issued_at or self.created_at)
        return max(0, int((_as_utc(expected_at) - issue).total_seconds() // 60))

    def with_issued_at(self, issued_at: datetime) -> "ScheduledNowcast":
        """Freeze the final business issue instant without changing request identity."""

        return replace(self, issued_at=_as_utc(issued_at))

    @property
    def lineage_complete(self) -> bool:
        required = (
            self.network_revision_id,
            self.target_manifest_id,
            self.target_manifest_sha256,
            self.rainfall_scenario_id,
            self.solver_id,
            self.solver_version,
            self.flood_forecast_probability_calibration_id,
            self.flood_forecast_probability_sha256,
            self.pipe_surcharge_probability_calibration_id,
            self.pipe_surcharge_probability_sha256,
            self.flood_forecast_rule_id,
            self.flood_forecast_rule_sha256,
            self.pipe_surcharge_rule_id,
            self.pipe_surcharge_rule_sha256,
            self.config_hash,
        )
        return all(value is not None and str(value).strip() for value in required)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "hydraulic-nowcast-request-v1",
            "task_id": str(self.task_id),
            "network_id": self.network_id,
            "scheduled_for": _iso(self.scheduled_for),
            "forecast_origin_at": _iso(self.forecast_origin_at),
            "deadline_at": _iso(self.deadline_at),
            "interval_minutes": self.config.interval_minutes,
            "horizon_minutes": self.config.horizon_minutes,
            "step_minutes": self.config.step_minutes,
            "model": _model_identity(self.model),
            "initial_state": self.initial_state.to_dict(),
            "request_fingerprint": self.request_fingerprint,
            "created_at": _iso(self.created_at),
            "issued_at": _iso(self.issued_at) if self.issued_at is not None else None,
            "network_revision_id": self.network_revision_id,
            "target_manifest_id": self.target_manifest_id,
            "target_manifest_sha256": self.target_manifest_sha256,
            "rainfall_scenario_id": self.rainfall_scenario_id,
            "solver_id": self.solver_id,
            "solver_version": self.solver_version,
            "flood_forecast_probability_calibration_id": (
                self.flood_forecast_probability_calibration_id
            ),
            "flood_forecast_probability_sha256": self.flood_forecast_probability_sha256,
            "pipe_surcharge_probability_calibration_id": (
                self.pipe_surcharge_probability_calibration_id
            ),
            "pipe_surcharge_probability_sha256": self.pipe_surcharge_probability_sha256,
            "flood_forecast_rule_id": self.flood_forecast_rule_id,
            "flood_forecast_rule_sha256": self.flood_forecast_rule_sha256,
            "pipe_surcharge_rule_id": self.pipe_surcharge_rule_id,
            "pipe_surcharge_rule_sha256": self.pipe_surcharge_rule_sha256,
            "forecast_policy_config_sha256": self.forecast_policy_config_sha256,
            "config_hash": self.config_hash,
        }


NowcastTask = ScheduledNowcast
NowcastCycle = ScheduledNowcast
ForecastCycle = ScheduledNowcast
NowcastRunRequest = ScheduledNowcast


@dataclass(frozen=True, slots=True, eq=False)
class ScheduleOutcome:
    """Result of a scheduler trigger, including duplicate information."""

    run: ScheduledNowcast
    created: bool

    @property
    def duplicate(self) -> bool:
        return not self.created

    @property
    def task_id(self) -> uuid.UUID:
        return self.run.task_id

    @property
    def id(self) -> uuid.UUID:
        return self.run.task_id

    def __getattr__(self, name: str) -> Any:
        # Keep the outcome pleasant to use in small worker adapters while
        # retaining an explicit ``run`` object for persistence code.
        return getattr(self.run, name)

    def __await__(self) -> Any:
        async def identity() -> "ScheduleOutcome":
            return self

        return identity().__await__()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ScheduleOutcome):
            return self.run == other.run and self.created == other.created
        if isinstance(other, ScheduledNowcast):
            return self.run == other
        return NotImplemented


NowcastSchedule = ScheduleOutcome
NowcastResult = ScheduleOutcome


class NowcastRepository(Protocol):
    """Minimal repository boundary used by the scheduler.

    ``get`` and ``list`` belong to the boundary because the scheduler calls
    them directly (``is_due``, ``get_run``, ``list_runs``).  Leaving them
    undeclared is what forced ``type: ignore[call-arg]`` at those call sites.
    """

    def get_or_create(
        self,
        network_id: str,
        scheduled_for: datetime,
        factory: Callable[[], ScheduledNowcast],
    ) -> tuple[ScheduledNowcast, bool]: ...

    def get(
        self, network_id: str, scheduled_for: datetime
    ) -> ScheduledNowcast | None: ...

    def list(self, network_id: str | None = None) -> tuple[ScheduledNowcast, ...]: ...


class InMemoryNowcastRepository:
    """Small repository with the same unique-slot semantics as PostgreSQL."""

    def __init__(self) -> None:
        self._runs: dict[tuple[str, datetime], ScheduledNowcast] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(network_id: str, scheduled_for: datetime) -> tuple[str, datetime]:
        return str(network_id), _as_utc(scheduled_for)

    def get_or_create(
        self,
        network_id: str,
        scheduled_for: datetime,
        factory: Callable[[], ScheduledNowcast],
    ) -> tuple[ScheduledNowcast, bool]:
        key = self._key(str(network_id), scheduled_for)
        with self._lock:
            existing = self._runs.get(key)
            if existing is not None:
                return existing, False
            created = factory()
            self._runs[key] = created
            return created, True

    def get(self, network_id: str, scheduled_for: datetime) -> ScheduledNowcast | None:
        with self._lock:
            return self._runs.get(self._key(str(network_id), scheduled_for))

    def list(self, network_id: str | None = None) -> tuple[ScheduledNowcast, ...]:
        with self._lock:
            selected: Iterable[ScheduledNowcast] = self._runs.values()
            if network_id is not None:
                selected = [
                    item for item in selected if item.network_id == str(network_id)
                ]
            return tuple(
                sorted(selected, key=lambda item: (item.network_id, item.scheduled_for))
            )

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._runs)


NowcastStore = InMemoryNowcastRepository


class NowcastScheduler:
    """Schedule one immutable nowcast per network/time slot.

    The scheduler is intentionally synchronous at its core.  This makes the
    duplicate-slot decision atomic under a normal lock and makes virtual-clock
    tests deterministic.  Provider callbacks may be synchronous; an
    ``run_once_async`` adapter is provided for async repository/provider
    integrations without changing the core API.
    """

    def __init__(
        self,
        config: NowcastConfig | None = None,
        *,
        clock: Clock | Callable[[], datetime] | None = None,
        repository: NowcastRepository | InMemoryNowcastRepository | None = None,
        model: Any | None = None,
        model_provider: Callable[..., Any] | None = None,
        observation_provider: Callable[..., Iterable[Any]] | None = None,
        assimilator: InitialStateAssimilator | None = None,
        task_factory: Callable[[ScheduledNowcast], ScheduledNowcast] | None = None,
        id_factory: Callable[[], uuid.UUID | str] | None = None,
        network_id: str | uuid.UUID | None = None,
        network: Any | None = None,
        target_ids: Iterable[str] | None = None,
        strict_fingerprint: bool = False,
        lineage: Mapping[str, Any] | Any | None = None,
        run_lineage: Mapping[str, Any] | Any | None = None,
        preflight_bundle: Mapping[str, Any] | Any | None = None,
        lineage_bundle: Mapping[str, Any] | Any | None = None,
        require_lineage: bool = True,
    ) -> None:
        if config is None:
            try:
                from app.core.config import settings as app_settings
            except ImportError:
                config = NowcastConfig()
            else:
                config = NowcastConfig.from_settings(app_settings)
        self.config = config
        self.clock = _coerce_clock(clock)
        self.repository = repository or InMemoryNowcastRepository()
        self.model = model
        self.model_provider = model_provider
        self.observation_provider = observation_provider
        self.assimilator = assimilator or InitialStateAssimilator(
            clock=self.clock,
            observation_lookback_seconds=self.config.observation_lookback_seconds,
        )
        self.task_factory = task_factory
        self.id_factory = id_factory or uuid.uuid4
        self.network_id = str(network_id) if network_id is not None else None
        self.network = network
        self.target_ids = (
            tuple(str(target) for target in target_ids)
            if target_ids is not None
            else None
        )
        self.strict_fingerprint = bool(strict_fingerprint)
        self.lineage = (
            lineage
            if lineage is not None
            else (
                run_lineage
                if run_lineage is not None
                else (
                    preflight_bundle if preflight_bundle is not None else lineage_bundle
                )
            )
        )
        self.require_lineage = bool(require_lineage)
        self._lock = threading.RLock()
        self._async_lock = __import__("asyncio").Lock()

    def _now(self, now: datetime | None) -> datetime:
        return _as_utc(now if now is not None else self.clock.now())

    def _network(self, network_id: str | uuid.UUID | None) -> str:
        value = network_id if network_id is not None else self.network_id
        if value is None or not str(value).strip():
            raise ValueError("network_id is required")
        return str(value)

    def slot_for(
        self,
        now: datetime | None = None,
        *,
        scheduled_for: datetime | None = None,
    ) -> datetime:
        """Return the due slot for ``now`` or validate an explicit slot."""

        current = self._now(now)
        if scheduled_for is not None:
            explicit = _as_utc(scheduled_for)
            if explicit.second or explicit.microsecond:
                # A slot may have a non-zero minute only when the interval
                # itself is not a whole-minute value; the frozen config is
                # whole-minute, so reject seconds rather than silently drift.
                raise InvalidNowcastSchedule("scheduled_for must be minute-aligned")
            epoch_minutes = int((explicit - _EPOCH).total_seconds() // 60)
            interval = self.config.interval_minutes
            if epoch_minutes % interval != 0:
                raise InvalidNowcastSchedule(
                    "scheduled_for must be aligned to the nowcast interval"
                )
            if explicit > current:
                raise InvalidNowcastSchedule("scheduled_for cannot be in the future")
            return explicit
        return align_to_interval(current, self.config.interval_minutes)

    def next_run_at(self, now: datetime | None = None) -> datetime:
        """Return the first future interval boundary after ``now``."""

        current = self._now(now)
        return self.slot_for(current) + timedelta(minutes=self.config.interval_minutes)

    next_due_at = next_run_at
    next_slot = next_run_at

    def is_due(
        self,
        network_id: str | uuid.UUID | None = None,
        *,
        now: datetime | None = None,
        scheduled_for: datetime | None = None,
    ) -> bool:
        current = self._now(now)
        slot = self.slot_for(current, scheduled_for=scheduled_for)
        identifier = self._network(network_id)
        return self.repository.get(identifier, slot) is None

    should_schedule = is_due

    def _call_provider(
        self,
        provider: Callable[..., Any],
        network_id: str,
        as_of: datetime,
    ) -> Any:
        attempts: tuple[Callable[[], Any], ...] = (
            lambda: provider(network_id=network_id, as_of=as_of),
            lambda: provider(network_id, as_of),
            lambda: provider(as_of=as_of),
            lambda: provider(),
        )
        last_error: TypeError | None = None
        for attempt in attempts:
            try:
                return attempt()
            except TypeError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise TypeError("provider callback could not be invoked")

    def _resolve_model(
        self,
        network_id: str,
        as_of: datetime,
        model: Any | None,
    ) -> Any:
        if model is not None:
            return model
        if self.model_provider is not None:
            resolved = self._call_provider(self.model_provider, network_id, as_of)
            if resolved is not None:
                return resolved
        return self.model

    def _resolve_observations(
        self,
        network_id: str,
        as_of: datetime,
        observations: Iterable[Any] | None,
    ) -> tuple[Any, ...]:
        if observations is not None:
            return tuple(observations)
        if self.observation_provider is None:
            return ()
        return tuple(
            self._call_provider(self.observation_provider, network_id, as_of) or ()
        )

    def _new_id(self) -> uuid.UUID:
        value = self.id_factory()
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))

    def _lineage_values(self) -> dict[str, Any]:
        raw = self.lineage
        if raw is None:
            values: dict[str, Any] = {}
        elif isinstance(raw, Mapping):
            nested = raw.get("lineage")
            values = dict(nested) if isinstance(nested, Mapping) else dict(raw)
        else:
            values = {
                name: _get(raw, name, default=None)
                for name in (
                    "network_revision_id",
                    "target_manifest_id",
                    "target_manifest_sha256",
                    "rainfall_scenario_id",
                    "solver_id",
                    "solver_version",
                    "flood_forecast_probability_calibration_id",
                    "flood_forecast_probability_sha256",
                    "pipe_surcharge_probability_calibration_id",
                    "pipe_surcharge_probability_sha256",
                    "flood_forecast_rule_id",
                    "flood_forecast_rule_sha256",
                    "pipe_surcharge_rule_id",
                    "pipe_surcharge_rule_sha256",
                    "forecast_policy_config_sha256",
                )
            }
        required = (
            "network_revision_id",
            "target_manifest_id",
            "target_manifest_sha256",
            "rainfall_scenario_id",
            "solver_id",
            "solver_version",
            "flood_forecast_probability_calibration_id",
            "flood_forecast_probability_sha256",
            "pipe_surcharge_probability_calibration_id",
            "pipe_surcharge_probability_sha256",
            "flood_forecast_rule_id",
            "flood_forecast_rule_sha256",
            "pipe_surcharge_rule_id",
            "pipe_surcharge_rule_sha256",
        )
        if self.require_lineage and any(
            values.get(name) is None or not str(values.get(name)).strip()
            for name in required
        ):
            raise ForecastPolicyUnavailable(
                "nowcast scheduler requires a complete frozen run lineage"
            )
        for name in required:
            if values.get(name) is not None:
                values[name] = str(values[name])
        for name in (
            "target_manifest_sha256",
            "flood_forecast_probability_sha256",
            "pipe_surcharge_probability_sha256",
            "flood_forecast_rule_sha256",
            "pipe_surcharge_rule_sha256",
            "forecast_policy_config_sha256",
        ):
            if (
                values.get(name) is not None
                and re.fullmatch(_HASH_RE, str(values[name])) is None
            ):
                raise ForecastPolicyUnavailable(
                    f"{name} must be a lowercase SHA-256 digest"
                )
        values["config_hash"] = _sha256(
            {
                "schema": "nowcast-run-config-v1",
                **values,
            }
        )
        return values

    def _build_run(
        self,
        network_id: str,
        slot: datetime,
        current: datetime,
        *,
        model: Any,
        observations: Iterable[Any] | None,
    ) -> ScheduledNowcast:
        resolved_model = _require_calibrated_model(
            self._resolve_model(network_id, current, model),
            network_id=network_id,
            as_of=current,
        )
        lineage = self._lineage_values()
        resolved_observations = self._resolve_observations(
            network_id, current, observations
        )
        state = self.assimilator.assimilate(
            self.network if self.network is not None else network_id,
            resolved_observations,
            as_of=current,
            model=resolved_model,
            target_ids=self.target_ids,
        )
        scheduled_at = _as_utc(slot)
        step_seconds = self.config.step_seconds
        origin = _EPOCH + timedelta(
            seconds=math.floor((scheduled_at - _EPOCH).total_seconds() / step_seconds)
            * step_seconds
        )
        deadline = scheduled_at + timedelta(seconds=self.config.deadline_seconds)
        request_fingerprint = _sha256(
            {
                "schema": "nowcast-request-fingerprint-v1",
                "network_id": network_id,
                "scheduled_for": scheduled_at,
                "config": {
                    "interval_minutes": self.config.interval_minutes,
                    "horizon_minutes": self.config.horizon_minutes,
                    "step_minutes": self.config.step_minutes,
                    "deadline_seconds": self.config.deadline_seconds,
                },
                "model": _model_identity(resolved_model),
                "lineage": {
                    key: value for key, value in lineage.items() if key != "config_hash"
                },
                "initial_state_content_sha256": state.initial_state_content_sha256,
            }
        )
        run = ScheduledNowcast(
            task_id=self._new_id(),
            network_id=network_id,
            scheduled_for=scheduled_at,
            forecast_origin_at=origin,
            deadline_at=deadline,
            config=self.config,
            model=resolved_model,
            initial_state=state,
            request_fingerprint=request_fingerprint,
            created_at=current,
            network_revision_id=lineage.get("network_revision_id"),
            target_manifest_id=lineage.get("target_manifest_id"),
            target_manifest_sha256=lineage.get("target_manifest_sha256"),
            rainfall_scenario_id=lineage.get("rainfall_scenario_id"),
            solver_id=lineage.get("solver_id"),
            solver_version=lineage.get("solver_version"),
            flood_forecast_probability_calibration_id=lineage.get(
                "flood_forecast_probability_calibration_id"
            ),
            flood_forecast_probability_sha256=lineage.get(
                "flood_forecast_probability_sha256"
            ),
            pipe_surcharge_probability_calibration_id=lineage.get(
                "pipe_surcharge_probability_calibration_id"
            ),
            pipe_surcharge_probability_sha256=lineage.get(
                "pipe_surcharge_probability_sha256"
            ),
            flood_forecast_rule_id=lineage.get("flood_forecast_rule_id"),
            flood_forecast_rule_sha256=lineage.get("flood_forecast_rule_sha256"),
            pipe_surcharge_rule_id=lineage.get("pipe_surcharge_rule_id"),
            pipe_surcharge_rule_sha256=lineage.get("pipe_surcharge_rule_sha256"),
            forecast_policy_config_sha256=lineage.get("forecast_policy_config_sha256"),
            config_hash=lineage.get("config_hash"),
        )
        if self.task_factory is not None:
            produced = self.task_factory(run)
            if not isinstance(produced, ScheduledNowcast):
                raise TypeError("task_factory must return ScheduledNowcast")
            return produced
        return run

    def _existing(
        self,
        repository: Any,
        network_id: str,
        slot: datetime,
        factory: Callable[[], ScheduledNowcast],
    ) -> tuple[ScheduledNowcast, bool]:
        getter = getattr(repository, "get_or_create", None)
        if callable(getter):
            result = getter(network_id, slot, factory)
            if isinstance(result, tuple) and len(result) == 2:
                return cast(tuple[ScheduledNowcast, bool], result)
            if isinstance(result, ScheduledNowcast):
                return result, False
        if isinstance(repository, MutableMapping):
            key = (str(network_id), _as_utc(slot))
            existing = repository.get(key)
            if existing is not None:
                return cast(ScheduledNowcast, existing), False
            created = factory()
            repository[key] = created
            return created, True
        raise TypeError("repository must provide get_or_create or be a mutable mapping")

    def run_due(
        self,
        network_id: str | uuid.UUID | None = None,
        now: datetime | None = None,
        *,
        scheduled_for: datetime | None = None,
        model: Any | None = None,
        observations: Iterable[Any] | None = None,
        request_fingerprint: str | None = None,
    ) -> ScheduleOutcome:
        """Create or replay the due slot.

        A repeated trigger returns the original task and does not call model,
        observation, or task-factory providers again.
        """

        current = self._now(now)
        identifier = self._network(network_id)
        slot = self.slot_for(current, scheduled_for=scheduled_for)
        with self._lock:
            existing = (
                self.repository.get(identifier, slot)
                if hasattr(self.repository, "get")
                else None
            )
            if existing is not None:
                if (
                    request_fingerprint is not None
                    and request_fingerprint != existing.request_fingerprint
                ):
                    raise NowcastDuplicateConflict(
                        "nowcast slot already exists with a "
                        "different request fingerprint"
                    )
                if model is not None and _model_identity(model) != _model_identity(
                    existing.model
                ):
                    raise NowcastDuplicateConflict(
                        "nowcast slot already exists with a different model lineage"
                    )
                return ScheduleOutcome(existing, created=False)

            def factory() -> ScheduledNowcast:
                return self._build_run(
                    identifier,
                    slot,
                    current,
                    model=model,
                    observations=observations,
                )

            try:
                run, created = self._existing(
                    self.repository, identifier, slot, factory
                )
            except TypeError:
                raise
            return ScheduleOutcome(run, created=bool(created))

    run = run_due
    schedule_for = run_due

    def schedule(self, *args: Any, **kwargs: Any) -> ScheduleOutcome:
        return self.run_due(*args, **kwargs)

    def trigger(self, *args: Any, **kwargs: Any) -> ScheduleOutcome:
        return self.run_due(*args, **kwargs)

    def tick(self, *args: Any, **kwargs: Any) -> ScheduleOutcome:
        return self.run_due(*args, **kwargs)

    def run_once(self, *args: Any, **kwargs: Any) -> ScheduleOutcome:
        return self.run_due(*args, **kwargs)

    def get_run(
        self,
        network_id: str | uuid.UUID,
        scheduled_for: datetime,
    ) -> ScheduledNowcast | None:
        return self.repository.get(self._network(network_id), _as_utc(scheduled_for))

    def list_runs(
        self,
        network_id: str | uuid.UUID | None = None,
    ) -> tuple[ScheduledNowcast, ...]:
        if network_id is None:
            return tuple(self.repository.list())
        return tuple(self.repository.list(self._network(network_id)))

    async def run_once_async(self, *args: Any, **kwargs: Any) -> ScheduleOutcome:
        """Async adapter without provider calls for an already-created slot.

        The asynchronous lock makes the check/provider/factory sequence atomic
        for callers sharing this scheduler.  A provider is therefore invoked
        only by the first trigger for a ``(network, slot)`` pair.
        """

        if len(args) >= 1 and not kwargs.get("network_id"):
            network_id = args[0]
        else:
            network_id = kwargs.get("network_id", self.network_id)
        current = self._now(kwargs.get("now"))
        identifier = self._network(network_id)
        slot = self.slot_for(current, scheduled_for=kwargs.get("scheduled_for"))
        async with self._async_lock:
            existing = (
                self.repository.get(identifier, slot)
                if hasattr(self.repository, "get")
                else None
            )
            if existing is not None:
                return ScheduleOutcome(existing, created=False)
            observations = kwargs.get("observations")
            if observations is None and self.observation_provider is not None:
                observations = self._call_provider(
                    self.observation_provider, identifier, current
                )
            if inspect.isawaitable(observations):
                observations = await observations
            model = kwargs.get("model")
            if model is None and self.model_provider is not None:
                model = self._call_provider(self.model_provider, identifier, current)
            if inspect.isawaitable(model):
                model = await model
            return self.run_due(
                identifier,
                now=current,
                scheduled_for=slot,
                model=model,
                observations=observations,
                request_fingerprint=kwargs.get("request_fingerprint"),
            )

    tick_async = run_once_async


__all__ = [
    "align_to_interval",
    "build_horizon",
    "CallableClock",
    "CalibratedModel",
    "Clock",
    "FakeClock",
    "FrozenClock",
    "ForecastConfig",
    "InitialState",
    "InitialStateAssimilator",
    "InitialStateSnapshot",
    "InMemoryNowcastRepository",
    "InvalidNowcastSchedule",
    "ManualClock",
    "ModelNotCalibrated",
    "ModelReference",
    "NowcastConfig",
    "NowcastDuplicateConflict",
    "nowcast_slot_key",
    "ForecastCycle",
    "ForecastPolicyUnavailable",
    "NowcastCycle",
    "NowcastError",
    "NowcastRepository",
    "NowcastResult",
    "NowcastRunRequest",
    "NowcastSchedule",
    "NowcastScheduler",
    "NowcastSchedulerConfig",
    "NowcastStore",
    "NowcastTask",
    "Observation",
    "ObservationSample",
    "ScheduleOutcome",
    "ScheduledNowcast",
    "SensorAssimilator",
    "SensorDataUnavailable",
    "SensorObservation",
    "SensorReading",
    "StateAssimilator",
    "SystemClock",
    "VirtualClock",
]
