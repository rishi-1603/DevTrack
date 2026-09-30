"""Password hashing and JWT token utilities."""
from datetime import datetime, timedelta, timezone
from typing import Any

# PyJWT rather than python-jose (Day-7 remediation, finding S2). python-jose
# 3.3.0 carried PYSEC-2024-232/233 (fixed in 3.4.0) plus PYSEC-2025-185, which
# has no published fix at all, and the project is effectively unmaintained; it
# also pulled in `ecdsa`, which has an unfixed advisory of its own. The API
# surface used across this codebase is two calls (`encode`, `decode`) and one
# exception type, so the migration is exact: `JWTError` becomes `PyJWTError`,
# which is likewise the base class for ExpiredSignatureError and
# InvalidSignatureError, so every existing 401 path behaves identically.
import jwt
from jwt.exceptions import PyJWTError
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against its bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


def _create_token(subject: str, expires_delta: timedelta, extra_claims: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(subject: str) -> str:
    """Create a short-lived JWT access token for the given subject (user id)."""
    return _create_token(
        subject,
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims={"type": "access"},
    )


def create_refresh_token(subject: str) -> str:
    """Create a long-lived JWT refresh token for the given subject (user id)."""
    return _create_token(
        subject,
        timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
        extra_claims={"type": "refresh"},
    )


def create_ws_token(subject: str) -> str:
    """Create a very short-lived ticket that is valid ONLY for the WebSocket
    handshake (Day-7 remediation, finding S9).

    Browsers cannot set an `Authorization` header when opening a WebSocket, so
    the credential has to travel in the query string -- and query strings end up
    in proxy logs, access logs and browser history. That is unavoidable, but
    what lands in those logs does not have to be a full-scope API credential.

    This ticket is `type: "ws"`, which `app/core/dependencies.py` rejects (it
    requires `type == "access"`), so a ticket leaked from a log line cannot be
    replayed against any REST endpoint. It also lives for
    `WS_TOKEN_EXPIRE_SECONDS` rather than `ACCESS_TOKEN_EXPIRE_MINUTES`, so the
    window in which a leaked ticket can open a socket is a minute at most.
    Clients mint one per connection from `POST /auth/ws-ticket`.
    """
    return _create_token(
        subject,
        timedelta(seconds=settings.WS_TOKEN_EXPIRE_SECONDS),
        extra_claims={"type": "ws"},
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises PyJWTError on failure."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


__all__ = [
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "create_ws_token",
    "decode_token",
    "PyJWTError",
]
