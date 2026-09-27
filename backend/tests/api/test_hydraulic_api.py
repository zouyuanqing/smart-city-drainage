"""HTTP contract tests for the hydraulic business API."""

from __future__ import annotations

import json
import uuid

import pytest

from app.api.hydraulic_routes import reset_hydraulic_api_store
from app.core.security import create_access_token


@pytest.fixture(autouse=True)
def _isolated_hydraulic_store():
    reset_hydraulic_api_store()
    yield
    reset_hydraulic_api_store()


def _headers(role: str = "operator", key: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            "00000000-0000-0000-0000-000000000001",
            extra_claims={"role": role},
        )
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


async def _network(client, role: str = "operator") -> dict:
    response = await client.post(
        "/api/networks",
        json={"code": "SH-TEST", "name": "Shanghai test"},
        headers=_headers(role, "network-create-1"),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _node(
    client, network: dict, key: str, coordinates: list[float], elevation: float
) -> dict:
    response = await client.post(
        f"/api/networks/{network['id']}/nodes",
        params={"expected_revision_id": network["current_revision_id"]},
        json={
            "stable_key": key,
            "kind": "junction",
            "geometry": {
                "geometry_geojson": {"type": "Point", "coordinates": coordinates}
            },
            "invert_elevation_m": elevation,
        },
        headers=_headers("operator", f"node-{key}"),
    )
    assert response.status_code == 201, response.text
    result = response.json()
    network["current_revision_id"] = result["revision"]["id"]
    return result["entity"]


@pytest.mark.asyncio
async def test_network_crud_bbox_and_rbac(client):
    network = await _network(client)
    first = await _node(client, network, "N-1", [121.30, 31.20], 2.0)
    second = await _node(client, network, "N-2", [121.40, 31.30], 1.0)

    pipe_response = await client.post(
        f"/api/networks/{network['id']}/pipes",
        params={"expected_revision_id": network["current_revision_id"]},
        json={
            "stable_key": "P-1",
            "upstream_node_id": first["id"],
            "downstream_node_id": second["id"],
            "nominal_diameter_m": 0.3,
            "length_m": 100.0,
            "roughness_manning_n": 0.013,
            "geometry": {
                "geometry_geojson": {
                    "type": "LineString",
                    "coordinates": [[121.30, 31.20], [121.40, 31.30]],
                }
            },
        },
        headers=_headers("operator", "pipe-create-1"),
    )
    assert pipe_response.status_code == 201, pipe_response.text
    network["current_revision_id"] = pipe_response.json()["revision"]["id"]

    map_response = await client.get(
        f"/api/networks/{network['id']}/map-data",
        params={"bbox": "121.35,31.25,121.45,31.35"},
        headers=_headers("viewer"),
    )
    assert map_response.status_code == 200, map_response.text
    map_data = map_response.json()
    assert map_data["schema_version"] == "network-map-v2"
    assert [
        feature["properties"]["stable_key"] for feature in map_data["nodes"]["features"]
    ] == ["N-2"]
    assert map_data["pipes"]["features"][0]["properties"]["feature_index"] == 0
    assert map_data["target_manifest_sha256"]

    invalid_bbox = await client.get(
        f"/api/networks/{network['id']}/map-data",
        params={"bbox": "121.45,31.35,121.35,31.25"},
        headers=_headers("viewer"),
    )
    assert invalid_bbox.status_code == 422
    assert invalid_bbox.json()["code"] == "VALIDATION_ERROR"

    viewer_write = await client.post(
        "/api/networks",
        json={"code": "VIEWER", "name": "forbidden"},
        headers=_headers("viewer", "viewer-write-1"),
    )
    assert viewer_write.status_code == 403
    assert viewer_write.json()["code"] == "FORBIDDEN"

    missing = await client.get("/api/networks", headers={})
    assert missing.status_code == 401
    assert missing.json()["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_import_rejects_unsafe_and_oversized_files_and_replays_valid_request(
    client,
):
    unsafe = await client.post(
        "/api/networks/import",
        files={"file": ("../escape.inp", b"[TITLE]\n", "application/octet-stream")},
        headers=_headers("operator", "import-unsafe-1"),
    )
    assert unsafe.status_code == 422
    assert unsafe.json()["code"] == "UNSAFE_PATH"

    oversized = await client.post(
        "/api/networks/import",
        files={
            "file": (
                "large.inp",
                b"x" * (20 * 1024 * 1024 + 1),
                "application/octet-stream",
            )
        },
        headers=_headers("operator", "import-large-1"),
    )
    assert oversized.status_code == 413
    assert oversized.json()["code"] == "INPUT_TOO_LARGE"

    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "N-import",
                "properties": {"stable_key": "N-import", "invert_elevation_m": 1.0},
                "geometry": {"type": "Point", "coordinates": [121.3, 31.2]},
            }
        ],
    }
    payload = json.dumps(document).encode()
    headers = _headers("operator", "import-valid-1")
    first = await client.post(
        "/api/networks/import",
        files={"file": ("network.geojson", payload, "application/geo+json")},
        headers=headers,
    )
    assert first.status_code == 202, first.text
    second = await client.post(
        "/api/networks/import",
        files={"file": ("network.geojson", payload, "application/geo+json")},
        headers=headers,
    )
    assert second.status_code == 202
    assert second.json()["network_job_id"] == first.json()["network_job_id"]


