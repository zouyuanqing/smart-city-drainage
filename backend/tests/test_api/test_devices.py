import pytest


@pytest.mark.asyncio
async def test_get_devices(client):
    response = await client.get("/api/devices")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "devices" in data
    assert isinstance(data["devices"], list)


@pytest.mark.asyncio
async def test_get_alerts(client):
    response = await client.get("/api/alerts")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "alerts" in data
    assert isinstance(data["alerts"], list)


@pytest.mark.asyncio
async def test_get_sensors_latest(client):
    response = await client.get("/api/sensors/latest")
    assert response.status_code == 200
