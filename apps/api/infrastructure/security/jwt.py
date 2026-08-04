import os
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-only-insecure-secret-change-me")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "30"))


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or the wrong type."""


def _create_token(subject: str, role: str, branch_id: str | None, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "role": role,
        "branch_id": branch_id,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_access_token(user_id: str, role: str, branch_id: str | None) -> str:
    return _create_token(
        user_id, role, branch_id, "access", timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )


def create_refresh_token(user_id: str, role: str, branch_id: str | None) -> str:
    return _create_token(
        user_id, role, branch_id, "refresh", timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    )


def decode_token(token: str, expected_type: str) -> dict:
    """Decode and validate a JWT. Raises TokenError on any problem —
    callers (API dependencies) turn this into a 401, never leak the
    underlying jose exception type to the client.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise TokenError("Invalid or expired token") from exc

    if payload.get("type") != expected_type:
        raise TokenError(f"Expected a '{expected_type}' token, got '{payload.get('type')}'")

    return payload
