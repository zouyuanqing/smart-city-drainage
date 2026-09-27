import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest

from app.api import routes
from app.core.security import get_password_hash
from app.models.db_models import RoleEnum, User


class _UnavailableDatabase:
    """Async context manager that fails like a dead database connection."""

    async def __aenter__(self):
        raise RuntimeError("database unavailable")

    async def __aexit__(self, *exc_info):
        return False


@pytest.fixture
def test_database(monkeypatch, db_session):
    """The login route opens its own session, so patch the symbol it uses."""

    @asynccontextmanager
    async def _session():
        yield db_session

    monkeypatch.setattr(routes, "get_db_session", _session)
    return db_session


@pytest.fixture
def failing_database(monkeypatch):
    monkeypatch.setattr(routes, "get_db_session", _UnavailableDatabase)
    monkeypatch.setattr(routes.settings, "AUTH_FALLBACK_ADMIN_ENABLED", False)
    monkeypatch.setattr(routes.settings, "DEPLOYMENT_ENVIRONMENT", "development")
    return _UnavailableDatabase


async def _create_user(db_session, username="admin", password="Admin@123456"):
    from sqlalchemy import select

    existing = await db_session.execute(select(User).where(User.username == username))
    user = existing.scalar_one_or_none()
    if user is None:
        user = User(
            id=uuid.uuid4(),
            username=username,
            email=f"{username}@smartcity.local",
            full_name="系统管理员",
            hashed_password=get_password_hash(password),
            role=RoleEnum.admin,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(user)
    else:
        user.hashed_password = get_password_hash(password)
        user.role = RoleEnum.admin
        user.is_active = True
    await db_session.commit()
    return user


@pytest.mark.asyncio
async def test_login_success(client, test_database):
    await _create_user(test_database)

    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "Admin@123456"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client, test_database):
    await _create_user(test_database)

    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrongpassword"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_missing_fields(client):
    response = await client.post("/api/auth/login", json={"username": "admin"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_production_never_falls_back_to_fixed_admin(
    client, failing_database, monkeypatch
):
    """The reported issue: a Settings-valid production config still minted admin."""
    monkeypatch.setattr(routes.settings, "DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setattr(routes.settings, "AUTH_FALLBACK_ADMIN_ENABLED", False)

    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "Admin@123456"}
    )

    assert response.status_code in {401, 503}
    assert "access_token" not in response.json()


@pytest.mark.asyncio
async def test_production_ignores_fallback_flag_even_when_enabled(
    client, failing_database, monkeypatch
):
    monkeypatch.setattr(routes.settings, "DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setattr(routes.settings, "AUTH_FALLBACK_ADMIN_ENABLED", True)

    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "Admin@123456"}
    )

    assert response.status_code in {401, 503}
    assert "access_token" not in response.json()


@pytest.mark.asyncio
async def test_database_outage_without_opt_in_never_returns_a_token(
    client, failing_database
):
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "Admin@123456"}
    )

    assert response.status_code == 503
    assert "access_token" not in response.json()


@pytest.mark.asyncio
async def test_fixed_admin_needs_explicit_non_production_opt_in(
    client, failing_database, monkeypatch
):
    """The switch is a runtime consumer, not decoration."""
    monkeypatch.setattr(routes.settings, "AUTH_FALLBACK_ADMIN_ENABLED", True)

    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "Admin@123456"}
    )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_opt_in_fallback_still_rejects_other_credentials(
    client, failing_database, monkeypatch
):
    monkeypatch.setattr(routes.settings, "AUTH_FALLBACK_ADMIN_ENABLED", True)

    response = await client.post(
        "/api/auth/login", json={"username": "root", "password": "Admin@123456"}
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_unknown_user_without_opt_in_is_rejected(
    client, test_database, monkeypatch
):
    monkeypatch.setattr(routes.settings, "AUTH_FALLBACK_ADMIN_ENABLED", False)

    response = await client.post(
        "/api/auth/login", json={"username": "ghost", "password": "whatever"}
    )

    assert response.status_code == 401
