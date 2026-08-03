import pytest


@pytest.mark.asyncio
async def test_health_endpoint(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_system_status(client):
    response = await client.get("/api/system/status")
    assert response.status_code == 200
    data = response.json()
    assert "postgresql" in data
    assert "redis" in data