@pytest.mark.asyncio
async def test_rainfall_crud_copy_and_idempotency(client):
    network = await _network(client)
    points = [
        {"point_index": 0, "valid_at": "2026-09-25T00:00:00Z", "intensity_mm_h": 0.0},
        {"point_index": 1, "valid_at": "2026-09-25T00:05:00Z", "intensity_mm_h": 2.0},
    ]
    body = {
        "network_id": network["id"],
        "name": "50 year",
        "kind": "design",
        "return_period_years": 50,
        "step_seconds": 300,
        "duration_seconds": 300,
        "source_type": "synthetic",
        "algorithm": "synthetic-design-chicago-v1",
        "random_seed": 7,
        "points": points,
        "content_sha256": "a" * 64,
    }
    first = await client.post(
        "/api/rainfall-scenarios",
        json=body,
        headers=_headers("operator", "rain-create-1"),
    )
    assert first.status_code == 201, first.text
    scenario = first.json()
    replay = await client.post(
        "/api/rainfall-scenarios",
        json=body,
        headers=_headers("operator", "rain-create-1"),
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == scenario["id"]

    copied = await client.post(
        f"/api/rainfall-scenarios/{scenario['id']}/copy",
        headers=_headers("operator", "rain-copy-1"),
    )
    assert copied.status_code == 201
    assert copied.json()["id"] != scenario["id"]
    assert copied.json()["random_seed"] == scenario["random_seed"]

    fetched = await client.get(
        f"/api/rainfall-scenarios/{scenario['id']}", headers=_headers("viewer")
    )
    assert fetched.status_code == 200
    assert len(fetched.json()["points"]) == 2


@pytest.mark.asyncio
async def test_simulation_submit_cancel_and_forecast_contract(client):
    network = await _network(client)
    scenario = await client.post(
        "/api/rainfall-scenarios",
        json={
            "network_id": network["id"],
            "name": "custom",
            "kind": "custom",
            "return_period_years": None,
            "step_seconds": 300,
            "duration_seconds": 300,
            "source_type": "synthetic",
            "algorithm": "synthetic-custom-v1",
            "random_seed": 1,
            "points": [
                {
                    "point_index": 0,
                    "valid_at": "2026-09-25T00:00:00Z",
                    "intensity_mm_h": 0.0,
                },
                {
                    "point_index": 1,
                    "valid_at": "2026-09-25T00:05:00Z",
                    "intensity_mm_h": 1.0,
                },
            ],
            "content_sha256": "b" * 64,
        },
        headers=_headers("operator", "rain-sim-1"),
    )
    assert scenario.status_code == 201, scenario.text
    request = {
        "network_id": network["id"],
        "network_revision_id": network["current_revision_id"],
        "rainfall_scenario_id": scenario.json()["id"],
        "parameter_set_id": str(uuid.uuid4()),
        "initial_state_id": None,
        "solver_selection": "auto",
        "start_at": "2026-09-25T00:00:00Z",
        "duration_seconds": 300,
        "frame_interval_seconds": 300,
        "ponding_enabled": False,
        "strict_stability": True,
    }
    submitted = await client.post(
        "/api/simulations", json=request, headers=_headers("operator", "sim-submit-1")
    )
    assert submitted.status_code == 202, submitted.text
    simulation = submitted.json()
    assert simulation["status"] == "queued"
    assert simulation["initial_state_id"]

    cancelled = await client.post(
        f"/api/simulations/{simulation['id']}/cancel",
        headers=_headers("operator", "sim-cancel-1"),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    replay = await client.post(
        f"/api/simulations/{simulation['id']}/cancel",
        headers=_headers("operator", "sim-cancel-1"),
    )
    assert replay.status_code == 200
    assert replay.json()["status"] == "cancelled"

    latest = await client.get(
        "/api/forecast/latest",
        params={"network_id": network["id"]},
        headers=_headers("viewer"),
    )
    assert latest.status_code == 200
    assert latest.json()["data_state"] == "not_ready"

    ranking = await client.get(
        "/api/forecast/risk-ranking",
        params={"network_id": network["id"]},
        headers=_headers("viewer"),
    )
    assert ranking.status_code == 200
    assert ranking.json()["items"] == []


@pytest.mark.asyncio
async def test_openapi_contains_hydraulic_surface(client):
    response = await client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path in (
        "/api/networks",
        "/api/networks/import",
        "/api/networks/{network_id}/map-data",
        "/api/rainfall-scenarios",
        "/api/simulations",
        "/api/forecast/latest",
        "/api/forecast/horizon",
        "/api/forecast/risk-ranking",
    ):
        assert path in paths
