"""Attempt-scoped hydraulic frame persistence and result artifact indexing.

The repository writes ``simulation_frame_registrations`` and the Timescale
``simulation_frames`` row in the caller's transaction.  It deliberately does
not commit individual frames: a worker must publish an attempt, summary,
quality report, and artifact index atomically through :meth:`finalize`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.hydraulic_models import (
    FrameQualityStatus,
    SimulationAttempt,
    SimulationAttemptStatus,
    SimulationFrame,
    SimulationFrameRegistration,
    SimulationResult,
    SimulationRun,
    SimulationStatus,
)

UTC = timezone.utc
MAX_FRAME_INTERVAL_SECONDS = 5 * 60
DEFAULT_RESULT_RETENTION_DAYS = 90
FRAME_ARTIFACT_CATEGORY = "simulation-results"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_QUALITY_VALUES = frozenset(item.value for item in FrameQualityStatus)


class ResultValidationError(ValueError):
    """A frame or result violates the frozen persistence contract."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class FrameAttemptConflict(ResultValidationError):
    """A replay attempted to change an already registered frame payload."""

    def __init__(self) -> None:
        super().__init__(
            "FRAME_ATTEMPT_CONFLICT",
            "the same attempt/frame index already has a different payload hash",
        )


class ArtifactConflictError(ResultValidationError):
    """An immutable controlled artifact already exists with other content."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(
            "ARTIFACT_CONFLICT", f"artifact already exists with different bytes: {path}"
        )


@dataclass(frozen=True, slots=True)
class ControlledArtifactReference:
    """A content-addressed JSON artifact inside the controlled artifact root."""

    path: Path
    uri: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_uri": self.uri,
            "artifact_sha256": self.sha256,
            "artifact_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class FrameWriteContext:
    """Immutable attempt identity and time grid used for every frame write."""

    simulation_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_no: int
    network_id: uuid.UUID
    network_revision_id: uuid.UUID
    target_manifest_id: uuid.UUID
    target_manifest_sha256: str
    solver_id: str
    solver_version: str
    fencing_token: int
    first_frame_at: datetime
    frame_interval_seconds: int = MAX_FRAME_INTERVAL_SECONDS
    expected_frame_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "first_frame_at", _as_utc(self.first_frame_at))
        if isinstance(self.frame_interval_seconds, bool) or not isinstance(
            self.frame_interval_seconds, int
        ):
            raise ResultValidationError(
                "INVALID_FRAME_INTERVAL", "frame_interval_seconds must be an integer"
            )
        if not 1 <= self.frame_interval_seconds <= MAX_FRAME_INTERVAL_SECONDS:
            raise ResultValidationError(
                "INVALID_FRAME_INTERVAL",
                "frame_interval_seconds must be between 1 and 300 seconds",
            )
        if self.expected_frame_count is not None and self.expected_frame_count < 1:
            raise ResultValidationError(
                "INVALID_FRAME_COUNT", "expected_frame_count must be positive"
            )
        if not _SHA256_RE.fullmatch(self.target_manifest_sha256):
            raise ResultValidationError(
                "INVALID_TARGET_MANIFEST",
                "target_manifest_sha256 must be 64 lowercase hexadecimal characters",
            )
        if self.attempt_no < 1 or self.fencing_token < 1:
            raise ResultValidationError(
                "INVALID_ATTEMPT_IDENTITY",
                "attempt_no and fencing_token must be positive",
            )


@dataclass(frozen=True, slots=True)
class FrameInput:
    """One solver frame before attempt-scoped persistence."""

    frame_index: int
    frame_time: datetime
    payload: Mapping[str, Any]
    quality_status: FrameQualityStatus | str = FrameQualityStatus.verified
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame_time", _as_utc(self.frame_time))
        if (
            isinstance(self.frame_index, bool)
            or not isinstance(self.frame_index, int)
            or self.frame_index < 0
        ):
            raise ResultValidationError(
                "INVALID_FRAME_INDEX", "frame_index must be a non-negative integer"
            )
        if self.schema_version < 1:
            raise ResultValidationError(
                "INVALID_FRAME_SCHEMA", "schema_version must be positive"
            )
        if not isinstance(self.payload, Mapping):
            raise ResultValidationError(
                "INVALID_FRAME_PAYLOAD", "payload must be a mapping"
            )


@dataclass(frozen=True, slots=True)
class PreparedFrame:
    """Validated canonical frame bytes ready for one transaction."""

    frame_index: int
    frame_time: datetime
    payload: dict[str, Any]
    canonical_payload: bytes
    payload_bytes: int
    payload_sha256: str
    quality_status: str
    schema_version: int


@dataclass(frozen=True, slots=True)
class FrameWriteResult:
    """Result of an idempotent registration + Timescale frame write."""

    frame_index: int
    frame_time: datetime
    payload_bytes: int
    payload_sha256: str
    canonical_payload: bytes
    inserted: bool


@dataclass(frozen=True, slots=True)
class FrameRetentionSweepResult:
    """Rows removed from the hypertable while audit metadata was retained."""

    cutoff: datetime
    deleted_frame_count: int


@dataclass(frozen=True, slots=True)
class ResultArtifactIndex:
    """Small immutable index stored beside, and inside, published result rows."""

    simulation_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_no: int
    frame_interval_seconds: int
    frame_count: int
    first_frame_time: datetime
    last_frame_time: datetime
    frame_manifest_sha256: str
    summary_sha256: str
    quality_report_sha256: str
    artifact: ControlledArtifactReference

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "simulation-artifact-index-v1",
            "simulation_id": str(self.simulation_id),
            "attempt_id": str(self.attempt_id),
            "attempt_no": self.attempt_no,
            "frame_interval_seconds": self.frame_interval_seconds,
            "frame_count": self.frame_count,
            "first_frame_time": _datetime_text(self.first_frame_time),
            "last_frame_time": _datetime_text(self.last_frame_time),
            "frame_manifest_sha256": self.frame_manifest_sha256,
            "summary_sha256": self.summary_sha256,
            "quality_report_sha256": self.quality_report_sha256,
            **self.artifact.to_dict(),
        }


def serialize_frame_payload(payload: Mapping[str, Any]) -> bytes:
    """Serialize a frame with the repository's frozen canonical JSON rules."""

    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResultValidationError(
            "INVALID_FRAME_PAYLOAD", f"frame payload is not canonical JSON: {exc}"
        ) from exc


