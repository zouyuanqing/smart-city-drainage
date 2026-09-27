"""Hydraulic business API.

This module is the transport boundary for the hydraulic twin.  It deliberately
keeps request validation, authorization, idempotency, pagination and response
shapes in one place while delegating geometry, rainfall, topology and SWMM
canonicalisation to the existing hydraulic services.  The small store below
is an injectable API-state adapter used by the HTTP tests and by deployments
that have not yet attached the durable worker repositories; worker code must
replace the adapter's mutation methods without changing the route contracts.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Literal, Mapping, Sequence, cast

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.core.database import get_db_session
from app.core.security import authenticate_access_token
from app.models.db_models import RoleEnum
from app.schemas.hydraulic_schemas import (
    CatchmentCreate,
    GeometryDocument,
    NetworkNodeCreate,
    PipeSegmentCreate,
)
from app.services.hydraulic.exporters.swmm import export_inp
from app.services.hydraulic.geometry import (
    EntityType,
    filter_rows_by_bbox,
    parse_query_bbox,
)
from app.services.hydraulic.importers.swmm import SwmmParseError, parse_swmm
from app.services.hydraulic.rainfall import ALLOWED_RETURN_PERIODS, MAX_DURATION_SECONDS
from app.services.hydraulic.topology import (
    TopologyNode,
    TopologyPipe,
    validate_topology,
)
from app.services.sse_manager import SSECursorExpiredError, sse_manager

router = APIRouter(tags=["hydraulic"])
hydraulic_router = router

UTC = timezone.utc
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_PATH_FORBIDDEN = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)|^[\\/]|^[A-Za-z]:|^\\\\")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Errors and authentication
# ---------------------------------------------------------------------------


def _error(
    status_code: int,
    code: str,
    detail: str,
    *,
    errors: Sequence[Mapping[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> HTTPException:
    """Create the single structured error shape used by all hydraulic routes."""

    normalized = [
        {
            "field": str(item.get("field", "$")),
            "message": str(item.get("message", detail)),
            "retryable": bool(item.get("retryable", False)),
            "details": dict(item.get("details", {})),
        }
        for item in (errors or ())
    ]
    return HTTPException(
        status_code=status_code,
        detail={"detail": detail, "code": code, "errors": normalized},
        headers=dict(headers or {}) or None,
    )


def _user_id(claims: Mapping[str, Any]) -> str:
    value = str(claims.get("sub", ""))
    return value if value else "anonymous"


async def _authenticate(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise _error(401, "AUTH_REQUIRED", "authentication is required")
    try:
        claims = await authenticate_access_token(credentials.credentials, db)
    except HTTPException as exc:
        if exc.status_code == 403:
            code = "FORBIDDEN"
        elif exc.status_code == 503:
            code = "AUTH_STATE_UNAVAILABLE"
        else:
            code = "UNAUTHENTICATED"
        raise _error(
            exc.status_code,
            code,
            str(exc.detail),
            headers=exc.headers,
        ) from exc
    role = claims.get("role")
    if not isinstance(role, str) or not role.strip():
        raise _error(401, "UNAUTHENTICATED", "authenticated role is missing")
    if role not in {item.value for item in RoleEnum}:
        raise _error(403, "FORBIDDEN", "authenticated role is not recognised")
    return dict(claims)


def _viewer(claims: dict[str, Any] = Depends(_authenticate)) -> dict[str, Any]:
    return claims


def _operator(claims: dict[str, Any] = Depends(_authenticate)) -> dict[str, Any]:
    if claims.get("role") not in {RoleEnum.operator.value, RoleEnum.admin.value}:
        raise _error(403, "FORBIDDEN", "operator or admin role is required")
    return claims


def _admin(claims: dict[str, Any] = Depends(_authenticate)) -> dict[str, Any]:
    if claims.get("role") != RoleEnum.admin.value:
        raise _error(403, "FORBIDDEN", "admin role is required")
    return claims


def _idempotency_key(
    value: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str:
    if value is None or not (8 <= len(value) <= 128) or not value.strip():
        raise _error(
            422,
            "VALIDATION_ERROR",
            "Idempotency-Key must contain 8..128 characters",
            errors=[{"field": "Idempotency-Key", "message": "invalid key length"}],
        )
    return value


# ---------------------------------------------------------------------------
# Canonical JSON and state adapter
# ---------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    if isinstance(value, EnumLike):  # type: ignore[misc]
        return value.value
    raise TypeError(f"unsupported value for canonical JSON: {type(value)!r}")


# ``Enum`` is imported lazily to keep the helper usable on Python 3.11 without
# making the public request models depend on the implementation detail.
from enum import Enum as EnumLike  # noqa: E402


def _json_safe(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, datetime)):
        return str(value)
    if isinstance(value, EnumLike):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (
        value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )


def _iso_required(value: datetime) -> str:
    """ISO-8601 for a value that is never absent (optional fields use ``_iso``)."""

    text = _iso(value)
    assert text is not None  # a datetime always formats
    return text


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise _error(422, "VALIDATION_ERROR", "timestamp must be ISO-8601") from exc
    else:
        raise _error(422, "VALIDATION_ERROR", "timestamp must be ISO-8601")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _error(422, "VALIDATION_ERROR", "timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _uuid(value: Any, field: str) -> str:
    text = str(value)
    if not _UUID_RE.fullmatch(text):
        raise _error(422, "VALIDATION_ERROR", f"{field} must be a UUID")
    return str(uuid.UUID(text))


def _hash(value: str, field: str) -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise _error(
            422, "VALIDATION_ERROR", f"{field} must be a lowercase SHA-256 hash"
        )
    return value


def _page(
    items: Sequence[Any], cursor: str | None, limit: int
) -> tuple[list[Any], str | None]:
    offset = 0
    if cursor:
        try:
            decoded = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii")
            offset = int(decoded)
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise _error(422, "VALIDATION_ERROR", "cursor is invalid") from exc
        if offset < 0:
            raise _error(422, "VALIDATION_ERROR", "cursor is invalid")
    end = min(len(items), offset + limit)
    next_cursor = (
        base64.urlsafe_b64encode(str(end).encode("ascii")).decode("ascii")
        if end < len(items)
        else None
    )
    return list(items[offset:end]), next_cursor


class HydraulicApiStore:
    """Thread-safe API adapter for resources not yet attached to a worker repo.

    The adapter deliberately has a narrow surface.  It stores immutable
    revision snapshots and exposes no arbitrary file/path operation.  A
    production integration can replace it with SQLAlchemy repositories while
    keeping the route functions and their wire contracts unchanged.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self.durable_backend = False
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", RLock()):
            self.networks: dict[str, dict[str, Any]] = {}
            self.revisions: dict[str, dict[str, Any]] = {}
            self.rainfall: dict[str, dict[str, Any]] = {}
            self.jobs: dict[str, dict[str, Any]] = {}
            self.imports: dict[str, dict[str, Any]] = {}
            self.validation_runs: dict[str, dict[str, Any]] = {}
            self.simulations: dict[str, dict[str, Any]] = {}
            self.calibrations: dict[str, dict[str, Any]] = {}
            self.forecast_runs: dict[str, dict[str, Any]] = {}
            self.forecast_alerts: dict[str, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.next_event_sequence = 1
        self.idempotency: dict[tuple[str, str, str], dict[str, Any]] = {}


hydraulic_store = HydraulicApiStore()


def reset_hydraulic_api_store() -> None:
    """Reset the adapter for isolated API tests and explicit app restarts."""

    hydraulic_store.reset()
    sse_manager.reset_forecast()


def _require_durable_api_store() -> None:
    if settings.DEPLOYMENT_ENVIRONMENT == "production" and not getattr(
        hydraulic_store, "durable_backend", False
    ):
        raise _error(
            503,
            "DURABLE_STORE_UNAVAILABLE",
            "hydraulic durable repository is not configured",
        )


# ---------------------------------------------------------------------------
# Request models (kept here so the hydraulic API owns its OpenAPI contract)
# ---------------------------------------------------------------------------


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class HydraulicResponse(ApiModel):
    """Named response envelope used by the hydraulic transport surface."""

    model_config = ConfigDict(
        extra="allow",
        from_attributes=True,
        json_schema_extra={"additionalProperties": False},
    )
    schema_version: int | str | None = None
    id: str | None = None
    status: str | None = None
    items: list[dict[str, Any]] | None = None
    next_cursor: str | None = None
    request_id: str | None = None


class NetworkCreateRequest(ApiModel):
    code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    status: Literal["draft", "active", "archived"] = "draft"
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    spatial_backend: Literal["postgis", "portable"] = "portable"


class NetworkPatchRequest(ApiModel):
    code: str | None = Field(default=None, min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: Literal["draft", "active", "archived"] | None = None
    expected_revision_id: str | None = None


class NodePatchRequest(ApiModel):
    stable_key: str | None = Field(default=None, min_length=1, max_length=128)
    kind: str | None = None
    geometry: dict[str, Any] | None = None
    invert_elevation_m: float | None = None
    rim_elevation_m: float | None = None
    max_depth_m: float | None = Field(default=None, gt=0)
    properties: dict[str, Any] | None = None


class PipePatchRequest(ApiModel):
    stable_key: str | None = Field(default=None, min_length=1, max_length=128)
    upstream_node_id: str | None = None
    downstream_node_id: str | None = None
    nominal_diameter_m: float | None = Field(default=None, gt=0)
    length_m: float | None = Field(default=None, gt=0)
    roughness_manning_n: float | None = Field(default=None, gt=0)
    geometry: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None


class CatchmentPatchRequest(ApiModel):
    stable_key: str | None = Field(default=None, min_length=1, max_length=128)
    area_m2: float | None = Field(default=None, gt=0)
    outfall_node_id: str | None = None
    geometry: dict[str, Any] | None = None
    properties: dict[str, Any] | None = None


class ElevationProfileRequest(ApiModel):
    stations: list[float] = Field(min_length=2)
    elevations_m: list[float] = Field(min_length=2)
    schema_version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_profile(self) -> "ElevationProfileRequest":
        if len(self.stations) != len(self.elevations_m):
            raise ValueError("stations and elevations_m must have equal length")
        if any(not math.isfinite(item) for item in self.stations + self.elevations_m):
            raise ValueError("profile values must be finite")
        if any(right <= left for left, right in zip(self.stations, self.stations[1:])):
            raise ValueError("stations must be strictly increasing")
        return self


class SeedRequest(ApiModel):
    n: int = Field(default=3, ge=1, le=100)
    m: int = Field(default=4, ge=2, le=100)
    k: int = Field(default=1, ge=0, le=99)
    random_seed: int = 0
    west: float = Field(default=121.30, ge=-180, le=180)
    south: float = Field(default=31.15, ge=-90, le=90)
    east: float = Field(default=121.60, ge=-180, le=180)
    north: float = Field(default=31.32, ge=-90, le=90)


class ValidationRequest(ApiModel):
    strict: bool = True
    expected_revision_id: str | None = None


class RainfallScenarioRequest(ApiModel):
    network_id: str
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["design", "custom", "synthetic"]
    return_period_years: int | Literal["custom"] | None = None
    step_seconds: int
    duration_seconds: int = Field(gt=0, le=MAX_DURATION_SECONDS)
    source_type: Literal["synthetic"] = "synthetic"
    algorithm: Literal[
        "synthetic-design-chicago-v1",
        "synthetic-custom-v1",
        "synthetic-random-block-v1",
    ]
    random_seed: int
    points: list[dict[str, Any]] = Field(min_length=1)
    content_sha256: str = Field(pattern=_HASH_RE.pattern)

    @model_validator(mode="after")
    def validate_points(self) -> "RainfallScenarioRequest":
        if self.step_seconds not in {60, 300, 900}:
            raise ValueError("step_seconds must be 60, 300, or 900")
        expected = self.duration_seconds // self.step_seconds + 1
        if len(self.points) != expected:
            raise ValueError(f"points must contain exactly {expected} samples")
        for index, point in enumerate(self.points):
            if point.get("point_index") != index:
                raise ValueError("point_index must be dense and ascending")
            intensity = point.get("intensity_mm_h")
            if isinstance(intensity, bool) or not isinstance(intensity, (int, float)):
                raise ValueError("intensity_mm_h must be numeric")
            if not math.isfinite(float(intensity)) or float(intensity) < 0:
                raise ValueError("intensity_mm_h must be finite and non-negative")
            if index:
                previous = self.points[index - 1].get("valid_at")
                current = point.get("valid_at")
                if (
                    not previous
                    or not current
                    or _parse_datetime(current) <= _parse_datetime(previous)
                ):
                    raise ValueError("valid_at values must be strictly increasing")
        if (
            self.kind == "design"
            and self.return_period_years not in ALLOWED_RETURN_PERIODS
        ):
            raise ValueError("design rainfall requires a supported return period")
        if self.kind == "synthetic" and self.return_period_years is not None:
            raise ValueError("synthetic rainfall must not set return_period_years")
        if self.kind == "custom" and self.return_period_years not in (None, "custom"):
            raise ValueError("custom rainfall may set return_period_years='custom'")
        expected_algorithm = {
            "design": "synthetic-design-chicago-v1",
            "custom": "synthetic-custom-v1",
            "synthetic": "synthetic-random-block-v1",
        }[self.kind]
        if self.algorithm != expected_algorithm:
            raise ValueError(f"{self.kind} rainfall requires {expected_algorithm}")
        return self


class RainfallPatchRequest(ApiModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: Literal["design", "custom", "synthetic"] | None = None
    return_period_years: int | Literal["custom"] | None = None
    step_seconds: int | None = Field(default=None)
    duration_seconds: int | None = Field(default=None, gt=0, le=MAX_DURATION_SECONDS)
    source_type: Literal["synthetic"] | None = None
    algorithm: str | None = None
    random_seed: int | None = None
    points: list[dict[str, Any]] | None = None
    content_sha256: str | None = Field(default=None, pattern=_HASH_RE.pattern)


class SimulationSubmitRequest(ApiModel):
    network_id: str
    network_revision_id: str
    rainfall_scenario_id: str
    parameter_set_id: str
    initial_state_id: str | None = None
    solver_selection: Literal["auto", "explicit"]
    requested_solver_id: str | None = None
    start_at: AwareDatetime
    duration_seconds: int = Field(gt=0, le=259_200)
    frame_interval_seconds: int = Field(gt=0, le=300)
    ponding_enabled: bool
    strict_stability: bool = True

    @model_validator(mode="after")
    def validate_solver(self) -> "SimulationSubmitRequest":
        if self.solver_selection == "explicit" and not self.requested_solver_id:
            raise ValueError("explicit solver selection requires requested_solver_id")
        if self.duration_seconds % self.frame_interval_seconds:
            raise ValueError("duration_seconds must align to frame_interval_seconds")
        if self.requested_solver_id not in {
            None,
            "swmm_kw",
            "swmm_dw",
            "py_mw",
            "py_dw",
            "py_ponding",
        }:
            raise ValueError("requested_solver_id is unsupported")
        return self


class CalibrationSubmitRequest(ApiModel):
    network_id: str
    network_revision_id: str
    baseline_simulation_id: str
    candidate_simulation_id: str | None = None
    observation_start: AwareDatetime | None = None
    observation_end: AwareDatetime | None = None
    search_method: Literal["grid", "random"] = "grid"
    seed: int = 0
    iterations: int = Field(default=100, ge=1, le=1000)
    parameter_whitelist: list[str] = Field(default_factory=list)
    split_manifest: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_window(self) -> "CalibrationSubmitRequest":
        if self.observation_start and self.observation_end:
            if self.observation_end <= self.observation_start:
                raise ValueError("observation_end must be later than observation_start")
        return self


class ForecastTransitionRequest(ApiModel):
    action: Literal["acknowledge", "resolve"]
    reason: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Geometry and revision helpers
# ---------------------------------------------------------------------------


def _geometry(value: Any, entity_type: EntityType) -> GeometryDocument:
    if isinstance(value, GeometryDocument):
        geometry = value
    elif isinstance(value, Mapping):
        raw = value.get("geometry", value)
        if isinstance(raw, Mapping) and "type" in raw and "coordinates" in raw:
            raw = {"geometry_geojson": raw}
        try:
            geometry = GeometryDocument.model_validate(raw)
        except (TypeError, ValueError) as exc:
            raise _error(422, "VALIDATION_ERROR", str(exc)) from exc
    else:
        raise _error(422, "VALIDATION_ERROR", "geometry must be an object")
    try:
        geometry.require_entity_geometry(entity_type)
    except (KeyError, ValueError) as exc:
        raise _error(422, "VALIDATION_ERROR", str(exc)) from exc
    return geometry


def _geometry_dict(geometry: GeometryDocument) -> dict[str, Any]:
    return geometry.model_dump(mode="json")


def _entity_feature(entity: Mapping[str, Any], feature_index: int) -> dict[str, Any]:
    geometry = entity["geometry"]
    return {
        "type": "Feature",
        "id": str(entity["id"]),
        "properties": {
            "stable_key": entity["stable_key"],
            "feature_index": feature_index,
            "kind": entity.get("kind"),
            **dict(entity.get("properties", {})),
        },
        "geometry": json.loads(geometry["geometry_geojson"]),
    }


def _manifest_for_revision(revision: Mapping[str, Any]) -> dict[str, Any]:
    target_types = ["nodes", "pipes", "catchments", "pond_cells"]
    target_ids: dict[str, list[str]] = {}
    feature_index: dict[str, list[int]] = {}
    counts: dict[str, int] = {}
    for target_type, collection_name in (
        ("nodes", "nodes"),
        ("pipes", "pipes"),
        ("catchments", "catchments"),
    ):
        entities = sorted(
            revision[collection_name].values(),
            key=lambda item: str(item["stable_key"]).encode("utf-8"),
        )
        counts[target_type] = len(entities)
        feature_index[target_type] = list(range(len(entities)))
        target_ids[target_type] = [str(item["id"]) for item in entities]
    counts["pond_cells"] = 0
    feature_index["pond_cells"] = []
    target_ids["pond_cells"] = []
    manifest = {
        "schema_version": 1,
        "target_types": target_types,
        "counts": counts,
        "feature_index": feature_index,
        "target_ids": target_ids,
        "units": {
            "depth": "m",
            "fill_ratio": "1",
            "flow": "m3/s",
        },
    }
    manifest["content_sha256"] = _fingerprint(manifest)
    return manifest


def _validation_for_revision(revision: Mapping[str, Any]) -> dict[str, Any]:
    nodes = []
    for entity in sorted(
        revision["nodes"].values(), key=lambda item: item["stable_key"]
    ):
        geometry = entity["geometry"]
        geo = json.loads(geometry["geometry_geojson"])
        coordinates = geo["coordinates"]
        nodes.append(
            TopologyNode(
                stable_key=entity["stable_key"],
                longitude=float(coordinates[0]),
                latitude=float(coordinates[1]),
                invert_elevation_m=entity.get("invert_elevation_m"),
                kind=entity.get("kind", "junction"),
            )
        )
    node_keys = {item.stable_key: item for item in nodes}
    pipes = []
    for entity in sorted(
        revision["pipes"].values(), key=lambda item: item["stable_key"]
    ):
        upstream = revision["nodes"].get(entity.get("upstream_node_id"))
        downstream = revision["nodes"].get(entity.get("downstream_node_id"))
        upstream_key = (
            upstream["stable_key"] if upstream else entity.get("upstream_node_key", "")
        )
        downstream_key = (
            downstream["stable_key"]
            if downstream
            else entity.get("downstream_node_key", "")
        )
        pipe = TopologyPipe(
            stable_key=entity["stable_key"],
            upstream_node_key=upstream_key,
            downstream_node_key=downstream_key,
            length_m=float(entity.get("length_m", 1.0)),
        )
        upstream_node = node_keys.get(upstream_key)
        downstream_node = node_keys.get(downstream_key)
        if upstream_node is not None and downstream_node is not None:
            pipes.append(
                TopologyPipe(
                    stable_key=pipe.stable_key,
                    upstream_node_key=pipe.upstream_node_key,
                    downstream_node_key=pipe.downstream_node_key,
                    length_m=pipe.length_m,
                    upstream_invert_elevation_m=entity.get(
                        "upstream_invert_elevation_m", upstream_node.invert_elevation_m
                    ),
                    downstream_invert_elevation_m=entity.get(
                        "downstream_invert_elevation_m",
                        downstream_node.invert_elevation_m,
                    ),
                )
            )
        else:
            pipes.append(pipe)
    if not nodes:
        return {
            "status": "invalid",
            "simulation_ready": False,
            "issues": [
                {
                    "code": "EMPTY_NETWORK",
                    "severity": "error",
                    "entity_type": "network",
                    "entity_key": "",
                    "message": "network has no nodes",
                    "details": {},
                }
            ],
            "node_count": 0,
            "pipe_count": 0,
            "component_count": 0,
            "components": [],
            "cycles": [],
        }
    return validate_topology(nodes, pipes).to_dict()


def _refresh_revision(revision: dict[str, Any]) -> dict[str, Any]:
    manifest = _manifest_for_revision(revision)
    revision["manifest"] = manifest
    revision["counts"] = {
        "nodes": len(revision["nodes"]),
        "pipes": len(revision["pipes"]),
        "catchments": len(revision["catchments"]),
        "ponding_cells": 0,
        "profiles": len(revision.get("profiles", {})),
    }
    bboxes = [
        entity["bbox"]
        for collection in ("nodes", "pipes", "catchments")
        for entity in revision[collection].values()
    ]
    if bboxes:
        revision["bounds"] = [
            min(item[0] for item in bboxes),
            min(item[1] for item in bboxes),
            max(item[2] for item in bboxes),
            max(item[3] for item in bboxes),
        ]
    else:
        revision["bounds"] = [121.30, 31.15, 121.60, 31.32]
    revision["validation"] = _validation_for_revision(revision)
    revision["content_sha256"] = _fingerprint(
        {
            "manifest": manifest,
            "nodes": revision["nodes"],
            "pipes": revision["pipes"],
            "catchments": revision["catchments"],
            "profiles": revision.get("profiles", {}),
        }
    )
    return revision


def _create_revision(
    network: dict[str, Any],
    source: Mapping[str, Any] | None = None,
    *,
    source_kind: str = "crud",
    source_sha256: str | None = None,
) -> dict[str, Any]:
    source_revision = source or {
        "nodes": {},
        "pipes": {},
        "catchments": {},
        "profiles": {},
    }
    revision = {
        "id": str(uuid.uuid4()),
        "network_id": network["id"],
        "revision_no": max(
            [
                item["revision_no"]
                for item in hydraulic_store.revisions.values()
                if item["network_id"] == network["id"]
            ]
            or [0]
        )
        + 1,
        "parent_revision_id": source.get("id") if source else None,
        "source_kind": source_kind,
        "source_sha256": source_sha256,
        "content_sha256": "",
        "parser_version": "hydraulic-api-v1",
        "manifest": {},
        "nodes": deepcopy(source_revision.get("nodes", {})),
        "pipes": deepcopy(source_revision.get("pipes", {})),
        "catchments": deepcopy(source_revision.get("catchments", {})),
        "profiles": deepcopy(source_revision.get("profiles", {})),
        "counts": {},
        "bounds": [],
        "validation": {},
        "created_by": None,
        "created_at": _iso(_now()),
    }
    _refresh_revision(revision)
    hydraulic_store.revisions[revision["id"]] = revision
    network["current_revision_id"] = revision["id"]
    network["updated_at"] = _iso(_now())
    return revision


def _network_or_404(network_id: str) -> dict[str, Any]:
    network = hydraulic_store.networks.get(network_id)
    if network is None:
        raise _error(404, "NETWORK_NOT_FOUND", "network was not found")
    return network


def _revision_or_404(network_id: str, revision_id: str | None = None) -> dict[str, Any]:
    network = _network_or_404(network_id)
    selected = revision_id or network.get("current_revision_id")
    if not selected or selected not in hydraulic_store.revisions:
        raise _error(
            404, "NETWORK_REVISION_NOT_FOUND", "network revision was not found"
        )
    revision = hydraulic_store.revisions[selected]
    if revision["network_id"] != network_id:
        raise _error(
            404, "NETWORK_REVISION_NOT_FOUND", "network revision was not found"
        )
    return revision


def _expected_revision(revision_id: str | None, if_match: str | None) -> str:
    value = revision_id or (if_match.strip('"') if if_match else None)
    if not value:
        raise _error(
            422,
            "VALIDATION_ERROR",
            "expected_revision_id or If-Match is required",
            errors=[
                {
                    "field": "expected_revision_id",
                    "message": "missing expected revision",
                }
            ],
        )
    return _uuid(value, "expected_revision_id")


def _revision_response(revision: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": str(revision["id"]),
        "network_id": str(revision["network_id"]),
        "revision_no": revision["revision_no"],
        "parent_revision_id": revision.get("parent_revision_id"),
        "source_kind": revision["source_kind"],
        "content_sha256": revision["content_sha256"],
        "manifest": revision["manifest"],
        "counts": revision["counts"],
        "bounds": revision["bounds"],
        "simulation_ready": bool(
            revision.get("validation", {}).get("simulation_ready")
        ),
        "validation": revision.get("validation", {}),
        "created_at": revision["created_at"],
    }


def _assert_network_mutable(network: Mapping[str, Any]) -> None:
    if network.get("status") == "archived":
        raise _error(
            409,
            "NETWORK_REVISION_CONFLICT",
            "archived networks cannot be mutated or used for new jobs",
        )


def _network_response(network: Mapping[str, Any]) -> dict[str, Any]:
    current_revision_id = network.get("current_revision_id")
    revision = (
        hydraulic_store.revisions.get(current_revision_id)
        if isinstance(current_revision_id, str)
        else None
    )
    return {
        "schema_version": 1,
        "id": str(network["id"]),
        "code": network["code"],
        "name": network["name"],
        "status": network["status"],
        "crs": network.get("crs", "EPSG:4326"),
        "spatial_backend": network.get("spatial_backend", "portable"),
        "current_revision_id": network.get("current_revision_id"),
        "current_revision": _revision_response(revision) if revision else None,
        "bounds": (
            revision.get("bounds") if revision else [121.30, 31.15, 121.60, 31.32]
        ),
        "counts": revision.get("counts") if revision else {},
        "simulation_ready": bool(
            revision and revision.get("validation", {}).get("simulation_ready")
        ),
        "created_at": network["created_at"],
        "updated_at": network["updated_at"],
    }


# ---------------------------------------------------------------------------
# Idempotency, jobs and imports
# ---------------------------------------------------------------------------


def _idempotent_lookup(
    scope: str,
    key: str,
    user: Mapping[str, Any],
    fingerprint: str,
) -> tuple[dict[str, Any] | None, bool]:
    _require_durable_api_store()
    record_key = (scope, key, _user_id(user))
    record = hydraulic_store.idempotency.get(record_key)
    if record is None:
        return None, False
    try:
        expired = _parse_datetime(record["expires_at"]) <= _now()
    except (KeyError, TypeError, ValueError):
        expired = True
    if expired:
        hydraulic_store.idempotency.pop(record_key, None)
        return None, False
    if record["request_fingerprint"] != fingerprint:
        raise _error(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency-Key was already used with a different request",
        )
    return record, True


def _idempotent_replay(
    scope: str,
    key: str,
    user: Mapping[str, Any],
    fingerprint: str,
) -> dict[str, Any] | None:
    """Return the stored response for a replayed key, or ``None`` for a new one.

    ``_idempotent_lookup`` reports a replay as a (record, flag) pair, which
    forces every route to re-derive "the record is present" from the flag and
    then index a value the checker still considers optional.  Returning the
    response directly makes the two facts one value and keeps the fingerprint
    conflict (409 IDEMPOTENCY_KEY_REUSED) behaviour unchanged.
    """

    record, _replay = _idempotent_lookup(scope, key, user, fingerprint)
    if record is None:
        return None
    return cast(dict[str, Any], record["response"])


def _stage_job_payload(job_id: str, job_type: str, payload: bytes) -> str:
    """Write a job's payload to the worker's staging area and return its hash.

    ``NetworkJobWorker.read_staged_input`` reads
    ``${HYDRAULIC_IMPORT_ROOT}/<job_id><suffix>`` and requires
    ``sha256(file_bytes) == job.request_fingerprint``.  A job created without
    staging that file can never be claimed successfully, so the API stages the
    bytes here and records the *byte* hash as the job fingerprint.

    This is deliberately the payload's own digest, not the idempotency
    envelope hash: idempotency answers "is this the same request?", while the
    worker contract answers "is this the same bytes?".  Collapsing the two made
    the two digests unable to ever agree.
    """

    from app.workers.network_job_worker import staged_input_path

    root = Path(settings.HYDRAULIC_IMPORT_ROOT).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _error(
            503, "IMPORT_STAGING_UNAVAILABLE", f"cannot create staging root: {exc}"
        ) from exc
    path = staged_input_path(root, uuid.UUID(job_id), job_type)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.part")
    try:
        # Write-then-rename so a worker can never observe a partial payload.
        temporary.write_bytes(payload)
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise _error(
            503, "IMPORT_STAGING_UNAVAILABLE", f"cannot stage import payload: {exc}"
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def _idempotent_store(
    scope: str,
    key: str,
    user: Mapping[str, Any],
    fingerprint: str,
    response: dict[str, Any],
    *,
    resource_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    replay = _idempotent_replay(scope, key, user, fingerprint)
    if replay is not None:
        return deepcopy(replay), True
    created = _now()
    record = {
        "scope": scope,
        "idempotency_key": key,
        "user_id": _user_id(user),
        "request_fingerprint": fingerprint,
        "resource_id": resource_id,
        "response": deepcopy(response),
        "created_at": _iso(created),
        "expires_at": _iso(created + timedelta(hours=24)),
    }
    hydraulic_store.idempotency[(scope, key, _user_id(user))] = record
    return deepcopy(response), False


def _job_response(job: Mapping[str, Any]) -> dict[str, Any]:
    response = {
        "schema_version": 1,
        "network_job_id": str(job["id"]),
        "id": str(job["id"]),
        "job_type": job["job_type"],
        "status": job["status"],
        "progress": job.get("progress", 0),
        "phase": job.get("phase", "accepted"),
        "attempt": job.get("attempt", 0),
        "max_attempts": job.get("max_attempts", 3),
        "network_id": job.get("network_id"),
        "created_at": job["created_at"],
        "finished_at": job.get("finished_at"),
        "error_code": job.get("error_code"),
        "error_message": job.get("error_message"),
        "result": deepcopy(job.get("result")),
    }
    if job.get("import_id"):
        response["import_id"] = job["import_id"]
    if job.get("validation_run_id"):
        response["validation_run_id"] = job["validation_run_id"]
    return response


def _new_job(
    job_type: str,
    user: Mapping[str, Any],
    network_id: str | None,
    *,
    request_fingerprint: str,
    idempotency_record_key: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    _require_durable_api_store()
    job: dict[str, Any] = {
        "id": job_id or str(uuid.uuid4()),
        "job_type": job_type,
        "network_id": network_id,
        "status": "queued",
        "progress": 0,
        "phase": "accepted",
        "attempt": 0,
        "max_attempts": 3,
        "request_fingerprint": request_fingerprint,
        "idempotency_scope": idempotency_record_key,
        "created_by": _user_id(user),
        "created_at": _iso(_now()),
        "finished_at": None,
        "error_code": None,
        "error_message": None,
        "result": None,
    }
    hydraulic_store.jobs[job["id"]] = job
    return job


def _safe_upload_name(filename: str | None) -> str:
    if not filename or not _SAFE_FILENAME.fullmatch(filename):
        raise _error(422, "UNSAFE_PATH", "upload filename is not a safe basename")
    if _PATH_FORBIDDEN.search(filename) or ".." in filename:
        raise _error(422, "UNSAFE_PATH", "upload filename contains an unsafe path")
    return filename


async def _read_upload(
    file: UploadFile, request: Request | None = None
) -> tuple[str, bytes]:
    raw_filename = (
        request.scope.get("hydraulic_raw_upload_filename")
        if request is not None
        and request.scope.get("hydraulic_raw_upload_filename_captured") is True
        else None
    )
    name = _safe_upload_name(
        cast(str, raw_filename) if raw_filename is not None else file.filename
    )
    if not name.lower().endswith((".inp", ".geojson")):
        raise _error(
            415, "UNSUPPORTED_IMPORT_FORMAT", "only .inp and .geojson are accepted"
        )
    chunks: list[bytes] = []
    size = 0
    limit = settings.NETWORK_INPUT_MAX_BYTES
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise _error(413, "INPUT_TOO_LARGE", "input exceeds the 20 MiB budget")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise _error(400, "MALFORMED_REQUEST", "uploaded file is empty")
    if data.startswith((b"PK\x03\x04", b"\x7fELF", b"\xca\xfe\xba\xbe")):
        raise _error(
            415, "UNSUPPORTED_IMPORT_FORMAT", "binary/archive content is not accepted"
        )
    try:
        data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise _error(
            422, "INVALID_ENCODING", "uploaded file is not UTF-8 text"
        ) from exc
    return name, data


def _preflight_import(filename: str, data: bytes) -> dict[str, Any]:
    if filename.lower().endswith(".inp"):
        try:
            parsed = parse_swmm(
                data,
                strict=False,
                filename=filename,
                max_bytes=settings.NETWORK_INPUT_MAX_BYTES,
            )
        except SwmmParseError as exc:
            status = 413 if exc.code == "INPUT_TOO_LARGE" else 422
            raise _error(
                status,
                (
                    exc.code
                    if exc.code in {"INPUT_TOO_LARGE", "UNSAFE_PATH"}
                    else "INVALID_INP"
                ),
                str(exc),
                errors=[
                    {
                        "field": "file",
                        "message": exc.message,
                        "details": {"line": exc.line, "section": exc.section},
                    }
                ],
            ) from exc
        errors = [issue for issue in parsed.issues if issue.severity == "error"]
        if errors:
            first = errors[0]
            raise _error(
                422,
                (
                    first.code
                    if first.code in {"UNSAFE_PATH", "CRS_UNKNOWN"}
                    else "INVALID_INP"
                ),
                first.message,
                errors=[
                    {
                        "field": "file",
                        "message": first.message,
                        "details": first.details,
                    }
                ],
            )
        return {"format": "inp", "parsed": parsed, "counts": parsed.counts}
    try:
        document = json.loads(
            data.decode("utf-8-sig"),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number {value}")
            ),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as exc:
        raise _error(
            422, "INVALID_GEOJSON", "uploaded GeoJSON is not valid finite JSON"
        ) from exc
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise _error(422, "INVALID_GEOJSON", "GeoJSON must be a FeatureCollection")
    features = document.get("features")
    if not isinstance(features, list):
        raise _error(422, "INVALID_GEOJSON", "GeoJSON features must be an array")
    stable_keys: set[str] = set()
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise _error(422, "INVALID_GEOJSON", f"feature {index} is invalid")
        try:
            GeometryDocument.model_validate(
                {"geometry_geojson": feature.get("geometry")}
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise _error(
                422, "INVALID_GEOJSON", f"feature {index} geometry is invalid"
            ) from exc
        properties = feature.get("properties", {})
        if not isinstance(properties, dict):
            raise _error(
                422, "INVALID_GEOJSON", f"feature {index} properties are invalid"
            )
        feature_id = feature.get("id")
        property_key = properties.get("stable_key")
        if property_key is not None and not isinstance(property_key, str):
            raise _error(
                422, "INVALID_GEOJSON", f"feature {index} stable_key is invalid"
            )
        if feature_id is not None and property_key is not None:
            if str(feature_id) != property_key:
                raise _error(
                    422,
                    "INVALID_GEOJSON",
                    f"feature {index} id and stable_key disagree",
                )
        stable_key = str(
            property_key if property_key is not None else feature_id or ""
        ).strip()
        if not stable_key:
            raise _error(422, "INVALID_GEOJSON", f"feature {index} requires stable_key")
        normalized_key = stable_key.casefold()
        if normalized_key in stable_keys:
            raise _error(
                422,
                "INVALID_GEOJSON",
                f"feature {index} duplicates stable_key {stable_key!r}",
            )
        stable_keys.add(normalized_key)
    entity_type = str(document.get("properties", {}).get("entity_type", "node"))
    if entity_type not in {"node", "pipe", "catchment"}:
        entity_type = "node"
    return {
        "format": "geojson",
        "document": document,
        "entity_type": entity_type,
        "counts": {"features": len(features)},
    }


def _entity_from_import_feature(
    feature: Mapping[str, Any], entity_type: str
) -> dict[str, Any]:
    geometry = _geometry(
        feature.get("geometry"),
        cast(EntityType, entity_type),
    )
    properties = dict(feature.get("properties") or {})
    stable_key = str(properties.get("stable_key", feature.get("id", "")))
    if entity_type == "node":
        return {
            "id": str(uuid.uuid4()),
            "stable_key": stable_key,
            "kind": str(properties.get("kind", "junction")),
            "geometry": _geometry_dict(geometry),
            "bbox": list(geometry.bbox.values),
            "invert_elevation_m": properties.get("invert_elevation_m"),
            "rim_elevation_m": properties.get("rim_elevation_m"),
            "max_depth_m": properties.get("max_depth_m"),
            "properties": properties,
        }
    if entity_type == "pipe":
        coordinates = json.loads(geometry.geometry_geojson)["coordinates"]
        upstream = properties.get("upstream_node_id")
        downstream = properties.get("downstream_node_id")
        return {
            "id": str(uuid.uuid4()),
            "stable_key": stable_key,
            "upstream_node_id": str(upstream) if upstream else None,
            "downstream_node_id": str(downstream) if downstream else None,
            "nominal_diameter_m": float(properties.get("nominal_diameter_m", 0.3)),
            "length_m": float(properties.get("length_m", 1.0)),
            "roughness_manning_n": float(properties.get("roughness_manning_n", 0.013)),
            "geometry": _geometry_dict(geometry),
            "bbox": list(geometry.bbox.values),
            "properties": properties,
            "coordinates": coordinates,
        }
    return {
        "id": str(uuid.uuid4()),
        "stable_key": stable_key,
        "area_m2": float(properties.get("area_m2", 1.0)),
        "outfall_node_id": properties.get("outfall_node_id"),
        "geometry": _geometry_dict(geometry),
        "bbox": list(geometry.bbox.values),
        "properties": properties,
    }


def _import_entities(preflight: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {
        "nodes": {},
        "pipes": {},
        "catchments": {},
        "profiles": {},
    }
    if preflight["format"] == "inp":
        parsed = preflight["parsed"]
        coordinates = {item.name.casefold(): item for item in parsed.coordinates}
        for node in parsed.nodes:
            coordinate = coordinates.get(node.name.casefold())
            if coordinate is None:
                raise _error(
                    422, "MISSING_COORDINATES", f"missing coordinates for {node.name}"
                )
            geometry = _geometry(
                {
                    "geometry_geojson": {
                        "type": "Point",
                        "coordinates": [coordinate.x, coordinate.y],
                    }
                },
                "node",
            )
            entity = {
                "id": str(uuid.uuid4()),
                "stable_key": node.name,
                "kind": node.kind,
                "geometry": _geometry_dict(geometry),
                "bbox": list(geometry.bbox.values),
                "invert_elevation_m": node.elevation,
                "rim_elevation_m": None,
                "max_depth_m": getattr(node, "max_depth", None),
                "properties": {},
            }
            entities["nodes"][entity["id"]] = entity
        by_key = {
            item["stable_key"].casefold(): item["id"]
            for item in entities["nodes"].values()
        }
        for pipe in parsed.conduits:
            upstream = by_key.get(pipe.from_node.casefold())
            downstream = by_key.get(pipe.to_node.casefold())
            if not upstream or not downstream:
                raise _error(
                    422, "DANGLING_NODE", f"pipe {pipe.name} references a missing node"
                )
            first = entities["nodes"][upstream]["geometry"]
            second = entities["nodes"][downstream]["geometry"]
            line_geo = {
                "type": "LineString",
                "coordinates": [
                    json.loads(first["geometry_geojson"])["coordinates"],
                    json.loads(second["geometry_geojson"])["coordinates"],
                ],
            }
            geometry = _geometry({"geometry_geojson": line_geo}, "pipe")
            entity = {
                "id": str(uuid.uuid4()),
                "stable_key": pipe.name,
                "upstream_node_id": upstream,
                "downstream_node_id": downstream,
                "nominal_diameter_m": 0.3,
                "length_m": pipe.length,
                "roughness_manning_n": pipe.roughness,
                "geometry": _geometry_dict(geometry),
                "bbox": list(geometry.bbox.values),
                "properties": {},
            }
            entities["pipes"][entity["id"]] = entity
    else:
        for feature in preflight["document"].get("features", []):
            entity = _entity_from_import_feature(feature, preflight["entity_type"])
            collection = {"node": "nodes", "pipe": "pipes", "catchment": "catchments"}[
                preflight["entity_type"]
            ]
            entities[collection][entity["id"]] = entity
    return entities


def _network_from_entities(
    name: str,
    code: str,
    entities: Mapping[str, Mapping[str, Any]],
    *,
    source_kind: str,
    source_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    network: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "code": code,
        "name": name,
        "status": "active",
        "crs": "EPSG:4326",
        "spatial_backend": "portable",
        "current_revision_id": None,
        "created_at": _iso(_now()),
        "updated_at": _iso(_now()),
    }
    hydraulic_store.networks[network["id"]] = network
    revision = _create_revision(
        network,
        {
            "nodes": dict(entities.get("nodes", {})),
            "pipes": dict(entities.get("pipes", {})),
            "catchments": dict(entities.get("catchments", {})),
            "profiles": dict(entities.get("profiles", {})),
        },
        source_kind=source_kind,
        source_sha256=source_sha256,
    )
    return network, revision


# ---------------------------------------------------------------------------
# Network routes
# ---------------------------------------------------------------------------


@router.get("/networks", response_model=HydraulicResponse)
async def list_networks(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    with hydraulic_store._lock:
        values = list(hydraulic_store.networks.values())
    if status_filter:
        values = [item for item in values if item["status"] == status_filter]
    values.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    page, next_cursor = _page(values, cursor, limit)
    return {
        "items": [_network_response(item) for item in page],
        "next_cursor": next_cursor,
    }


@router.post("/networks", response_model=HydraulicResponse, status_code=201)
async def create_network(
    request: NetworkCreateRequest,
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    fingerprint = _fingerprint(request.model_dump(mode="json"))
    replay = _idempotent_replay("network.create", key, user, fingerprint)
    if replay is not None:
        return replay
    with hydraulic_store._lock:
        if any(
            item["code"] == request.code for item in hydraulic_store.networks.values()
        ):
            raise _error(
                409, "NETWORK_REVISION_CONFLICT", "network code already exists"
            )
        network: dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "code": request.code,
            "name": request.name,
            "status": request.status,
            "crs": request.crs,
            "spatial_backend": request.spatial_backend,
            "current_revision_id": None,
            "created_at": _iso(_now()),
            "updated_at": _iso(_now()),
        }
        hydraulic_store.networks[network["id"]] = network
        _create_revision(network, source_kind="crud")
        body = _network_response(network)
    body, _ = _idempotent_store(
        "network.create", key, user, fingerprint, body, resource_id=body["id"]
    )
    return body


@router.post("/networks/import", response_model=HydraulicResponse, status_code=202)
async def import_network(
    file: UploadFile = File(...),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
    request: Request = cast(Request, None),
) -> dict[str, Any]:
    filename, data = await _read_upload(file, request)
    fingerprint = _fingerprint(
        {"filename": filename, "sha256": hashlib.sha256(data).hexdigest()}
    )
    replay = _idempotent_replay("network.import", key, user, fingerprint)
    if replay is not None:
        return replay
    preflight = _preflight_import(filename, data)
    # Parsing/preflight is bounded and side-effect free.  The durable worker
    # owns materialization, revision publication, and the terminal transition.
    import_id = str(uuid.uuid4())
    # The staged filename is keyed by job id, so the id is minted first and the
    # payload hashed before the job row is built.  The job's
    # request_fingerprint must match the staged file's bytes, not the
    # idempotency envelope, or the worker fails the attempt closed.
    job_id = str(uuid.uuid4())
    payload_sha256 = _stage_job_payload(job_id, "import", data)
    job = _new_job(
        "import",
        user,
        None,
        request_fingerprint=payload_sha256,
        idempotency_record_key=key,
        job_id=job_id,
    )
    job.update(
        {
            "import_id": import_id,
            "source_filename": filename,
            "source_sha256": payload_sha256,
            "preflight": {
                "format": preflight["format"],
                "counts": preflight.get("counts", {}),
            },
        }
    )
    hydraulic_store.imports[import_id] = {
        "id": import_id,
        "job_id": job["id"],
        "network_id": None,
        "filename": filename,
        "source_sha256": payload_sha256,
        "status": "queued",
        "counts": preflight.get("counts", {}),
        "validation": None,
        "committed": False,
    }
    response = _job_response(job)
    _idempotent_store(
        "network.import", key, user, fingerprint, response, resource_id=job["id"]
    )
    return response


@router.get("/networks/{network_id}", response_model=HydraulicResponse)
async def get_network(
    network_id: str,
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    return _network_response(_network_or_404(network_id))


@router.patch("/networks/{network_id}", response_model=HydraulicResponse)
async def patch_network(
    network_id: str,
    request: NetworkPatchRequest,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if current["id"] != network["current_revision_id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "network revision changed")
    expected = _expected_revision(
        request.expected_revision_id or expected_revision_id, if_match
    )
    if expected != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    _assert_network_mutable(network)
    if request.status == "archived" and user.get("role") != RoleEnum.admin.value:
        raise _error(403, "FORBIDDEN", "only an administrator can archive a network")
    fingerprint = _fingerprint(
        {"network_id": network_id, "request": request.model_dump(mode="json")}
    )
    replay = _idempotent_replay("network.patch", key, user, fingerprint)
    if replay is not None:
        return replay
    for field, value in request.model_dump(
        exclude_none=True, exclude={"expected_revision_id"}
    ).items():
        network[field] = value
    _create_revision(network, current, source_kind="crud")
    body = _network_response(network)
    _idempotent_store(
        "network.patch", key, user, fingerprint, body, resource_id=network_id
    )
    return body


@router.delete("/networks/{network_id}", status_code=204, response_model=None)
async def archive_network(
    network_id: str,
    _: dict[str, Any] = Depends(_admin),
) -> None:
    network = _network_or_404(network_id)
    network["status"] = "archived"
    network["updated_at"] = _iso(_now())
    return None


@router.get("/networks/{network_id}/revisions", response_model=HydraulicResponse)
async def list_revisions(
    network_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    _network_or_404(network_id)
    with hydraulic_store._lock:
        values = sorted(
            [
                item
                for item in hydraulic_store.revisions.values()
                if item["network_id"] == network_id
            ],
            key=lambda item: item["revision_no"],
        )
    page, next_cursor = _page(values, cursor, limit)
    return {
        "items": [_revision_response(item) for item in page],
        "next_cursor": next_cursor,
    }


@router.get("/networks/{network_id}/target-manifest", response_model=HydraulicResponse)
async def target_manifest(
    network_id: str,
    revision_id: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    revision = _revision_or_404(network_id, revision_id)
    return {
        "schema_version": 1,
        "network_id": network_id,
        "network_revision_id": revision["id"],
        "target_manifest_id": revision["id"],
        "target_manifest_sha256": revision["manifest"]["content_sha256"],
        "target_types": revision["manifest"]["target_types"],
        "counts": revision["manifest"]["counts"],
        "feature_index": revision["manifest"]["feature_index"],
        "target_ids": revision["manifest"]["target_ids"],
        "units": revision["manifest"].get("units", {}),
    }


# ---------------------------------------------------------------------------
# Revision and entity CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/networks/{network_id}/revisions",
    response_model=HydraulicResponse,
    status_code=201,
)
async def clone_revision(
    network_id: str,
    revision_id: str | None = Query(default=None),
    expected_revision_id: str | None = Query(default=None),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    source_id = revision_id or expected_revision_id or network["current_revision_id"]
    source = _revision_or_404(network_id, _uuid(source_id, "revision_id"))
    expected = _expected_revision(expected_revision_id, None)
    if expected != source["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {"network_id": network_id, "source_revision_id": source["id"]}
    )
    replay = _idempotent_replay("network.revision.clone", key, user, fingerprint)
    if replay is not None:
        return replay
    revision = _create_revision(network, source, source_kind="crud")
    body = _revision_response(revision)
    _idempotent_store(
        "network.revision.clone",
        key,
        user,
        fingerprint,
        body,
        resource_id=revision["id"],
    )
    return body


@router.post(
    "/networks/{network_id}/revisions/{revision_id}/activate",
    response_model=HydraulicResponse,
)
async def activate_revision(
    network_id: str,
    revision_id: str,
    expected_current_revision_id: str | None = Query(default=None),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    revision = _revision_or_404(network_id, _uuid(revision_id, "revision_id"))
    if not expected_current_revision_id:
        raise _error(
            422, "VALIDATION_ERROR", "expected_current_revision_id is required"
        )
    if network["current_revision_id"] != _uuid(
        expected_current_revision_id, "expected_current_revision_id"
    ):
        raise _error(409, "NETWORK_REVISION_CONFLICT", "current revision changed")
    fingerprint = _fingerprint({"network_id": network_id, "revision_id": revision_id})
    replay = _idempotent_replay("network.revision.activate", key, user, fingerprint)
    if replay is not None:
        return replay
    network["current_revision_id"] = revision["id"]
    network["updated_at"] = _iso(_now())
    body = _network_response(network)
    _idempotent_store(
        "network.revision.activate",
        key,
        user,
        fingerprint,
        body,
        resource_id=network_id,
    )
    return body


def _entity_response(entity: Mapping[str, Any], entity_type: str) -> dict[str, Any]:
    response = {
        "schema_version": 1,
        "id": str(entity["id"]),
        "stable_key": entity["stable_key"],
        "geometry": entity["geometry"],
        "geometry_wkt": entity["geometry"].get("geometry_wkt"),
        "bbox": entity["bbox"],
        "properties": deepcopy(entity.get("properties", {})),
    }
    if entity_type == "nodes":
        response.update(
            {
                "kind": entity.get("kind"),
                "invert_elevation_m": entity.get("invert_elevation_m"),
                "rim_elevation_m": entity.get("rim_elevation_m"),
                "max_depth_m": entity.get("max_depth_m"),
            }
        )
    elif entity_type == "pipes":
        response.update(
            {
                "upstream_node_id": entity.get("upstream_node_id"),
                "downstream_node_id": entity.get("downstream_node_id"),
                "nominal_diameter_m": entity.get("nominal_diameter_m"),
                "length_m": entity.get("length_m"),
                "roughness_manning_n": entity.get("roughness_manning_n"),
            }
        )
    else:
        response.update(
            {
                "area_m2": entity.get("area_m2"),
                "outfall_node_id": entity.get("outfall_node_id"),
            }
        )
    return response


def _list_entities(
    network_id: str,
    entity_type: str,
    *,
    revision_id: str | None,
    bbox: str | None,
    stable_key: str | None,
    limit: int,
    cursor: str | None,
) -> dict[str, Any]:
    revision = _revision_or_404(network_id, revision_id)
    values = list(revision[entity_type].values())
    if stable_key:
        values = [item for item in values if item["stable_key"] == stable_key]
    if bbox:
        try:
            box = parse_query_bbox(bbox)
        except (TypeError, ValueError) as exc:
            raise _error(422, "VALIDATION_ERROR", str(exc)) from exc
        geometry_type = {"nodes": "node", "pipes": "pipe", "catchments": "catchment"}[
            entity_type
        ]
        report = filter_rows_by_bbox(
            [
                {"id": item["id"], "geometry_wkt": item["geometry"]["geometry_wkt"]}
                for item in values
            ],
            box,
            entity_type=cast(EntityType, geometry_type),
        )
        by_id = {item["id"]: item for item in values}
        values = [by_id[item["id"]] for item in report.matches]
    values.sort(key=lambda item: str(item["stable_key"]).encode("utf-8"))
    page, next_cursor = _page(values, cursor, limit)
    return {
        "items": [_entity_response(item, entity_type) for item in page],
        "next_cursor": next_cursor,
        "network_id": network_id,
        "network_revision_id": revision["id"],
    }


@router.get("/networks/{network_id}/nodes", response_model=HydraulicResponse)
async def list_nodes(
    network_id: str,
    revision_id: str | None = Query(default=None),
    bbox: str | None = Query(default=None),
    stable_key: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=200),
    cursor: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    return _list_entities(
        network_id,
        "nodes",
        revision_id=revision_id,
        bbox=bbox,
        stable_key=stable_key,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/networks/{network_id}/nodes", response_model=HydraulicResponse, status_code=201
)
async def create_node(
    network_id: str,
    request: NetworkNodeCreate,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    expected = _expected_revision(expected_revision_id, if_match)
    if current["id"] != expected:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {"network_id": network_id, "request": request.model_dump(mode="json")}
    )
    replay = _idempotent_replay("network.node.create", key, user, fingerprint)
    if replay is not None:
        return replay
    geometry = _geometry(request.geometry, "node")
    entity: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "stable_key": request.stable_key,
        "kind": request.kind.value if hasattr(request.kind, "value") else request.kind,
        "geometry": _geometry_dict(geometry),
        "bbox": list(geometry.bbox.values),
        "invert_elevation_m": request.invert_elevation_m,
        "rim_elevation_m": request.rim_elevation_m,
        "max_depth_m": request.max_depth_m,
        "properties": deepcopy(request.properties),
    }
    if any(
        item["stable_key"].casefold() == entity["stable_key"].casefold()
        for item in current["nodes"].values()
    ):
        raise _error(409, "NETWORK_REVISION_CONFLICT", "node stable_key already exists")
    revision = _create_revision(
        network,
        {**current, "nodes": {**current["nodes"], entity["id"]: entity}},
        source_kind="crud",
    )
    body = {
        "revision": _revision_response(revision),
        "entity": _entity_response(entity, "nodes"),
    }
    _idempotent_store(
        "network.node.create", key, user, fingerprint, body, resource_id=entity["id"]
    )
    return body


@router.get("/networks/{network_id}/nodes/{node_id}", response_model=HydraulicResponse)
async def get_node(
    network_id: str, node_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    revision = _revision_or_404(network_id)
    entity = revision["nodes"].get(node_id)
    if entity is None:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "node was not found")
    return _entity_response(entity, "nodes")


@router.patch(
    "/networks/{network_id}/nodes/{node_id}", response_model=HydraulicResponse
)
async def patch_node(
    network_id: str,
    node_id: str,
    request: NodePatchRequest,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "node_id": node_id,
            "request": request.model_dump(mode="json"),
            "expected_revision_id": current["id"],
        }
    )
    replay = _idempotent_replay("network.node.patch", key, user, fingerprint)
    if replay is not None:
        return replay
    entity = deepcopy(current["nodes"].get(node_id))
    if entity is None:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "node was not found")
    for field, value in request.model_dump(exclude_none=True).items():
        if field == "geometry":
            geometry = _geometry(value, "node")
            entity["geometry"] = _geometry_dict(geometry)
            entity["bbox"] = list(geometry.bbox.values)
        else:
            entity[field] = value
    revision = _create_revision(
        network,
        {**current, "nodes": {**current["nodes"], node_id: entity}},
        source_kind="crud",
    )
    body = {
        "revision": _revision_response(revision),
        "entity": _entity_response(entity, "nodes"),
    }
    _idempotent_store(
        "network.node.patch", key, user, fingerprint, body, resource_id=node_id
    )
    return body


@router.delete(
    "/networks/{network_id}/nodes/{node_id}", response_model=HydraulicResponse
)
async def delete_node(
    network_id: str,
    node_id: str,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "node_id": node_id,
            "expected_revision_id": current["id"],
            "action": "delete",
        }
    )
    replay = _idempotent_replay("network.node.delete", key, user, fingerprint)
    if replay is not None:
        return replay
    if node_id not in current["nodes"]:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "node was not found")
    nodes = dict(current["nodes"])
    nodes.pop(node_id)
    revision = _create_revision(
        network, {**current, "nodes": nodes}, source_kind="crud"
    )
    body = {"revision": _revision_response(revision)}
    _idempotent_store(
        "network.node.delete", key, user, fingerprint, body, resource_id=node_id
    )
    return body


@router.get("/networks/{network_id}/pipes", response_model=HydraulicResponse)
async def list_pipes(
    network_id: str,
    revision_id: str | None = Query(default=None),
    bbox: str | None = Query(default=None),
    stable_key: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=200),
    cursor: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    return _list_entities(
        network_id,
        "pipes",
        revision_id=revision_id,
        bbox=bbox,
        stable_key=stable_key,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/networks/{network_id}/pipes", response_model=HydraulicResponse, status_code=201
)
async def create_pipe(
    network_id: str,
    request: PipeSegmentCreate,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "request": request.model_dump(mode="json"),
            "expected_revision_id": current["id"],
        }
    )
    replay = _idempotent_replay("network.pipe.create", key, user, fingerprint)
    if replay is not None:
        return replay
    upstream_id = str(request.upstream_node_id)
    downstream_id = str(request.downstream_node_id)
    if upstream_id not in current["nodes"] or downstream_id not in current["nodes"]:
        raise _error(
            422, "DANGLING_NODE", "pipe endpoints must belong to the current revision"
        )
    geometry = _geometry(request.geometry, "pipe")
    entity: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "stable_key": request.stable_key,
        "upstream_node_id": str(request.upstream_node_id),
        "downstream_node_id": str(request.downstream_node_id),
        "nominal_diameter_m": request.nominal_diameter_m,
        "length_m": request.length_m,
        "roughness_manning_n": request.roughness_manning_n,
        "geometry": _geometry_dict(geometry),
        "bbox": list(geometry.bbox.values),
        "properties": deepcopy(request.properties),
    }
    if any(
        item["stable_key"].casefold() == entity["stable_key"].casefold()
        for item in current["pipes"].values()
    ):
        raise _error(409, "NETWORK_REVISION_CONFLICT", "pipe stable_key already exists")
    revision = _create_revision(
        network,
        {**current, "pipes": {**current["pipes"], entity["id"]: entity}},
        source_kind="crud",
    )
    body = {
        "revision": _revision_response(revision),
        "entity": _entity_response(entity, "pipes"),
    }
    _idempotent_store(
        "network.pipe.create", key, user, fingerprint, body, resource_id=entity["id"]
    )
    return body


@router.get("/networks/{network_id}/pipes/{pipe_id}", response_model=HydraulicResponse)
async def get_pipe(
    network_id: str, pipe_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    revision = _revision_or_404(network_id)
    entity = revision["pipes"].get(pipe_id)
    if entity is None:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "pipe was not found")
    return _entity_response(entity, "pipes")


@router.patch(
    "/networks/{network_id}/pipes/{pipe_id}", response_model=HydraulicResponse
)
async def patch_pipe(
    network_id: str,
    pipe_id: str,
    request: PipePatchRequest,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "pipe_id": pipe_id,
            "request": request.model_dump(mode="json"),
            "expected_revision_id": current["id"],
        }
    )
    replay = _idempotent_replay("network.pipe.patch", key, user, fingerprint)
    if replay is not None:
        return replay
    entity = deepcopy(current["pipes"].get(pipe_id))
    if entity is None:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "pipe was not found")
    for field, value in request.model_dump(exclude_none=True).items():
        if field == "geometry":
            geometry = _geometry(value, "pipe")
            entity["geometry"] = _geometry_dict(geometry)
            entity["bbox"] = list(geometry.bbox.values)
        else:
            entity[field] = value
    revision = _create_revision(
        network,
        {**current, "pipes": {**current["pipes"], pipe_id: entity}},
        source_kind="crud",
    )
    body = {
        "revision": _revision_response(revision),
        "entity": _entity_response(entity, "pipes"),
    }
    _idempotent_store(
        "network.pipe.patch", key, user, fingerprint, body, resource_id=pipe_id
    )
    return body


@router.delete(
    "/networks/{network_id}/pipes/{pipe_id}", response_model=HydraulicResponse
)
async def delete_pipe(
    network_id: str,
    pipe_id: str,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "pipe_id": pipe_id,
            "expected_revision_id": current["id"],
            "action": "delete",
        }
    )
    replay = _idempotent_replay("network.pipe.delete", key, user, fingerprint)
    if replay is not None:
        return replay
    if pipe_id not in current["pipes"]:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "pipe was not found")
    pipes = dict(current["pipes"])
    pipes.pop(pipe_id)
    revision = _create_revision(
        network, {**current, "pipes": pipes}, source_kind="crud"
    )
    body = {"revision": _revision_response(revision)}
    _idempotent_store(
        "network.pipe.delete", key, user, fingerprint, body, resource_id=pipe_id
    )
    return body


@router.get("/networks/{network_id}/catchments", response_model=HydraulicResponse)
async def list_catchments(
    network_id: str,
    revision_id: str | None = Query(default=None),
    bbox: str | None = Query(default=None),
    stable_key: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=200),
    cursor: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    return _list_entities(
        network_id,
        "catchments",
        revision_id=revision_id,
        bbox=bbox,
        stable_key=stable_key,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/networks/{network_id}/catchments",
    response_model=HydraulicResponse,
    status_code=201,
)
async def create_catchment(
    network_id: str,
    request: CatchmentCreate,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "request": request.model_dump(mode="json"),
            "expected_revision_id": current["id"],
        }
    )
    replay = _idempotent_replay("network.catchment.create", key, user, fingerprint)
    if replay is not None:
        return replay
    outfall_id = (
        str(request.outfall_node_id) if request.outfall_node_id is not None else None
    )
    if outfall_id is not None and outfall_id not in current["nodes"]:
        raise _error(
            422, "DANGLING_NODE", "outfall node must belong to the current revision"
        )
    geometry = _geometry(request.geometry, "catchment")
    entity: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "stable_key": request.stable_key,
        "area_m2": request.area_m2,
        "outfall_node_id": outfall_id,
        "geometry": _geometry_dict(geometry),
        "bbox": list(geometry.bbox.values),
        "properties": deepcopy(request.properties),
    }
    revision = _create_revision(
        network,
        {**current, "catchments": {**current["catchments"], entity["id"]: entity}},
        source_kind="crud",
    )
    body = {
        "revision": _revision_response(revision),
        "entity": _entity_response(entity, "catchments"),
    }
    _idempotent_store(
        "network.catchment.create",
        key,
        user,
        fingerprint,
        body,
        resource_id=entity["id"],
    )
    return body


@router.get(
    "/networks/{network_id}/catchments/{catchment_id}", response_model=HydraulicResponse
)
async def get_catchment(
    network_id: str, catchment_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    revision = _revision_or_404(network_id)
    entity = revision["catchments"].get(catchment_id)
    if entity is None:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "catchment was not found")
    return _entity_response(entity, "catchments")


@router.patch(
    "/networks/{network_id}/catchments/{catchment_id}", response_model=HydraulicResponse
)
async def patch_catchment(
    network_id: str,
    catchment_id: str,
    request: CatchmentPatchRequest,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "catchment_id": catchment_id,
            "request": request.model_dump(mode="json"),
            "expected_revision_id": current["id"],
        }
    )
    replay = _idempotent_replay("network.catchment.patch", key, user, fingerprint)
    if replay is not None:
        return replay
    entity = deepcopy(current["catchments"].get(catchment_id))
    if entity is None:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "catchment was not found")
    for field, value in request.model_dump(exclude_none=True).items():
        if field == "geometry":
            geometry = _geometry(value, "catchment")
            entity["geometry"] = _geometry_dict(geometry)
            entity["bbox"] = list(geometry.bbox.values)
        else:
            entity[field] = value
    revision = _create_revision(
        network,
        {**current, "catchments": {**current["catchments"], catchment_id: entity}},
        source_kind="crud",
    )
    body = {
        "revision": _revision_response(revision),
        "entity": _entity_response(entity, "catchments"),
    }
    _idempotent_store(
        "network.catchment.patch",
        key,
        user,
        fingerprint,
        body,
        resource_id=catchment_id,
    )
    return body


