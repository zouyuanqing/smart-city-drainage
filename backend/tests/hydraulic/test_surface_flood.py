"""Behavioral tests for the reduced-fidelity surface ponding model."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.services.hydraulic.surface_flood import (
    FloodStatus,
    SurfaceFloodConfig,
    SurfaceFloodModel,
    SurfaceFloodValidationError,
    assert_monotonic_cleanup,
    build_catchment_mapping,
    calculate_flood_depth,
    compute_surface_flood,
    recede_surface_flood,
    validate_surface_flood_frame,
)

UTC = timezone.utc
START = datetime(2026, 9, 25, tzinfo=UTC)
GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]],
}


def _catchment() -> dict[str, object]:
    return {
        "stable_key": "C-1",
        "area_m2": 1_000.0,
        "elevation_m": 10.0,
        "outlet_node_key": "N-1",
        "geometry": GEOMETRY,
    }


def _node(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "stable_key": "N-1",
        "longitude": 5.0,
        "latitude": 5.0,
        "rim_elevation_m": 11.0,
        "invert_elevation_m": 8.0,
    }
    value.update(overrides)
    return value


def _pipe(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "stable_key": "P-1",
        "upstream_node_key": "N-1",
        "downstream_node_key": "N-2",
        "fill_ratio": 0.0,
    }
    value.update(overrides)
    return value


def _device(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "stable_key": "D-1",
        "longitude": 5.0,
        "latitude": 5.0,
        "elevation_m": 9.5,
        "catchment_id": "C-1",
    }
    value.update(overrides)
    return value


def test_overflow_maps_to_catchment_and_emits_depth_area_and_geojson() -> None:
    frame = compute_surface_flood(
        [_catchment()],
        [_node()],
        [],
        [_device()],
        node_overflow_m3={"N-1": 100.0},
        frame_index=0,
        valid_at=START,
        dt_seconds=300.0,
        config=SurfaceFloodConfig(
            flood_depth_threshold_m=0.05,
            reporting_depth_threshold_m=0.0,
            max_depth_m=1.0,
            recession_rate_m_per_s=0.0,
        ),
    )

    assert len(frame.cells) == 1
    cell = frame.cells[0]
    assert cell.catchment_id == "C-1"
    assert cell.depth_m == pytest.approx(0.1)
    assert cell.area_m2 == pytest.approx(100.0)
    assert cell.volume_m3 == pytest.approx(100.0)
    assert cell.status is FloodStatus.flooded
    assert cell.threshold_exceeded is True
    assert cell.device_elevation_m == pytest.approx(9.5)
    assert cell.device_ids == ("D-1",)
    assert frame.mapping.node_to_catchment == {"N-1": "C-1"}
    assert frame.mapping.device_to_catchment == {"D-1": "C-1"}

    assert frame.geojson["type"] == "FeatureCollection"
    assert frame.geojson["properties"]["entity_type"] == "ponding"
    assert frame.geojson["properties"]["simplified_extent"] is True
    assert len(frame.geojson["features"]) == 1
    feature = frame.geojson["features"][0]
    assert feature["geometry"] == GEOMETRY
    assert feature["properties"]["depth_m"] == pytest.approx(0.1)
    assert feature["properties"]["area_m2"] == pytest.approx(100.0)
    json.dumps(frame.geojson, allow_nan=False)


def test_no_rain_recession_is_non_increasing_and_reaches_dry() -> None:
    config = SurfaceFloodConfig(
        recession_rate_m_per_s=0.0001,
        flood_depth_threshold_m=0.15,
        max_depth_m=1.0,
    )
    first = compute_surface_flood(
        [_catchment()],
        [_node()],
        [],
        [_device()],
        initial_depth_m={"C-1": 0.1},
        frame_index=0,
        valid_at=START,
        dt_seconds=60.0,
        config=config,
    )
    second = compute_surface_flood(
        [_catchment()],
        [_node()],
        [],
        [_device()],
        frame_index=1,
        valid_at=datetime(2026, 9, 25, 0, 1, tzinfo=UTC),
        dt_seconds=60.0,
        previous_frame=first,
        config=config,
    )

    assert second.cells[0].depth_m < first.cells[0].depth_m
    assert second.cells[0].area_m2 <= first.cells[0].area_m2
    assert second.cells[0].volume_m3 <= first.cells[0].volume_m3
    assert second.cells[0].status is FloodStatus.receding
    assert second.geojson["properties"]["simplified_extent"] is True
    assert_monotonic_cleanup(first, second)

    model = SurfaceFloodModel(
        [_catchment()],
        [_node()],
        [],
        [_device()],
        config=config,
        initial_frame=first,
    )
    model.step(valid_at=second.valid_at)
    assert model.current_frame is not None
    assert model.current_frame.cells[0].depth_m <= second.cells[0].depth_m


def test_thresholds_gate_node_and_pipe_sources() -> None:
    config = SurfaceFloodConfig(
        node_overflow_threshold_m=0.10,
        pipe_overload_threshold_ratio=1.0,
        flood_depth_threshold_m=0.01,
        max_depth_m=1.0,
        recession_rate_m_per_s=0.0,
    )
    below = compute_surface_flood(
        [_catchment()],
        [
            _node(overflow_depth_m=0.10),
            _node(stable_key="N-2", longitude=50.0, latitude=50.0),
        ],
        [_pipe(fill_ratio=1.0)],
        [_device()],
        frame_index=0,
        valid_at=START,
        dt_seconds=300.0,
        config=config,
    )
    assert below.cells[0].depth_m == pytest.approx(0.0)
    assert below.cells[0].status is FloodStatus.dry
    assert below.geojson["features"] == []

    above = compute_surface_flood(
        [_catchment()],
        [
            _node(overflow_depth_m=0.20),
            _node(stable_key="N-2", longitude=50.0, latitude=50.0),
        ],
        [_pipe(fill_ratio=1.20, capacity_m3s=2.0)],
        [_device()],
        pipe_overload_ratio={"P-1": 1.20},
        frame_index=1,
        valid_at=datetime(2026, 9, 25, 0, 5, tzinfo=UTC),
        dt_seconds=300.0,
        config=config,
    )
    assert above.cells[0].depth_m > 0.0
    assert above.cells[0].threshold_exceeded is True
    assert "node:N-1" in above.cells[0].source_ids
    assert "pipe:P-1" in above.cells[0].source_ids


def test_invalid_catchment_geometry_fails_closed() -> None:
    invalid = _catchment()
    invalid["geometry"] = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]],
    }
    with pytest.raises(SurfaceFloodValidationError) as exc_info:
        compute_surface_flood([invalid], [_node()], [], [_device()])
    assert exc_info.value.code == "INVALID_GEOJSON"


def test_negative_source_values_are_rejected_instead_of_clamped() -> None:
    with pytest.raises(SurfaceFloodValidationError) as exc_info:
        compute_surface_flood(
            [_catchment()],
            [_node(overflow_m3s=-0.1)],
            [],
            [_device()],
        )
    assert exc_info.value.code == "NEGATIVE_INPUT"


def test_mapping_is_explicit_and_ambiguity_is_reported() -> None:
    mapping = build_catchment_mapping(
        [_catchment()],
        [
            _node(),
            _node(stable_key="N-2", longitude=50.0, latitude=50.0),
        ],
        [_pipe()],
        [_device(catchment_id=None, longitude=50.0, latitude=50.0)],
    )
    assert mapping.node_to_catchment == {"N-1": "C-1"}
    assert mapping.pipe_to_catchment == {"P-1": "C-1"}
    assert mapping.device_to_catchment == {}
    assert mapping.unmapped_devices == ("D-1",)
    assert mapping.complete is False
    assert mapping.warnings


def test_integer_coordinates_and_negative_elevations_remain_valid() -> None:
    geometry = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
    }
    frame = compute_surface_flood(
        [
            {
                "stable_key": "C-LOW",
                "area_m2": 100.0,
                "elevation_m": -2.0,
                "geometry": geometry,
                "outlet_node_key": "N-LOW",
            }
        ],
        [
            {
                "stable_key": "N-LOW",
                "geometry": {"type": "Point", "coordinates": [0.5, 0.5]},
                "rim_elevation_m": -1.0,
            }
        ],
        [],
        [
            {
                "stable_key": "D-LOW",
                "longitude": 0.5,
                "latitude": 0.5,
                "elevation_m": -3.0,
            }
        ],
        node_overflow_m3={"N-LOW": 10.0},
        config=SurfaceFloodConfig(recession_rate_m_per_s=0.0),
    )
    assert frame.cells[0].reference_elevation_m == pytest.approx(-3.0)
    assert frame.cells[0].water_surface_elevation_m < 0.0
    assert frame.geojson["features"][0]["geometry"]["coordinates"][0][0] == [0.0, 0.0]


def test_frame_validation_rejects_invalid_state_without_publishing() -> None:
    frame = compute_surface_flood(
        [_catchment()],
        [_node()],
        [],
        [_device()],
        initial_depth_m={"C-1": 0.1},
        frame_index=0,
        valid_at=START,
        config=SurfaceFloodConfig(recession_rate_m_per_s=0.0),
    )
    validate_surface_flood_frame(frame)
    assert frame.to_dict()["model_fidelity"] == "reduced_fidelity"
    assert frame.to_dict()["is_authoritative"] is False
    assert len(frame.content_sha256()) == 64


def test_pipe_mapping_rejects_conflicting_upstream_and_downstream_catchments() -> None:
    second_geometry = {
        "type": "Polygon",
        "coordinates": [
            [[10.0, 0.0], [20.0, 0.0], [20.0, 10.0], [10.0, 10.0], [10.0, 0.0]]
        ],
    }
    catchments = [
        _catchment(),
        {
            "stable_key": "C-2",
            "area_m2": 1_000.0,
            "elevation_m": 9.0,
            "outlet_node_key": "N-2",
            "geometry": second_geometry,
        },
    ]
    nodes = [
        _node(),
        _node(stable_key="N-2", longitude=15.0, latitude=5.0),
    ]
    pipe = _pipe(downstream_node_key="N-2")

    with pytest.raises(SurfaceFloodValidationError, match="ambiguous|conflict"):
        build_catchment_mapping(
            catchments,
            nodes,
            [pipe],
            [],
            strict_mapping=True,
        )


@pytest.mark.parametrize(
    ("catchment", "device"),
    [
        (_catchment() | {"outlet_node_key": "MISSING"}, _device()),
        (
            _catchment(),
            _device(
                catchment_id=None, node_key="MISSING", longitude=50.0, latitude=50.0
            ),
        ),
    ],
)
def test_dangling_outlet_and_device_references_fail_in_strict_mode(
    catchment: dict[str, object],
    device: dict[str, object],
) -> None:
    with pytest.raises(SurfaceFloodValidationError, match="missing|dangling|unknown"):
        build_catchment_mapping(
            [catchment],
            [_node()],
            [],
            [device],
            strict_mapping=True,
        )


def test_previous_frame_mapping_cannot_bypass_frame_grid_and_revision_checks() -> None:
    frame = compute_surface_flood(
        [_catchment()],
        [_node()],
        [],
        [_device()],
        valid_at=START,
    )
    previous = frame.to_dict()
    previous["frame_index"] = 0
    previous["valid_at"] = frame.valid_at.isoformat()
    previous["network_revision_id"] = "other-revision"

    with pytest.raises(
        SurfaceFloodValidationError, match="previous|revision|monotonic"
    ):
        compute_surface_flood(
            [_catchment()],
            [_node()],
            [],
            [_device()],
            frame_index=1,
            valid_at=frame.valid_at + timedelta(minutes=5),
            previous_frame=previous,
        )


def test_negative_water_surface_is_rejected_instead_of_clamped_to_dry() -> None:
    with pytest.raises(SurfaceFloodValidationError, match="water_surface|depth"):
        calculate_flood_depth(-0.1, 0.0)


def test_geojson_validation_requires_complete_feature_collection_envelope() -> None:
    frame = compute_surface_flood(
        [_catchment()],
        [_node()],
        [],
        [_device()],
        valid_at=START,
    )
    incomplete = replace(frame, geojson={"type": "FeatureCollection"})

    with pytest.raises(SurfaceFloodValidationError, match="GeoJSON"):
        validate_surface_flood_frame(incomplete)


def test_public_frame_mappings_and_geojson_are_deeply_immutable() -> None:
    frame = compute_surface_flood(
        [_catchment()],
        [_node()],
        [],
        [_device()],
        valid_at=START,
    )
    original_hash = frame.content_sha256()

    with pytest.raises(TypeError):
        frame.geojson["properties"]["entity_type"] = "changed"
    with pytest.raises(TypeError):
        frame.mapping.node_to_catchment["N-1"] = "C-2"
    assert frame.content_sha256() == original_hash


def test_string_surcharge_flag_is_rejected_instead_of_truthy() -> None:
    with pytest.raises(SurfaceFloodValidationError, match="surcharged"):
        compute_surface_flood(
            [_catchment()],
            [
                _node(),
                _node(stable_key="N-2", longitude=50.0, latitude=50.0),
            ],
            [_pipe(surcharged="false")],
            [_device()],
            valid_at=START,
        )


def test_recession_preserves_devices_outlets_mapping_and_explicit_config() -> None:
    config = SurfaceFloodConfig(recession_rate_m_per_s=0.00001)
    frame = compute_surface_flood(
        [_catchment()],
        [_node()],
        [],
        [_device()],
        initial_depth_m={"C-1": 0.1},
        valid_at=START,
        config=config,
    )

    receded = recede_surface_flood(frame, config=config)

    assert receded.cells[0].device_ids == frame.cells[0].device_ids
    assert receded.cells[0].device_elevation_m == frame.cells[0].device_elevation_m
    assert receded.mapping.node_to_catchment == frame.mapping.node_to_catchment
    assert receded.mapping.device_to_catchment == frame.mapping.device_to_catchment


def test_stage_area_curve_links_and_transfer_ledger_are_not_silently_ignored() -> None:
    second_geometry = {
        "type": "Polygon",
        "coordinates": [
            [[10.0, 0.0], [20.0, 0.0], [20.0, 10.0], [10.0, 10.0], [10.0, 0.0]]
        ],
    }
    curve = [
        {"stage_m": 0.0, "area_m2": 0.0},
        {"stage_m": 0.5, "area_m2": 100.0},
        {"stage_m": 1.0, "area_m2": 1_000.0},
    ]
    catchments = [
        _catchment()
        | {
            "stage_area_curve": curve,
            "roughness_manning_n": 0.03,
            "depression_storage_m3": 0.0,
            "cell_links": [
                {
                    "stable_key": "L-1",
                    "from_catchment_id": "C-1",
                    "to_catchment_id": "C-2",
                    "crest_elevation_m": 9.9,
                    "width_m": 2.0,
                    "roughness_manning_n": 0.03,
                    "length_m": 10.0,
                }
            ],
        },
        {
            "stable_key": "C-2",
            "area_m2": 1_000.0,
            "elevation_m": 9.0,
            "outlet_node_key": "N-2",
            "geometry": second_geometry,
            "stage_area_curve": curve,
        },
    ]
    nodes = [
        _node(),
        _node(stable_key="N-2", longitude=15.0, latitude=5.0, rim_elevation_m=10.0),
    ]

    frame = compute_surface_flood(
        catchments,
        nodes,
        [],
        [],
        initial_depth_m={"C-1": 0.2},
        valid_at=START,
        config=SurfaceFloodConfig(recession_rate_m_per_s=0.0),
    )

    assert len(frame.transfer_ledger) == 1
    assert frame.transfer_ledger[0].kind == "cell_link:L-1"
    assert frame.transfer_ledger[0].volume_m3 > 0.0
    assert frame.cells[0].stage_area_curve
    assert frame.cells[0].roughness_manning_n == pytest.approx(0.03)
    assert frame.cells[1].depth_m > 0.0
