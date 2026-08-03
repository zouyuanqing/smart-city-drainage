import pytest


@pytest.mark.asyncio
async def test_login_success(client):
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "Admin@123456"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrongpassword"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_missing_fields(client):
    response = await client.post("/api/auth/login", json={"username": "admin"})
    assert response.status_code == 422
