"""Behavioral tests for canonical geometry and spatial-query helpers."""

from __future__ import annotations

import json

import pytest

from app.schemas.hydraulic_schemas import CatchmentCreate, NetworkNodeCreate
from app.services.hydraulic.geometry import (
    GeometryValidationError,
    build_spatial_query_plan,
    canonical_geojson,
    export_geojson_feature_collection,
    filter_rows_by_bbox,
    geometry_intersects_bbox,
    parse_geojson,
    parse_geojson_feature_collection,
    parse_query_bbox,
    parse_wkt,
    validate_geometry,
    validate_query_bbox,
)


def test_point_wkt_and_geojson_round_trip_to_the_same_geometry() -> None:
    from_wkt = parse_wkt("point(121.3000 31.2000)", entity_type="node")
    from_geojson = parse_geojson(
        {
            "type": "Feature",
            "properties": {"stable_key": "N-001"},
            "geometry": {"type": "Point", "coordinates": [121.3, 31.2]},
        },
        entity_type="node",
    )

    assert from_wkt.geometry_wkt == "POINT (121.3 31.2)"
    assert canonical_geojson(from_geojson) == (
        '{"coordinates":[121.3,31.2],"type":"Point"}'
    )
    assert from_geojson.geometry_wkt == from_wkt.geometry_wkt
    assert from_wkt.bbox.model_dump() == {
        "west": 121.3,
        "south": 31.2,
        "east": 121.3,
        "north": 31.2,
    }
    assert from_wkt.crs == "EPSG:4326"


def test_line_and_polygon_feature_collection_round_trip() -> None:
    network_id = "00000000-0000-0000-0000-000000000001"
    revision_id = "00000000-0000-0000-0000-000000000002"
    line_source = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "bbox": [121.3, 31.2, 121.31, 31.21],
        "properties": {
            "entity_type": "pipe",
            "network_id": network_id,
            "network_revision_id": revision_id,
        },
        "features": [
            {
                "type": "Feature",
                "id": "P-001",
                "properties": {"kind": "pipe", "network_id": network_id},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[121.3, 31.2], [121.31, 31.21]],
                },
            }
        ],
    }
    polygon_source = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "bbox": [121.3, 31.2, 121.31, 31.21],
        "properties": {
            "entity_type": "catchment",
            "network_id": network_id,
            "network_revision_id": revision_id,
        },
        "features": [
            {
                "type": "Feature",
                "id": "C-001",
                "properties": {"area_m2": 1250.0},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [121.3, 31.2],
                            [121.31, 31.2],
                            [121.31, 31.21],
                            [121.3, 31.2],
                        ]
                    ],
                },
            }
        ],
    }

    lines = parse_geojson_feature_collection(line_source, entity_type="pipe")
    polygons = parse_geojson_feature_collection(polygon_source, entity_type="catchment")
    exported = export_geojson_feature_collection(lines)

    assert [feature.id for feature in lines] == ["P-001"]
    assert [feature.geometry.geometry_type for feature in polygons] == ["Polygon"]
    assert lines[0].geometry.geometry_wkt == ("LINESTRING (121.3 31.2, 121.31 31.21)")
    assert exported["crs"]["properties"]["name"] == "EPSG:4326"
    assert exported["bbox"] == [121.3, 31.2, 121.31, 31.21]
    assert exported["properties"] == line_source["properties"]
    assert parse_geojson_feature_collection(exported, entity_type="pipe") == lines


def test_bbox_filter_is_inclusive_and_handles_point_line_and_polygon() -> None:
    bbox = parse_query_bbox("0,0,10,10")

    assert geometry_intersects_bbox(parse_wkt("POINT (0 0)"), bbox)
    assert not geometry_intersects_bbox(parse_wkt("POINT (10.0001 5)"), bbox)
    assert geometry_intersects_bbox(parse_wkt("LINESTRING (-1 5, 0 5)"), bbox)
    assert not geometry_intersects_bbox(parse_wkt("LINESTRING (-2 5, -1 5)"), bbox)
    assert geometry_intersects_bbox(
        parse_wkt("POLYGON ((2 2, 4 2, 4 4, 2 4, 2 2))"),
        bbox,
    )
    assert geometry_intersects_bbox(
        parse_wkt("POLYGON ((-1 -1, 11 -1, 11 11, -1 11, -1 -1))"),
        bbox,
    )
    assert not geometry_intersects_bbox(
        parse_wkt("POLYGON ((20 20, 21 20, 21 21, 20 21, 20 20))"),
        bbox,
    )
    assert not geometry_intersects_bbox(
        parse_wkt(
            "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0)," " (2 2, 8 2, 8 8, 2 8, 2 2))"
        ),
        parse_query_bbox("3,3,4,4"),
    )


