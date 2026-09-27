"""
安全认证模块
--------------
JWT Token 生成与验证，密码哈希，RBAC 角色检查。
"""

from __future__ import annotations

import ipaddress
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, cast
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db_session
from app.models.db_models import RoleEnum, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
_SAFE_JWT_ALGORITHMS = frozenset(
    {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
)


class OutboundURLRejected(ValueError):
    """Raised when a user supplied URL is not safe for an outbound request."""


@dataclass(frozen=True)
class ResolvedOutboundURL:
    """A validated URL plus the exact public address to connect to."""

    original_url: str
    connection_url: str
    host_header: str
    sni_hostname: str
    address: str


def extract_bearer_token(authorization: str | None) -> str | None:
    """Extract a bearer token without accepting query-string credentials."""
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _is_public_ip(address: str) -> bool:
    """Return whether every address is globally routable.

    ``ipaddress.is_global`` alone is not sufficient across Python versions for
    IPv4-mapped IPv6 and some reserved ranges, so the explicitly dangerous
    classes are checked as well.
    """
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
        or not parsed.is_global
    )


def resolve_outbound_url(
    url: str,
    *,
    allowed_schemes: set[str] | frozenset[str] = frozenset(
        {"http", "https", "rtsp", "rtsps", "rtmp", "rtmps"}
    ),
) -> ResolvedOutboundURL:
    """Resolve and validate a URL, returning a connection-pinned equivalent.

    Callers must connect to ``connection_url`` rather than resolving the
    original hostname again. The original host is retained in ``Host`` and TLS
    SNI, closing the DNS-rebinding gap between validation and socket creation.
    """
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise OutboundURLRejected("outbound URL is invalid")
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise OutboundURLRejected("outbound URL is invalid") from exc

    if scheme not in {item.lower() for item in allowed_schemes}:
        raise OutboundURLRejected("outbound URL scheme is not allowed")
    if not hostname or parsed.username or parsed.password or parsed.fragment:
        raise OutboundURLRejected(
            "outbound URL credentials or fragment are not allowed"
        )

    default_ports = {
        "http": 80,
        "https": 443,
        "rtsp": 554,
        "rtsps": 322,
        "rtmp": 1935,
        "rtmps": 443,
    }
    allowed_ports = {
        "http": {80},
        "https": {443},
        "rtsp": {554},
        "rtsps": {322},
        "rtmp": {1935},
        "rtmps": {443},
    }
    effective_port = port or default_ports.get(scheme)
    if effective_port not in allowed_ports.get(scheme, set()):
        raise OutboundURLRejected("outbound URL port is not allowed")

    try:
        ascii_hostname = hostname.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise OutboundURLRejected("outbound URL host is invalid") from exc
    host_header = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    try:
        literal = ipaddress.ip_address(ascii_hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(str(literal)):
            raise OutboundURLRejected("outbound URL resolves to a non-public address")
        address = str(literal)
    else:
        try:
            addresses = socket.getaddrinfo(
                ascii_hostname,
                effective_port,
                type=socket.SOCK_STREAM,
            )
        except (OSError, socket.gaierror) as exc:
            raise OutboundURLRejected("outbound URL host cannot be resolved") from exc
        if not addresses or any(
            not _is_public_ip(str(item[4][0])) for item in addresses
        ):
            raise OutboundURLRejected("outbound URL resolves to a non-public address")
        address = str(ipaddress.ip_address(str(addresses[0][4][0]).split("%", 1)[0]))

    connection_host = f"[{address}]" if ":" in address else address
    connection_url = urlunsplit(
        (
            scheme,
            connection_host,
            parsed.path or "/",
            parsed.query,
            "",
        )
    )
    return ResolvedOutboundURL(
        original_url=url,
        connection_url=connection_url,
        host_header=host_header,
        sni_hostname=ascii_hostname,
        address=address,
    )


def media_protocol_allowlist(url: str) -> str:
    """Return FFmpeg's protocol set for one already-approved media URL."""
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError as exc:
        raise OutboundURLRejected("outbound URL is invalid") from exc
    allowlists = {
        "rtsp": "rtsp,tcp,tls",
        "rtsps": "rtsps,tcp,tls",
        "rtmp": "rtmp,tcp,tls",
        "rtmps": "rtmps,tcp,tls",
    }
    try:
        return allowlists[scheme]
    except KeyError as exc:
        raise OutboundURLRejected("outbound URL scheme is not allowed") from exc


def validate_outbound_url(
    url: str,
    *,
    allowed_schemes: set[str] | frozenset[str] = frozenset(
        {"http", "https", "rtsp", "rtsps", "rtmp", "rtmps"}
    ),
) -> str:
    """Validate a URL before handing it to an outbound client or media process."""
    return resolve_outbound_url(url, allowed_schemes=allowed_schemes).original_url


def _is_known_role(value: Any) -> bool:
    return isinstance(value, str) and value in {role.value for role in RoleEnum}


def _unauthenticated(detail: str = "authentication is required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _validate_identity_claims(payload: dict[str, Any]) -> None:
    if payload.get("is_active") is False:
        raise _unauthenticated("用户已停用")
    role = payload.get("role")
    if not isinstance(role, str) or not role.strip():
        raise _unauthenticated("authenticated role is missing")
    if not _is_known_role(role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="authenticated role is not recognised",
        )


async def authenticate_access_token(token: str, db: AsyncSession) -> dict[str, Any]:
    """Validate a token and its current user record when one exists.

    JWT role/activity claims are only issuance-time hints. If the subject maps
    to a persisted user, authorization reads that authoritative row so a
    deactivation or role change revokes access immediately. Deployments also
    reject unknown subjects instead of trusting a signed development identity.
    """
    payload = decode_access_token(token)
    if payload is None:
        raise _unauthenticated("无效的认证令牌")
    _validate_identity_claims(payload)

    deployment = settings.DEPLOYMENT_ENVIRONMENT in {"staging", "production"}
    try:
        user_id = uuid.UUID(str(payload.get("sub", "")))
    except (TypeError, ValueError, AttributeError) as exc:
        if deployment:
            raise _unauthenticated("无效的认证令牌") from exc
        return payload

    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证状态暂时不可用",
        ) from exc

    if user is None:
        if deployment:
            raise _unauthenticated("用户已停用")
        return payload
    if not user.is_active:
        raise _unauthenticated("用户已停用")

    current = dict(payload)
    current.update(
        {
            "role": user.role.value if isinstance(user.role, RoleEnum) else user.role,
            "username": user.username,
            "is_active": True,
        }
    )
    return current


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希是否匹配"""
    return bool(pwd_context.verify(plain_password, hashed_password))


def get_password_hash(password: str) -> str:
    """生成密码的 bcrypt 哈希"""
    return str(pwd_context.hash(password))


def _is_asymmetric_algorithm(algorithm: str) -> bool:
    """Return whether an algorithm requires PEM key material."""
    return algorithm.startswith(("RS", "ES", "PS"))


def _verification_algorithms() -> tuple[str, ...]:
    """Return the runtime verification domain, independent of stale config."""
    if settings.DEPLOYMENT_ENVIRONMENT == "production":
        return ("RS256",)
    return tuple(settings.JWT_ALGORITHMS)


def _signing_key() -> str:
    """Select signing material from the configured algorithm."""
    algorithm = settings.JWT_ALGORITHM
    if settings.DEPLOYMENT_ENVIRONMENT == "production" and algorithm != "RS256":
        raise RuntimeError("production JWT signing is restricted to RS256")
    if _is_asymmetric_algorithm(algorithm):
        if not settings.JWT_PRIVATE_KEY:
            raise RuntimeError("JWT_PRIVATE_KEY is required for asymmetric signing")
        return settings.JWT_PRIVATE_KEY
    if not settings.JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY is required for symmetric signing")
    return settings.JWT_SECRET_KEY


def _verification_key(algorithm: str) -> str | None:
    """Select the key for a token's already-allowlisted algorithm.

    The unverified header is used only to choose between the configured HMAC
    secret and RSA public key.  ``jwt.decode`` still verifies the signature,
    registered claims, issuer, audience, and the allowlist below.
    """
    if _is_asymmetric_algorithm(algorithm):
        return settings.JWT_PUBLIC_KEY or None
    return settings.JWT_SECRET_KEY or None


def create_access_token(
    subject: str | Any,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """
    创建 JWT Access Token

    Args:
        subject: 令牌主体 (通常是 user_id)
        expires_delta: 自定义过期时间增量
        extra_claims: 额外的 JWT claims

    Returns:
        编码后的 JWT 字符串
    """
    if expires_delta is not None:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    issued_at = datetime.now(timezone.utc)
    to_encode: dict[str, Any] = {
        "exp": expire,
        "sub": str(subject),
        "iat": issued_at,
        "nbf": issued_at,
        "type": "access",
    }

    if extra_claims:
        to_encode.update(extra_claims)

    # Identity, lifetime and token type are server-owned boundaries; callers
    # may add profile claims but cannot extend a token or change its subject.
    to_encode.update(
        {
            "sub": str(subject),
            "exp": expire,
            "iat": issued_at,
            "nbf": issued_at,
            "type": "access",
        }
    )
    if "role" not in to_encode:
        to_encode["role"] = RoleEnum.operator.value

    # Applied last so caller-supplied claims cannot spoof the token identity.
    if settings.JWT_ISSUER:
        to_encode["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        to_encode["aud"] = settings.JWT_AUDIENCE

    # Settings guarantees JWT_ALGORITHM is part of the locked allowlist.
    return str(jwt.encode(to_encode, _signing_key(), algorithm=settings.JWT_ALGORITHM))


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    """
    解码并验证 JWT Token

    Signature algorithms are restricted to the configured allowlist and the
    ``iss``/``aud`` claims are verified whenever they are configured (both are
    mandatory in production). ``exp``/``iat``/``sub`` are always required.

    Args:
        token: JWT 字符串

    Returns:
        解码后的 payload，失败返回 None
    """
    options: dict[str, Any] = {
        "require_exp": True,
        "require_iat": True,
        "require_nbf": True,
        "require_sub": True,
    }
    if settings.JWT_ISSUER:
        options["require_iss"] = True
    if settings.JWT_AUDIENCE:
        options["require_aud"] = True
    try:
        verification_algorithms = _verification_algorithms()
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if (
            not isinstance(algorithm, str)
            or algorithm not in _SAFE_JWT_ALGORITHMS
            or algorithm not in verification_algorithms
        ):
            return None
        verification_key = _verification_key(algorithm)
        if verification_key is None:
            return None
        payload = cast(
            dict[str, Any],
            jwt.decode(
                token,
                verification_key,
                algorithms=list(verification_algorithms),
                audience=settings.JWT_AUDIENCE or None,
                issuer=settings.JWT_ISSUER or None,
                options=options,
            ),
        )
        if payload.get("type") != "access" or not str(payload.get("sub", "")).strip():
            return None
        return payload
    except JWTError:
        return None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db_session),
) -> Optional[dict]:
    """获取当前认证用户 (可选认证)"""
    if credentials is None:
        return None
    return await authenticate_access_token(credentials.credentials, db)


async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """强制认证依赖；缺少 Bearer 凭证必须稳定返回 401。"""
    if credentials is None or not credentials.credentials:
        raise _unauthenticated()
    return await authenticate_access_token(credentials.credentials, db)


def require_role(*allowed_roles: RoleEnum):
    """RBAC 角色检查依赖注入工厂。"""

    async def role_checker(
        current_user: Optional[dict] = Depends(get_current_user),
    ) -> dict:
        if current_user is None:
            raise _unauthenticated("未认证")
        user_role = current_user.get("role")
        try:
            role = RoleEnum(user_role)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="authenticated role is not recognised",
            ) from None
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{user_role}' not permitted. "
                    f"Required: {[r.value for r in allowed_roles]}"
                ),
            )
        return current_user

    return role_checker
