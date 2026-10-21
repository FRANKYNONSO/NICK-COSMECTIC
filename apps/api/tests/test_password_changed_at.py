"""Tests for patch 25b: a token issued before a password change is refused."""
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt as jose_jwt

from api.dependencies import get_current_user_optional
from infrastructure.db.models import User
from infrastructure.security.jwt import (
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    create_access_token,
    create_refresh_token,
    decode_token,
    mark_password_changed,
    token_predates_password_change,
)
from infrastructure.security.passwords import hash_password
from infrastructure.security.tokens import expires_at, generate_token, hash_token
from main import app

OLD_PASSWORD = "Password123!"
NEW_PASSWORD = "BrandNewPassword9!"


async def _make_user(db_session, role="staff"):
    user = User(
        full_name="Test User",
        role=role,
        email=uuid.uuid4().hex[:8] + "@example.com",
        password_hash=hash_password(OLD_PASSWORD),
        is_active=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _token(user, token_type, seconds_ago):
    """A real signed token whose 'iat' lies seconds_ago in the past."""
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "role": user.role,
        "branch_id": str(user.branch_id) if user.branch_id else None,
        "type": token_type,
        "iat": now - seconds_ago,
        "exp": now + 3600,
        "jti": uuid.uuid4().hex,
    }
    return jose_jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def _bearer(token):
    return {"Authorization": "Bearer " + token}


# HELPER-FIXED-PATHS: this FastAPI hides routes inside _IncludedRouter wrappers,
# so the route list cannot be scanned. These paths were read from main.app.openapi().
_API_PATHS = {
    ("/auth/me", "GET"): "/api/v1/auth/me",
    ("/auth/login", "POST"): "/api/v1/auth/login",
    ("/auth/refresh", "POST"): "/api/v1/auth/refresh",
    ("/auth/reset-password", "POST"): "/api/v1/auth/reset-password",
    ("/change-password", "POST"): "/api/v1/me/change-password",
}


def _find_path(suffix, method, prefer=None):
    return _API_PATHS[(suffix, method)]


# ---------------------------------------------------------------- the rule itself


def test_empty_column_means_no_limit():
    assert token_predates_password_change({"iat": 1}, None) is False
    assert token_predates_password_change({}, None) is False


def test_older_token_is_refused_newer_and_same_second_are_accepted():
    changed = datetime(2026, 5, 1, 12, 0, 5, 700000, tzinfo=timezone.utc)
    second = int(changed.timestamp())
    assert token_predates_password_change({"iat": second - 1}, changed) is True
    assert token_predates_password_change({"iat": second}, changed) is False
    assert token_predates_password_change({"iat": second + 30}, changed) is False


def test_token_without_a_usable_iat_counts_as_old():
    changed = datetime.now(timezone.utc)
    for payload in ({}, {"iat": None}, {"iat": "123"}, {"iat": True}):
        assert token_predates_password_change(payload, changed) is True


def test_naive_change_time_is_read_as_utc():
    naive = datetime(2026, 5, 1, 12, 0, 5)
    second = int(naive.replace(tzinfo=timezone.utc).timestamp())
    assert token_predates_password_change({"iat": second - 1}, naive) is True
    assert token_predates_password_change({"iat": second}, naive) is False


def test_mark_password_changed_sets_an_aware_recent_time():
    user = SimpleNamespace(password_changed_at=None)
    mark_password_changed(user)
    assert user.password_changed_at.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - user.password_changed_at).total_seconds()) < 5


def test_real_tokens_carry_iat_as_whole_seconds():
    for kind, make in (("access", create_access_token), ("refresh", create_refresh_token)):
        token = make(str(uuid.uuid4()), "staff", None)
        payload = decode_token(token, expected_type=kind)
        assert isinstance(payload["iat"], int)
        assert abs(payload["iat"] - int(time.time())) <= 5


# ---------------------------------------------------------------- through the API


