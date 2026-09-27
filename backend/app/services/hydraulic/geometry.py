"""Canonical geometry helpers for the hydraulic spatial profiles.

This module is deliberately free of database and network access.  It converts
between the public WKT/GeoJSON representations, validates them through the
hydraulic schema contract, and provides the pure query plan consumed by the
PostGIS and portable repositories.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence, cast

from app.schemas.hydraulic_schemas import BoundingBox, GeometryDocument

EntityType = Literal["node", "pipe", "catchment"]


@dataclass(frozen=True)
class GeometryIssue:
    """A stable, serializable geometry validation problem."""

    code: str
    message: str
    path: str = "$"
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)


class GeometryValidationError(ValueError):
    """Raised by callers that prefer fail-fast handling over a report."""

    def __init__(self, issues: Sequence[GeometryIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(issue.message for issue in self.issues))


@dataclass(frozen=True)
class GeometryValidationReport:
    """Result of non-throwing geometry validation."""

    geometry: GeometryDocument | None
    issues: tuple[GeometryIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues

    def raise_for_errors(self) -> GeometryDocument:
        if self.issues:
            raise GeometryValidationError(self.issues)
        assert self.geometry is not None
        return self.geometry


@dataclass(frozen=True)
class BoundingBoxValidationReport:
    """Result of non-throwing map-query bbox validation."""

    bbox: BoundingBox | None
    issues: tuple[GeometryIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues

    def raise_for_errors(self) -> BoundingBox:
        if self.issues:
            raise GeometryValidationError(self.issues)
        assert self.bbox is not None
        return self.bbox


@dataclass(frozen=True)
class SpatialFilterReport:
    """Portable text-path result retaining rejected and invalid rows."""

    matches: tuple[Mapping[str, Any], ...]
    rejected: tuple[Mapping[str, Any], ...]
    invalid: tuple[Any, ...]
    issues: tuple[GeometryIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class SpatialQueryPlan:
    """Backend-neutral repository input for one fixed spatial query."""

    backend: Literal["postgis", "portable"]
    entity_type: EntityType
    table: str
    bbox: BoundingBox
    where_sql: str
    parameters: Mapping[str, Any]
    network_id: str
    network_revision_id: str
    order_by: str
    geometry_column: str | None
    exact_filter_required: bool


def _position(value: Any, *, path: str = "coordinates") -> None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(
            not isinstance(ordinate, (int, float)) or isinstance(ordinate, bool)
            for ordinate in value
        )
    ):
        raise ValueError(f"{path} must contain exactly two numeric ordinates")
    if not all(math.isfinite(float(ordinate)) for ordinate in value):
        raise ValueError(f"{path} coordinates must be finite")


def _line_positions(value: Any, *, path: str) -> None:
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError(f"{path} requires at least two positions")
    for index, position in enumerate(value):
        _position(position, path=f"{path}[{index}]")


def _ring_positions(value: Any, *, path: str) -> None:
    if not isinstance(value, list) or len(value) < 4:
        raise ValueError(f"{path} requires at least four positions")
    for index, position in enumerate(value):
        _position(position, path=f"{path}[{index}]")


def _validate_geojson_coordinate_shape(document: Mapping[str, Any]) -> None:
    """Reject coordinate nesting/Z ordinates before the schema flattener sees them."""

    geometry: Any = document
    if document.get("type") == "Feature":
        geometry = document.get("geometry")
    if not isinstance(geometry, Mapping):
        raise ValueError("GeoJSON geometry must be an object")
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        _position(coordinates)
    elif geometry_type == "LineString":
        _line_positions(coordinates, path="LineString.coordinates")
    elif geometry_type == "Polygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("Polygon.coordinates must contain at least one ring")
        for index, ring in enumerate(coordinates):
            _ring_positions(ring, path=f"Polygon.coordinates[{index}]")
    elif geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError(
                "MultiPolygon.coordinates must contain at least one polygon"
            )
        for polygon_index, polygon in enumerate(coordinates):
            if not isinstance(polygon, list) or not polygon:
                raise ValueError(
                    f"MultiPolygon.coordinates[{polygon_index}] must contain rings"
                )
            for ring_index, ring in enumerate(polygon):
                _ring_positions(
                    ring,
                    path=(
                        f"MultiPolygon.coordinates[{polygon_index}]" f"[{ring_index}]"
                    ),
                )
    else:
        # The schema owns the stable unsupported-type error and CRS checks.
        return


@dataclass(frozen=True)
class GeoJSONFeature:
    """A canonical feature ready for scoped GeoJSON export."""

    geometry: GeometryDocument
    properties: Mapping[str, Any]
    id: str | int | None = None
    stable_key: str | None = None
    entity_type: EntityType | None = None
    network_id: str | None = None
    network_revision_id: str | None = None


def parse_wkt(value: str, *, entity_type: EntityType | None = None) -> GeometryDocument:
    """Parse WKT into the canonical WKT/GeoJSON/bbox representation."""

    geometry = cast(
        GeometryDocument,
        GeometryDocument.model_validate({"geometry_wkt": value}),
    )
    _validate_canonical_geometry(geometry)
    if entity_type is not None:
        geometry.require_entity_geometry(entity_type)
    return geometry


def parse_geojson(
    value: str | Mapping[str, Any], *, entity_type: EntityType | None = None
) -> GeometryDocument:
    """Parse a GeoJSON geometry or Feature into canonical geometry fields."""

    if isinstance(value, str):
        try:
            document = json.loads(value)
        except (TypeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"invalid GeoJSON text: {exc}") from exc
        if not isinstance(document, Mapping):
            raise ValueError("GeoJSON geometry must be a JSON object")
        _validate_geojson_coordinate_shape(document)
        geometry_geojson = value
    elif isinstance(value, Mapping):
        _validate_geojson_coordinate_shape(value)
        try:
            geometry_geojson = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(f"invalid GeoJSON object: {exc}") from exc
    else:
        raise TypeError("GeoJSON must be JSON text or a mapping")
    geometry = cast(
        GeometryDocument,
        GeometryDocument.model_validate({"geometry_geojson": geometry_geojson}),
    )
    _validate_canonical_geometry(geometry)
    if entity_type is not None:
        geometry.require_entity_geometry(entity_type)
    return geometry


def _normalize_entity_type(value: Any) -> EntityType | None:
    if isinstance(value, str):
        normalized = value.strip().lower()
        aliases = {
            "nodes": "node",
            "pipes": "pipe",
            "pipe_segments": "pipe",
            "catchments": "catchment",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in {"node", "pipe", "catchment"}:
            return normalized  # type: ignore[return-value]
    return None


def _collection_metadata(
    document: Mapping[str, Any], entity_type: EntityType
) -> tuple[str, str, BoundingBox]:
    crs = document.get("crs")
    crs_name = (
        crs.get("properties", {}).get("name")
        if isinstance(crs, Mapping) and isinstance(crs.get("properties"), Mapping)
        else None
    )
    if crs_name != "EPSG:4326":
        raise ValueError("FeatureCollection CRS must be EPSG:4326")
    raw_bbox = document.get("bbox")
    if raw_bbox is None:
        raise ValueError("FeatureCollection requires a bbox")
    if not isinstance(raw_bbox, Sequence) or isinstance(raw_bbox, (str, bytes)):
        raise ValueError("FeatureCollection bbox must be west,south,east,north")
    if len(raw_bbox) != 4:
        raise ValueError("FeatureCollection bbox must be west,south,east,north")
    bbox = BoundingBox.model_validate(
        {
            "west": raw_bbox[0],
            "south": raw_bbox[1],
            "east": raw_bbox[2],
            "north": raw_bbox[3],
        }
    )
    metadata = document.get("properties", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("FeatureCollection.properties must be an object")
    declared_type = _normalize_entity_type(
        metadata.get("entity_type", metadata.get("layer"))
    )
    if declared_type is None or declared_type != entity_type:
        raise ValueError(
            f"FeatureCollection entity_type must match requested {entity_type}"
        )
    network_id = metadata.get("network_id")
    network_revision_id = metadata.get("network_revision_id")
    if not isinstance(network_id, str) or not network_id.strip():
        raise ValueError("FeatureCollection requires network_id scope")
    if not isinstance(network_revision_id, str) or not network_revision_id.strip():
        raise ValueError("FeatureCollection requires network_revision_id scope")
    return network_id.strip(), network_revision_id.strip(), bbox


def _feature_stable_key(
    raw_feature: Mapping[str, Any], index: int
) -> tuple[str | int | None, str]:
    feature_id = raw_feature.get("id")
    if feature_id is not None and (
        not isinstance(feature_id, (str, int)) or isinstance(feature_id, bool)
    ):
        raise ValueError(f"GeoJSON feature at index {index}.id must be text or number")
    properties = raw_feature.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValueError(
            f"GeoJSON feature at index {index}.properties must be an object"
        )
    property_key = properties.get("stable_key")
    if property_key is not None and (
        not isinstance(property_key, str) or not property_key.strip()
    ):
        raise ValueError(f"GeoJSON feature at index {index}.stable_key must be text")
    if feature_id is None and property_key is None:
        raise ValueError(
            f"GeoJSON feature at index {index} requires id or properties.stable_key"
        )
    if (
        feature_id is not None
        and property_key is not None
        and str(feature_id) != property_key
    ):
        raise ValueError(f"GeoJSON feature at index {index} id and stable_key disagree")
    stable_key = str(property_key if property_key is not None else feature_id).strip()
    if not stable_key:
        raise ValueError(f"GeoJSON feature at index {index} stable_key is empty")
    return feature_id, stable_key


def parse_geojson_feature_collection(
    value: str | Mapping[str, Any],
    *,
    entity_type: EntityType,
) -> tuple[GeoJSONFeature, ...]:
    """Import one scoped, single-layer FeatureCollection."""

    if isinstance(value, str):
        try:
            document = json.loads(value)
        except (TypeError, json.JSONDecodeError, RecursionError) as exc:
            raise ValueError(f"invalid GeoJSON text: {exc}") from exc
    elif isinstance(value, Mapping):
        document = value
    else:
        raise TypeError("GeoJSON FeatureCollection must be JSON text or a mapping")
    if not isinstance(document, Mapping) or document.get("type") != "FeatureCollection":
        raise ValueError("GeoJSON document must be a FeatureCollection")
    network_id, network_revision_id, collection_bbox = _collection_metadata(
        document, entity_type
    )
    raw_features = document.get("features")
    if not isinstance(raw_features, list):
        raise ValueError("GeoJSON FeatureCollection.features must be an array")
    features: list[GeoJSONFeature] = []
    stable_keys: set[str] = set()
    for index, raw_feature in enumerate(raw_features):
        if not isinstance(raw_feature, Mapping) or raw_feature.get("type") != "Feature":
            raise ValueError(f"GeoJSON feature at index {index} must be a Feature")
        raw_properties = raw_feature.get("properties", {})
        if raw_properties is None:
            raw_properties = {}
        if not isinstance(raw_properties, Mapping):
            raise ValueError(
                f"GeoJSON feature at index {index}.properties must be an object"
            )
        properties = dict(raw_properties)
        try:
            json.dumps(properties, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(
                f"GeoJSON feature at index {index}.properties is not finite JSON"
            ) from exc
        feature_id, stable_key = _feature_stable_key(raw_feature, index)
        normalized_key = stable_key.casefold()
        if normalized_key in stable_keys:
            raise ValueError(f"duplicate FeatureCollection stable_key {stable_key!r}")
        stable_keys.add(normalized_key)
        for property_name, expected in (
            ("network_id", network_id),
            ("network_revision_id", network_revision_id),
        ):
            if property_name in properties and properties[property_name] != expected:
                raise ValueError(
                    f"GeoJSON feature at index {index}.{property_name} is outside "
                    "the collection scope"
                )
        try:
            geometry = parse_geojson(raw_feature, entity_type=entity_type)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(
                f"invalid GeoJSON feature at index {index}: {exc}"
            ) from exc
        if not (
            collection_bbox.west <= geometry.bbox.west
            and collection_bbox.south <= geometry.bbox.south
            and collection_bbox.east >= geometry.bbox.east
            and collection_bbox.north >= geometry.bbox.north
        ):
            raise ValueError(
                f"GeoJSON feature at index {index} lies outside collection bbox"
            )
        features.append(
            GeoJSONFeature(
                geometry,
                properties,
                feature_id,
                stable_key=stable_key,
                entity_type=entity_type,
                network_id=network_id,
                network_revision_id=network_revision_id,
            )
        )
    return tuple(features)


def export_geojson_feature_collection(
    features: Iterable[GeoJSONFeature],
) -> dict[str, Any]:
    """Export canonical features as one explicitly scoped EPSG:4326 collection."""

    feature_list = tuple(features)
    if not feature_list:
        raise ValueError("FeatureCollection export requires at least one feature")
    scopes = {
        (
            feature.entity_type,
            feature.network_id,
            feature.network_revision_id,
        )
        for feature in feature_list
    }
    if len(scopes) != 1:
        raise ValueError("all exported features must share one network revision scope")
    entity_type, network_id, network_revision_id = next(iter(scopes))
    if entity_type not in {"node", "pipe", "catchment"}:
        raise ValueError("exported features require a valid entity_type")
    if not isinstance(network_id, str) or not network_id:
        raise ValueError("exported features require network_id scope")
    if not isinstance(network_revision_id, str) or not network_revision_id:
        raise ValueError("exported features require network_revision_id scope")

    exported: list[dict[str, Any]] = []
    stable_keys: set[str] = set()
    bboxes: list[BoundingBox] = []
    for index, feature in enumerate(feature_list):
        if not isinstance(feature.geometry, GeometryDocument):
            raise TypeError(
                f"feature at index {index} does not contain GeometryDocument"
            )
        try:
            feature.geometry.require_entity_geometry(entity_type)
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"feature at index {index} has the wrong geometry type"
            ) from exc
        if not feature.stable_key or not feature.stable_key.strip():
            raise ValueError(f"feature at index {index} requires a stable_key")
        key = feature.stable_key.casefold()
        if key in stable_keys:
            raise ValueError(f"duplicate exported stable_key {feature.stable_key!r}")
        stable_keys.add(key)
        properties = dict(feature.properties)
        try:
            json.dumps(properties, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(
                f"feature at index {index}.properties is not finite JSON"
            ) from exc
        item: dict[str, Any] = {
            "type": "Feature",
            "properties": properties,
            "geometry": json.loads(canonical_geojson(feature.geometry)),
        }
        if feature.id is not None:
            item["id"] = feature.id
        exported.append(item)
        bboxes.append(feature.geometry.bbox)
    collection_bbox = BoundingBox(
        west=min(bbox.west for bbox in bboxes),
        south=min(bbox.south for bbox in bboxes),
        east=max(bbox.east for bbox in bboxes),
        north=max(bbox.north for bbox in bboxes),
    )
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "bbox": list(collection_bbox.values),
        "properties": {
            "entity_type": entity_type,
            "network_id": network_id,
            "network_revision_id": network_revision_id,
        },
        "features": exported,
    }


def canonical_geojson(geometry: GeometryDocument) -> str:
    """Return the schema-defined compact GeoJSON geometry JSON."""

    return geometry.geometry_geojson


def _validation_issue_code(message: str, source: str) -> str:
    lowered = message.lower()
    if "crs" in lowered or "epsg:4326" in lowered:
        return "CRS_UNKNOWN"
    if "bbox" in lowered:
        return "BBOX_MISMATCH"
    if "unsupported" in lowered and "geometry type" in lowered:
        return "UNSUPPORTED_GEOMETRY_TYPE"
    if (
        "non-finite" in lowered
        or "finite" in lowered
        or "coordinate" in lowered
        or "longitude" in lowered
        or "latitude" in lowered
    ):
        return "INVALID_COORDINATES"
    return "INVALID_WKT" if source == "wkt" else "INVALID_GEOJSON"


def validate_geometry(
    value: GeometryDocument | str | Mapping[str, Any],
    *,
    entity_type: EntityType | None = None,
) -> GeometryValidationReport:
    """Validate a geometry without forcing callers to catch parser exceptions."""

    source = "geojson"
    try:
        if isinstance(value, GeometryDocument):
            geometry = GeometryDocument.model_validate(value.model_dump())
            _validate_canonical_geometry(geometry)
        elif isinstance(value, Mapping):
            if "geometry_wkt" in value or "geometry_geojson" in value:
                source = "wkt" if value.get("geometry_wkt") else "geojson"
                geometry = GeometryDocument.model_validate(value)
                _validate_canonical_geometry(geometry)
            else:
                geometry = parse_geojson(value)
        elif isinstance(value, str):
            stripped = value.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                source = "geojson"
                geometry = parse_geojson(value)
            else:
                source = "wkt"
                geometry = parse_wkt(value)
        else:
            raise TypeError(
                "geometry must be GeometryDocument, JSON text, or a mapping"
            )
        if entity_type is not None:
            try:
                geometry.require_entity_geometry(entity_type)
            except (KeyError, ValueError) as exc:
                return GeometryValidationReport(
                    None,
                    (
                        GeometryIssue(
                            "ENTITY_GEOMETRY_MISMATCH",
                            str(exc),
                            details={"entity_type": entity_type},
                        ),
                    ),
                )
    except (TypeError, ValueError, RecursionError) as exc:
        message = str(exc)
        return GeometryValidationReport(
            None,
            (GeometryIssue(_validation_issue_code(message, source), message),),
        )
    return GeometryValidationReport(geometry)


def _normalise_scope_id(value: str | uuid.UUID, field_name: str) -> str:
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"spatial query requires {field_name} scope")
    return value.strip()


def build_spatial_query_plan(
    backend: Literal["postgis", "portable"],
    entity_type: EntityType,
    bbox: BoundingBox | Mapping[str, float] | str,
    *,
    network_id: str | uuid.UUID,
    network_revision_id: str | uuid.UUID,
) -> SpatialQueryPlan:
    """Build the fixed SQL fragment used by either spatial storage profile."""

    if backend not in {"postgis", "portable"}:
        raise ValueError("spatial backend must be 'postgis' or 'portable'")
    tables: dict[EntityType, str] = {
        "node": "hydraulic.network_nodes",
        "pipe": "hydraulic.pipe_segments",
        "catchment": "hydraulic.catchments",
    }
    if entity_type not in tables:
        raise ValueError("entity_type must be 'node', 'pipe', or 'catchment'")
    if not isinstance(bbox, BoundingBox):
        bbox = parse_query_bbox(bbox)
    bbox = parse_query_bbox(
        {
            "west": bbox.west,
            "south": bbox.south,
            "east": bbox.east,
            "north": bbox.north,
        }
    )
    normalized_network_id = _normalise_scope_id(network_id, "network_id")
    normalized_revision_id = _normalise_scope_id(
        network_revision_id, "network_revision_id"
    )
    parameters: dict[str, Any] = {
        "network_id": normalized_network_id,
        "network_revision_id": normalized_revision_id,
        "west": bbox.west,
        "south": bbox.south,
        "east": bbox.east,
        "north": bbox.north,
    }
    scope_sql = (
        "network_id = :network_id " "AND network_revision_id = :network_revision_id"
    )
    if backend == "postgis":
        spatial_sql = "geometry && ST_MakeEnvelope(:west, :south, :east, :north, 4326)"
        geometry_column = "geometry"
        exact_filter_required = False
    else:
        spatial_sql = (
            "min_lon <= :east AND max_lon >= :west "
            "AND min_lat <= :north AND max_lat >= :south"
        )
        geometry_column = None
        exact_filter_required = True
    where_sql = f"{scope_sql} AND ({spatial_sql})"
    return SpatialQueryPlan(
        backend=backend,
        entity_type=entity_type,
        table=tables[entity_type],
        bbox=bbox,
        where_sql=where_sql,
        parameters=parameters,
        order_by=("network_id ASC, network_revision_id ASC, stable_key ASC, id ASC"),
        geometry_column=geometry_column,
        exact_filter_required=exact_filter_required,
        network_id=normalized_network_id,
        network_revision_id=normalized_revision_id,
    )


def filter_rows_by_bbox(
    rows: Iterable[Mapping[str, Any]],
    bbox: BoundingBox | Mapping[str, float] | str,
    *,
    entity_type: EntityType,
    geometry_key: str = "geometry_wkt",
) -> SpatialFilterReport:
    """Apply the portable WKT+bbox exact predicate to already-loaded rows."""

    if not isinstance(bbox, BoundingBox):
        bbox = parse_query_bbox(bbox)
    bbox = parse_query_bbox(
        {
            "west": bbox.west,
            "south": bbox.south,
            "east": bbox.east,
            "north": bbox.north,
        }
    )
    matches: list[Mapping[str, Any]] = []
    rejected: list[Mapping[str, Any]] = []
    invalid: list[Mapping[str, Any]] = []
    issues: list[GeometryIssue] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            issues.append(
                GeometryIssue(
                    "INVALID_ROW",
                    "spatial row must be a mapping",
                    path=f"$[{index}]",
                )
            )
            invalid.append(row)
            continue
        raw_geometry = row.get(geometry_key)
        if not isinstance(raw_geometry, str):
            issues.append(
                GeometryIssue(
                    "MISSING_COORDINATES",
                    f"spatial row is missing {geometry_key}",
                    path=f"$[{index}].{geometry_key}",
                )
            )
            invalid.append(row)
            continue
        report = validate_geometry(raw_geometry, entity_type=entity_type)
        if not report.valid or report.geometry is None:
            issues.extend(
                GeometryIssue(
                    issue.code,
                    issue.message,
                    path=f"$[{index}].{geometry_key}",
                    retryable=issue.retryable,
                    details=issue.details,
                )
                for issue in report.issues
            )
            invalid.append(row)
        elif geometry_intersects_bbox(report.geometry, bbox):
            matches.append(row)
        else:
            rejected.append(row)
    return SpatialFilterReport(
        tuple(matches), tuple(rejected), tuple(invalid), tuple(issues)
    )


def _coerce_query_ordinate(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("bbox ordinates must be finite numbers, not booleans")
    try:
        number = float(value)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("bbox ordinates must be finite numbers") from exc
    if not math.isfinite(number):
        raise ValueError("bbox ordinates must be finite numbers")
    return number


def parse_query_bbox(
    value: str | Sequence[float] | Mapping[str, float],
) -> BoundingBox:
    """Parse a map query bbox and enforce the stricter non-empty query rule."""

    if isinstance(value, str):
        parts = value.split(",")
        if len(parts) != 4:
            raise ValueError("bbox must contain west,south,east,north")
        try:
            coordinates = tuple(_coerce_query_ordinate(part.strip()) for part in parts)
        except ValueError as exc:
            raise ValueError("bbox ordinates must be finite numbers") from exc
    elif isinstance(value, Mapping):
        try:
            coordinates = tuple(
                _coerce_query_ordinate(value[key])
                for key in ("west", "south", "east", "north")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("bbox must contain west,south,east,north") from exc
    else:
        try:
            coordinates = tuple(_coerce_query_ordinate(item) for item in value)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("bbox ordinates must be finite numbers") from exc
    if len(coordinates) != 4 or not all(math.isfinite(item) for item in coordinates):
        raise ValueError("bbox ordinates must be finite numbers")
    bbox = cast(
        BoundingBox,
        BoundingBox.model_validate(
            dict(zip(("west", "south", "east", "north"), coordinates))
        ),
    )
    if bbox.west >= bbox.east or bbox.south >= bbox.north:
        raise ValueError("query bbox must have west < east and south < north")
    return bbox


def validate_query_bbox(
    value: str | Sequence[float] | Mapping[str, float],
) -> BoundingBoxValidationReport:
    """Validate a map-query bbox without raising on malformed user input."""

    try:
        return BoundingBoxValidationReport(parse_query_bbox(value))
    except (TypeError, ValueError, RecursionError) as exc:
        return BoundingBoxValidationReport(
            None,
            (GeometryIssue("INVALID_BBOX", str(exc)),),
        )


def _coordinates(geometry: GeometryDocument) -> tuple[str, Any]:
    document = json.loads(geometry.geometry_geojson)
    return document["type"], document["coordinates"]


def _point_in_bbox(point: tuple[float, float], bbox: BoundingBox) -> bool:
    return bbox.west <= point[0] <= bbox.east and bbox.south <= point[1] <= bbox.north


def _orientation(
    first: Sequence[float],
    second: Sequence[float],
    third: Sequence[float],
) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (
        third[0] - first[0]
    )


def _on_segment(
    start: Sequence[float], end: Sequence[float], point: Sequence[float]
) -> bool:
    return min(start[0], end[0]) <= point[0] <= max(start[0], end[0]) and min(
        start[1], end[1]
    ) <= point[1] <= max(start[1], end[1])


def _segments_intersect(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    first = _orientation(first_start, first_end, second_start)
    second = _orientation(first_start, first_end, second_end)
    third = _orientation(second_start, second_end, first_start)
    fourth = _orientation(second_start, second_end, first_end)
    if ((first > 0 and second < 0) or (first < 0 and second > 0)) and (
        (third > 0 and fourth < 0) or (third < 0 and fourth > 0)
    ):
        return True
    return (
        (first == 0 and _on_segment(first_start, first_end, second_start))
        or (second == 0 and _on_segment(first_start, first_end, second_end))
        or (third == 0 and _on_segment(second_start, second_end, first_start))
        or (fourth == 0 and _on_segment(second_start, second_end, first_end))
    )


def _point_in_ring(point: tuple[float, float], ring: Sequence[Sequence[float]]) -> bool:
    points = [(float(item[0]), float(item[1])) for item in ring]
    for start, end in zip(points, points[1:]):
        if _orientation(start, end, point) == 0 and _on_segment(start, end, point):
            return True
    inside = False
    for start, end in zip(points, points[1:]):
        if (start[1] > point[1]) != (end[1] > point[1]):
            crossing_x = (end[0] - start[0]) * (point[1] - start[1]) / (
                end[1] - start[1]
            ) + start[0]
            if point[0] < crossing_x:
                inside = not inside
    return inside


def _point_in_polygon(
    point: tuple[float, float], rings: Sequence[Sequence[Sequence[float]]]
) -> bool:
    if not rings or not _point_in_ring(point, rings[0]):
        return False
    return not any(_point_in_ring(point, hole) for hole in rings[1:])


def _point_strictly_in_ring(
    point: Sequence[float], ring: Sequence[Sequence[float]]
) -> bool:
    points = [(float(item[0]), float(item[1])) for item in ring]
    for start, end in zip(points, points[1:]):
        if _orientation(start, end, point) == 0 and _on_segment(start, end, point):
            return False
    inside = False
    for start, end in zip(points, points[1:]):
        if (start[1] > point[1]) != (end[1] > point[1]):
            crossing_x = (end[0] - start[0]) * (point[1] - start[1]) / (
                end[1] - start[1]
            ) + start[0]
            if point[0] < crossing_x:
                inside = not inside
    return inside


def _ring_area(ring: Sequence[Sequence[float]]) -> float:
    points = [(float(item[0]), float(item[1])) for item in ring]
    return 0.5 * sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(points, points[1:])
    )


def _rings_intersect(
    first: Sequence[Sequence[float]], second: Sequence[Sequence[float]]
) -> bool:
    return any(
        _segments_intersect(
            (float(first_start[0]), float(first_start[1])),
            (float(first_end[0]), float(first_end[1])),
            (float(second_start[0]), float(second_start[1])),
            (float(second_end[0]), float(second_end[1])),
        )
        for first_start, first_end in zip(first, first[1:])
        for second_start, second_end in zip(second, second[1:])
    )


def _rings_overlap_interiors(
    first: Sequence[Sequence[float]], second: Sequence[Sequence[float]]
) -> bool:
    normalized_first = [(float(point[0]), float(point[1])) for point in first]
    normalized_second = [(float(point[0]), float(point[1])) for point in second]

    def overlaps(
        first_start: tuple[float, float],
        first_end: tuple[float, float],
        second_start: tuple[float, float],
        second_end: tuple[float, float],
    ) -> bool:
        first_side = _orientation(first_start, first_end, second_start)
        second_side = _orientation(first_start, first_end, second_end)
        third_side = _orientation(second_start, second_end, first_start)
        fourth_side = _orientation(second_start, second_end, first_end)
        if first_side * second_side < 0 and third_side * fourth_side < 0:
            return True
        if first_side == second_side == third_side == fourth_side == 0:
            overlap_x = max(
                min(first_start[0], first_end[0]),
                min(second_start[0], second_end[0]),
            ) < min(
                max(first_start[0], first_end[0]),
                max(second_start[0], second_end[0]),
            )
            overlap_y = max(
                min(first_start[1], first_end[1]),
                min(second_start[1], second_end[1]),
            ) < min(
                max(first_start[1], first_end[1]),
                max(second_start[1], second_end[1]),
            )
            return overlap_x and overlap_y
        return False

    return any(
        overlaps(first_start, first_end, second_start, second_end)
        for first_start, first_end in zip(normalized_first, normalized_first[1:])
        for second_start, second_end in zip(normalized_second, normalized_second[1:])
    )


def _validate_polygon_topology(
    polygon: Sequence[Sequence[Sequence[float]]],
) -> None:
    if not polygon:
        raise ValueError("Polygon requires an exterior ring")
    shell = polygon[0]
    if abs(_ring_area(shell)) <= 1e-15:
        raise ValueError("Polygon exterior ring has zero area")
    for hole in polygon[1:]:
        if abs(_ring_area(hole)) <= 1e-15:
            raise ValueError("Polygon hole has zero area")
        if any(not _point_strictly_in_ring(point, shell) for point in hole):
            raise ValueError("Polygon hole must lie strictly inside its shell")
        if _rings_intersect(shell, hole):
            raise ValueError("Polygon hole must not intersect its shell")
    for index, first_hole in enumerate(polygon[1:]):
        for second_hole in polygon[index + 2 :]:
            if (
                _rings_intersect(first_hole, second_hole)
                or any(
                    _point_strictly_in_ring(point, second_hole) for point in first_hole
                )
                or any(
                    _point_strictly_in_ring(point, first_hole) for point in second_hole
                )
            ):
                raise ValueError("Polygon holes must not overlap")


def _validate_canonical_geometry(geometry: GeometryDocument) -> None:
    geometry_type, coordinates = _coordinates(geometry)
    if geometry_type == "Polygon":
        _validate_polygon_topology(coordinates)
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:
            _validate_polygon_topology(polygon)
        for index, first in enumerate(coordinates):
            for second in coordinates[index + 1 :]:
                boundary_overlap = any(
                    _rings_overlap_interiors(first_ring, second_ring)
                    for first_ring in first
                    for second_ring in second
                )
                containment = any(
                    _point_strictly_in_ring(point, second[0])
                    and not any(
                        _point_strictly_in_ring(point, hole) for hole in second[1:]
                    )
                    for point in first[0]
                ) or any(
                    _point_strictly_in_ring(point, first[0])
                    and not any(
                        _point_strictly_in_ring(point, hole) for hole in first[1:]
                    )
                    for point in second[0]
                )
                if boundary_overlap or containment:
                    raise ValueError(
                        "MultiPolygon components must not overlap "
                        "or contain one another"
                    )


def _bbox_edges(
    bbox: BoundingBox,
) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
    west, south = bbox.west, bbox.south
    east, north = bbox.east, bbox.north
    return (
        ((west, south), (east, south)),
        ((east, south), (east, north)),
        ((east, north), (west, north)),
        ((west, north), (west, south)),
    )


def _line_intersects_bbox(points: Sequence[Sequence[float]], bbox: BoundingBox) -> bool:
    normalized = [(float(point[0]), float(point[1])) for point in points]
    if any(_point_in_bbox(point, bbox) for point in normalized):
        return True
    for start, end in zip(normalized, normalized[1:]):
        if any(
            _segments_intersect(start, end, edge_start, edge_end)
            for edge_start, edge_end in _bbox_edges(bbox)
        ):
            return True
    return False


def _polygon_intersects_bbox(
    rings: Sequence[Sequence[Sequence[float]]], bbox: BoundingBox
) -> bool:
    normalized_rings = [
        [(float(point[0]), float(point[1])) for point in ring] for ring in rings
    ]
    for ring in normalized_rings:
        if any(_point_in_bbox(point, bbox) for point in ring):
            return True
    for corner in (
        (bbox.west, bbox.south),
        (bbox.east, bbox.south),
        (bbox.east, bbox.north),
        (bbox.west, bbox.north),
    ):
        if _point_in_polygon(corner, normalized_rings):
            return True
    bbox_edges = _bbox_edges(bbox)
    for ring in normalized_rings:
        for start, end in zip(ring, ring[1:]):
            if any(
                _segments_intersect(start, end, edge_start, edge_end)
                for edge_start, edge_end in bbox_edges
            ):
                return True
    return False


def geometry_intersects_bbox(geometry: GeometryDocument, bbox: BoundingBox) -> bool:
    """Return whether a canonical geometry intersects an inclusive EPSG:4326 bbox."""

    if not isinstance(geometry, GeometryDocument):
        raise TypeError("geometry must be a canonical GeometryDocument")
    if not isinstance(bbox, BoundingBox):
        bbox = BoundingBox.model_validate(bbox)
    if (
        geometry.bbox.east < bbox.west
        or geometry.bbox.west > bbox.east
        or geometry.bbox.north < bbox.south
        or geometry.bbox.south > bbox.north
    ):
        return False
    geometry_type, coordinates = _coordinates(geometry)
    if geometry_type == "Point":
        return _point_in_bbox((float(coordinates[0]), float(coordinates[1])), bbox)
    if geometry_type == "LineString":
        return _line_intersects_bbox(coordinates, bbox)
    if geometry_type == "Polygon":
        return _polygon_intersects_bbox(coordinates, bbox)
    if geometry_type == "MultiPolygon":
        return any(_polygon_intersects_bbox(polygon, bbox) for polygon in coordinates)
    raise ValueError(f"unsupported geometry type: {geometry_type}")