_NONNEGATIVE_HYDRAULIC_FIELDS = frozenset(
    {
        "depth_m",
        "volume_m3",
        "area_m3",
        "area_m2",
        "storage_m3",
        "rainfall_m3",
        "inflow_m3",
        "outflow_m3",
        "overflow_m3",
        "flooding_m3s",
        "fill_ratio",
    }
)


def _validate_hydraulic_state(
    value: Any,
    *,
    path: str = "payload",
    field: str | None = None,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            child_field = str(key)
            if child_field in _NONNEGATIVE_HYDRAULIC_FIELDS and item is not None:
                if isinstance(item, (list, tuple, Mapping)):
                    pass
                elif isinstance(item, bool) or not isinstance(item, (int, float)):
                    raise ResultValidationError(
                        "INVALID_STATE",
                        f"{child_path} must be a finite non-negative number",
                    )
                else:
                    numeric = float(item)
                    if not math.isfinite(numeric) or numeric < 0.0:
                        raise ResultValidationError(
                            "INVALID_STATE",
                            f"{child_path} must be a finite non-negative number",
                        )
            _validate_hydraulic_state(item, path=child_path, field=child_field)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]"
            if field in _NONNEGATIVE_HYDRAULIC_FIELDS and item is not None:
                if isinstance(item, bool) or not isinstance(item, (int, float)):
                    raise ResultValidationError(
                        "INVALID_STATE",
                        f"{child_path} must be a finite non-negative number",
                    )
                numeric = float(item)
                if not math.isfinite(numeric) or numeric < 0.0:
                    raise ResultValidationError(
                        "INVALID_STATE",
                        f"{child_path} must be a finite non-negative number",
                    )
            _validate_hydraulic_state(item, path=child_path)