@pytest.mark.asyncio
async def test_own_password_change_logs_out_every_older_login(client, db_session):
    user = await _make_user(db_session)
    await db_session.commit()
    me = _find_path("/auth/me", "GET")
    change = _find_path("/change-password", "POST", prefer="/me/")
    refresh = _find_path("/auth/refresh", "POST")
    old_access = _token(user, "access", 30)
    old_refresh = _token(user, "refresh", 30)

    assert (await client.get(me, headers=_bearer(old_access))).status_code == 200

    resp = await client.post(
        change,
        json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
        headers=_bearer(old_access),
    )
    assert resp.status_code == 204
    assert user.password_changed_at is not None

    assert (await client.get(me, headers=_bearer(old_access))).status_code == 401
    assert (await client.post(refresh, json={"refresh_token": old_refresh})).status_code == 401


@pytest.mark.asyncio
async def test_signing_in_again_after_a_change_works(client, db_session):
    user = await _make_user(db_session)
    await db_session.commit()
    me = _find_path("/auth/me", "GET")
    change = _find_path("/change-password", "POST", prefer="/me/")
    refresh = _find_path("/auth/refresh", "POST")
    login = _find_path("/auth/login", "POST")

    resp = await client.post(
        change,
        json={"current_password": OLD_PASSWORD, "new_password": NEW_PASSWORD},
        headers=_bearer(_token(user, "access", 30)),
    )
    assert resp.status_code == 204

    signed_in = await client.post(login, json={"email": user.email, "password": NEW_PASSWORD})
    assert signed_in.status_code == 200
    tokens = signed_in.json()
    assert (await client.get(me, headers=_bearer(tokens["access_token"]))).status_code == 200
    renewed = await client.post(refresh, json={"refresh_token": tokens["refresh_token"]})
    assert renewed.status_code == 200


@pytest.mark.asyncio
async def test_admin_reset_logs_out_the_staff_member_only(client, db_session):
    admin = await _make_user(db_session, role="super_admin")
    staff = await _make_user(db_session)
    await db_session.commit()
    me = _find_path("/auth/me", "GET")
    old_staff = _token(staff, "access", 30)
    admin_token = _token(admin, "access", 30)

    resp = await client.post(
        "/api/v1/users/" + str(staff.id) + "/reset-password",
        json={"new_password": NEW_PASSWORD},
        headers=_bearer(admin_token),
    )
    assert resp.status_code == 204

    assert (await client.get(me, headers=_bearer(old_staff))).status_code == 401
    assert (await client.get(me, headers=_bearer(admin_token))).status_code == 200


@pytest.mark.asyncio
async def test_emailed_link_reset_logs_out_older_logins(client, db_session):
    user = await _make_user(db_session)
    raw = generate_token()
    user.password_reset_token_hash = hash_token(raw)
    user.password_reset_expires_at = expires_at(30)
    await db_session.commit()
    me = _find_path("/auth/me", "GET")
    reset = _find_path("/auth/reset-password", "POST")
    old_access = _token(user, "access", 30)

    assert (await client.get(me, headers=_bearer(old_access))).status_code == 200
    resp = await client.post(reset, json={"token": raw, "new_password": NEW_PASSWORD})
    assert resp.status_code == 204
    assert (await client.get(me, headers=_bearer(old_access))).status_code == 401


@pytest.mark.asyncio
async def test_optional_user_is_none_for_an_older_token_after_a_change(db_session):
    user = await _make_user(db_session)
    await db_session.commit()
    old = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_token(user, "access", 30))
    assert await get_current_user_optional(credentials=old, session=db_session) is not None

    user.password_changed_at = datetime.now(timezone.utc)
    await db_session.commit()
    assert await get_current_user_optional(credentials=old, session=db_session) is None

    fresh = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=create_access_token(str(user.id), user.role, None)
    )
    assert await get_current_user_optional(credentials=fresh, session=db_session) is not None
