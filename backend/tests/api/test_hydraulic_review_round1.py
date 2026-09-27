"""Round-one API boundary regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.api.hydraulic_routes import reset_hydraulic_api_store
from app.core.config import settings
from app.core.security import create_access_token


def _headers(role: str = "operator", key: str = "round-one-key") -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token("round-one-user", extra_claims={"role": role}),
        "Idempotency-Key": key,
    }


@pytest.mark.asyncio
async def test_request_id_is_shared_by_header_and_error_body(client):
    reset_hydraulic_api_store()
    response = await client.get("/api/networks")
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


@pytest.mark.asyncio
async def test_operator_cannot_archive_and_archived_network_rejects_rainfall(client):
    reset_hydraulic_api_store()
    network = (
        await client.post(
            "/api/networks",
            json={"code": "ARCHIVE-ROUND", "name": "archive"},
            headers=_headers(key="archive-network-create"),
        )
    ).json()
    denied = await client.patch(
        f"/api/networks/{network['id']}",
        params={"expected_revision_id": network["current_revision_id"]},
        json={"status": "archived"},
        headers=_headers(key="archive-network-denied"),
    )
    assert denied.status_code == 403
    archived = await client.patch(
        f"/api/networks/{network['id']}",
        params={"expected_revision_id": network["current_revision_id"]},
        json={"status": "archived"},
        headers=_headers(role="admin", key="archive-network-admin"),
    )
    assert archived.status_code == 200
    body = {
        "network_id": network["id"],
        "name": "rain",
        "kind": "custom",
        "return_period_years": None,
        "step_seconds": 300,
        "duration_seconds": 300,
        "source_type": "synthetic",
        "algorithm": "synthetic-custom-v1",
        "random_seed": 1,
        "content_sha256": "a" * 64,
        "points": [
            {"point_index": 0, "valid_at": "2026-09-25T00:00:00Z", "intensity_mm_h": 0},
            {"point_index": 1, "valid_at": "2026-09-25T00:05:00Z", "intensity_mm_h": 1},
        ],
    }
    rejected = await client.post(
        "/api/rainfall-scenarios",
        json=body,
        headers=_headers(key="archive-rainfall"),
    )
    assert rejected.status_code == 409


@pytest.mark.asyncio
async def test_rainfall_patch_reuses_closed_invariants(client):
    reset_hydraulic_api_store()
    network = (
        await client.post(
            "/api/networks",
            json={"code": "RAIN-PATCH-ROUND", "name": "rain"},
            headers=_headers(key="rain-network"),
        )
    ).json()
    body = {
        "network_id": network["id"],
        "name": "rain",
        "kind": "custom",
        "return_period_years": None,
        "step_seconds": 300,
        "duration_seconds": 300,
        "source_type": "synthetic",
        "algorithm": "synthetic-custom-v1",
        "random_seed": 1,
        "content_sha256": "a" * 64,
        "points": [
            {"point_index": 0, "valid_at": "2026-09-25T00:00:00Z", "intensity_mm_h": 0},
            {"point_index": 1, "valid_at": "2026-09-25T00:05:00Z", "intensity_mm_h": 1},
        ],
    }
    scenario = (
        await client.post(
            "/api/rainfall-scenarios", json=body, headers=_headers(key="rain-create")
        )
    ).json()
    response = await client.patch(
        f"/api/rainfall-scenarios/{scenario['id']}",
        json={
            "expected_version": 1,
            "points": [
                {
                    "point_index": 0,
                    "valid_at": "2026-09-25T00:00:00Z",
                    "intensity_mm_h": -1,
                }
            ],
        },
        headers=_headers(key="rain-patch"),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_jwt_requires_access_type_and_nbf(client):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "round-one-user",
        "role": "viewer",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
    }
    missing_type = jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm="HS256")
    refresh = jwt.encode(
        {**claims, "type": "refresh"}, settings.JWT_SECRET_KEY, algorithm="HS256"
    )
    assert (
        await client.get(
            "/api/networks", headers={"Authorization": f"Bearer {missing_type}"}
        )
    ).status_code == 401
    assert (
        await client.get(
            "/api/networks", headers={"Authorization": f"Bearer {refresh}"}
        )
    ).status_code == 401


@pytest.mark.asyncio
async def test_map_data_reports_budgeted_incomplete_response(client):
    reset_hydraulic_api_store()
    network = (
        await client.post(
            "/api/networks",
            json={"code": "MAP-ROUND", "name": "map"},
            headers=_headers(key="map-network"),
        )
    ).json()
    for index in range(3):
        response = await client.post(
            f"/api/networks/{network['id']}/nodes",
            params={"expected_revision_id": network["current_revision_id"]},
            json={
                "stable_key": f"N-{index}",
                "kind": "junction",
                "geometry": {
                    "geometry_geojson": {
                        "type": "Point",
                        "coordinates": [121.3 + index / 100, 31.2],
                    }
                },
                "invert_elevation_m": 1,
            },
            headers=_headers(key=f"map-node-{index}"),
        )
        network["current_revision_id"] = response.json()["revision"]["id"]
    response = await client.get(
        f"/api/networks/{network['id']}/map-data",
        params={"limit": 1},
        headers={"Authorization": _headers()["Authorization"]},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["complete"] is False
    assert payload["truncated_layers"] == ["nodes"]
    assert payload["next_cursor"]