def prepare_frame(context: FrameWriteContext, frame: FrameInput) -> PreparedFrame:
    """Validate payload size, quality, index, and the exact reporting grid."""

    _validate_hydraulic_state(frame.payload)
    canonical_payload = serialize_frame_payload(frame.payload)
    payload_bytes = len(canonical_payload)
    if payload_bytes > settings.SIMULATION_FRAME_MAX_BYTES:
        raise ResultValidationError(
            "OUTPUT_LIMIT_EXCEEDED",
            f"frame payload is {payload_bytes} bytes; limit is "
            f"{settings.SIMULATION_FRAME_MAX_BYTES}",
        )
    expected_time = context.first_frame_at + timedelta(
        seconds=context.frame_interval_seconds * frame.frame_index
    )
    if frame.frame_time != expected_time:
        raise ResultValidationError(
            "FRAME_TIME_GRID_MISMATCH",
            "frame_time does not match first_frame_at + frame_index * interval",
        )
    if (
        context.expected_frame_count is not None
        and frame.frame_index >= context.expected_frame_count
    ):
        raise ResultValidationError(
            "INVALID_FRAME_INDEX",
            "frame_index exceeds the frozen expected_frame_count",
        )
    quality = _enum_value(frame.quality_status)
    if quality not in _QUALITY_VALUES:
        raise ResultValidationError(
            "INVALID_FRAME_QUALITY", f"unsupported frame quality status: {quality}"
        )
    return PreparedFrame(
        frame_index=frame.frame_index,
        frame_time=frame.frame_time,
        payload=json.loads(canonical_payload.decode("utf-8")),
        canonical_payload=canonical_payload,
        payload_bytes=payload_bytes,
        payload_sha256=hashlib.sha256(canonical_payload).hexdigest(),
        quality_status=quality,
        schema_version=frame.schema_version,
    )


def retention_cutoff(
    now: datetime, retention_days: int = DEFAULT_RESULT_RETENTION_DAYS
) -> datetime:
    """Return the inclusive lower bound retained by the Timescale policy."""

    if (
        isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or retention_days <= 0
    ):
        raise ResultValidationError(
            "INVALID_RETENTION", "retention_days must be a positive integer"
        )
    return _as_utc(now) - timedelta(days=retention_days)


def retention_state(
    frame_time: datetime,
    *,
    now: datetime,
    retention_days: int = DEFAULT_RESULT_RETENTION_DAYS,
) -> Literal["retained", "expired"]:
    """Classify a frame using the same boundary as the retention sweeper."""

    cutoff = retention_cutoff(now, retention_days)
    return "retained" if _as_utc(frame_time) >= cutoff else "expired"