def test_validation_report_distinguishes_geometry_errors() -> None:
    invalid_coordinate = validate_geometry(
        {"geometry_geojson": '{"type":"Point","coordinates":[NaN,31.2]}'}
    )
    assert invalid_coordinate.valid is False
    assert invalid_coordinate.issues[0].code == "INVALID_COORDINATES"

    wrong_crs = validate_geometry(
        {
            "geometry_geojson": (
                '{"type":"Point","coordinates":[121.3,31.2],'
                '"crs":{"properties":{"name":"EPSG:3857"}}}'
            )
        }
    )
    assert wrong_crs.issues[0].code == "CRS_UNKNOWN"

    wrong_bbox = validate_geometry(
        {
            "geometry_wkt": "POINT (121.3 31.2)",
            "bbox": {"west": 0, "south": 0, "east": 1, "north": 1},
        }
    )
    assert wrong_bbox.issues[0].code == "BBOX_MISMATCH"

    wrong_entity = validate_geometry(
        "LINESTRING (121.3 31.2, 121.31 31.21)", entity_type="node"
    )
    assert wrong_entity.issues[0].code == "ENTITY_GEOMETRY_MISMATCH"
    with pytest.raises(GeometryValidationError):
        wrong_entity.raise_for_errors()

    invalid_bbox = validate_query_bbox("10,0,10,10")
    assert invalid_bbox.valid is False
    assert invalid_bbox.issues[0].code == "INVALID_BBOX"

    nested_position = validate_geometry(
        '{"type":"Point","coordinates":[[[121.3,31.2]]]} '
    )
    assert nested_position.issues[0].code == "INVALID_COORDINATES"
    three_d = validate_geometry('{"type":"Point","coordinates":[121.3,31.2,10.0]}')
    assert three_d.issues[0].code == "INVALID_COORDINATES"
    self_intersecting = validate_geometry("POLYGON ((0 0, 2 2, 0 2, 2 0, 0 0))")
    assert self_intersecting.valid is False
    invalid_longitude = validate_geometry("POINT (181 31)")
    assert invalid_longitude.issues[0].code == "INVALID_COORDINATES"
    hole_outside_shell = validate_geometry(
        "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0),"
        " (20 20, 21 20, 21 21, 20 21, 20 20))"
    )
    assert hole_outside_shell.valid is False


def test_query_plans_keep_postgis_and_portable_paths_explicit() -> None:
    bbox = parse_query_bbox("121,31,122,32")

    scope = {
        "network_id": "00000000-0000-0000-0000-000000000001",
        "network_revision_id": "00000000-0000-0000-0000-000000000002",
    }
    portable = build_spatial_query_plan("portable", "pipe", bbox, **scope)
    assert portable.table == "hydraulic.pipe_segments"
    assert portable.geometry_column is None
    assert "min_lon" in portable.where_sql
    assert "ST_MakeEnvelope" not in portable.where_sql
    assert portable.exact_filter_required is True

    postgis = build_spatial_query_plan("postgis", "pipe", bbox, **scope)
    assert postgis.geometry_column == "geometry"
    assert "ST_MakeEnvelope" in postgis.where_sql
    assert postgis.exact_filter_required is False
    assert postgis.parameters == portable.parameters


def test_portable_text_filter_returns_rows_and_geometry_errors() -> None:
    result = filter_rows_by_bbox(
        [
            {"id": "inside", "geometry_wkt": "LINESTRING (1 1, 2 2)"},
            {"id": "outside", "geometry_wkt": "LINESTRING (20 20, 21 21)"},
            {"id": "broken", "geometry_wkt": "LINESTRING (not-a-number 1, 2 2)"},
            {"id": "wrong-type", "geometry_wkt": "POINT (1 1)"},
            "not-a-mapping",
        ],
        parse_query_bbox("0,0,10,10"),
        entity_type="pipe",
    )

    assert [row["id"] for row in result.matches] == ["inside"]
    assert [row["id"] for row in result.rejected] == ["outside"]
    assert [row["id"] for row in result.invalid if isinstance(row, dict)] == [
        "broken",
        "wrong-type",
    ]
    assert "not-a-mapping" in result.invalid
    assert {issue.code for issue in result.issues} == {
        "INVALID_WKT",
        "ENTITY_GEOMETRY_MISMATCH",
        "INVALID_ROW",
    }


def _collection() -> dict[str, object]:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "bbox": [0, 0, 2, 2],
        "properties": {
            "entity_type": "node",
            "network_id": "network-1",
            "network_revision_id": "revision-1",
        },
        "features": [
            {
                "type": "Feature",
                "id": "N-1",
                "geometry": {"type": "Point", "coordinates": [1, 1]},
                "properties": {},
            }
        ],
    }


