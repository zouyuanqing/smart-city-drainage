"""Regression tests for authentication and legacy write-boundary hardening."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.api import routes
from app.core.security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
)
from app.models.db_models import RoleEnum, User
from app.services.mock_data_generator import mock_generator


@asynccontextmanager
async def _session_for(db_session):
    yield db_session


def _token(role: str) -> str:
    return create_access_token(
        subject=str(uuid.uuid4()),
        extra_claims={"role": role, "username": "security-test"},
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_server_owned_jwt_claims_cannot_be_overridden():
    token = create_access_token(
        "real-subject",
        extra_claims={
            "sub": "attacker-subject",
            "exp": datetime.now(timezone.utc) + timedelta(days=3650),
            "type": "refresh",
        },
    )
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "real-subject"
    assert payload["type"] == "access"
    expiry = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    assert expiry < datetime.now(timezone.utc) + timedelta(days=1)


@pytest.mark.asyncio
async def test_missing_bearer_is_401_not_500(client):
    response = await client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_inactive_user_cannot_obtain_a_token(client, db_session, monkeypatch):
    username = f"inactive-{uuid.uuid4().hex}"
    db_session.add(
        User(
            id=uuid.uuid4(),
            username=username,
            email=f"{username}@example.invalid",
            full_name="Inactive",
            hashed_password=get_password_hash("ValidPassword123!"),
            role=RoleEnum.viewer,
            is_active=False,
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()
    monkeypatch.setattr(routes, "get_db_session", lambda: _session_for(db_session))

    response = await client.post(
        "/api/auth/login",
        json={"username": username, "password": "ValidPassword123!"},
    )

    assert response.status_code == 401
    assert "access_token" not in response.json()


@pytest.mark.asyncio
async def test_inactive_claim_cannot_authorize_a_write(client):
    token = create_access_token(
        str(uuid.uuid4()),
        extra_claims={"role": RoleEnum.operator.value, "is_active": False},
    )
    response = await client.put("/api/mock/config", json={}, headers=_auth(token))
    assert response.status_code == 401


@pytest.mark.parametrize("deployment_environment", ["development", "staging"])
@pytest.mark.asyncio
async def test_deactivated_user_revokes_an_existing_admin_token(
    client, db_session, monkeypatch, deployment_environment
):
    user_id = uuid.uuid4()
    username = f"revoked-{user_id.hex}"
    user = User(
        id=user_id,
        username=username,
        email=f"{username}@example.invalid",
        full_name="Revoked admin",
        hashed_password=get_password_hash("ValidPassword123!"),
        role=RoleEnum.admin,
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(user)
    await db_session.commit()
    token = create_access_token(
        str(user_id),
        extra_claims={
            "username": username,
            "role": RoleEnum.admin.value,
            "is_active": True,
        },
    )

    user.is_active = False
    await db_session.commit()
    monkeypatch.setattr(
        routes.settings, "DEPLOYMENT_ENVIRONMENT", deployment_environment
    )
    response = await client.post(
        "/api/networks",
        json={"code": f"REVOKED-{user_id.hex[:8]}", "name": "must not persist"},
        headers={**_auth(token), "Idempotency-Key": f"revoked-{user_id.hex}"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_mock_write_requires_authentication(client):
    response = await client.put("/api/mock/config", json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_viewer_cannot_change_mock_configuration(client):
    response = await client.put(
        "/api/mock/config",
        json={},
        headers=_auth(_token(RoleEnum.viewer.value)),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_operator_can_change_mock_configuration(client, monkeypatch):
    monkeypatch.setattr(mock_generator, "alert_interval_seconds", 30)
    response = await client.put(
        "/api/mock/config",
        json={"alert_interval_seconds": 31},
        headers=_auth(_token(RoleEnum.operator.value)),
    )
    assert response.status_code == 200
    assert mock_generator.alert_interval_seconds == 31


@pytest.mark.asyncio
async def test_unknown_role_is_forbidden_not_server_error(client):
    response = await client.put(
        "/api/mock/config",
        json={},
        headers=_auth(_token("not-a-role")),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_production_legacy_sse_remains_public(client, monkeypatch):
    cursors: list[str | None] = []

    async def connect(last_event_id):
        cursors.append(last_event_id)
        return "legacy-client", object()

    async def events(_client_id, _queue, _last_event_id):
        if False:
            yield {}

    monkeypatch.setattr(routes.settings, "DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setattr(routes.sse_manager, "connect", connect)
    monkeypatch.setattr(routes.sse_manager, "event_generator", events)

    response = await client.get(
        "/api/sse/events", headers={"Last-Event-ID": "event-42"}
    )

    assert response.status_code == 200
    assert cursors == ["event-42"]


class _FakeWebSocket:
    def __init__(
        self,
        authorization: str | None = None,
        first_message: dict | None = None,
    ):
        self.headers = {"authorization": authorization} if authorization else {}
        self.first_message = first_message
        self.accepted = False
        self.close_code = None

    async def accept(self):
        self.accepted = True

    async def receive_json(self):
        if self.first_message is None:
            raise RuntimeError("no authentication frame")
        return self.first_message

    async def close(self, code: int):
        self.close_code = code


@pytest.mark.asyncio
async def test_control_websocket_rejects_deactivated_existing_token(
    db_session, monkeypatch
):
    user_id = uuid.uuid4()
    username = f"ws-revoked-{user_id.hex}"
    db_session.add(
        User(
            id=user_id,
            username=username,
            email=f"{username}@example.invalid",
            full_name="Revoked operator",
            hashed_password=get_password_hash("ValidPassword123!"),
            role=RoleEnum.operator,
            is_active=False,
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()
    token = create_access_token(
        str(user_id),
        extra_claims={
            "username": username,
            "role": RoleEnum.operator.value,
            "is_active": True,
        },
    )
    websocket = _FakeWebSocket(_auth(token)["Authorization"])
    monkeypatch.setattr(routes.settings, "DEPLOYMENT_ENVIRONMENT", "staging")
    monkeypatch.setattr(routes, "get_db_session", lambda: _session_for(db_session))

    claims = await routes._require_websocket_role(websocket, {"operator", "admin"})

    assert claims is None
    assert websocket.close_code == 1008


@pytest.mark.asyncio
async def test_control_websocket_rejects_missing_bearer():
    websocket = _FakeWebSocket()
    assert (
        await routes._require_websocket_role(websocket, {"operator", "admin"}) is None
    )
    assert websocket.close_code == 1008


@pytest.mark.asyncio
async def test_control_websocket_accepts_browser_authentication_frame(
    db_session, monkeypatch
):
    token = _token(RoleEnum.operator.value)
    websocket = _FakeWebSocket(first_message={"type": "authenticate", "token": token})
    monkeypatch.setattr(routes, "get_db_session", lambda: _session_for(db_session))

    claims = await routes._require_websocket_role(websocket, {"operator", "admin"})

    assert claims is not None
    assert claims["role"] == RoleEnum.operator.value
    assert websocket.accepted is True
    assert websocket.close_code is None


@pytest.mark.asyncio
async def test_control_websocket_rejects_viewer(db_session, monkeypatch):
    websocket = _FakeWebSocket(_auth(_token(RoleEnum.viewer.value))["Authorization"])
    monkeypatch.setattr(routes, "get_db_session", lambda: _session_for(db_session))
    assert (
        await routes._require_websocket_role(websocket, {"operator", "admin"}) is None
    )
    assert websocket.close_code == 1008


@pytest.mark.asyncio
async def test_control_websocket_allows_operator(db_session, monkeypatch):
    websocket = _FakeWebSocket(_auth(_token(RoleEnum.operator.value))["Authorization"])
    monkeypatch.setattr(routes, "get_db_session", lambda: _session_for(db_session))
    claims = await routes._require_websocket_role(websocket, {"operator", "admin"})
    assert claims is not None
    assert claims["role"] == RoleEnum.operator.value
    assert websocket.close_code is None