@router.delete(
    "/networks/{network_id}/catchments/{catchment_id}", response_model=HydraulicResponse
)
async def delete_catchment(
    network_id: str,
    catchment_id: str,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "catchment_id": catchment_id,
            "expected_revision_id": current["id"],
            "action": "delete",
        }
    )
    replay = _idempotent_replay("network.catchment.delete", key, user, fingerprint)
    if replay is not None:
        return replay
    if catchment_id not in current["catchments"]:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "catchment was not found")
    catchments = dict(current["catchments"])
    catchments.pop(catchment_id)
    revision = _create_revision(
        network, {**current, "catchments": catchments}, source_kind="crud"
    )
    body = {"revision": _revision_response(revision)}
    _idempotent_store(
        "network.catchment.delete",
        key,
        user,
        fingerprint,
        body,
        resource_id=catchment_id,
    )
    return body


@router.get(
    "/networks/{network_id}/pipes/{pipe_id}/elevation-profile",
    response_model=HydraulicResponse,
)
async def get_elevation_profile(
    network_id: str, pipe_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    revision = _revision_or_404(network_id)
    if pipe_id not in revision["pipes"]:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "pipe was not found")
    profile = revision.get("profiles", {}).get(pipe_id)
    if profile is None:
        raise _error(
            404, "ELEVATION_PROFILE_NOT_FOUND", "elevation profile was not found"
        )
    return {"schema_version": 1, "pipe_id": pipe_id, **profile}


