"""Shared helper for any "emailed link with a token" flow (password
reset, email-change verification). The token that goes in the email
is a high-entropy random string; only its SHA-256 hash is ever stored
in the database, same principle as a password hash -- if the DB
leaked, the tokens in it couldn't be replayed.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def expires_at(minutes: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def is_expired(expiry: datetime | None) -> bool:
    if expiry is None:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) > expiry
