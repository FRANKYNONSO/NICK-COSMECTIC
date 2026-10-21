import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jose import JWTError, jwt

# apps/api/.env  (this file lives in apps/api/infrastructure/security/)
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

_INSECURE_SECRETS = {
    "",
    "dev-only-insecure-secret-change-me",
}


def _read_secret_from_env_file() -> str | None:
    """Read JWT_SECRET_KEY from apps/api/.env (only this one setting)."""
    try:
        text = _ENV_FILE.read_text(encoding="utf-8-sig")  # tolerates a Windows BOM
    except OSError:
        return None

    found = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, sep, value = line.partition("=")
        if not sep or name.strip() != "JWT_SECRET_KEY":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        found = value.strip()  # last occurrence wins
    return found


def _load_secret() -> str:
    from_env = os.environ.get("JWT_SECRET_KEY", "").strip()
    if from_env:
        secret, source = from_env, "environment variable"
    else:
        secret, source = (_read_secret_from_env_file() or ""), str(_ENV_FILE)

    lowered = secret.lower()
    if secret in _INSECURE_SECRETS or "change-me" in lowered or "changeme" in lowered:
        raise RuntimeError(
            "JWT_SECRET_KEY is missing or still a placeholder. "
            "Put a real value in apps\\api\\.env as a line like JWT_SECRET_KEY=<64 random characters>. "
            'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
        )

    if len(secret) < 32:
        print("[jwt] WARNING: JWT_SECRET_KEY is shorter than 32 characters", flush=True)
    # Never print the secret itself, only where it came from and its length.
    print(f"[jwt] secret loaded from {source} ({len(secret)} characters)", flush=True)
    return secret


JWT_SECRET_KEY = _load_secret()
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
# Access tokens used to expire after 15 minutes, relying entirely on the
# frontend's silent-refresh-on-401 flow to renew them invisibly. In
# practice that made the app feel like it randomly logged people out
# whenever that refresh hiccuped (a network blip, two requests racing
# the same refresh token, etc). Staff expect to stay signed in on a
# shop till until they explicitly log out, so both lifetimes are now
# long-lived by default; the frontend's silent refresh is kept as a
# safety net, not the primary mechanism keeping someone logged in.
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24h
REFRESH_TOKEN_EXPIRE_DAYS = int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "90"))


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
    """Decode and validate a JWT. Raises TokenError on any problem --
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


# PATCH25B-PASSWORD-CHANGED-AT
def mark_password_changed(user) -> None:
    """Call right after a password is changed, before the commit. Tokens issued
    before this moment are refused afterwards."""
    user.password_changed_at = datetime.now(timezone.utc)


def token_predates_password_change(payload: dict, password_changed_at) -> bool:
    """True if the token was issued before the password last changed.
    An empty column means no limit. Once a change is on record, a token without a
    usable 'iat' counts as old. 'iat' is whole seconds, so the change time is cut to
    whole seconds before comparing: a token issued in the same second as the change
    is accepted."""
    if password_changed_at is None:
        return False
    issued = payload.get("iat")
    if isinstance(issued, bool) or not isinstance(issued, (int, float)):
        return True
    if password_changed_at.tzinfo is None:
        password_changed_at = password_changed_at.replace(tzinfo=timezone.utc)
    return int(issued) < int(password_changed_at.timestamp())