@router.post(
    "/networks/{network_id}/pipes/{pipe_id}/elevation-profile",
    response_model=HydraulicResponse,
    status_code=201,
)
async def put_elevation_profile(
    network_id: str,
    pipe_id: str,
    request: ElevationProfileRequest,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "pipe_id": pipe_id,
            "request": request.model_dump(mode="json"),
            "expected_revision_id": current["id"],
        }
    )
    replay = _idempotent_replay("network.profile.put", key, user, fingerprint)
    if replay is not None:
        return replay
    if pipe_id not in current["pipes"]:
        raise _error(404, "NETWORK_ENTITY_NOT_FOUND", "pipe was not found")
    profiles = dict(current.get("profiles", {}))
    profiles[pipe_id] = request.model_dump(mode="json")
    revision = _create_revision(
        network, {**current, "profiles": profiles}, source_kind="crud"
    )
    body = {
        "revision": _revision_response(revision),
        "profile": {"pipe_id": pipe_id, **request.model_dump(mode="json")},
    }
    _idempotent_store(
        "network.profile.put", key, user, fingerprint, body, resource_id=pipe_id
    )
    return body


@router.delete(
    "/networks/{network_id}/pipes/{pipe_id}/elevation-profile",
    response_model=HydraulicResponse,
)
async def delete_elevation_profile(
    network_id: str,
    pipe_id: str,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    current = _revision_or_404(network_id)
    if _expected_revision(expected_revision_id, if_match) != current["id"]:
        raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "pipe_id": pipe_id,
            "expected_revision_id": current["id"],
            "action": "delete",
        }
    )
    replay = _idempotent_replay("network.profile.delete", key, user, fingerprint)
    if replay is not None:
        return replay
    profiles = dict(current.get("profiles", {}))
    if pipe_id not in profiles:
        raise _error(
            404, "ELEVATION_PROFILE_NOT_FOUND", "elevation profile was not found"
        )
    profiles.pop(pipe_id)
    revision = _create_revision(
        network, {**current, "profiles": profiles}, source_kind="crud"
    )
    body = {"revision": _revision_response(revision)}
    _idempotent_store(
        "network.profile.delete", key, user, fingerprint, body, resource_id=pipe_id
    )
    return body


