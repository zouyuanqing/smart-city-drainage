"""Typed hydraulic-domain persistence and geometry contracts.

The persistence contract keeps canonical WKT and a numeric EPSG:4326 bounding box
in every spatial profile.  PostGIS may additionally materialize a typed geometry
column; the portable profile uses the same WKT/GeoJSON codec without requiring
PostGIS.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from enum import Enum
from typing import Any, Literal, Mapping, Sequence

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

GeometryType = Literal["Point", "LineString", "Polygon", "MultiPolygon"]
SpatialBackend = Literal["postgis", "portable"]
PublicCRS = Literal["EPSG:4326"]

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_WKT_TOKEN_RE = re.compile(
    r"(?P<space>\s+)|"
    r"(?P<word>[A-Za-z]+)|"
    r"(?P<number>[-+]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))(?:[Ee][-+]?\d+)?)|"
    r"(?P<syntax>[(),])"
)
_ALLOWED_GEOMETRY_TYPES = {"Point", "LineString", "Polygon", "MultiPolygon"}
_ALLOWED_ENTITY_GEOMETRY = {
    "node": {"Point"},
    "pipe": {"LineString"},
    "catchment": {"Polygon", "MultiPolygon"},
}


class HydraulicModel(BaseModel):
    """Base contract shared by hydraulic request/response models."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class NetworkStatus(str, Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class NetworkNodeKind(str, Enum):
    junction = "junction"
    outfall = "outfall"
    manhole = "manhole"
    storage = "storage"
    other = "other"


class RainfallScenarioKind(str, Enum):
    design = "design"
    custom = "custom"
    synthetic = "synthetic"


class RainfallReturnPeriodYears(int, Enum):
    one = 1
    two = 2
    three = 3
    five = 5
    ten = 10
    twenty = 20
    fifty = 50
    one_hundred = 100


class SimulationStatus(str, Enum):
    queued = "queued"
    leased = "leased"
    running = "running"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"
    timed_out = "timed_out"


class FrameQualityStatus(str, Enum):
    verified = "verified"
    warning = "warning"
    failed = "failed"


class CalibrationStatus(str, Enum):
    queued = "queued"
    leased = "leased"
    running = "running"
    retry_wait = "retry_wait"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"
    timed_out = "timed_out"


class ForecastAlertType(str, Enum):
    flood_forecast = "flood_forecast"
    pipe_surcharge = "pipe_surcharge"