def test_feature_collection_enforces_crs_bbox_stable_keys_type_and_scope() -> None:
    valid = _collection()
    parsed = parse_geojson_feature_collection(valid, entity_type="node")
    assert parsed[0].stable_key == "N-1"

    mutations: list[tuple[str, dict[str, object]]] = []
    wrong_crs = json.loads(json.dumps(valid))
    wrong_crs["crs"]["properties"]["name"] = "EPSG:3857"
    mutations.append(("CRS", wrong_crs))
    missing_bbox = json.loads(json.dumps(valid))
    missing_bbox.pop("bbox")
    mutations.append(("bbox", missing_bbox))
    duplicate = json.loads(json.dumps(valid))
    duplicate["features"].append(json.loads(json.dumps(duplicate["features"][0])))
    mutations.append(("stable", duplicate))
    wrong_type = json.loads(json.dumps(valid))
    wrong_type["features"][0]["geometry"] = {
        "type": "LineString",
        "coordinates": [[0, 0], [2, 2]],
    }
    mutations.append(("node requires", wrong_type))
    wrong_scope = json.loads(json.dumps(valid))
    wrong_scope["features"][0]["properties"]["network_id"] = "network-2"
    mutations.append(("network_id", wrong_scope))

    for expected, document in mutations:
        with pytest.raises(ValueError, match=expected):
            parse_geojson_feature_collection(document, entity_type="node")


def test_production_entity_dtos_reject_hole_topology_and_deep_nesting() -> None:
    with pytest.raises(ValueError, match="hole"):
        CatchmentCreate(
            stable_key="C-1",
            area_m2=1.0,
            geometry={
                "geometry_wkt": (
                    "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0),"
                    " (20 20, 21 20, 21 21, 20 21, 20 20))"
                )
            },
        )

    nested: object = [121.3, 31.2]
    for _ in range(40):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting|depth"):
        NetworkNodeCreate(
            stable_key="N-DEEP",
            kind="manhole",
            geometry={"geometry_geojson": json.dumps(nested)},
        )


@pytest.mark.parametrize(
    "wkt",
    [
        (
            "MULTIPOLYGON (((0 0, 4 0, 4 4, 0 4, 0 0),"
            " (1 1, 2 1, 2 2, 1 2, 1 1)),"
            " ((2 2, 6 2, 6 6, 2 6, 2 2)))"
        ),
        (
            "MULTIPOLYGON (((0 0, 10 0, 10 10, 0 10, 0 0)),"
            " ((1 1, 2 1, 2 2, 1 2, 1 1)))"
        ),
    ],
)
def test_multipolygon_components_may_not_overlap_or_be_contained(wkt: str) -> None:
    with pytest.raises(ValueError, match="overlap|contain"):
        CatchmentCreate(
            stable_key="C-MULTI",
            area_m2=1.0,
            geometry={"geometry_wkt": wkt},
        )


def test_multipolygon_components_may_touch_at_finite_boundary_points() -> None:
    geometry = CatchmentCreate(
        stable_key="C-TOUCH",
        area_m2=1.0,
        geometry={
            "geometry_wkt": (
                "MULTIPOLYGON (((0 0, 2 0, 2 2, 0 2, 0 0)),"
                " ((2 2, 4 2, 4 4, 2 4, 2 2)))"
            )
        },
    )

    assert geometry.geometry.geometry_type == "MultiPolygon"


def test_query_bbox_rejects_bool_and_direct_zero_area_instances() -> None:
    with pytest.raises(ValueError):
        parse_query_bbox([True, 0, 1, 1])

    zero_area = type(parse_query_bbox("0,0,1,1"))(
        west=0,
        south=0,
        east=0,
        north=1,
    )
    with pytest.raises(ValueError, match="west < east|south < north"):
        build_spatial_query_plan(
            "portable",
            "node",
            zero_area,
            network_id="network-1",
            network_revision_id="revision-1",
        )
    with pytest.raises(ValueError, match="west < east|south < north"):
        filter_rows_by_bbox([], zero_area, entity_type="node")


async def test_scoped_portable_repository_executes_candidates_and_exact_filter() -> (
    None
):
    import uuid

    from app.services.hydraulic.spatial_repository import HydraulicSpatialRepository

    class _Mappings:
        def all(self) -> list[dict[str, object]]:
            return [
                {"id": "inside", "geometry_wkt": "LINESTRING (1 1, 2 2)"},
                {"id": "outside", "geometry_wkt": "LINESTRING (20 20, 21 21)"},
                {"id": "wrong", "geometry_wkt": "POINT (1 1)"},
            ]

    class _Result:
        def mappings(self) -> _Mappings:
            return _Mappings()

    class _Session:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def execute(
            self, statement: object, parameters: dict[str, object]
        ) -> _Result:
            self.calls.append((str(statement), parameters))
            return _Result()

    network_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    revision_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    session = _Session()
    report = await HydraulicSpatialRepository("portable", session).list_by_bbox(
        entity_type="pipe",
        bbox=parse_query_bbox("0,0,10,10"),
        network_id=network_id,
        network_revision_id=revision_id,
    )
    assert [row["id"] for row in report.matches] == ["inside"]
    assert [row["id"] for row in report.rejected] == ["outside"]
    assert [row["id"] for row in report.invalid] == ["wrong"]
    sql, parameters = session.calls[0]
    assert "network_id = :network_id" in sql
    assert "network_revision_id = :network_revision_id" in sql
    assert parameters["network_id"] == str(network_id)
    assert parameters["network_revision_id"] == str(revision_id)