# ---------------------------------------------------------------------------
# Map, validation, export and seed
# ---------------------------------------------------------------------------


@router.get("/networks/{network_id}/map-data", response_model=HydraulicResponse)
async def map_data(
    network_id: str,
    bbox: str | None = Query(default=None),
    zoom: int = Query(default=12, ge=0, le=24),
    layers: str = Query(default="nodes,pipes,catchments,ponding"),
    simplify: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=2000),
    cursor: str | None = Query(default=None),
    revision_id: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    revision = _revision_or_404(network_id, revision_id)
    requested_layers = {item.strip() for item in layers.split(",") if item.strip()}
    allowed = {"nodes", "pipes", "catchments", "ponding"}
    if not requested_layers or not requested_layers <= allowed:
        raise _error(422, "VALIDATION_ERROR", "layers contains an unsupported layer")
    try:
        box = parse_query_bbox(bbox) if bbox else None
    except (TypeError, ValueError) as exc:
        raise _error(422, "VALIDATION_ERROR", str(exc)) from exc
    output: dict[str, Any] = {}
    source_counts: dict[str, int] = {}
    returned_counts: dict[str, int] = {}
    truncated_layers: list[str] = []
    next_cursors: dict[str, str] = {}
    for layer, collection_name, entity_type in (
        ("nodes", "nodes", "node"),
        ("pipes", "pipes", "pipe"),
        ("catchments", "catchments", "catchment"),
        ("ponding", "pond_cells", "pond_cell"),
    ):
        if layer not in requested_layers:
            continue
        values = sorted(
            revision.get(collection_name, {}).values(),
            key=lambda item: str(item["stable_key"]).encode("utf-8"),
        )
        index_by_id = {item["id"]: index for index, item in enumerate(values)}
        if box is not None and values:
            report = filter_rows_by_bbox(
                [
                    {"id": item["id"], "geometry_wkt": item["geometry"]["geometry_wkt"]}
                    for item in values
                ],
                box,
                entity_type=cast(
                    EntityType, entity_type if entity_type != "pond_cell" else "node"
                ),
            )
            ids = {item["id"] for item in report.matches}
            values = [item for item in values if item["id"] in ids]
        matched_count = len(values)
        source_counts[layer] = matched_count
        page_values, next_cursor = _page(values, cursor, limit)
        features = [
            _entity_feature(item, index_by_id[item["id"]]) for item in page_values
        ]
        output[layer] = {"type": "FeatureCollection", "features": features}
        returned_counts[layer] = len(features)
        if matched_count > len(features):
            truncated_layers.append(layer)
        if next_cursor:
            next_cursors[layer] = next_cursor
    for layer in allowed - requested_layers:
        source_counts[layer] = 0
        returned_counts[layer] = 0
        if layer != "ponding":
            output[layer] = {"type": "FeatureCollection", "features": []}
    if "ponding" not in requested_layers:
        output.pop("ponding", None)
    bounds = list(box.values) if box else revision["bounds"]
    return {
        "schema_version": "network-map-v2",
        "network_id": network_id,
        "network_revision_id": revision["id"],
        "target_manifest_id": revision["id"],
        "target_manifest_sha256": revision["manifest"]["content_sha256"],
        "crs": "EPSG:4326",
        "bbox": bounds,
        "zoom": zoom,
        "complete": not truncated_layers,
        "simplified": simplify,
        "truncated_layers": sorted(truncated_layers),
        "next_cursor": next_cursors.get("nodes")
        or next_cursors.get("pipes")
        or next_cursors.get("catchments")
        or next_cursors.get("ponding"),
        "next_cursors": next_cursors,
        "source_counts": source_counts,
        "returned_counts": returned_counts,
        "generated_at": _iso(_now()),
        **output,
    }