class ForecastAlertStatus(str, Enum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"
    expired = "expired"


class ForecastAlertPhase(str, Enum):
    predicted = "predicted"
    observed = "observed"


class ConfidenceMethod(str, Enum):
    ensemble_exceedance_frequency = "ensemble_exceedance_frequency"
    calibrated_residual = "calibrated_residual"
    conformal = "conformal"
    calibrated_regression = "calibrated_regression"


class SolverId(str, Enum):
    swmm_kw = "swmm_kw"
    swmm_dw = "swmm_dw"
    py_mw = "py_mw"
    py_dw = "py_dw"
    py_ponding = "py_ponding"


class SolverSelection(str, Enum):
    auto = "auto"
    explicit = "explicit"


class BoundingBox(HydraulicModel):
    """Finite EPSG:4326 bounds in lon/lat order."""

    west: float = Field(ge=-180.0, le=180.0, allow_inf_nan=False)
    south: float = Field(ge=-90.0, le=90.0, allow_inf_nan=False)
    east: float = Field(ge=-180.0, le=180.0, allow_inf_nan=False)
    north: float = Field(ge=-90.0, le=90.0, allow_inf_nan=False)

    @field_validator("west", "south", "east", "north", mode="before")
    @classmethod
    def reject_boolean_and_nonfinite_ordinates(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("bbox ordinates must be finite numbers, not booleans")
        if isinstance(value, (int, float)) and not math.isfinite(float(value)):
            raise ValueError("bbox ordinates must be finite numbers")
        return value

    @model_validator(mode="after")
    def validate_order(self) -> "BoundingBox":
        if self.west > self.east:
            raise ValueError("bbox west must be less than or equal to east")
        if self.south > self.north:
            raise ValueError("bbox south must be less than or equal to north")
        return self

    @property
    def values(self) -> tuple[float, float, float, float]:
        return self.west, self.south, self.east, self.north


def _fail_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def _flatten_coordinates(
    value: Any, *, depth: int = 0, max_depth: int = 32
) -> list[tuple[float, float]]:
    """Flatten a GeoJSON coordinate tree and validate its final pair."""

    if depth > max_depth:
        raise ValueError(f"geometry coordinate nesting exceeds {max_depth} levels")
    if not isinstance(value, list) or not value:
        raise ValueError("geometry coordinates must be a non-empty array")
    if all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
    ):
        if len(value) != 2:
            raise ValueError("each coordinate must contain exactly two ordinates")
        lon, lat = float(value[0]), float(value[1])
        if not math.isfinite(lon) or not math.isfinite(lat):
            raise ValueError("coordinates must be finite")
        if not -180.0 <= lon <= 180.0:
            raise ValueError("longitude must be within [-180, 180]")
        if not -90.0 <= lat <= 90.0:
            raise ValueError("latitude must be within [-90, 90]")
        return [(lon, lat)]
    flattened: list[tuple[float, float]] = []
    for child in value:
        flattened.extend(
            _flatten_coordinates(child, depth=depth + 1, max_depth=max_depth)
        )
    return flattened


def _orientation(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (
        third[0] - first[0]
    )


def _on_segment(
    start: tuple[float, float],
    end: tuple[float, float],
    point: tuple[float, float],
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


def _validate_ring_is_simple(points: Sequence[tuple[float, float]]) -> None:
    edges = list(zip(points, points[1:]))
    for index, (first_start, first_end) in enumerate(edges):
        for other_index in range(index + 1, len(edges)):
            adjacent = other_index == index + 1 or (
                index == 0 and other_index == len(edges) - 1
            )
            if adjacent:
                continue
            second_start, second_end = edges[other_index]
            if _segments_intersect(first_start, first_end, second_start, second_end):
                raise ValueError("Polygon ring must not self-intersect")


def _ring_area(points: Sequence[tuple[float, float]]) -> float:
    return 0.5 * sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(points, points[1:])
    )


def _point_strictly_in_ring(
    point: Sequence[float], ring: Sequence[tuple[float, float]]
) -> bool:
    x, y = float(point[0]), float(point[1])
    for start, end in zip(ring, ring[1:]):
        if _orientation(start, end, (x, y)) == 0 and _on_segment(start, end, (x, y)):
            return False
    inside = False
    for start, end in zip(ring, ring[1:]):
        if (start[1] > y) != (end[1] > y):
            crossing_x = (end[0] - start[0]) * (y - start[1]) / (
                end[1] - start[1]
            ) + start[0]
            if x < crossing_x:
                inside = not inside
    return inside


def _rings_intersect(
    first: Sequence[tuple[float, float]], second: Sequence[tuple[float, float]]
) -> bool:
    return any(
        _segments_intersect(first_start, first_end, second_start, second_end)
        for first_start, first_end in zip(first, first[1:])
        for second_start, second_end in zip(second, second[1:])
    )


def _segments_overlap_interiors(
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
            min(first_start[0], first_end[0]), min(second_start[0], second_end[0])
        ) < min(max(first_start[0], first_end[0]), max(second_start[0], second_end[0]))
        overlap_y = max(
            min(first_start[1], first_end[1]), min(second_start[1], second_end[1])
        ) < min(max(first_start[1], first_end[1]), max(second_start[1], second_end[1]))
        return overlap_x and overlap_y
    return False


def _rings_overlap_interiors(
    first: Sequence[tuple[float, float]], second: Sequence[tuple[float, float]]
) -> bool:
    return any(
        _segments_overlap_interiors(first_start, first_end, second_start, second_end)
        for first_start, first_end in zip(first, first[1:])
        for second_start, second_end in zip(second, second[1:])
    )


def _point_in_polygon(
    point: Sequence[float], rings: Sequence[Sequence[tuple[float, float]]]
) -> bool:
    if not rings or not _point_strictly_in_ring(point, rings[0]):
        return False
    return not any(_point_strictly_in_ring(point, hole) for hole in rings[1:])


def _validate_polygon_topology(rings: Sequence[Sequence[tuple[float, float]]]) -> None:
    shell = rings[0]
    if abs(_ring_area(shell)) <= 1e-15:
        raise ValueError("Polygon exterior ring must have positive area")
    for hole in rings[1:]:
        if abs(_ring_area(hole)) <= 1e-15:
            raise ValueError("Polygon hole must have positive area")
        if any(not _point_strictly_in_ring(point, shell) for point in hole):
            raise ValueError("Polygon hole must lie strictly inside its shell")
        if _rings_intersect(shell, hole):
            raise ValueError("Polygon hole must not intersect its shell")
    holes = rings[1:]
    for index, first_hole in enumerate(holes):
        for second_hole in holes[index + 1 :]:
            if (
                _rings_intersect(first_hole, second_hole)
                or any(
                    _point_strictly_in_ring(point, second_hole) for point in first_hole
                )
                or any(
                    _point_strictly_in_ring(point, first_hole) for point in second_hole
                )
            ):
                raise ValueError(
                    "Polygon holes must not overlap or contain one another"
                )


def _validate_multipolygon_topology(polygons: Sequence[Any]) -> None:
    parsed: list[list[list[tuple[float, float]]]] = []
    for polygon in polygons:
        rings = [_flatten_coordinates(ring) for ring in polygon]
        _validate_polygon_topology(rings)
        parsed.append(rings)
    for index, first in enumerate(parsed):
        for second in parsed[index + 1 :]:
            boundary_overlap = any(
                _rings_overlap_interiors(first_ring, second_ring)
                for first_ring in first
                for second_ring in second
            )
            containment = any(
                _point_in_polygon(point, second) for point in first[0]
            ) or any(_point_in_polygon(point, first) for point in second[0])
            if boundary_overlap or containment:
                raise ValueError(
                    "MultiPolygon components must not overlap or contain one another"
                )


def _validate_polygon(coords: Any) -> list[tuple[float, float]]:
    if not isinstance(coords, list) or not coords:
        raise ValueError("Polygon coordinates require at least one ring")
    all_points: list[tuple[float, float]] = []
    rings: list[list[tuple[float, float]]] = []
    for ring in coords:
        points = _flatten_coordinates(ring)
        if len(points) < 4:
            raise ValueError("each Polygon ring requires at least four positions")
        if points[0] != points[-1]:
            raise ValueError("each Polygon ring must be closed")
        unique = {(round(x, 12), round(y, 12)) for x, y in points[:-1]}
        if len(unique) < 3:
            raise ValueError("each Polygon ring requires three distinct positions")
        _validate_ring_is_simple(points)
        rings.append(points)
        all_points.extend(points)
    _validate_polygon_topology(rings)
    return all_points


def _canonical_wkt_number(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("WKT coordinates must be finite")
    if value == 0.0:
        value = 0.0
    return format(value, ".15g")


def _format_wkt_coordinate(point: Sequence[float]) -> str:
    return f"{_canonical_wkt_number(point[0])} {_canonical_wkt_number(point[1])}"


def _geometry_to_wkt(geometry_type: str, coordinates: Any) -> str:
    if geometry_type == "Point":
        points = _flatten_coordinates(coordinates)
        if len(points) != 1:
            raise ValueError("Point requires exactly one position")
        return f"POINT ({_format_wkt_coordinate(points[0])})"
    if geometry_type == "LineString":
        points = _flatten_coordinates(coordinates)
        if len(points) < 2:
            raise ValueError("LineString requires at least two distinct positions")
        if len({(x, y) for x, y in points}) < 2:
            raise ValueError("LineString requires at least two distinct positions")
        return (
            "LINESTRING (" + ", ".join(_format_wkt_coordinate(p) for p in points) + ")"
        )
    if geometry_type == "Polygon":
        _validate_polygon(coordinates)
        rings = []
        for ring in coordinates:
            points = _flatten_coordinates(ring)
            rings.append(
                "(" + ", ".join(_format_wkt_coordinate(p) for p in points) + ")"
            )
        return "POLYGON (" + ", ".join(rings) + ")"
    if geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("MultiPolygon requires at least one Polygon")
        _validate_multipolygon_topology(coordinates)
        polygons = []
        for polygon in coordinates:
            _validate_polygon(polygon)
            rings = []
            for ring in polygon:
                points = _flatten_coordinates(ring)
                rings.append(
                    "(" + ", ".join(_format_wkt_coordinate(p) for p in points) + ")"
                )
            polygons.append("(" + ", ".join(rings) + ")")
        return "MULTIPOLYGON (" + ", ".join(polygons) + ")"
    raise ValueError(f"unsupported geometry type: {geometry_type}")


def _validate_geojson_crs(document: Mapping[str, Any]) -> None:
    if "crs" not in document:
        return
    crs = document["crs"]
    name = (
        crs.get("properties", {}).get("name")
        if isinstance(crs, dict) and isinstance(crs.get("properties"), dict)
        else None
    )
    if name != "EPSG:4326":
        raise ValueError("geometry CRS must be EPSG:4326")


def _parse_geojson_geometry(value: Any) -> tuple[str, Any, list[tuple[float, float]]]:
    try:
        document = json.loads(
            value if isinstance(value, str) else json.dumps(value),
            parse_constant=_fail_json_constant,
        )
    except (TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"invalid GeoJSON text: {exc}") from exc
    if not isinstance(document, dict):
        if isinstance(document, list):
            _flatten_coordinates(document)
        raise ValueError("GeoJSON geometry must be a JSON object")
    _validate_geojson_crs(document)
    if document.get("type") == "Feature":
        geometry = document.get("geometry")
        if not isinstance(geometry, dict):
            raise ValueError("GeoJSON Feature.geometry must be an object")
        document = geometry
        _validate_geojson_crs(document)
    geometry_type = document.get("type")
    if geometry_type not in _ALLOWED_GEOMETRY_TYPES:
        raise ValueError(f"unsupported GeoJSON geometry type: {geometry_type!r}")
    coordinates = document.get("coordinates")
    if geometry_type in {"Point", "LineString"}:
        points = _flatten_coordinates(coordinates)
        if geometry_type == "Point" and len(points) != 1:
            raise ValueError("Point requires exactly one position")
        if geometry_type == "LineString" and len(points) < 2:
            raise ValueError("LineString requires at least two positions")
    else:
        points = _validate_polygon(coordinates) if geometry_type == "Polygon" else []
        if geometry_type == "MultiPolygon":
            if not isinstance(coordinates, list) or not coordinates:
                raise ValueError("MultiPolygon requires at least one Polygon")
            for polygon in coordinates:
                points.extend(_validate_polygon(polygon))
            _validate_multipolygon_topology(coordinates)
    return geometry_type, coordinates, points


class _WktParser:
    def __init__(self, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("geometry_wkt must be non-empty text")
        self.tokens: list[str] = []
        position = 0
        while position < len(text):
            match = _WKT_TOKEN_RE.match(text, position)
            if match is None:
                raise ValueError(f"invalid WKT near character {position}")
            position = match.end()
            token = match.group(0)
            if match.lastgroup != "space":
                self.tokens.append(
                    token.lower() if match.lastgroup == "word" else token
                )
        if not self.tokens:
            raise ValueError("geometry_wkt must contain a geometry")
        self.index = 0

    def _peek(self) -> str | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def _take(self, expected: str | None = None) -> str:
        token = self._peek()
        if token is None or (expected is not None and token != expected):
            raise ValueError(f"expected WKT token {expected!r}, found {token!r}")
        self.index += 1
        assert token is not None
        return token

    def _coordinate(self) -> list[float]:
        x = float(self._take())
        if self._peek() == ",":
            raise ValueError("WKT coordinates use space separators, not commas")
        y = float(self._take())
        return [x, y]

    def _point(self) -> list[float]:
        self._take("(")
        point = list(self._coordinate())
        self._take(")")
        return point

    def _line(self) -> list[list[float]]:
        self._take("(")
        points = [self._coordinate()]
        while self._peek() == ",":
            self._take(",")
            points.append(self._coordinate())
        self._take(")")
        if len(points) < 2:
            raise ValueError("LineString requires at least two positions")
        return points

    def _polygon(self) -> list[list[list[float]]]:
        self._take("(")
        rings = [self._line()]
        while self._peek() == ",":
            self._take(",")
            rings.append(self._line())
        self._take(")")
        return rings

    def _geometry(self) -> tuple[str, Any]:
        raw_geometry_type = self._take().upper()
        geometry_type = next(
            (
                candidate
                for candidate in _ALLOWED_GEOMETRY_TYPES
                if candidate.upper() == raw_geometry_type
            ),
            None,
        )
        if geometry_type is None:
            raise ValueError(f"unsupported WKT geometry type: {raw_geometry_type}")
        coordinates: Any
        if geometry_type == "Point":
            coordinates = self._point()
        elif geometry_type == "LineString":
            coordinates = self._line()
        elif geometry_type == "Polygon":
            coordinates = self._polygon()
        else:
            self._take("(")
            polygons = [self._polygon()]
            while self._peek() == ",":
                self._take(",")
                polygons.append(self._polygon())
            self._take(")")
            coordinates = polygons
        return geometry_type, coordinates

    def parse(self) -> tuple[str, Any, list[tuple[float, float]]]:
        geometry_type, coordinates = self._geometry()
        if self._peek() is not None:
            raise ValueError("unexpected trailing WKT tokens")
        # Reuse the GeoJSON validator so both input paths have identical rules.
        parsed_type, _, points = _parse_geojson_geometry(
            {"type": geometry_type, "coordinates": coordinates}
        )
        return parsed_type, coordinates, points


def _parse_wkt_geometry(value: str) -> tuple[str, Any, list[tuple[float, float]]]:
    return _WktParser(value).parse()


def _bbox_from_points(points: Sequence[tuple[float, float]]) -> BoundingBox:
    if not points:
        raise ValueError("geometry has no coordinates")
    longitudes = [point[0] for point in points]
    latitudes = [point[1] for point in points]
    return BoundingBox(
        west=min(longitudes),
        south=min(latitudes),
        east=max(longitudes),
        north=max(latitudes),
    )


def _canonical_geojson(geometry_type: str, coordinates: Any) -> str:
    return json.dumps(
        {"type": geometry_type, "coordinates": coordinates},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


class GeometryDocument(HydraulicModel):
    """Canonical geometry representation shared by both spatial profiles."""

    geometry_wkt: str
    geometry_geojson: str
    geometry_type: GeometryType
    bbox: BoundingBox
    crs: PublicCRS = "EPSG:4326"
    spatial_backend: SpatialBackend = "portable"

    @model_validator(mode="before")
    @classmethod
    def hydrate_dual_path(cls, value: Any) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError("geometry must be an object")
        data = dict(value)
        wkt = data.get("geometry_wkt")
        geojson = data.get("geometry_geojson")
        if not wkt and not geojson:
            raise ValueError("provide geometry_wkt or geometry_geojson")
        parsed_wkt = _parse_wkt_geometry(wkt) if wkt else None
        parsed_geojson = _parse_geojson_geometry(geojson) if geojson else None
        if parsed_wkt and parsed_geojson:
            if parsed_wkt[0] != parsed_geojson[0] or _canonical_geojson(
                parsed_wkt[0], parsed_wkt[1]
            ) != _canonical_geojson(parsed_geojson[0], parsed_geojson[1]):
                raise ValueError("geometry_wkt and geometry_geojson disagree")
        if parsed_wkt is not None:
            parsed_geometry = parsed_wkt
        elif parsed_geojson is not None:
            parsed_geometry = parsed_geojson
        else:
            raise ValueError("provide geometry_wkt or geometry_geojson")
        geometry_type, coordinates, points = parsed_geometry
        expected_type = data.get("geometry_type")
        if expected_type and expected_type != geometry_type:
            raise ValueError("geometry_type does not match supplied coordinates")
        computed_bbox = _bbox_from_points(points)
        supplied_bbox = data.get("bbox")
        if supplied_bbox is not None:
            bbox = (
                BoundingBox.model_validate(supplied_bbox)
                if not isinstance(supplied_bbox, BoundingBox)
                else supplied_bbox
            )
            if any(
                abs(actual - expected) > 1e-9
                for actual, expected in zip(computed_bbox.values, bbox.values)
            ):
                raise ValueError("bbox does not match geometry coordinates")
        data.update(
            geometry_type=geometry_type,
            geometry_wkt=_geometry_to_wkt(geometry_type, coordinates),
            geometry_geojson=_canonical_geojson(geometry_type, coordinates),
            bbox=computed_bbox,
        )
        return data

    def require_entity_geometry(
        self, entity_type: Literal["node", "pipe", "catchment"]
    ) -> None:
        allowed = _ALLOWED_ENTITY_GEOMETRY[entity_type]
        if self.geometry_type not in allowed:
            raise ValueError(
                f"{entity_type} requires one of {sorted(allowed)}, "
                f"got {self.geometry_type}"
            )


class NetworkCreate(HydraulicModel):
    code: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=255)
    status: NetworkStatus = NetworkStatus.draft
    crs: PublicCRS = "EPSG:4326"
    spatial_backend: SpatialBackend = "portable"


class NetworkResponse(NetworkCreate):
    id: uuid.UUID
    current_revision_id: uuid.UUID | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class NetworkNodeCreate(HydraulicModel):
    stable_key: str = Field(min_length=1, max_length=128)
    kind: NetworkNodeKind
    geometry: GeometryDocument
    invert_elevation_m: float | None = Field(default=None, allow_inf_nan=False)
    rim_elevation_m: float | None = Field(default=None, allow_inf_nan=False)
    max_depth_m: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_geometry_and_levels(self) -> "NetworkNodeCreate":
        self.geometry.require_entity_geometry("node")
        levels = [
            value
            for value in (
                self.invert_elevation_m,
                self.rim_elevation_m,
                self.max_depth_m,
            )
            if value is not None
        ]
        if self.invert_elevation_m is not None and self.rim_elevation_m is not None:
            if self.rim_elevation_m <= self.invert_elevation_m:
                raise ValueError("rim_elevation_m must exceed invert_elevation_m")
        if (
            self.max_depth_m is not None
            and self.invert_elevation_m is not None
            and self.rim_elevation_m is not None
        ):
            if self.invert_elevation_m + self.max_depth_m > self.rim_elevation_m + 1e-9:
                raise ValueError("invert + max_depth must not exceed rim elevation")
        if not all(math.isfinite(value) for value in levels):
            raise ValueError("elevations must be finite")
        return self


class PipeSegmentCreate(HydraulicModel):
    stable_key: str = Field(min_length=1, max_length=128)
    upstream_node_id: uuid.UUID
    downstream_node_id: uuid.UUID
    nominal_diameter_m: float = Field(gt=0.0, allow_inf_nan=False)
    length_m: float = Field(gt=0.0, allow_inf_nan=False)
    roughness_manning_n: float = Field(gt=0.0, allow_inf_nan=False)
    geometry: GeometryDocument
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_pipe(self) -> "PipeSegmentCreate":
        self.geometry.require_entity_geometry("pipe")
        if self.upstream_node_id == self.downstream_node_id:
            raise ValueError("upstream and downstream node IDs must differ")
        return self


class CatchmentCreate(HydraulicModel):
    stable_key: str = Field(min_length=1, max_length=128)
    area_m2: float = Field(gt=0.0, allow_inf_nan=False)
    outfall_node_id: uuid.UUID | None = None
    geometry: GeometryDocument
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_catchment(self) -> "CatchmentCreate":
        self.geometry.require_entity_geometry("catchment")
        return self


class RainfallPoint(HydraulicModel):
    point_index: int = Field(ge=0)
    valid_at: AwareDatetime
    intensity_mm_h: float = Field(ge=0.0, allow_inf_nan=False)


class RainfallScenarioCreate(HydraulicModel):
    network_id: uuid.UUID
    name: str = Field(min_length=1, max_length=255)
    kind: RainfallScenarioKind
    return_period_years: RainfallReturnPeriodYears | Literal["custom"] | None = None
    step_seconds: int
    duration_seconds: int = Field(gt=0, le=259_200)
    source_type: Literal["synthetic"] = "synthetic"
    algorithm: Literal[
        "synthetic-design-chicago-v1",
        "synthetic-custom-v1",
        "synthetic-random-block-v1",
    ]
    random_seed: int
    points: tuple[RainfallPoint, ...] = Field(min_length=1)
    content_sha256: str = Field(pattern=_HASH_RE.pattern)

    @model_validator(mode="after")
    def validate_series(self) -> "RainfallScenarioCreate":
        if self.step_seconds not in {60, 300, 900}:
            raise ValueError("step_seconds must be one of 60, 300, 900")
        if self.kind is RainfallScenarioKind.design and not isinstance(
            self.return_period_years, RainfallReturnPeriodYears
        ):
            raise ValueError("design rainfall requires a numeric return_period_years")
        if (
            self.kind is RainfallScenarioKind.custom
            and self.return_period_years not in (None, "custom")
        ):
            raise ValueError("custom rainfall may set return_period_years='custom'")
        if (
            self.kind is RainfallScenarioKind.synthetic
            and self.return_period_years is not None
        ):
            raise ValueError("synthetic rainfall must not set return_period_years")
        expected_algorithms = {
            RainfallScenarioKind.design: "synthetic-design-chicago-v1",
            RainfallScenarioKind.custom: "synthetic-custom-v1",
            RainfallScenarioKind.synthetic: "synthetic-random-block-v1",
        }
        if self.algorithm != expected_algorithms[self.kind]:
            raise ValueError(
                f"{self.kind.value} rainfall requires its built-in algorithm"
            )
        expected_count = self.duration_seconds // self.step_seconds + 1
        if len(self.points) != expected_count:
            raise ValueError(
                "rainfall point count must equal "
                f"floor(duration/step)+1={expected_count}"
            )
        indexes = [point.point_index for point in self.points]
        if indexes != list(range(expected_count)):
            raise ValueError(
                "rainfall point indexes must be dense and ascending from zero"
            )
        if any(
            right.valid_at <= left.valid_at
            for left, right in zip(self.points, self.points[1:])
        ):
            raise ValueError("rainfall valid_at values must be strictly increasing")
        return self


class SimulationRunCreate(HydraulicModel):
    requested_by: uuid.UUID
    network_id: uuid.UUID
    network_revision_id: uuid.UUID
    network_revision_content_sha256: str = Field(pattern=_HASH_RE.pattern)
    target_manifest_id: uuid.UUID
    target_manifest_sha256: str = Field(pattern=_HASH_RE.pattern)
    snapshot_content_sha256: str = Field(pattern=_HASH_RE.pattern)
    initial_state_id: uuid.UUID
    initial_state_content_sha256: str = Field(pattern=_HASH_RE.pattern)
    rainfall_scenario_id: uuid.UUID
    parameter_set_id: uuid.UUID
    start_at: AwareDatetime
    duration_seconds: int = Field(gt=0)
    frame_interval_seconds: int = Field(gt=0)
    solver_selection: SolverSelection
    requested_solver_id: SolverId | None = None
    solver_id: SolverId
    solver_version: str = Field(min_length=1, max_length=128)
    request_fingerprint: str = Field(pattern=_HASH_RE.pattern)
    ponding_enabled: bool = False

    @model_validator(mode="after")
    def validate_solver(self) -> "SimulationRunCreate":
        if (
            self.solver_selection is SolverSelection.explicit
            and self.requested_solver_id is None
        ):
            raise ValueError("explicit solver selection requires requested_solver_id")
        if self.duration_seconds % self.frame_interval_seconds:
            raise ValueError("duration must align to the frame interval")
        return self


class SimulationFrame(HydraulicModel):
    attempt_id: uuid.UUID
    simulation_id: uuid.UUID
    attempt_no: int = Field(ge=1, le=3)
    frame_index: int = Field(ge=0)
    frame_time: AwareDatetime
    network_id: uuid.UUID
    network_revision_id: uuid.UUID
    target_manifest_id: uuid.UUID
    target_manifest_sha256: str = Field(pattern=_HASH_RE.pattern)
    solver_id: SolverId
    solver_version: str = Field(min_length=1, max_length=128)
    schema_version: int = Field(ge=1)
    payload: dict[str, Any]
    payload_bytes: int = Field(ge=0)
    payload_sha256: str = Field(pattern=_HASH_RE.pattern)
    quality_status: FrameQualityStatus


class SimulationResult(HydraulicModel):
    id: uuid.UUID
    simulation_id: uuid.UUID
    network_revision_id: uuid.UUID
    attempt_id: uuid.UUID
    fencing_token: int = Field(gt=0)
    summary: dict[str, Any]
    summary_sha256: str = Field(pattern=_HASH_RE.pattern)
    quality_report: dict[str, Any]
    quality_report_sha256: str = Field(pattern=_HASH_RE.pattern)
    max_mass_residual_relative: float | None = Field(
        default=None, ge=0.0, allow_inf_nan=False
    )
    max_courant: float | None = Field(default=None, ge=0.0, allow_inf_nan=False)
    completed_at: AwareDatetime


class CalibrationRunCreate(HydraulicModel):
    network_id: uuid.UUID
    network_revision_id: uuid.UUID
    baseline_simulation_id: uuid.UUID
    candidate_simulation_id: uuid.UUID | None = None
    observation_dataset_sha256: str = Field(pattern=_HASH_RE.pattern)
    split_manifest: dict[str, Any]
    split_manifest_sha256: str = Field(pattern=_HASH_RE.pattern)
    calibration_config_hash: str = Field(pattern=_HASH_RE.pattern)
    request_fingerprint: str = Field(pattern=_HASH_RE.pattern)
    requested_by: uuid.UUID

    @model_validator(mode="after")
    def validate_simulation_pair(self) -> "CalibrationRunCreate":
        if (
            self.candidate_simulation_id is not None
            and self.candidate_simulation_id == self.baseline_simulation_id
        ):
            raise ValueError("candidate simulation must differ from baseline")
        return self


class CalibrationReport(HydraulicModel):
    id: uuid.UUID
    calibration_run_id: uuid.UUID
    observation_dataset_sha256: str = Field(pattern=_HASH_RE.pattern)
    split_manifest: dict[str, Any]
    split_manifest_sha256: str = Field(pattern=_HASH_RE.pattern)
    metrics: dict[str, Any]
    report_sha256: str = Field(pattern=_HASH_RE.pattern)
    artifact_uri: str = Field(min_length=1)
    created_at: AwareDatetime


class ForecastAlertCreate(HydraulicModel):
    network_id: uuid.UUID
    nowcast_run_id: uuid.UUID
    nowcast_issued_at: AwareDatetime
    simulation_id: uuid.UUID
    network_revision_id: uuid.UUID
    target_manifest_id: uuid.UUID
    target_manifest_sha256: str = Field(pattern=_HASH_RE.pattern)
    probability_calibration_id: uuid.UUID
    probability_calibration_sha256: str = Field(pattern=_HASH_RE.pattern)
    forecast_rule_id: uuid.UUID
    forecast_rule_sha256: str = Field(pattern=_HASH_RE.pattern)
    alert_type: ForecastAlertType
    target_type: Literal["node", "pipe_segment"]
    target_id: uuid.UUID
    target_stable_key: str = Field(min_length=1, max_length=128)
    expected_at: AwareDatetime
    lead_time_minutes: int = Field(ge=0)
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    confidence_method: ConfidenceMethod
    status: ForecastAlertStatus = ForecastAlertStatus.open
    phase: ForecastAlertPhase = ForecastAlertPhase.predicted
    risk_score: float = Field(ge=0.0, le=100.0, allow_inf_nan=False)

    @field_validator("target_id")
    @classmethod
    def reject_nil_target(cls, value: uuid.UUID) -> uuid.UUID:
        if value == uuid.UUID(int=0):
            raise ValueError("target_id must not be the nil UUID")
        return value


class ResultRetentionPolicyRead(HydraulicModel):
    table_name: str
    hypertable: bool
    partition_column: str | None
    retention_days: int = Field(ge=1)
    chunk_interval: str | None
    enabled: bool


__all__ = [
    "BoundingBox",
    "CalibrationReport",
    "CalibrationRunCreate",
    "CalibrationStatus",
    "CatchmentCreate",
    "ConfidenceMethod",
    "ForecastAlertCreate",
    "ForecastAlertPhase",
    "ForecastAlertStatus",
    "ForecastAlertType",
    "FrameQualityStatus",
    "GeometryDocument",
    "NetworkCreate",
    "NetworkNodeCreate",
    "NetworkNodeKind",
    "NetworkResponse",
    "NetworkStatus",
    "PipeSegmentCreate",
    "RainfallPoint",
    "RainfallReturnPeriodYears",
    "RainfallScenarioCreate",
    "RainfallScenarioKind",
    "ResultRetentionPolicyRead",
    "SimulationFrame",
    "SimulationResult",
    "SimulationRunCreate",
    "SimulationStatus",
    "SolverId",
    "SolverSelection",
]