def write_controlled_json_artifact(
    artifact_root: Path | str,
    category: str,
    artifact_id: uuid.UUID | str,
    payload: Mapping[str, Any],
) -> ControlledArtifactReference:
    """Atomically write an immutable JSON artifact below a controlled root."""

    if category not in {"simulation-results", "calibration-reports"}:
        raise ResultValidationError(
            "INVALID_ARTIFACT_CATEGORY", "artifact category is not allowlisted"
        )
    try:
        identifier = uuid.UUID(str(artifact_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ResultValidationError(
            "INVALID_ARTIFACT_ID", "artifact_id must be a UUID"
        ) from exc
    root = Path(artifact_root).expanduser().resolve()
    directory = (root / category).resolve()
    if directory != root and root not in directory.parents:
        raise ResultValidationError(
            "UNSAFE_ARTIFACT_PATH", "artifact directory escapes the controlled root"
        )
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{identifier}.json"
    if directory not in destination.resolve().parents:
        raise ResultValidationError(
            "UNSAFE_ARTIFACT_PATH", "artifact file escapes the controlled root"
        )
    canonical_payload = _canonical_bytes(payload)
    if destination.exists():
        existing = destination.read_bytes()
        if existing != canonical_payload:
            raise ArtifactConflictError(destination)
        return ControlledArtifactReference(
            path=destination,
            uri=str(destination),
            sha256=hashlib.sha256(existing).hexdigest(),
            size_bytes=len(existing),
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{identifier}.", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # A hard link publishes the fully fsynced temporary file without
            # replacing a concurrently created immutable artifact.
            os.link(temporary_name, destination)
        except FileExistsError:
            existing = destination.read_bytes()
            if existing != canonical_payload:
                raise ArtifactConflictError(destination) from None
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return ControlledArtifactReference(
        path=destination,
        uri=str(destination),
        sha256=hashlib.sha256(canonical_payload).hexdigest(),
        size_bytes=len(canonical_payload),
    )


class ResultRepository:
    """Persist attempt frames and atomically publish their result metadata."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        artifact_root: Path | str | None = None,
        retention_days: int | None = None,
        max_frame_bytes: int | None = None,
    ) -> None:
        self.session = session
        self.artifact_root = Path(
            artifact_root
            if artifact_root is not None
            else settings.HYDRAULIC_ARTIFACT_ROOT
        )
        self.retention_days = (
            settings.SIMULATION_RESULT_RETENTION_DAYS
            if retention_days is None
            else retention_days
        )
        if (
            isinstance(self.retention_days, bool)
            or not isinstance(self.retention_days, int)
            or self.retention_days <= 0
        ):
            raise ResultValidationError(
                "INVALID_RETENTION", "retention_days must be positive"
            )
        self.max_frame_bytes = (
            settings.SIMULATION_FRAME_MAX_BYTES
            if max_frame_bytes is None
            else max_frame_bytes
        )
        if self.max_frame_bytes <= 0:
            raise ResultValidationError(
                "OUTPUT_LIMIT_EXCEEDED", "max_frame_bytes must be positive"
            )

    async def write_frame(
        self, context: FrameWriteContext, frame: FrameInput
    ) -> FrameWriteResult:
        """Write registration + hypertable row without committing the worker TX."""

        prepared = prepare_frame(context, frame)
        if prepared.payload_bytes > self.max_frame_bytes:
            raise ResultValidationError(
                "OUTPUT_LIMIT_EXCEEDED",
                f"frame payload exceeds configured limit {self.max_frame_bytes}",
            )
        attempt = await self._lock_attempt(context)
        self._validate_attempt(attempt, context)
        replay_result = await self.session.execute(
            select(SimulationFrameRegistration).where(
                SimulationFrameRegistration.attempt_id == context.attempt_id,
                SimulationFrameRegistration.frame_index == prepared.frame_index,
            )
        )
        replay = replay_result.scalars().one_or_none()
        if replay is not None:
            if (
                replay.frame_time == prepared.frame_time
                and replay.payload_sha256 == prepared.payload_sha256
                and int(replay.payload_bytes) == prepared.payload_bytes
            ):
                return FrameWriteResult(
                    frame_index=prepared.frame_index,
                    frame_time=prepared.frame_time,
                    payload_bytes=prepared.payload_bytes,
                    payload_sha256=prepared.payload_sha256,
                    canonical_payload=prepared.canonical_payload,
                    inserted=False,
                )
            raise FrameAttemptConflict()
        last_result = await self.session.execute(
            select(SimulationFrameRegistration)
            .where(SimulationFrameRegistration.attempt_id == context.attempt_id)
            .order_by(SimulationFrameRegistration.frame_index.desc())
            .limit(1)
        )
        last_registration = last_result.scalars().all()
        existing = last_registration[0] if last_registration else None
        if existing is not None and existing.frame_index == prepared.frame_index:
            if (
                existing.frame_time == prepared.frame_time
                and existing.payload_sha256 == prepared.payload_sha256
                and int(existing.payload_bytes) == prepared.payload_bytes
            ):
                return FrameWriteResult(
                    frame_index=prepared.frame_index,
                    frame_time=prepared.frame_time,
                    payload_bytes=prepared.payload_bytes,
                    payload_sha256=prepared.payload_sha256,
                    canonical_payload=prepared.canonical_payload,
                    inserted=False,
                )
            raise FrameAttemptConflict()
        expected_index = 0 if existing is None else int(existing.frame_index) + 1
        if prepared.frame_index != expected_index:
            raise ResultValidationError(
                "FRAME_INDEX_GAP",
                f"expected frame index {expected_index}, got {prepared.frame_index}",
            )
        if (
            context.expected_frame_count is not None
            and prepared.frame_index >= context.expected_frame_count
        ):
            raise ResultValidationError(
                "INVALID_FRAME_INDEX",
                "frame_index exceeds the frozen expected_frame_count",
            )

        registration = SimulationFrameRegistration(
            attempt_id=context.attempt_id,
            frame_index=prepared.frame_index,
            frame_time=prepared.frame_time,
            payload_bytes=prepared.payload_bytes,
            payload_sha256=prepared.payload_sha256,
            quality_status=prepared.quality_status,
        )
        frame_row = SimulationFrame(
            frame_time=prepared.frame_time,
            attempt_id=context.attempt_id,
            simulation_id=context.simulation_id,
            attempt_no=context.attempt_no,
            frame_index=prepared.frame_index,
            network_id=context.network_id,
            network_revision_id=context.network_revision_id,
            target_manifest_id=context.target_manifest_id,
            target_manifest_sha256=context.target_manifest_sha256,
            solver_id=context.solver_id,
            solver_version=context.solver_version,
            schema_version=prepared.schema_version,
            payload=prepared.payload,
            payload_bytes=prepared.payload_bytes,
            payload_sha256=prepared.payload_sha256,
            quality_status=prepared.quality_status,
        )
        self.session.add(registration)
        self.session.add(frame_row)
        attempt.frame_count = prepared.frame_index + 1
        attempt.first_frame_time = context.first_frame_at
        attempt.last_frame_time = prepared.frame_time
        await self.session.flush()
        return FrameWriteResult(
            frame_index=prepared.frame_index,
            frame_time=prepared.frame_time,
            payload_bytes=prepared.payload_bytes,
            payload_sha256=prepared.payload_sha256,
            canonical_payload=prepared.canonical_payload,
            inserted=True,
        )

    async def finalize(
        self,
        context: FrameWriteContext,
        *,
        summary: Mapping[str, Any],
        quality_report: Mapping[str, Any],
        completed_at: datetime | None = None,
    ) -> ResultArtifactIndex:
        """Validate all staged frames and publish result + artifact atomically."""

        _validate_hydraulic_state(summary, path="summary")
        attempt = await self._lock_attempt(context)
        self._validate_attempt(attempt, context, allow_published=True)
        completed = _as_utc(completed_at or datetime.now(UTC))
        registrations = (
            (
                await self.session.execute(
                    select(SimulationFrameRegistration)
                    .where(SimulationFrameRegistration.attempt_id == context.attempt_id)
                    .order_by(SimulationFrameRegistration.frame_index)
                )
            )
            .scalars()
            .all()
        )
        frames = (
            (
                await self.session.execute(
                    select(SimulationFrame)
                    .where(SimulationFrame.attempt_id == context.attempt_id)
                    .order_by(SimulationFrame.frame_index)
                )
            )
            .scalars()
            .all()
        )
        self._validate_complete_frame_grid(
            context, registrations=registrations, frames=frames
        )
        self._validate_quality_report(context, quality_report)
        # Serialize competing finalizers before publishing an immutable artifact.
        simulation = await self._lock_simulation(context.simulation_id)
        summary_sha256 = hashlib.sha256(_canonical_bytes(summary)).hexdigest()
        quality_sha256 = hashlib.sha256(_canonical_bytes(quality_report)).hexdigest()
        frame_manifest = self._frame_manifest(context, registrations)
        frame_manifest_sha256 = hashlib.sha256(
            _canonical_bytes(frame_manifest)
        ).hexdigest()
        provisional_index = {
            "schema": "simulation-artifact-index-v1",
            "simulation_id": str(context.simulation_id),
            "attempt_id": str(context.attempt_id),
            "attempt_no": context.attempt_no,
            "frame_interval_seconds": context.frame_interval_seconds,
            "frame_count": len(registrations),
            "first_frame_time": _datetime_text(context.first_frame_at),
            "last_frame_time": _datetime_text(registrations[-1].frame_time),
            "frame_manifest_sha256": frame_manifest_sha256,
            "summary_sha256": summary_sha256,
            "quality_report_sha256": quality_sha256,
        }
        artifact = write_controlled_json_artifact(
            self.artifact_root,
            FRAME_ARTIFACT_CATEGORY,
            context.attempt_id,
            provisional_index,
        )
        index = ResultArtifactIndex(
            simulation_id=context.simulation_id,
            attempt_id=context.attempt_id,
            attempt_no=context.attempt_no,
            frame_interval_seconds=context.frame_interval_seconds,
            frame_count=len(registrations),
            first_frame_time=context.first_frame_at,
            last_frame_time=registrations[-1].frame_time,
            frame_manifest_sha256=frame_manifest_sha256,
            summary_sha256=summary_sha256,
            quality_report_sha256=quality_sha256,
            artifact=artifact,
        )
        published_summary = {
            **dict(summary),
            "artifact_index": index.to_dict(),
        }
        published_summary_sha256 = hashlib.sha256(
            _canonical_bytes(published_summary)
        ).hexdigest()
        existing_result = (
            (
                await self.session.execute(
                    select(SimulationResult).where(
                        SimulationResult.simulation_id == context.simulation_id,
                        SimulationResult.attempt_id == context.attempt_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing_result is not None:
            if (
                existing_result.summary_sha256 != published_summary_sha256
                or existing_result.quality_report_sha256 != quality_sha256
            ):
                raise ResultValidationError(
                    "RESULT_ATTEMPT_CONFLICT",
                    "published result already exists with different summary or quality",
                )
            published_result_id = existing_result.id
        else:
            published_result_id = uuid.uuid4()
            mass_residual = _optional_finite_float(
                quality_report.get(
                    "max_mass_residual_relative",
                    quality_report.get("mass_residual_relative"),
                )
            )
            self.session.add(
                SimulationResult(
                    id=published_result_id,
                    simulation_id=context.simulation_id,
                    network_revision_id=context.network_revision_id,
                    attempt_id=context.attempt_id,
                    fencing_token=context.fencing_token,
                    summary=published_summary,
                    summary_sha256=published_summary_sha256,
                    quality_report=dict(quality_report),
                    quality_report_sha256=quality_sha256,
                    max_mass_residual_relative=mass_residual,
                    max_courant=_optional_finite_float(
                        quality_report.get("max_courant")
                    ),
                    completed_at=completed,
                )
            )
        attempt.summary_id = published_result_id
        attempt.status = SimulationAttemptStatus.published.value
        attempt.frame_count = len(registrations)
        attempt.first_frame_time = context.first_frame_at
        attempt.last_frame_time = registrations[-1].frame_time
        attempt.frame_manifest_sha256 = frame_manifest_sha256
        attempt.quality_report_sha256 = quality_sha256
        attempt.finished_at = completed
        attempt.published_at = completed
        if (
            simulation.published_attempt_id is not None
            and simulation.published_attempt_id != context.attempt_id
        ):
            raise ResultValidationError(
                "RESULT_ATTEMPT_CONFLICT",
                "simulation already publishes a different attempt",
            )
        simulation.published_attempt_id = context.attempt_id
        simulation.published_attempt_no = context.attempt_no
        simulation.status = SimulationStatus.completed.value
        simulation.phase = "finalizing"
        simulation.progress = 100
        simulation.lease_owner = None
        simulation.lease_token = None
        simulation.lease_until = None
        simulation.result_summary = published_summary
        simulation.finished_at = completed
        await self.session.flush()
        return index

    async def expire_frames(
        self, now: datetime | None = None
    ) -> FrameRetentionSweepResult:
        """Delete only expired hypertable rows; retain result/audit metadata."""

        cutoff = retention_cutoff(now or datetime.now(UTC), self.retention_days)
        result = await self.session.execute(
            delete(SimulationFrame).where(SimulationFrame.frame_time < cutoff)
        )
        await self.session.flush()
        rowcount = getattr(result, "rowcount", -1)
        return FrameRetentionSweepResult(
            cutoff=cutoff,
            deleted_frame_count=max(0, int(rowcount)) if rowcount >= 0 else 0,
        )

    async def _lock_attempt(self, context: FrameWriteContext) -> SimulationAttempt:
        result = await self.session.execute(
            select(SimulationAttempt)
            .where(SimulationAttempt.id == context.attempt_id)
            .with_for_update()
        )
        attempt = result.scalars().one_or_none()
        if attempt is None:
            raise ResultValidationError(
                "FRAME_ATTEMPT_NOT_FOUND",
                f"attempt {context.attempt_id} does not exist",
            )
        return attempt

    async def _lock_simulation(self, simulation_id: uuid.UUID) -> SimulationRun:
        result = await self.session.execute(
            select(SimulationRun)
            .where(SimulationRun.id == simulation_id)
            .with_for_update()
        )
        simulation = result.scalars().one_or_none()
        if simulation is None:
            raise ResultValidationError(
                "SIMULATION_NOT_FOUND", f"simulation {simulation_id} does not exist"
            )
        return simulation

    @staticmethod
    def _validate_attempt(
        attempt: SimulationAttempt,
        context: FrameWriteContext,
        *,
        allow_published: bool = False,
    ) -> None:
        valid_statuses = {SimulationAttemptStatus.running.value}
        if allow_published:
            valid_statuses.add(SimulationAttemptStatus.published.value)
        if attempt.status not in valid_statuses:
            raise ResultValidationError(
                "FRAME_ATTEMPT_NOT_WRITABLE",
                f"attempt status {attempt.status} cannot accept frame writes",
            )
        expected = {
            "simulation_id": context.simulation_id,
            "attempt_no": context.attempt_no,
            "network_revision_id": context.network_revision_id,
            "target_manifest_id": context.target_manifest_id,
            "target_manifest_sha256": context.target_manifest_sha256,
            "solver_id": context.solver_id,
            "solver_version": context.solver_version,
            "fencing_token": context.fencing_token,
        }
        mismatches = [
            name for name, value in expected.items() if getattr(attempt, name) != value
        ]
        if mismatches:
            raise ResultValidationError(
                "STALE_FENCING_TOKEN",
                "attempt identity or fencing token does not match: "
                + ", ".join(sorted(mismatches)),
            )

    @staticmethod
    def _validate_complete_frame_grid(
        context: FrameWriteContext,
        *,
        registrations: Sequence[SimulationFrameRegistration],
        frames: Sequence[SimulationFrame],
    ) -> None:
        if not registrations:
            raise ResultValidationError("FRAME_SCHEMA_INVALID", "attempt has no frames")
        if context.expected_frame_count is None:
            raise ResultValidationError(
                "FRAME_SCHEMA_INVALID",
                "expected_frame_count is required before publishing an attempt",
            )
        expected_count = context.expected_frame_count
        if len(registrations) != expected_count or len(frames) != expected_count:
            raise ResultValidationError(
                "FRAME_SCHEMA_INVALID",
                f"expected {expected_count} complete frames, got "
                f"{len(registrations)} registrations and {len(frames)} rows",
            )
        frames_by_index = {int(item.frame_index): item for item in frames}
        for index, registration in enumerate(registrations):
            expected_time = context.first_frame_at + timedelta(
                seconds=context.frame_interval_seconds * index
            )
            if int(registration.frame_index) != index:
                raise ResultValidationError(
                    "FRAME_SCHEMA_INVALID", "frame indexes must be dense and increasing"
                )
            if registration.frame_time != expected_time:
                raise ResultValidationError(
                    "FRAME_SCHEMA_INVALID", "frame time grid is incomplete or irregular"
                )
            frame = frames_by_index.get(index)
            if frame is None or (
                frame.payload_sha256 != registration.payload_sha256
                or int(frame.payload_bytes) != int(registration.payload_bytes)
                or frame.frame_time != registration.frame_time
            ):
                raise ResultValidationError(
                    "FRAME_SCHEMA_INVALID",
                    "frame registration and Timescale row do not match",
                )
            if (
                _enum_value(registration.quality_status) == "failed"
                or _enum_value(frame.quality_status) == "failed"
            ):
                raise ResultValidationError(
                    "FRAME_QUALITY_FAILED",
                    "failed frame quality cannot be published",
                )

    @staticmethod
    def _validate_quality_report(
        context: FrameWriteContext, quality_report: Mapping[str, Any]
    ) -> None:
        status = quality_report.get("status", quality_report.get("result_status"))
        if status not in {"verified", "warning"}:
            raise ResultValidationError(
                "QUALITY_REPORT_INVALID",
                "quality report status must be verified or warning",
            )
        residual = _optional_finite_float(
            quality_report.get(
                "max_mass_residual_relative",
                quality_report.get("mass_residual_relative"),
            )
        )
        if context.solver_id.startswith("py_") and residual is None:
            raise ResultValidationError(
                "QUALITY_REPORT_INVALID",
                "Python solver quality reports require max_mass_residual_relative",
            )
        if residual is not None and residual > 1e-3:
            raise ResultValidationError(
                "MASS_BALANCE_FAILED",
                "relative mass residual exceeds the 1e-3 publication limit",
            )
        courant = _optional_finite_float(quality_report.get("max_courant"))
        if courant is not None and (courant < 0.0 or courant > 0.5):
            raise ResultValidationError(
                "NUMERICAL_UNSTABLE",
                "Courant max_courant must be between 0 and the frozen 0.5 limit",
            )
        if (
            context.solver_id.startswith("py_")
            and status == "verified"
            and residual is not None
            and residual > 1e-4
        ):
            raise ResultValidationError(
                "MASS_BALANCE_FAILED",
                "verified Python solver residual must be <= 1e-4",
            )

    @staticmethod
    def _frame_manifest(
        context: FrameWriteContext,
        registrations: Sequence[SimulationFrameRegistration],
    ) -> dict[str, Any]:
        return {
            "schema": "simulation-frame-manifest-v1",
            "simulation_id": str(context.simulation_id),
            "attempt_id": str(context.attempt_id),
            "attempt_no": context.attempt_no,
            "frame_interval_seconds": context.frame_interval_seconds,
            "frames": [
                {
                    "frame_index": int(item.frame_index),
                    "frame_time": _datetime_text(item.frame_time),
                    "payload_bytes": int(item.payload_bytes),
                    "payload_sha256": item.payload_sha256,
                    "quality_status": _enum_value(item.quality_status),
                }
                for item in registrations
            ],
        }


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResultValidationError(
            "INVALID_CANONICAL_JSON", f"value is not canonical JSON: {exc}"
        ) from exc


def _as_utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ResultValidationError(
            "NAIVE_DATETIME", "timestamps must be timezone-aware datetimes"
        )
    return value.astimezone(UTC)


def _datetime_text(value: datetime) -> str:
    text = _as_utc(value).isoformat(timespec="microseconds")
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def _enum_value(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _optional_finite_float(value: Any) -> float | None:
    if value is None:
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ResultValidationError(
            "INVALID_QUALITY_REPORT", "quality report values must be finite"
        )
    return numeric


__all__ = [
    "ArtifactConflictError",
    "ControlledArtifactReference",
    "DEFAULT_RESULT_RETENTION_DAYS",
    "FrameAttemptConflict",
    "FrameInput",
    "FrameRetentionSweepResult",
    "FrameWriteContext",
    "FrameWriteResult",
    "MAX_FRAME_INTERVAL_SECONDS",
    "PreparedFrame",
    "ResultArtifactIndex",
    "ResultRepository",
    "ResultValidationError",
    "prepare_frame",
    "retention_cutoff",
    "retention_state",
    "serialize_frame_payload",
    "write_controlled_json_artifact",
]