@router.post(
    "/networks/{network_id}/validate", response_model=HydraulicResponse, status_code=202
)
async def validate_network(
    network_id: str,
    request: ValidationRequest | None = None,
    expected_revision_id: str | None = Query(default=None),
    if_match: str | None = Header(default=None, alias="If-Match"),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    request = request or ValidationRequest()
    revision_id = request.expected_revision_id or expected_revision_id
    if if_match and not revision_id:
        revision_id = if_match.strip('"')
    revision = _revision_or_404(network_id, revision_id)
    fingerprint = _fingerprint(
        {
            "network_id": network_id,
            "revision_id": revision["id"],
            "strict": request.strict,
        }
    )
    replay = _idempotent_replay("network.validate", key, user, fingerprint)
    if replay is not None:
        return replay
    validation_id = str(uuid.uuid4())
    job = _new_job(
        "validate",
        user,
        network_id,
        request_fingerprint=fingerprint,
        idempotency_record_key=key,
    )
    hydraulic_store.validation_runs[validation_id] = {
        "id": validation_id,
        "network_id": network_id,
        "network_revision_id": revision["id"],
        "status": "queued",
        "report": None,
        "created_at": _iso(_now()),
    }
    job.update({"validation_run_id": validation_id})
    body = {
        "network_job_id": job["id"],
        "validation_run_id": validation_id,
        "status": "queued",
        "report": None,
    }
    _idempotent_store(
        "network.validate", key, user, fingerprint, body, resource_id=job["id"]
    )
    return body


@router.get("/validation-runs/{validation_id}", response_model=HydraulicResponse)
async def get_validation_run(
    validation_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    result = hydraulic_store.validation_runs.get(validation_id)
    if result is None:
        raise _error(404, "VALIDATION_RUN_NOT_FOUND", "validation run was not found")
    return {"schema_version": 1, **result}


@router.get("/network-jobs/{job_id}", response_model=HydraulicResponse)
async def get_network_job(
    job_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    job = hydraulic_store.jobs.get(job_id)
    if job is None:
        raise _error(404, "NETWORK_JOB_NOT_FOUND", "network job was not found")
    return _job_response(job)


@router.get("/network-imports/{import_id}", response_model=HydraulicResponse)
async def get_network_import(
    import_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    result = hydraulic_store.imports.get(import_id)
    if result is None:
        raise _error(404, "NETWORK_IMPORT_NOT_FOUND", "network import was not found")
    return {"schema_version": 1, **result}


def _swmm_export_mapping(revision: Mapping[str, Any]) -> dict[str, Any]:
    nodes = sorted(revision["nodes"].values(), key=lambda item: item["stable_key"])
    node_names = {item["id"]: item["stable_key"] for item in nodes}
    coordinates = []
    junctions = []
    outfalls = []
    for item in nodes:
        coordinates.append(
            {"name": item["stable_key"], "x": item["bbox"][0], "y": item["bbox"][1]}
        )
        common = {
            "name": item["stable_key"],
            "elevation": item.get("invert_elevation_m") or 0.0,
        }
        if item.get("kind") == "outfall":
            outfalls.append({**common, "type": "FREE", "stage": 0.0})
        else:
            junctions.append({**common, "max_depth": item.get("max_depth_m") or 0.0})
    conduits = []
    slopes = []
    for item in sorted(revision["pipes"].values(), key=lambda item: item["stable_key"]):
        conduits.append(
            {
                "name": item["stable_key"],
                "from_node": node_names.get(item.get("upstream_node_id"), ""),
                "to_node": node_names.get(item.get("downstream_node_id"), ""),
                "length": item.get("length_m", 1.0),
                "roughness": item.get("roughness_manning_n", 0.013),
            }
        )
        slopes.append(
            {
                "conduit": item["stable_key"],
                "upstream_node": node_names.get(item.get("upstream_node_id"), ""),
                "upstream_offset": 0.0,
                "downstream_node": node_names.get(item.get("downstream_node_id"), ""),
                "downstream_offset": 0.0,
            }
        )
    return {
        "title": ["hydraulic-api-export"],
        "junctions": junctions,
        "outfalls": outfalls,
        "conduits": conduits,
        "subcatchments": [],
        "coordinates": coordinates,
        "slopes": slopes,
        "raw_sections": [
            {
                "section": "XSECTIONS",
                "tokens": [
                    item["stable_key"],
                    "CIRCULAR",
                    str(item.get("nominal_diameter_m", 0.3)),
                ],
            }
            for item in sorted(
                revision["pipes"].values(), key=lambda row: row["stable_key"]
            )
        ],
        "options": {"FLOW_UNITS": "CFS"},
    }


@router.get("/networks/{network_id}/exports/{file_format}", response_model=None)
async def export_network(
    network_id: str,
    file_format: str,
    revision_id: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_operator),
) -> StreamingResponse:
    if file_format.lower() not in {"geojson", "inp"}:
        raise _error(422, "VALIDATION_ERROR", "format must be geojson or inp")
    revision = _revision_or_404(network_id, revision_id)
    if file_format.lower() == "geojson":
        features = []
        for collection, entity_type in (
            ("nodes", "node"),
            ("pipes", "pipe"),
            ("catchments", "catchment"),
        ):
            values = sorted(
                revision[collection].values(),
                key=lambda item: str(item["stable_key"]).encode("utf-8"),
            )
            for index, entity in enumerate(values):
                feature = _entity_feature(entity, index)
                feature["properties"]["entity_type"] = entity_type
                features.append(feature)
        document = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
            "bbox": revision["bounds"],
            "properties": {
                "network_id": network_id,
                "network_revision_id": revision["id"],
            },
            "features": features,
        }
        content = json.dumps(
            document, ensure_ascii=False, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        media_type = "application/geo+json"
        filename = f"network-{network_id}.geojson"
    else:
        try:
            content = export_inp(_swmm_export_mapping(revision)).encode("utf-8")
        except ValueError as exc:
            raise _error(422, "EXPORT_NOT_REPRESENTABLE", str(exc)) from exc
        media_type = "text/plain"
        filename = f"network-{network_id}.inp"
    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/networks/{network_id}/seed", response_model=HydraulicResponse, status_code=202
)
async def seed_network(
    network_id: str,
    request: SeedRequest | None = None,
    expected_revision_id: str | None = Query(default=None),
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_admin),
) -> dict[str, Any]:
    _assert_network_mutable(_network_or_404(network_id))
    request = request or SeedRequest()
    if expected_revision_id:
        current = _revision_or_404(network_id, expected_revision_id)
        if current["id"] != _network_or_404(network_id)["current_revision_id"]:
            raise _error(409, "NETWORK_REVISION_CONFLICT", "expected revision is stale")
    fingerprint = _fingerprint(
        {"network_id": network_id, "request": request.model_dump(mode="json")}
    )
    replay = _idempotent_replay("network.seed", key, user, fingerprint)
    if replay is not None:
        return replay
    job = _new_job(
        "seed",
        user,
        network_id,
        request_fingerprint=fingerprint,
        idempotency_record_key=key,
    )
    body = {
        "network_job_id": job["id"],
        "status": "queued",
        "network_id": network_id,
        "committed": False,
    }
    _idempotent_store(
        "network.seed", key, user, fingerprint, body, resource_id=job["id"]
    )
    return body


def current_revision(network: Mapping[str, Any]) -> dict[str, Any]:
    return hydraulic_store.revisions[network["current_revision_id"]]


# ---------------------------------------------------------------------------
# Rainfall scenarios
# ---------------------------------------------------------------------------


def _rainfall_content_hash(item: Mapping[str, Any]) -> str:
    return _fingerprint(
        {
            "network_id": item.get("network_id"),
            "name": item.get("name"),
            "kind": item.get("kind"),
            "return_period_years": item.get("return_period_years"),
            "step_seconds": item.get("step_seconds"),
            "duration_seconds": item.get("duration_seconds"),
            "source_type": item.get("source_type"),
            "algorithm": item.get("algorithm"),
            "random_seed": item.get("random_seed"),
            "points": item.get("points", []),
        }
    )


def _rainfall_response(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": str(item["id"]),
        "network_id": item["network_id"],
        "root_scenario_id": item.get("root_scenario_id"),
        "version": item["version"],
        "name": item["name"],
        "kind": item["kind"],
        "return_period_years": item.get("return_period_years"),
        "step_seconds": item["step_seconds"],
        "duration_seconds": item["duration_seconds"],
        "source_type": item["source_type"],
        "algorithm": item["algorithm"],
        "random_seed": item["random_seed"],
        "content_sha256": item["content_sha256"],
        "points": deepcopy(item["points"]),
        "samples": deepcopy(item["points"]),
        "created_at": item["created_at"],
    }


@router.get("/rainfall-scenarios", response_model=HydraulicResponse)
async def list_rainfall_scenarios(
    network_id: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    return_period: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    with hydraulic_store._lock:
        values = list(hydraulic_store.rainfall.values())
    if network_id:
        values = [item for item in values if item["network_id"] == network_id]
    if kind:
        values = [item for item in values if item["kind"] == kind]
    if return_period is not None:
        values = [
            item for item in values if item.get("return_period_years") == return_period
        ]
    values.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    page, next_cursor = _page(values, cursor, limit)
    return {
        "items": [_rainfall_response(item) for item in page],
        "next_cursor": next_cursor,
    }


@router.post("/rainfall-scenarios", response_model=HydraulicResponse, status_code=201)
async def create_rainfall_scenario(
    request: RainfallScenarioRequest,
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    _require_durable_api_store()
    _assert_network_mutable(_network_or_404(_uuid(request.network_id, "network_id")))
    fingerprint = _fingerprint(request.model_dump(mode="json"))
    replay = _idempotent_replay("rainfall.create", key, user, fingerprint)
    if replay is not None:
        return replay
    item: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "network_id": _uuid(request.network_id, "network_id"),
        "root_scenario_id": None,
        "version": 1,
        "name": request.name,
        "kind": request.kind,
        "return_period_years": request.return_period_years,
        "step_seconds": request.step_seconds,
        "duration_seconds": request.duration_seconds,
        "source_type": request.source_type,
        "algorithm": request.algorithm,
        "random_seed": request.random_seed,
        "content_sha256": request.content_sha256,
        "points": deepcopy(request.points),
        "created_at": _iso(_now()),
    }
    hydraulic_store.rainfall[item["id"]] = item
    computed_hash = _rainfall_content_hash(item)
    if (
        settings.DEPLOYMENT_ENVIRONMENT == "production"
        and request.content_sha256 != computed_hash
    ):
        hydraulic_store.rainfall.pop(item["id"], None)
        raise _error(
            422, "VALIDATION_ERROR", "content_sha256 does not match rainfall content"
        )
    item["content_sha256"] = computed_hash
    body = _rainfall_response(item)
    _idempotent_store(
        "rainfall.create", key, user, fingerprint, body, resource_id=item["id"]
    )
    return body


@router.get("/rainfall-scenarios/{scenario_id}", response_model=HydraulicResponse)
async def get_rainfall_scenario(
    scenario_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    item = hydraulic_store.rainfall.get(scenario_id)
    if item is None:
        raise _error(
            404, "RAINFALL_SCENARIO_NOT_FOUND", "rainfall scenario was not found"
        )
    return _rainfall_response(item)


@router.patch("/rainfall-scenarios/{scenario_id}", response_model=HydraulicResponse)
async def patch_rainfall_scenario(
    scenario_id: str,
    request: RainfallPatchRequest,
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    _require_durable_api_store()
    old = hydraulic_store.rainfall.get(scenario_id)
    if old is None:
        raise _error(
            404, "RAINFALL_SCENARIO_NOT_FOUND", "rainfall scenario was not found"
        )
    if old["version"] != request.expected_version:
        raise _error(
            409, "NETWORK_REVISION_CONFLICT", "rainfall scenario version is stale"
        )
    if any(
        item.get("rainfall_scenario_id") == scenario_id
        for item in hydraulic_store.simulations.values()
    ):
        raise _error(
            409,
            "NETWORK_REVISION_CONFLICT",
            "referenced rainfall scenario is immutable",
        )
    _assert_network_mutable(_network_or_404(old["network_id"]))
    merged = {
        "network_id": old["network_id"],
        "name": request.name if request.name is not None else old["name"],
        "kind": request.kind if request.kind is not None else old["kind"],
        "return_period_years": (
            request.return_period_years
            if request.return_period_years is not None
            else old.get("return_period_years")
        ),
        "step_seconds": request.step_seconds or old["step_seconds"],
        "duration_seconds": request.duration_seconds or old["duration_seconds"],
        "source_type": request.source_type or old["source_type"],
        "algorithm": request.algorithm or old["algorithm"],
        "random_seed": (
            request.random_seed
            if request.random_seed is not None
            else old["random_seed"]
        ),
        "points": deepcopy(
            request.points if request.points is not None else old["points"]
        ),
        "content_sha256": request.content_sha256 or old["content_sha256"],
    }
    try:
        RainfallScenarioRequest.model_validate(merged)
    except ValidationError as exc:
        raise _error(
            422,
            "VALIDATION_ERROR",
            "rainfall scenario invariants failed",
            errors=exc.errors(),
        ) from exc
    fingerprint = _fingerprint(
        {"scenario_id": scenario_id, "request": request.model_dump(mode="json")}
    )
    replay = _idempotent_replay("rainfall.update", key, user, fingerprint)
    if replay is not None:
        return replay
    item = deepcopy(old)
    item["id"] = str(uuid.uuid4())
    item["root_scenario_id"] = old.get("root_scenario_id") or old["id"]
    item["version"] = old["version"] + 1
    for field in (
        "name",
        "kind",
        "return_period_years",
        "step_seconds",
        "duration_seconds",
        "source_type",
        "algorithm",
        "random_seed",
    ):
        value = getattr(request, field)
        if value is not None:
            item[field] = value
    if request.points is not None:
        item["points"] = deepcopy(request.points)
    if request.content_sha256 is not None:
        item["content_sha256"] = request.content_sha256
    item["created_at"] = _iso(_now())
    computed_hash = _rainfall_content_hash(item)
    if (
        settings.DEPLOYMENT_ENVIRONMENT == "production"
        and request.content_sha256 is not None
        and request.content_sha256 != computed_hash
    ):
        raise _error(
            422, "VALIDATION_ERROR", "content_sha256 does not match rainfall content"
        )
    item["content_sha256"] = computed_hash
    hydraulic_store.rainfall[item["id"]] = item
    body = _rainfall_response(item)
    _idempotent_store(
        "rainfall.update", key, user, fingerprint, body, resource_id=item["id"]
    )
    return body


@router.delete(
    "/rainfall-scenarios/{scenario_id}", status_code=204, response_model=None
)
async def delete_rainfall_scenario(
    scenario_id: str,
    _: dict[str, Any] = Depends(_operator),
) -> None:
    if scenario_id not in hydraulic_store.rainfall:
        raise _error(
            404, "RAINFALL_SCENARIO_NOT_FOUND", "rainfall scenario was not found"
        )
    if any(
        item.get("rainfall_scenario_id") == scenario_id
        for item in hydraulic_store.simulations.values()
    ):
        raise _error(
            409,
            "NETWORK_REVISION_CONFLICT",
            "referenced rainfall scenario cannot be deleted",
        )
    _assert_network_mutable(
        _network_or_404(hydraulic_store.rainfall[scenario_id]["network_id"])
    )
    hydraulic_store.rainfall.pop(scenario_id, None)
    return None


@router.post(
    "/rainfall-scenarios/{scenario_id}/copy",
    response_model=HydraulicResponse,
    status_code=201,
)
async def copy_rainfall_scenario(
    scenario_id: str,
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    _require_durable_api_store()
    old = hydraulic_store.rainfall.get(scenario_id)
    if old is None:
        raise _error(
            404, "RAINFALL_SCENARIO_NOT_FOUND", "rainfall scenario was not found"
        )
    _assert_network_mutable(_network_or_404(old["network_id"]))
    fingerprint = _fingerprint({"scenario_id": scenario_id})
    replay = _idempotent_replay("rainfall.copy", key, user, fingerprint)
    if replay is not None:
        return replay
    item = deepcopy(old)
    item["id"] = str(uuid.uuid4())
    item["root_scenario_id"] = old.get("root_scenario_id") or old["id"]
    item["version"] = old["version"] + 1
    item["created_at"] = _iso(_now())
    hydraulic_store.rainfall[item["id"]] = item
    item["content_sha256"] = _rainfall_content_hash(item)
    body = _rainfall_response(item)
    _idempotent_store(
        "rainfall.copy", key, user, fingerprint, body, resource_id=item["id"]
    )
    return body


# ---------------------------------------------------------------------------
# Simulations and result surfaces
# ---------------------------------------------------------------------------


def _simulation_response(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": str(item["id"]),
        "status": item["status"],
        "progress": item["progress"],
        "attempt": item["attempt"],
        "max_attempts": item["max_attempts"],
        "network_id": item["network_id"],
        "network_revision_id": item["network_revision_id"],
        "target_manifest_id": item["target_manifest_id"],
        "target_manifest_sha256": item["target_manifest_sha256"],
        "network_revision_content_sha256": item["network_revision_content_sha256"],
        "snapshot_content_sha256": item["snapshot_content_sha256"],
        "initial_state_id": item["initial_state_id"],
        "initial_state_content_sha256": item["initial_state_content_sha256"],
        "rainfall_scenario_id": item["rainfall_scenario_id"],
        "parameter_set_id": item.get("parameter_set_id"),
        "solver_selection": item["solver_selection"],
        "requested_solver_id": item.get("requested_solver_id"),
        "solver_id": item["solver_id"],
        "solver_version": item["solver_version"],
        "solver_fallback_reason": item.get("solver_fallback_reason"),
        "error_code": item.get("error_code"),
        "error_message": item.get("error_message"),
        "next_attempt_at": item.get("next_attempt_at"),
        "retry_backoff_seconds": item.get("retry_backoff_seconds"),
        "last_retry_error_code": item.get("last_retry_error_code"),
        "phase": item.get("phase"),
        "published_attempt_id": item.get("published_attempt_id"),
        "published_attempt_no": item.get("published_attempt_no"),
        "frame_count": item.get("frame_count"),
        "created_at": item["created_at"],
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "updated_at": item.get("updated_at"),
        "links": {
            "self": f"/api/simulations/{item['id']}",
            "cancel": f"/api/simulations/{item['id']}/cancel",
            "result": f"/api/simulations/{item['id']}/result",
            "frames": f"/api/simulations/{item['id']}/frames",
        },
        "request": deepcopy(item.get("request")),
    }


@router.get("/simulations", response_model=HydraulicResponse)
async def list_simulations(
    status_filter: str | None = Query(default=None, alias="status"),
    network_id: str | None = Query(default=None),
    rainfall_scenario_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    with hydraulic_store._lock:
        values = list(hydraulic_store.simulations.values())
    if status_filter:
        values = [item for item in values if item["status"] == status_filter]
    if network_id:
        values = [item for item in values if item["network_id"] == network_id]
    if rainfall_scenario_id:
        values = [
            item
            for item in values
            if item["rainfall_scenario_id"] == rainfall_scenario_id
        ]
    values.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
    page, next_cursor = _page(values, cursor, limit)
    return {
        "items": [_simulation_response(item) for item in page],
        "next_cursor": next_cursor,
    }


@router.post("/simulations", response_model=HydraulicResponse, status_code=202)
async def submit_simulation(
    request: SimulationSubmitRequest,
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    _require_durable_api_store()
    network_id = _uuid(request.network_id, "network_id")
    revision_id = _uuid(request.network_revision_id, "network_revision_id")
    network = _network_or_404(network_id)
    _assert_network_mutable(network)
    revision = _revision_or_404(network_id, revision_id)
    if revision["id"] != _network_or_404(network_id)["current_revision_id"]:
        # Historical revisions are still valid for comparison/import lineage, but
        # new simulations are intentionally pinned to the current revision.
        raise _error(
            409, "NETWORK_REVISION_CONFLICT", "simulation revision is not current"
        )
    rainfall_id = _uuid(request.rainfall_scenario_id, "rainfall_scenario_id")
    rainfall = hydraulic_store.rainfall.get(rainfall_id)
    if rainfall is None or rainfall["network_id"] != network_id:
        raise _error(
            422, "VALIDATION_ERROR", "rainfall_scenario_id is not owned by the network"
        )
    parameter_id = _uuid(request.parameter_set_id, "parameter_set_id")
    fingerprint = _fingerprint(request.model_dump(mode="json"))
    replay = _idempotent_replay("simulation.submit", key, user, fingerprint)
    if replay is not None:
        return replay
    solver_id = settings.resolve_auto_solver(request.requested_solver_id)
    if (
        request.solver_selection == "explicit"
        and request.requested_solver_id in {"swmm_kw", "swmm_dw"}
        and not settings.epa_swmm_available
    ):
        raise _error(
            503, "SOLVER_UNAVAILABLE", "requested EPA SWMM solver is unavailable"
        )
    solver_version = (
        "python-fallback-v1" if solver_id.startswith("py_") else "epa-swmm-probed"
    )
    initial_state_id = (
        _uuid(request.initial_state_id, "initial_state_id")
        if request.initial_state_id
        else str(uuid.uuid4())
    )
    initial_hash = _fingerprint(
        {
            "state": "dry",
            "revision_id": revision_id,
            "initial_state_id": initial_state_id,
        }
    )
    snapshot_hash = _fingerprint(
        {
            "revision": revision["content_sha256"],
            "rainfall": rainfall["content_sha256"],
            "parameter_set_id": parameter_id,
        }
    )
    item: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "status": "queued",
        "progress": 0,
        "attempt": 0,
        "max_attempts": 3,
        "network_id": network_id,
        "network_revision_id": revision_id,
        "target_manifest_id": revision["id"],
        "target_manifest_sha256": revision["manifest"]["content_sha256"],
        "network_revision_content_sha256": revision["content_sha256"],
        "snapshot_content_sha256": snapshot_hash,
        "initial_state_id": initial_state_id,
        "initial_state_content_sha256": initial_hash,
        "rainfall_scenario_id": rainfall_id,
        "parameter_set_id": parameter_id,
        "solver_selection": request.solver_selection,
        "requested_solver_id": request.requested_solver_id,
        "solver_id": solver_id,
        "solver_version": solver_version,
        "solver_fallback_reason": (
            "epa_swmm_probe_failed"
            if request.requested_solver_id in {"swmm_kw", "swmm_dw"}
            and solver_id == "py_mw"
            else None
        ),
        "start_at": _iso(request.start_at),
        "duration_seconds": request.duration_seconds,
        "frame_interval_seconds": request.frame_interval_seconds,
        "frame_count": request.duration_seconds // request.frame_interval_seconds + 1,
        "ponding_enabled": request.ponding_enabled,
        "strict_stability": request.strict_stability,
        "phase": "accepted",
        "published_attempt_id": None,
        "published_attempt_no": None,
        "error_code": None,
        "error_message": None,
        "created_at": _iso(_now()),
        "updated_at": _iso(_now()),
        "started_at": None,
        "finished_at": None,
        "next_attempt_at": None,
        "retry_backoff_seconds": None,
        "last_retry_error_code": None,
        "request": request.model_dump(mode="json"),
        "result": None,
        "frames": [],
        "published_frames": {},
    }
    hydraulic_store.simulations[item["id"]] = item
    body = _simulation_response(item)
    _idempotent_store(
        "simulation.submit", key, user, fingerprint, body, resource_id=item["id"]
    )
    return body


@router.get("/simulations/{simulation_id}", response_model=HydraulicResponse)
async def get_simulation(
    simulation_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    item = hydraulic_store.simulations.get(simulation_id)
    if item is None:
        raise _error(404, "SIMULATION_NOT_FOUND", "simulation was not found")
    return _simulation_response(item)


@router.post("/simulations/{simulation_id}/cancel", response_model=HydraulicResponse)
async def cancel_simulation(
    simulation_id: str,
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    item = hydraulic_store.simulations.get(simulation_id)
    if item is None:
        raise _error(404, "SIMULATION_NOT_FOUND", "simulation was not found")
    fingerprint = _fingerprint({"simulation_id": simulation_id, "action": "cancel"})
    replay = _idempotent_replay("simulation.cancel", key, user, fingerprint)
    if replay is not None:
        return replay
    if item["status"] in {"completed", "failed", "timed_out"}:
        raise _error(
            409, "SIMULATION_NOT_CANCELLABLE", "simulation is already terminal"
        )
    if item["status"] not in {"cancelled", "cancelling"}:
        item["status"] = "cancelled"
        item["error_code"] = "SIMULATION_CANCELLED"
        item["finished_at"] = _iso(_now())
        item["updated_at"] = _iso(_now())
    body = _simulation_response(item)
    _idempotent_store(
        "simulation.cancel", key, user, fingerprint, body, resource_id=simulation_id
    )
    return body


def _require_completed(item: Mapping[str, Any]) -> None:
    if (
        item.get("status") != "completed"
        or not item.get("published_attempt_id")
        or not item.get("result")
    ):
        raise _error(409, "VALIDATION_ERROR", "simulation has not published a result")


def _frame_count(item: Mapping[str, Any]) -> int:
    try:
        return int(item["duration_seconds"] // item["frame_interval_seconds"] + 1)
    except (KeyError, TypeError, ZeroDivisionError) as exc:
        raise _error(
            409, "SIMULATION_RESULT_NOT_READY", "published frame grid is incomplete"
        ) from exc


def _frame_time(item: Mapping[str, Any], index: int) -> str:
    start = _parse_datetime(item["start_at"])
    return _iso_required(
        start + timedelta(seconds=item["frame_interval_seconds"] * index)
    )


@router.get("/simulations/{simulation_id}/result", response_model=HydraulicResponse)
async def get_simulation_result(
    simulation_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    item = hydraulic_store.simulations.get(simulation_id)
    if item is None:
        raise _error(404, "SIMULATION_NOT_FOUND", "simulation was not found")
    _require_completed(item)
    result = item.get("result") or {"summary": {}, "quality": {}, "metrics": {}}
    return {
        "schema_version": 1,
        "simulation_id": item["id"],
        "network_revision_id": item["network_revision_id"],
        "target_manifest_id": item["target_manifest_id"],
        "target_manifest_sha256": item["target_manifest_sha256"],
        "published_attempt_id": item["published_attempt_id"],
        "published_attempt_no": item["published_attempt_no"],
        "frame_count": item["frame_count"],
        "completed_at": item["finished_at"],
        "summary": result.get("summary", {}),
        "quality": result.get("quality", {}),
        "metrics": result.get("metrics", {}),
    }


@router.get("/simulations/{simulation_id}/frames", response_model=HydraulicResponse)
async def list_frames(
    simulation_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    item = hydraulic_store.simulations.get(simulation_id)
    if item is None:
        raise _error(404, "SIMULATION_NOT_FOUND", "simulation was not found")
    _require_completed(item)
    frame_count = _frame_count(item)
    frames = []
    for index in range(frame_count):
        frame_time = _frame_time(item, index)
        payload = _frame_payload(item, index)
        canonical = _canonical(payload).encode("utf-8")
        frames.append(
            {
                "frame_index": index,
                "frame_time": frame_time,
                "etag": hashlib.sha256(canonical).hexdigest(),
                "checksum": hashlib.sha256(canonical).hexdigest(),
                "bytes": len(canonical),
            }
        )
    return {
        "schema_version": 1,
        "simulation_id": item["id"],
        "network_revision_id": item["network_revision_id"],
        "target_manifest_id": item["target_manifest_id"],
        "target_manifest_sha256": item["target_manifest_sha256"],
        "published_attempt_id": item["published_attempt_id"],
        "published_attempt_no": item["published_attempt_no"],
        "frame_interval_seconds": item["frame_interval_seconds"],
        "frame_count": frame_count,
        "frames": frames,
    }


def _frame_payload(
    item: Mapping[str, Any], index: int, fields: set[str] | None = None
) -> dict[str, Any]:
    if not item.get("published_frames"):
        raise _error(
            409, "SIMULATION_RESULT_NOT_READY", "published frame is not available"
        )
    published = item["published_frames"]
    if isinstance(published, Mapping):
        payload = published.get(str(index), published.get(index))
    else:
        payload = published[index] if index < len(published) else None
    if not isinstance(payload, Mapping):
        raise _error(
            404, "SIMULATION_FRAME_NOT_FOUND", "published frame is not registered"
        )
    if fields:
        payload = dict(payload)
        nodes = payload.get("nodes")
        pipes = payload.get("pipes")
        if isinstance(nodes, Mapping):
            payload["nodes"] = {
                key: value
                for key, value in nodes.items()
                if key in fields or key == "feature_index"
            }
        if isinstance(pipes, Mapping):
            payload["pipes"] = {
                key: value
                for key, value in pipes.items()
                if key in fields or key == "feature_index"
            }
    return dict(payload)


@router.get(
    "/simulations/{simulation_id}/frames/{frame_index}",
    response_model=HydraulicResponse,
)
async def get_frame(
    simulation_id: str,
    frame_index: int,
    fields: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    item = hydraulic_store.simulations.get(simulation_id)
    if item is None:
        raise _error(404, "SIMULATION_NOT_FOUND", "simulation was not found")
    if frame_index < 0 or frame_index >= _frame_count(item):
        raise _error(
            422, "VALIDATION_ERROR", "frame_index is outside the published grid"
        )
    _require_completed(item)
    if fields:
        allowed = {"depth_m", "fill_ratio", "flow_m3s", "surcharged"}
        selected = {part.strip() for part in fields.split(",") if part.strip()}
        if not selected or not selected <= allowed:
            raise _error(
                422, "VALIDATION_ERROR", "fields contains an unsupported frame field"
            )
    else:
        selected = None
    return _frame_payload(item, frame_index, selected)


@router.get(
    "/simulations/{simulation_id}/observed-comparison", response_model=HydraulicResponse
)
async def observed_comparison(
    simulation_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    item = hydraulic_store.simulations.get(simulation_id)
    if item is None:
        raise _error(404, "SIMULATION_NOT_FOUND", "simulation was not found")
    return {
        "schema_version": 1,
        "simulation_id": simulation_id,
        "status": "not_ready",
        "pairs": [],
        "coverage": {"observation_count": 0, "paired_count": 0},
        "metrics": {"rmse": None, "nse": None, "nse_code": "NSE_UNDEFINED"},
    }


@router.get(
    "/simulations/{simulation_id}/calibration", response_model=HydraulicResponse
)
async def simulation_calibration(
    simulation_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    item = hydraulic_store.simulations.get(simulation_id)
    if item is None:
        raise _error(404, "SIMULATION_NOT_FOUND", "simulation was not found")
    calibration = next(
        (
            value
            for value in hydraulic_store.calibrations.values()
            if value.get("baseline_simulation_id") == simulation_id
        ),
        None,
    )
    if calibration is None:
        raise _error(404, "CALIBRATION_NOT_FOUND", "calibration report was not found")
    return _calibration_response(calibration)


def _comparison_side(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "simulation_id": item["id"],
        "published_attempt_id": item["published_attempt_id"],
        "published_attempt_no": item["published_attempt_no"],
        "rainfall_scenario_id": item["rainfall_scenario_id"],
        "solver": item["solver_id"],
        "parameters_hash": item.get(
            "parameter_set_id", item.get("snapshot_content_sha256")
        ),
    }


@router.get(
    "/simulations/{baseline_id}/compare/{candidate_id}",
    response_model=HydraulicResponse,
)
async def compare_simulations(
    baseline_id: str,
    candidate_id: str,
    metrics: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    baseline = hydraulic_store.simulations.get(baseline_id)
    candidate = hydraulic_store.simulations.get(candidate_id)
    if baseline is None or candidate is None:
        raise _error(404, "SIMULATION_NOT_FOUND", "simulation was not found")
    _require_completed(baseline)
    _require_completed(candidate)
    compatible = all(
        baseline[field] == candidate[field]
        for field in (
            "network_revision_id",
            "target_manifest_id",
            "target_manifest_sha256",
            "start_at",
            "duration_seconds",
            "frame_interval_seconds",
        )
    )
    if not compatible:
        raise _error(
            409,
            "COMPARISON_INCOMPATIBLE",
            "simulations do not share a compatible frame grid",
        )
    baseline_metrics = (baseline.get("result") or {}).get("metrics", {})
    candidate_metrics = (candidate.get("result") or {}).get("metrics", {})
    if not baseline_metrics or not candidate_metrics:
        raise _error(
            409, "SIMULATION_RESULT_NOT_READY", "comparison metrics are not published"
        )
    result_metrics = []
    baseline_units = (baseline.get("result") or {}).get("metric_units", {})
    candidate_units = (candidate.get("result") or {}).get("metric_units", {})
    baseline_directions = (baseline.get("result") or {}).get("better_directions", {})
    candidate_directions = (candidate.get("result") or {}).get("better_directions", {})
    for key in sorted(set(baseline_metrics) & set(candidate_metrics)):
        unit = baseline_units.get(key)
        better_direction = baseline_directions.get(key)
        if (
            not isinstance(unit, str)
            or not unit.strip()
            or candidate_units.get(key) != unit
            or better_direction not in {"lower", "higher"}
            or candidate_directions.get(key) != better_direction
        ):
            raise _error(
                409,
                "COMPARISON_INCOMPATIBLE",
                "simulation metrics have incompatible units or directions",
            )
        baseline_value = float(baseline_metrics[key])
        candidate_value = float(candidate_metrics[key])
        delta = candidate_value - baseline_value
        result_metrics.append(
            {
                "key": key,
                "unit": unit,
                "baseline": baseline_value,
                "candidate": candidate_value,
                "absolute_delta": delta,
                "relative_delta": delta / baseline_value if baseline_value else None,
                "better_direction": better_direction,
            }
        )
    if metrics:
        requested = {item.strip() for item in metrics.split(",") if item.strip()}
        result_metrics = [item for item in result_metrics if item["key"] in requested]
    return {
        "schema_version": "simulation-comparison-v2",
        "compatible": True,
        "network_revision_id": baseline["network_revision_id"],
        "target_manifest_id": baseline["target_manifest_id"],
        "target_manifest_sha256": baseline["target_manifest_sha256"],
        "frame_grid": {
            "start_at": baseline["start_at"],
            "end_at": _frame_time(baseline, _frame_count(baseline) - 1),
            "interval_seconds": baseline["frame_interval_seconds"],
            "frame_count": _frame_count(baseline),
        },
        "baseline": _comparison_side(baseline),
        "candidate": _comparison_side(candidate),
        "metrics": result_metrics,
    }


# ---------------------------------------------------------------------------
# Calibration and forecast
# ---------------------------------------------------------------------------


def _calibration_response(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": str(item["id"]),
        "network_id": item["network_id"],
        "network_revision_id": item["network_revision_id"],
        "baseline_simulation_id": item["baseline_simulation_id"],
        "candidate_simulation_id": item.get("candidate_simulation_id"),
        "status": item["status"],
        "progress": item.get("progress", 0),
        "attempt": item.get("attempt", 0),
        "max_attempts": 3,
        "observation_dataset_sha256": item["observation_dataset_sha256"],
        "split_manifest": item["split_manifest"],
        "split_manifest_sha256": item["split_manifest_sha256"],
        "calibration_config_hash": item["calibration_config_hash"],
        "metrics": item.get("metrics", {}),
        "improved": item.get("improved"),
        "report": item.get("report"),
        "created_at": item["created_at"],
        "finished_at": item.get("finished_at"),
    }


@router.post("/calibrations", response_model=HydraulicResponse, status_code=202)
@router.post(
    "/calibrate",
    response_model=HydraulicResponse,
    status_code=202,
    include_in_schema=False,
)
async def submit_calibration(
    request: CalibrationSubmitRequest,
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    _require_durable_api_store()
    network_id = _uuid(request.network_id, "network_id")
    revision = _revision_or_404(
        network_id, _uuid(request.network_revision_id, "network_revision_id")
    )
    baseline = hydraulic_store.simulations.get(
        _uuid(request.baseline_simulation_id, "baseline_simulation_id")
    )
    if (
        baseline is None
        or baseline["network_id"] != network_id
        or baseline["network_revision_id"] != revision["id"]
    ):
        raise _error(
            422,
            "VALIDATION_ERROR",
            "baseline simulation does not belong to the network revision",
        )
    fingerprint = _fingerprint(request.model_dump(mode="json"))
    replay = _idempotent_replay("calibration.submit", key, user, fingerprint)
    if replay is not None:
        return replay
    dataset_hash = _fingerprint(
        {
            "start": request.observation_start,
            "end": request.observation_end,
            "baseline": baseline["id"],
        }
    )
    split_hash = _fingerprint(request.split_manifest)
    item: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "network_id": network_id,
        "network_revision_id": revision["id"],
        "baseline_simulation_id": baseline["id"],
        "candidate_simulation_id": request.candidate_simulation_id,
        "status": "queued",
        "progress": 0,
        "attempt": 0,
        "observation_dataset_sha256": dataset_hash,
        "split_manifest": deepcopy(request.split_manifest),
        "split_manifest_sha256": split_hash,
        "calibration_config_hash": _fingerprint(request.model_dump(mode="json")),
        "metrics": {},
        "improved": None,
        "report": None,
        "created_at": _iso(_now()),
        "finished_at": None,
    }
    hydraulic_store.calibrations[item["id"]] = item
    body = _calibration_response(item)
    _idempotent_store(
        "calibration.submit", key, user, fingerprint, body, resource_id=item["id"]
    )
    return body


@router.get("/calibrations/{calibration_id}", response_model=HydraulicResponse)
async def get_calibration(
    calibration_id: str, _: dict[str, Any] = Depends(_viewer)
) -> dict[str, Any]:
    item = hydraulic_store.calibrations.get(calibration_id)
    if item is None:
        raise _error(404, "CALIBRATION_NOT_FOUND", "calibration was not found")
    return _calibration_response(item)


def _forecast_alert_response(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item["id"]),
        "type": item["alert_type"],
        "status": item["status"],
        "phase": item["phase"],
        "level": item["level"],
        "target": {
            "type": item["target_type"],
            "id": item["target_id"],
            "stable_key": item.get("target_stable_key"),
        },
        "expected_at": item["expected_at"],
        "lead_time_minutes": item["lead_time_minutes"],
        "measurements": item.get("measurements", {}),
        "confidence": item["confidence"],
        "confidence_method": item["confidence_method"],
        "risk_score": item["risk_score"],
        "network_revision_id": item.get("network_revision_id"),
        "target_manifest_id": item.get("target_manifest_id"),
        "target_manifest_sha256": item.get("target_manifest_sha256"),
        "version": item["version"],
    }


def _latest_forecast(
    network_id: str | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    runs = [
        item
        for item in hydraulic_store.forecast_runs.values()
        if (network_id is None or item["network_id"] == network_id)
        and item.get("status") in {"completed", "published"}
        and item.get("issued_at")
        and item.get("published_at")
    ]
    run = max(
        runs,
        key=lambda item: item.get("issued_at") or item.get("scheduled_for", ""),
        default=None,
    )
    alerts = [
        item
        for item in hydraulic_store.forecast_alerts.values()
        if run is not None and item["nowcast_run_id"] == run["id"]
    ]
    return run, alerts


@router.get("/forecast/latest", response_model=HydraulicResponse)
async def forecast_latest(
    network_id: str = Query(...),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    run, alerts = _latest_forecast(network_id)
    if run is None:
        return {
            "schema_version": 1,
            "data_state": "not_ready",
            "network_id": network_id,
            "nowcast_run": None,
            "alerts": [],
            "age_seconds": None,
            "server_time": _iso(_now()),
            "code": "FORECAST_POLICY_UNAVAILABLE",
        }
    issued = _parse_datetime(run["issued_at"])
    age = max(0, int((_now() - issued).total_seconds()))
    state = "ready" if age <= settings.FORECAST_FRESH_MAX_AGE_SECONDS else "stale"
    return {
        "schema_version": 1,
        "data_state": state,
        "network_id": network_id,
        "nowcast_run": {
            "id": run["id"],
            "scheduled_for": run["scheduled_for"],
            "forecast_origin_at": run["forecast_origin_at"],
            "issued_at": run["issued_at"],
            "published_at": run["published_at"],
            "simulation_id": run["simulation_id"],
        },
        "alerts": [_forecast_alert_response(item) for item in alerts],
        "age_seconds": age,
        "server_time": _iso(_now()),
    }


@router.get("/forecast/horizon", response_model=HydraulicResponse)
async def forecast_horizon(
    network_id: str = Query(...),
    target_type: str = Query(default="node"),
    target_id: str = Query(...),
    horizon_minutes: int = Query(default=120, ge=1, le=120),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    if target_type not in {"node", "pipe_segment"}:
        raise _error(
            422, "VALIDATION_ERROR", "target_type must be node or pipe_segment"
        )
    target_id = _uuid(target_id, "target_id")
    run, alerts = _latest_forecast(network_id)
    points = [
        {
            "target": {"type": target_type, "id": target_id},
            "expected_at": item["expected_at"],
            "lead_time_minutes": item["lead_time_minutes"],
            "value": item.get("measurements", {}).get("depth_m"),
            "confidence": item["confidence"],
        }
        for item in alerts
        if item["target_type"] == target_type
        and item["target_id"] == target_id
        and item["lead_time_minutes"] <= horizon_minutes
    ]
    return {
        "schema_version": 1,
        "data_state": "ready" if run and alerts else "not_ready",
        "network_id": network_id,
        "nowcast_run_id": run["id"] if run else None,
        "target": {"type": target_type, "id": target_id},
        "horizon_minutes": horizon_minutes,
        "step_minutes": settings.NOWCAST_STEP_MINUTES,
        "points": points,
        "server_time": _iso(_now()),
    }


@router.get("/forecast/risk-ranking", response_model=HydraulicResponse)
async def forecast_risk_ranking(
    network_id: str = Query(...),
    limit: int = Query(default=10, ge=1, le=100),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    run, alerts = _latest_forecast(network_id)
    active = [item for item in alerts if item["status"] in {"open", "acknowledged"}]
    active.sort(key=lambda item: (-item["risk_score"], item["expected_at"], item["id"]))
    items = []
    for item in active[:limit]:
        components = item.get("score_components")
        if not isinstance(components, Mapping):
            continue
        response = _forecast_alert_response(item)
        response["score_components"] = deepcopy(components)
        response["policy_version"] = item.get("risk_policy_version", "risk-v1")
        items.append(response)
    return {
        "schema_version": 1,
        "data_state": "ready" if run and alerts else "not_ready",
        "network_id": network_id,
        "nowcast_run_id": run["id"] if run else None,
        "risk_policy_version": "risk-v1",
        "items": items,
        "server_time": _iso(_now()),
    }


@router.get("/forecast/alerts", response_model=HydraulicResponse)
async def forecast_alerts(
    network_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    _: dict[str, Any] = Depends(_viewer),
) -> dict[str, Any]:
    values = list(hydraulic_store.forecast_alerts.values())
    if network_id:
        values = [item for item in values if item["network_id"] == network_id]
    if status_filter:
        values = [item for item in values if item["status"] == status_filter]
    values.sort(key=lambda item: (item["expected_at"], item["id"]))
    page, next_cursor = _page(values, cursor, limit)
    return {
        "items": [_forecast_alert_response(item) for item in page],
        "next_cursor": next_cursor,
    }


@router.post(
    "/forecast/alerts/{alert_id}/transitions", response_model=HydraulicResponse
)
async def forecast_transition(
    alert_id: str,
    request: ForecastTransitionRequest,
    key: str = Depends(_idempotency_key),
    user: dict[str, Any] = Depends(_operator),
) -> dict[str, Any]:
    alert = hydraulic_store.forecast_alerts.get(alert_id)
    if alert is None:
        raise _error(404, "FORECAST_ALERT_NOT_FOUND", "forecast alert was not found")
    _assert_network_mutable(_network_or_404(alert["network_id"]))
    fingerprint = _fingerprint(
        {"alert_id": alert_id, "request": request.model_dump(mode="json")}
    )
    replay = _idempotent_replay("forecast.transition", key, user, fingerprint)
    if replay is not None:
        return replay
    if alert["version"] != request.expected_version:
        raise _error(
            409,
            "FORECAST_VERSION_CONFLICT",
            "forecast alert version is stale",
            errors=[{"field": "expected_version", "message": "refresh and retry"}],
        )
    if alert["status"] in {"resolved", "expired"}:
        raise _error(
            409,
            "INVALID_FORECAST_TRANSITION",
            "terminal forecast alerts cannot transition",
        )
    if request.action == "acknowledge" and alert["status"] != "open":
        raise _error(
            409, "INVALID_FORECAST_TRANSITION", "only open alerts can be acknowledged"
        )
    old_status = alert["status"]
    alert["status"] = "acknowledged" if request.action == "acknowledge" else "resolved"
    if request.action == "acknowledge":
        alert.setdefault("acknowledged_at", _iso(_now()))
        alert.setdefault("acknowledged_by", _user_id(user))
    else:
        alert["resolved_at"] = _iso(_now())
        alert["resolved_by"] = _user_id(user)
        alert["resolution_reason"] = request.reason
    alert["version"] += 1
    transition = {
        "id": str(uuid.uuid4()),
        "alert_id": alert_id,
        "aggregate_version": alert["version"],
        "from_status": old_status,
        "to_status": alert["status"],
        "from_phase": alert["phase"],
        "to_phase": alert["phase"],
        "action": request.action,
        "reason": request.reason,
        "actor_id": _user_id(user),
        "occurred_at": _iso(_now()),
    }
    event = {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "sequence": hydraulic_store.next_event_sequence,
        "event_type": (
            "alert.updated" if request.action == "acknowledge" else "alert.resolved"
        ),
        "occurred_at": transition["occurred_at"],
        "network_id": alert["network_id"],
        "nowcast_run_id": alert["nowcast_run_id"],
        "simulation_id": alert["simulation_id"],
        "alert": _forecast_alert_response(alert),
    }
    hydraulic_store.events.append(event)
    hydraulic_store.next_event_sequence += 1
    body = {
        "schema_version": 1,
        "alert": _forecast_alert_response(alert),
        "transition": transition,
        "event": event,
    }
    _idempotent_store(
        "forecast.transition", key, user, fingerprint, body, resource_id=alert_id
    )
    return body


@router.get("/forecast/events")
async def forecast_events(
    request: Request,
    network_id: str = Query(...),
    _: dict[str, Any] = Depends(_viewer),
) -> EventSourceResponse:
    """Canonical durable forecast stream with cursor replay and live fan-out."""

    network_id = _uuid(network_id, "network_id")
    last_event_id = request.headers.get("Last-Event-ID")
    if settings.DEPLOYMENT_ENVIRONMENT != "production":
        # Test/development adapter bridge: API-local outbox rows are fed into
        # the same manager ingress; production consumes Redis only.
        for event in list(hydraulic_store.events):
            try:
                await sse_manager.broadcast_forecast_event(event)
            except ValueError:
                continue
    try:
        client_id, queue = await sse_manager.connect_forecast(
            last_event_id, network_id=network_id
        )
    except SSECursorExpiredError as exc:
        raise _error(
            409,
            exc.code,
            "SSE cursor is outside the retained forecast replay window",
            headers={
                "X-SSE-Earliest-Sequence": str(exc.earliest_sequence),
                "X-SSE-Latest-Sequence": str(exc.latest_sequence),
            },
        ) from exc
    generator = sse_manager.forecast_event_generator(client_id, queue, last_event_id)
    return EventSourceResponse(generator)


__all__ = ["hydraulic_router", "hydraulic_store", "reset_hydraulic_api_store", "router"]
