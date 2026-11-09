# Tests for patch 19e: login limit. PATCH19E-LOGIN-LIMIT
# Run ONLY against shelf_test.
import uuid

import pytest

from api.v1.routers import auth as auth_router
from infrastructure.db.models import User
from infrastructure.security.passwords import hash_password

pytestmark = pytest.mark.asyncio

GOOD = "Password123!"
BAD = "WrongPass999!"
LOCK_TEXT = "Too many failed attempts. Please wait 15 minutes and try again."


async def _user(db, role="cashier"):
    tag = uuid.uuid4().hex[:10]
    email = "p19e" + tag + "@example.com"
    db.add(User(full_name="P19E " + role, role=role, email=email,
                password_hash=hash_password(GOOD), is_active=True))
    await db.commit()
    return email


async def _login(client, email, password):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def test_a_sixth_attempt_after_five_wrong_is_429(client, db_session):
    email = await _user(db_session)
    for i in range(5):
        r = await _login(client, email, BAD)
        assert r.status_code == 401, (i, r.text)
    r = await _login(client, email, BAD)
    assert r.status_code == 429, r.text
    assert r.json()["detail"] == LOCK_TEXT
    print("PASS a: 5 wrong give 401, the 6th gives 429 with the exact text")


async def test_b_right_password_is_refused_while_locked(client, db_session):
    email = await _user(db_session)
    for _ in range(5):
        await _login(client, email, BAD)
    r = await _login(client, email, GOOD)
    assert r.status_code == 429, r.text
    print("PASS b: the right password is refused while locked")


async def test_c_right_password_resets_the_count(client, db_session):
    email = await _user(db_session)
    for _ in range(4):
        assert (await _login(client, email, BAD)).status_code == 401
    assert (await _login(client, email, GOOD)).status_code == 200
    for i in range(4):
        r = await _login(client, email, BAD)
        assert r.status_code == 401, (i, r.text)
    print("PASS c: a right password clears the count")


async def test_d_other_email_is_not_affected(client, db_session):
    locked = await _user(db_session)
    other = await _user(db_session)
    for _ in range(5):
        await _login(client, locked, BAD)
    assert (await _login(client, locked, GOOD)).status_code == 429
    assert (await _login(client, other, GOOD)).status_code == 200
    print("PASS d: a different email is not affected")


async def test_e_lock_ends_after_15_minutes(client, db_session, monkeypatch):
    email = await _user(db_session)
    for _ in range(5):
        await _login(client, email, BAD)
    assert (await _login(client, email, GOOD)).status_code == 429
    later = auth_router._login_now() + 16 * 60
    monkeypatch.setattr(auth_router, "_login_now", lambda: later)
    assert (await _login(client, email, GOOD)).status_code == 200
    print("PASS e: the lock ends after 15 minutes (fake clock)")


async def test_f_unknown_email_is_counted_too(client, db_session):
    email = "nobody" + uuid.uuid4().hex[:10] + "@example.com"
    for i in range(5):
        r = await _login(client, email, BAD)
        assert r.status_code == 401, (i, r.text)
    r = await _login(client, email, BAD)
    assert r.status_code == 429, r.text
    print("PASS f: an unknown email is counted and locked the same way")


async def test_g_storefront_service_is_never_locked(client, db_session):
    email = await _user(db_session, role="storefront_service")
    for i in range(8):
        r = await _login(client, email, BAD)
        assert r.status_code == 401, (i, r.text)
    r = await _login(client, email, GOOD)
    assert r.status_code == 200, r.text
    print("PASS g: the storefront_service role is never locked")


async def test_h_normal_login_still_works(client, db_session):
    email = await _user(db_session)
    r = await _login(client, email, GOOD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    print("PASS h: a normal login still returns tokens")
