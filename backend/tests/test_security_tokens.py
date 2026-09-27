"""Runtime coverage for the JWT algorithm allowlist and iss/aud enforcement."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.core import security
from app.core.config import Settings


@pytest.fixture
def jwt_settings(monkeypatch):
    """Pin the values the module-level settings singleton exposes to security.py."""
    settings = security.settings
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "unit-test-signing-secret")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "JWT_ALGORITHMS", ["HS256"])
    monkeypatch.setattr(settings, "JWT_ISSUER", "smart-city-drainage")
    monkeypatch.setattr(settings, "JWT_AUDIENCE", "smart-city-api")
    return settings


def _claims(**overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "user-1",
        "role": "operator",
        "type": "access",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "iss": "smart-city-drainage",
        "aud": "smart-city-api",
    }
    claims.update(overrides)
    return claims


def test_round_trip_token_carries_and_verifies_iss_aud(jwt_settings):
    token = security.create_access_token("user-1", extra_claims={"role": "admin"})

    payload = security.decode_access_token(token)

    assert payload is not None
    assert payload["sub"] == "user-1"
    assert payload["iss"] == "smart-city-drainage"
    assert payload["aud"] == "smart-city-api"
    assert payload["role"] == "admin"


def test_extra_claims_cannot_spoof_issuer_or_audience(jwt_settings):
    token = security.create_access_token(
        "user-1", extra_claims={"iss": "evil", "aud": "evil"}
    )

    payload = security.decode_access_token(token)

    assert payload is not None
    assert payload["iss"] == "smart-city-drainage"
    assert payload["aud"] == "smart-city-api"


@pytest.mark.parametrize(
    ("claim", "value", "algorithm"),
    [
        ("iss", "evil-issuer", "HS256"),
        ("aud", "evil-audience", "HS256"),
    ],
)
def test_wrong_issuer_or_audience_is_rejected(jwt_settings, claim, value, algorithm):
    token = jwt.encode(
        _claims(**{claim: value}), jwt_settings.JWT_SECRET_KEY, algorithm=algorithm
    )

    assert security.decode_access_token(token) is None


def test_algorithm_outside_allowlist_is_rejected(jwt_settings):
    token = jwt.encode(_claims(), jwt_settings.JWT_SECRET_KEY, algorithm="HS512")

    assert security.decode_access_token(token) is None


def test_algorithm_inside_allowlist_is_accepted(jwt_settings, monkeypatch):
    monkeypatch.setattr(jwt_settings, "JWT_ALGORITHMS", ["HS256", "HS512"])
    token = jwt.encode(_claims(), jwt_settings.JWT_SECRET_KEY, algorithm="HS512")

    payload = security.decode_access_token(token)

    assert payload is not None
    assert payload["sub"] == "user-1"


def test_unsigned_none_token_is_rejected(jwt_settings):
    token = jwt.encode(_claims(), jwt_settings.JWT_SECRET_KEY, algorithm="HS256")
    unsigned = token.rsplit(".", 1)[0] + "."

    assert jwt.get_unverified_header(unsigned) is not None
    assert security.decode_access_token(unsigned) is None


@pytest.mark.parametrize("missing", ["exp", "iat", "sub"])
def test_required_time_and_subject_claims_are_enforced(jwt_settings, missing):
    claims = _claims()
    claims.pop(missing)
    token = jwt.encode(claims, jwt_settings.JWT_SECRET_KEY, algorithm="HS256")

    assert security.decode_access_token(token) is None


def test_expired_token_is_rejected(jwt_settings):
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        _claims(iat=now - timedelta(hours=2), exp=now - timedelta(hours=1)),
        jwt_settings.JWT_SECRET_KEY,
        algorithm="HS256",
    )

    assert security.decode_access_token(token) is None


def test_token_signed_with_another_secret_is_rejected(jwt_settings):
    token = jwt.encode(_claims(), "another-secret", algorithm="HS256")

    assert security.decode_access_token(token) is None


def test_production_rejects_hmac_even_if_allowlist_is_mutated(
    jwt_settings, monkeypatch
):
    monkeypatch.setattr(jwt_settings, "DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setattr(jwt_settings, "JWT_ALGORITHMS", ["HS256", "RS256"])
    token = jwt.encode(_claims(), jwt_settings.JWT_SECRET_KEY, algorithm="HS256")

    assert security.decode_access_token(token) is None


def test_production_rs256_round_trip(jwt_settings, monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    monkeypatch.setattr(jwt_settings, "DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setattr(jwt_settings, "JWT_ALGORITHM", "RS256")
    monkeypatch.setattr(jwt_settings, "JWT_ALGORITHMS", ["RS256"])
    monkeypatch.setattr(jwt_settings, "JWT_PRIVATE_KEY", private_pem)
    monkeypatch.setattr(jwt_settings, "JWT_PUBLIC_KEY", public_pem)

    token = security.create_access_token("user-1", extra_claims={"role": "admin"})
    payload = security.decode_access_token(token)

    assert payload is not None
    assert payload["sub"] == "user-1"
    assert payload["role"] == "admin"


def test_settings_reject_legacy_signer_outside_allowlist():
    with pytest.raises(Exception, match="JWT_ALGORITHM"):
        Settings(
            _env_file=None, JWT_ALGORITHM="HS512", JWT_ALGORITHMS=["HS256", "RS256"]
        )


def test_settings_accept_signer_inside_allowlist():
    settings = Settings(
        _env_file=None, JWT_ALGORITHM="HS256", JWT_ALGORITHMS=["HS256", "RS256"]
    )

    assert settings.JWT_ALGORITHM in settings.JWT_ALGORITHMS
